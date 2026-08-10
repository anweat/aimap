"""API 路由:采集、论文、领域树、地图、搜索、分析、状态。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agents.orchestrator import OrchestratorAgent
from app.config import settings
from app.crawler.registry import available_sources
from app.db import (
    category_stats,
    count_domain_nodes,
    count_papers,
    get_job,
    get_position,
    get_positions_by_ids,
    get_session,
    list_classifications,
    list_domain_nodes,
    list_jobs,
    list_papers,
    list_papers_with_positions,
    list_tags,
    search_papers,
    tag_stats,
    upsert_paper,
)
from app.domain.builder import build_domain_tree
from app.llm.registry import provider_status
from app.models.entities import Paper
from app.quaternion.core import Projector, Quaternion

router = APIRouter(prefix="/api")

_projector = Projector(scale=1.4)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class CrawlRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    max_results: int = Field(default=20, ge=1, le=200)
    analyze: bool = Field(default=True, description="爬取后是否立即触发多层分析")


class AnalyzeRequest(BaseModel):
    paper_ids: list[int] = Field(default_factory=list, max_length=500)
    all_unanchored: bool = False


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------
@router.get("/status")
def status():
    from sqlmodel import select

    from app.models.entities import CrawlJob

    with get_session() as s:
        n_jobs = len(s.exec(select(CrawlJob)).all())
        n_failed = len(s.exec(select(CrawlJob).where(CrawlJob.status == "failed")).all())
        n_dups = sum(j.total_duplicates for j in s.exec(select(CrawlJob)).all())
        return {
            "sources": available_sources(),
            "provider": provider_status(),
            "papers": count_papers(s),
            "domain_nodes": count_domain_nodes(s),
            "tree_ready": count_domain_nodes(s) > 0,
            "crawl": {
                "jobs": n_jobs,
                "failed": n_failed,
                "duplicates_filtered": n_dups,
                "min_interval": settings.crawl_min_interval,
                "max_retries": settings.crawl_max_retries,
                "circuit_threshold": settings.crawl_circuit_threshold,
            },
        }


# ---------------------------------------------------------------------------
# 采集(异步:创建即返回,轮询任务进度/日志)
# ---------------------------------------------------------------------------
@router.post("/crawl/arxiv")
def crawl_arxiv(req: CrawlRequest):
    """创建并异步执行 arXiv 采集任务。返回任务信息(前端轮询进度)。"""
    from app.crawler.service import CrawlService, job_to_dict

    with get_session() as s:
        job = CrawlService(s).create("arxiv", req.query, max_results=req.max_results,
                                     analyze=req.analyze, async_run=True)
        return job_to_dict(job)


@router.get("/crawl/jobs")
def crawl_jobs(limit: int = Query(default=50, ge=1, le=200)):
    from app.crawler.service import job_to_dict

    with get_session() as s:
        return {"jobs": [job_to_dict(j) for j in list_jobs(s, limit=limit)]}


@router.get("/crawl/jobs/{job_id}")
def crawl_job_detail(job_id: int):
    from app.crawler.service import job_to_dict

    with get_session() as s:
        job = get_job(s, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job_to_dict(job)


@router.get("/crawl/jobs/{job_id}/logs")
def crawl_job_logs(job_id: int, limit: int = Query(default=200, ge=1, le=500)):
    """任务过程日志(采集进度/重试/错误),供前端面板展示。"""
    from app.db import list_crawl_logs

    with get_session() as s:
        logs = list_crawl_logs(s, job_id, limit=limit)
        return {
            "logs": [
                {"ts": lg.ts.isoformat() if lg.ts else None, "level": lg.level, "message": lg.message}
                for lg in reversed(logs)  # 时间正序
            ]
        }


@router.post("/crawl/jobs/{job_id}/resume")
def crawl_job_resume(job_id: int):
    """断点续爬:从上次游标继续抓取。"""
    from app.crawler.service import CrawlService, job_to_dict

    with get_session() as s:
        try:
            job = CrawlService(s).resume_job(job_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if job.status == "failed":
            raise HTTPException(status_code=502, detail=f"续爬失败: {job.last_error}")
        return job_to_dict(job)


# ---------------------------------------------------------------------------
# 数据源管理(面板编辑/添加/探测)
# ---------------------------------------------------------------------------
class SourcePayload(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    display_name: str = ""
    source_type: str = Field(default="open", pattern="^(open|library)$")
    enabled: bool = True
    config: dict = Field(default_factory=dict)   # api_url / delay 等


@router.get("/sources")
def sources_list():
    import json as _json

    from app.db import list_sources

    with get_session() as s:
        rows = list_sources(s)
        return {
            "sources": [
                {
                    "name": src.name,
                    "display_name": src.display_name,
                    "source_type": src.source_type,
                    "enabled": src.enabled,
                    "config": _json.loads(src.config or "{}"),
                    "last_crawl_at": src.last_crawl_at.isoformat() if src.last_crawl_at else None,
                    "last_crawl_stats": _json.loads(src.last_crawl_stats or "{}"),
                    "session": _session_status(src),
                }
                for src in rows
            ]
        }


def _session_status(src) -> dict | None:
    """library 来源的登录会话状态(open 来源返回 None)。"""
    if src.source_type != "library":
        return None
    try:
        from app.crawler.auth import PlaywrightLoginManager

        manager = PlaywrightLoginManager(src.name)
        if not manager.has_session():
            return {"has": False}
        meta = manager.session_meta() or {}
        return {
            "has": True,
            "expired": meta.get("expired", False),
            "cookies": meta.get("cookie_count", 0),
            "expires_at": meta.get("expires_at"),
        }
    except Exception:
        return {"has": False, "error": "playwright 未安装"}


@router.post("/sources/{name}/login")
def source_login(name: str, timeout: int = 600):
    """图书馆来源登录:弹出浏览器(playwright),完成登录后保存会话。

    复用 scripts/library_login.py 的 PlaywrightLoginManager:
    人工模式下弹出有头浏览器,用户完成登录(验证码/SSO),自动检测成功后保存。
    """
    from app.crawler.auth import LoginTimeout, PlaywrightLoginManager, PlaywrightUnavailable
    from app.db import get_source

    with get_session() as s:
        source = get_source(s, name)
        if source is None:
            raise HTTPException(status_code=404, detail=f"数据源 {name} 不存在")
        if source.source_type != "library":
            raise HTTPException(status_code=400, detail=f"{name} 为公开源,无需登录")

    try:
        manager = PlaywrightLoginManager(name)
    except PlaywrightUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    cred = settings.library_credentials.get(name, {})
    try:
        meta = manager.login(
            account=cred.get("account", ""),
            password=cred.get("password", ""),
            headless=False,   # 弹出真实浏览器,由用户完成登录
            auto=False,
            timeout_s=timeout,
        )
    except LoginTimeout as e:
        return JSONResponse(
            status_code=504,
            content={
                "detail": f"[{name}] 登录等待超时,请重试",
                "diagnostics": e.diagnostics,
            },
        )
    return {
        "status": "ok",
        "source": name,
        "expires_at": meta.get("expires_at"),
        "verified": meta.get("verified"),
        "session_file": str(manager.session_file),
    }


@router.post("/sources")
def source_create(payload: SourcePayload):
    import json as _json

    from app.db import get_source, upsert_source
    from app.models.entities import SourceConfig

    with get_session() as s:
        if get_source(s, payload.name) is not None:
            raise HTTPException(status_code=409, detail=f"数据源 {payload.name} 已存在")
        src = upsert_source(
            s,
            SourceConfig(
                name=payload.name,
                display_name=payload.display_name or payload.name,
                source_type=payload.source_type,
                enabled=payload.enabled,
                config=_json.dumps(payload.config, ensure_ascii=False),
            ),
        )
        return {"name": src.name, "status": "created"}


@router.put("/sources/{name}")
def source_update(name: str, payload: SourcePayload):
    import json as _json

    from app.db import get_source, upsert_source
    from app.models.entities import SourceConfig

    with get_session() as s:
        existing = get_source(s, name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"数据源 {name} 不存在")
        payload.name = name  # 名称不可改
        src = upsert_source(
            s,
            SourceConfig(
                name=name,
                display_name=payload.display_name or existing.display_name,
                source_type=payload.source_type,
                enabled=payload.enabled,
                config=_json.dumps(payload.config, ensure_ascii=False),
            ),
        )
        return {"name": src.name, "status": "updated"}


@router.delete("/sources/{name}")
def source_delete(name: str):
    from app.db import delete_source

    with get_session() as s:
        if not delete_source(s, name):
            raise HTTPException(status_code=404, detail=f"数据源 {name} 不存在")
        return {"name": name, "status": "deleted"}


@router.post("/sources/{name}/probe")
def source_probe(name: str):
    """自动探测来源状态(连通性/凭据/会话)并输出适配建议。"""
    from app.crawler.probe import probe_source
    from app.db import get_source

    with get_session() as s:
        source = get_source(s, name)
        if source is None:
            raise HTTPException(status_code=404, detail=f"数据源 {name} 不存在")
        return probe_source(s, source)


# ---------------------------------------------------------------------------
# 论文
# ---------------------------------------------------------------------------
@router.get("/papers")
def papers(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    domain: str | None = None,
):
    with get_session() as s:
        rows = list_papers(s, limit=limit, offset=offset, domain_key=domain)
        positions = {
            pid: {"qw": pos.qw, "qx": pos.qx, "qy": pos.qy, "qz": pos.qz}
            for pid, pos in get_positions_by_ids(s, [p.id for p in rows]).items()
        }
        return [
            {
                "id": p.id,
                "title": p.title,
                "source": p.source,
                "url": p.url,
                "published_at": p.published_at.isoformat() if p.published_at else None,
                "domain_key": p.anchored_domain_key,
                "domain_name": p.anchored_domain_name,
                "confidence": p.anchored_confidence,
                "position": positions.get(p.id),
            }
            for p in rows
        ]


@router.get("/papers/{paper_id}")
def paper_detail(paper_id: int):
    with get_session() as s:
        p = s.get(Paper, paper_id)
        if p is None:
            raise HTTPException(status_code=404, detail="论文不存在")
        classifications = [
            {
                "layer": c.layer,
                "domain_key": c.domain_key,
                "domain_name": c.domain_name,
                "confidence": c.confidence,
                "evidence": c.evidence,
            }
            for c in list_classifications(s, paper_id)
        ]
        tags = [
            {"tag": t.tag, "domain_key": t.domain_key, "source": t.source,
             "confidence": t.confidence}
            for t in list_tags(s, paper_id)
        ]
        return {
            "id": p.id,
            "title": p.title,
            "abstract": p.abstract,
            "authors": p.authors,
            "source": p.source,
            "source_id": p.source_id,
            "categories": p.categories,
            "url": p.url,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "anchored": {
                "domain_key": p.anchored_domain_key,
                "domain_name": p.anchored_domain_name,
                "confidence": p.anchored_confidence,
                "position": _position_of(s, p.id)["position"],
            },
            "classifications": classifications,
            "tags": tags,
        }


@router.post("/papers/{paper_id}/analyze")
def analyze_paper(paper_id: int):
    with get_session() as s:
        orchestrator = OrchestratorAgent(s)
        result = orchestrator.analyze_paper(paper_id)
        if result.status == "error":
            raise HTTPException(status_code=404, detail=result.message)
        return result.model_dump()


@router.post("/analyze")
def analyze_many(req: AnalyzeRequest):
    with get_session() as s:
        orchestrator = OrchestratorAgent(s)
        if req.all_unanchored:
            result = orchestrator.analyze_all_unanchored()
        else:
            results = [orchestrator.analyze_paper(pid) for pid in req.paper_ids]
            result = {
                "status": "ok",
                "message": f"分析完成: {len(results)} 篇",
                "results": [r.model_dump() for r in results],
            }
        return result


# ---------------------------------------------------------------------------
# 领域树
# ---------------------------------------------------------------------------
@router.get("/tree")
def tree():
    from app.domain.evolution import domain_stats

    with get_session() as s:
        nodes = list_domain_nodes(s)
        heat_map = {st.key: st.heat for st in domain_stats(s)}
        return {
            "nodes": [
                {
                    "key": n.key,
                    "name": n.name,
                    "parent": n.parent_key,
                    "level": n.level,
                    "paper_count": n.paper_count,
                    "heat": heat_map.get(n.key, "normal"),
                    "created_by": n.created_by,
                    "description": n.description,
                    "position": {"qw": n.qw, "qx": n.qx, "qy": n.qy, "qz": n.qz},
                    "xyz": _projector.project(Quaternion(n.qw, n.qx, n.qy, n.qz)),
                }
                for n in nodes
            ]
        }


# ---------------------------------------------------------------------------
# 研究方向总览与增量领域
# ---------------------------------------------------------------------------
@router.get("/domains/recent")
def recent_domains(limit: int = Query(default=20, ge=1, le=100)):
    """最近由 AI 动态创建的新领域(研究方向增量)。"""
    from app.domain.policy import recent_ai_domains

    with get_session() as s:
        nodes = recent_ai_domains(s, limit=limit)
        return {
            "domains": [
                {
                    "key": n.key,
                    "name": n.name,
                    "parent": n.parent_key,
                    "description": n.description,
                    "paper_count": n.paper_count,
                    "position": {"qw": n.qw, "qx": n.qx, "qy": n.qy, "qz": n.qz},
                }
                for n in nodes
            ]
        }


@router.get("/domains/stats")
def domains_stats():
    """领域热度分级(hot/normal/cold,论文数含子树)。"""
    from app.domain.evolution import domain_stats

    with get_session() as s:
        stats = domain_stats(s)
        return {
            "domains": [
                {"key": st.key, "name": st.name, "level": st.level,
                 "parent": st.parent_key, "paper_count": st.paper_count,
                 "heat": st.heat, "created_by": st.created_by}
                for st in stats
            ]
        }


class EvolveRequest(BaseModel):
    auto_create: bool = True
    limit: int = Field(default=3, ge=1, le=6)


@router.post("/domains/evolve")
def domains_evolve(req: EvolveRequest):
    """论文数量驱动的领域演化:热门领域 LLM 聚类细分(可选自动创建)+ 冷门报告。"""
    from app.domain.evolution import evolve

    with get_session() as s:
        report = evolve(s, auto_create=req.auto_create, limit=req.limit)
        from app.domain.builder import _refresh_counts

        _refresh_counts(s)
        return report.to_dict()


@router.get("/overview")
def overview():
    """整体研究方向:根领域分布、AI 新领域数、热门 tag、学科分布、论文统计。"""
    from sqlmodel import select

    from app.models.entities import CrawlJob

    with get_session() as s:
        nodes = list_domain_nodes(s)
        roots = [n for n in nodes if n.parent_key is None]
        root_stats = [
            {"key": r.key, "name": r.name, "paper_count": r.paper_count,
             "children": sum(1 for n in nodes if n.parent_key == r.key)}
            for r in roots
        ]
        ai_domains = [n for n in nodes if n.created_by == "ai"]
        return {
            "roots": root_stats,
            "ai_domains": len(ai_domains),
            "hot_tags": tag_stats(s, limit=15),
            "categories": category_stats(s, limit=12),
            "papers": count_papers(s),
            "stats": _paper_stats(s),
        }


def _paper_stats(session) -> dict:
    """论文统计:总数/锚定/来源/热度/置信度/月度趋势。"""
    from sqlalchemy import func
    from sqlmodel import select

    from app.domain.evolution import domain_stats
    from app.models.entities import Paper

    papers = session.exec(select(Paper)).all()
    total = len(papers)
    anchored = sum(1 for p in papers if p.anchored_domain_key)
    avg_conf = (
        round(sum(p.anchored_confidence for p in papers if p.anchored_confidence) / anchored, 3)
        if anchored else 0.0
    )
    # 来源分布
    by_source: dict[str, int] = {}
    for p in papers:
        by_source[p.source] = by_source.get(p.source, 0) + 1
    # 热度分布(按主锚定领域)
    heat_counts = {"hot": 0, "normal": 0, "cold": 0}
    heat_map = {st.key: st.heat for st in domain_stats(session)}
    for p in papers:
        if p.anchored_domain_key:
            heat_counts[heat_map.get(p.anchored_domain_key, "normal")] += 1
    # 月度趋势(按发布时间)
    monthly: dict[str, int] = {}
    for p in papers:
        if p.published_at:
            key = p.published_at.strftime("%Y-%m")
            monthly[key] = monthly.get(key, 0) + 1
    trend = [{"month": k, "count": monthly[k]} for k in sorted(monthly)[-12:]]
    # 置信度分布
    bins = {"0.5-0.7": 0, "0.7-0.9": 0, "0.9-1.0": 0}
    for p in papers:
        if p.anchored_confidence:
            c = p.anchored_confidence
            if c < 0.7: bins["0.5-0.7"] += 1
            elif c < 0.9: bins["0.7-0.9"] += 1
            else: bins["0.9-1.0"] += 1
    return {
        "total": total,
        "anchored": anchored,
        "anchored_rate": round(anchored / total, 3) if total else 0.0,
        "avg_confidence": avg_conf,
        "by_source": by_source,
        "heat": heat_counts,
        "monthly_trend": trend,
        "confidence_bins": bins,
    }


# ---------------------------------------------------------------------------
# 地图(四元数坐标)
# ---------------------------------------------------------------------------
@router.get("/map/nodes")
def map_nodes():
    """地图数据:领域节点 + 已锚定论文点(含 4D 坐标、3D 投影、学科着色数据)。"""
    from sqlmodel import select

    from app.domain.evolution import domain_stats
    from app.models.entities import PaperTag

    with get_session() as s:
        nodes = list_domain_nodes(s)
        heat_map = {st.key: st.heat for st in domain_stats(s)}
        domains = [
            {
                "key": n.key,
                "name": n.name,
                "parent": n.parent_key,
                "level": n.level,
                "paper_count": n.paper_count,
                "heat": heat_map.get(n.key, "normal"),
                "q": [n.qw, n.qx, n.qy, n.qz],
                "xyz": _projector.project(Quaternion(n.qw, n.qx, n.qy, n.qz)),
                "type": "domain",
            }
            for n in nodes
        ]
        papers_list = list_papers_with_positions(s, limit=2000)
        # 论文主学科(第一个 category tag)→ 用于按学科着色
        paper_cats: dict[int, str] = {}
        for t in s.exec(select(PaperTag).where(PaperTag.source == "category")):
            paper_cats.setdefault(t.paper_id, t.tag)
        paper_points = []
        for p, pos in papers_list:
            if pos is None:
                continue
            q = Quaternion(pos.qw, pos.qx, pos.qy, pos.qz)
            paper_points.append(
                {
                    "id": p.id,
                    "title": p.title,
                    "domain_key": pos.domain_key,
                    "category": paper_cats.get(p.id),
                    "q": q.to_list(),
                    "xyz": _projector.project(q),
                    "confidence": pos.confidence,
                    "type": "paper",
                }
            )
        return {"domains": domains, "papers": paper_points}


# ---------------------------------------------------------------------------
# 搜索(支持跳转)
# ---------------------------------------------------------------------------
@router.get("/search")
def search(q: str = Query(min_length=1), limit: int = Query(default=50, le=100)):
    with get_session() as s:
        hits = search_papers(s, q, limit=limit)
        return {
            "query": q,
            "hits": [h.model_dump() for h in hits],
        }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
@router.post("/tree/rebuild")
def rebuild_tree():
    """从种子数据重建领域树(幂等,保留论文数据)。"""
    with get_session() as s:
        build_domain_tree(s)
        return {"status": "ok", "nodes": count_domain_nodes(s)}


def _position_of(session, paper_id: int) -> dict:
    pos = get_position(session, paper_id)
    if pos is None:
        return {"position": None}
    return {"position": {"qw": pos.qw, "qx": pos.qx, "qy": pos.qy, "qz": pos.qz}}
