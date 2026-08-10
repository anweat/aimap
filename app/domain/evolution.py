"""论文数量驱动的领域演化:热门细分 / 冷门标记 / 热度分级。

背景:领域树由基准规则 + AI 增量生长,但"生长方向"需要数据反馈。
本模块让论文数量驱动一级分类下的领域内容更新:

  1. 热度分级:每领域(含子树)论文数 → hot / normal / cold;
     - hot  :论文数 ≥ HOT_THRESHOLD,说明方向拥挤,应细分;
     - cold :论文数 ≤ COLD_THRESHOLD,说明方向冷门,展示层折叠提示;
  2. 热门细分:对最热的领域,LLM 分析其论文标题聚类,
     建议 2~5 个细分方向(名称/描述/覆盖论文),经去重后自动创建子领域;
  3. 演化报告:每次 evolve 输出创建/复用/跳过明细,可审计。

触发:POST /api/domains/evolve(参数 auto_create、limit)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.config import settings
from app.db import get_domain_node
from app.domain.policy import create_domain, find_domain
from app.llm.base import BaseProvider, ChatMessage, extract_json
from app.llm.registry import get_provider
from app.models.entities import DomainNode, Paper

HOT_THRESHOLD = 12      # 论文数 ≥ 该值 → 热门(建议细分)
COLD_THRESHOLD = 1      # 论文数 ≤ 该值 → 冷门(展示折叠)
SUGGEST_MAX_ITEMS = 3   # 每次演化建议的领域数
SUGGEST_MIN_CONFIDENCE = 0.6  # LLM 细分建议的最低置信度


@dataclass
class DomainStats:
    key: str
    name: str
    level: int
    parent_key: str | None
    paper_count: int
    heat: str  # hot | normal | cold
    created_by: str


@dataclass
class EvolutionReport:
    suggested: list[dict] = field(default_factory=list)   # LLM 细分建议明细
    created: list[dict] = field(default_factory=list)     # 实际创建的领域
    reused: list[dict] = field(default_factory=list)      # 命中去重复用的领域
    cold: list[dict] = field(default_factory=list)        # 冷门领域
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "suggested": self.suggested,
            "created": self.created,
            "reused": self.reused,
            "cold": self.cold,
            "errors": self.errors,
        }


def domain_stats(session: Session) -> list[DomainStats]:
    """全领域热度统计(论文数含子树)。"""
    # 论文数(含子树):锚定领域自身 + 祖先累加
    # 注意:exec(select(单列)) 返回标量而非 Row
    counts: dict[str, int] = {}
    for key in session.exec(select(Paper.anchored_domain_key)):
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        parts = key.split(".")
        for i in range(1, len(parts)):
            anc = ".".join(parts[:i])
            counts[anc] = counts.get(anc, 0) + 1

    stats: list[DomainStats] = []
    for node in session.exec(select(DomainNode)):
        n = counts.get(node.key, 0)
        heat = "hot" if n >= HOT_THRESHOLD else ("cold" if n <= COLD_THRESHOLD else "normal")
        stats.append(
            DomainStats(key=node.key, name=node.name, level=node.level,
                        parent_key=node.parent_key, paper_count=n,
                        heat=heat, created_by=node.created_by)
        )
    return stats


def hot_domains(session: Session, limit: int = SUGGEST_MAX_ITEMS, min_level: int = 1) -> list[DomainStats]:
    """最热门的领域(优先深层,便于细分)。"""
    stats = [s for s in domain_stats(session) if s.heat == "hot" and s.level >= min_level]
    stats.sort(key=lambda s: (-s.paper_count, s.level))
    return stats[:limit]


def cold_domains(session: Session) -> list[dict]:
    """冷门领域(叶子优先,供展示折叠)。"""
    stats = [s for s in domain_stats(session) if s.heat == "cold" and s.level >= 1]
    stats.sort(key=lambda s: s.level)
    return [
        {"key": s.key, "name": s.name, "parent": s.parent_key,
         "paper_count": s.paper_count, "level": s.level}
        for s in stats
    ]


# ---------------------------------------------------------------------------
# 热门细分(LLM 聚类建议)
# ---------------------------------------------------------------------------
def _paper_titles(session: Session, domain_key: str, limit: int = 60) -> list[str]:
    papers = session.exec(
        select(Paper).where(Paper.anchored_domain_key == domain_key).limit(limit)
    ).all()
    return [p.title for p in papers]


def suggest_subdivisions(session: Session, provider: BaseProvider | None = None,
                         limit: int = SUGGEST_MAX_ITEMS) -> list[dict]:
    """对热门领域做 LLM 聚类,建议细分方向(不落库)。"""
    provider = provider or get_provider()
    results: list[dict] = []
    for hot in hot_domains(session, limit=limit):
        titles = _paper_titles(session, hot.key)
        if len(titles) < 5:
            continue  # 论文太少不值得细分
        prompt = (
            f"领域 [{hot.key}] ({hot.name}) 下有 {len(titles)} 篇论文。\n"
            "请按研究方向将其聚类为 2~5 个细分领域。\n"
            "论文标题:\n" + "\n".join(f"- {t[:120]}" for t in titles[:50]) + "\n\n"
            '只输出 JSON 数组: [{"name": "细分方向名(简短专有名词)", '
            '"description": "一句话描述", "confidence": 0.0~1.0}]'
        )
        try:
            content = provider.chat_structured(
                [ChatMessage(role="user", content=prompt)], temperature=0.0
            )
            suggestions = json.loads(extract_json(content))
            if not isinstance(suggestions, list):
                suggestions = []
        except Exception as e:
            results.append({"domain": hot.key, "error": str(e)[:120]})
            continue
        valid = [
            s for s in suggestions
            if isinstance(s, dict) and s.get("name") and float(s.get("confidence", 0)) >= SUGGEST_MIN_CONFIDENCE
        ]
        results.append({
            "domain": hot.key,
            "domain_name": hot.name,
            "paper_count": hot.paper_count,
            "suggestions": valid[:5],
        })
    return results


def evolve(session: Session, *, auto_create: bool = True, limit: int = SUGGEST_MAX_ITEMS,
           provider: BaseProvider | None = None) -> EvolutionReport:
    """执行领域演化:热门细分(可选自动创建)+ 冷门报告。"""
    report = EvolutionReport()
    report.cold = cold_domains(session)

    for item in suggest_subdivisions(session, provider=provider, limit=limit):
        if "error" in item:
            report.errors.append(f"{item['domain']}: {item['error']}")
            continue
        report.suggested.append(item)
        if not auto_create:
            continue
        for s in item.get("suggestions", []):
            try:
                existing = find_domain(session, s["name"], item["domain"])
                if existing:
                    report.reused.append(
                        {"name": s["name"], "domain": item["domain"], "existing_key": existing.key}
                    )
                    continue
                node = create_domain(
                    session, s["name"], parent_key=item["domain"],
                    description=s.get("description", ""), created_by="ai",
                )
                report.created.append(
                    {"key": node.key, "name": node.name, "parent": node.parent_key,
                     "description": node.description}
                )
            except Exception as e:  # 单个建议失败不影响整体
                report.errors.append(f"{item['domain']}/{s.get('name')}: {e}")
    return report
