"""SQLite 存储层:连接、建表、基础 CRUD 与搜索。"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sqlalchemy import event as _sa_event, func, or_
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.models.entities import (
    Classification,
    CrawlJob,
    CrawlLog,
    DomainNode,
    Paper,
    PaperPosition,
    PaperSearchHit,
    PaperTag,
    SourceConfig,
)


def get_engine():
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
    )


engine = get_engine()


def _set_sqlite_pragma(dbapi_connection, connection_record):
    """每个 SQLite 连接:WAL 并发模式 + 30s 写锁等待(避免 database is locked)。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


_sa_event.listen(engine, "connect", _set_sqlite_pragma)


def init_db() -> None:
    """建表(幂等)+ 轻量迁移。"""
    SQLModel.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """SQLite 轻量迁移:为已存在的表补充新增列并回填。"""
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(paper)")}
        if "title_norm" not in cols:
            conn.exec_driver_sql("ALTER TABLE paper ADD COLUMN title_norm VARCHAR DEFAULT ''")
            conn.commit()
    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(domainnode)")}
        if "description" not in cols:
            conn.exec_driver_sql("ALTER TABLE domainnode ADD COLUMN description VARCHAR DEFAULT ''")
        if "created_by" not in cols:
            conn.exec_driver_sql("ALTER TABLE domainnode ADD COLUMN created_by VARCHAR DEFAULT 'seed'")
        if "created_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE domainnode ADD COLUMN created_at DATETIME")
            conn.commit()
    # 回填旧数据的 title_norm
    from app.crawler.dedup import normalize_title

    with get_session() as s:
        for p in s.exec(select(Paper).where(Paper.title_norm == "")):
            p.title_norm = normalize_title(p.title)
            s.add(p)
        s.commit()


def get_session() -> Session:
    return Session(engine)


# ---------------------------------------------------------------------------
# Paper
# ---------------------------------------------------------------------------
def upsert_paper(session: Session, paper: Paper) -> Paper:
    """按 (source, source_id) 幂等写入。

    已存在的论文只更新爬取侧字段(标题/摘要/元数据),保留分析侧字段
    (anchored_* / analyzed_at),避免重复采集覆盖已锚定结果。
    """
    from app.crawler.dedup import normalize_title

    if not paper.title_norm:
        paper.title_norm = normalize_title(paper.title)
    existing = session.exec(
        select(Paper).where(Paper.source == paper.source, Paper.source_id == paper.source_id)
    ).first()
    if existing:
        for field, value in paper.model_dump(exclude={"id", "crawled_at"}).items():
            if field.startswith("anchored_") or field == "analyzed_at":
                continue  # 保留分析侧状态
            setattr(existing, field, value)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    session.add(paper)
    session.commit()
    session.refresh(paper)
    return paper


def get_paper(session: Session, paper_id: int) -> Paper | None:
    return session.get(Paper, paper_id)


def list_papers(session: Session, limit: int = 100, offset: int = 0, domain_key: str | None = None) -> list[Paper]:
    stmt = select(Paper)
    if domain_key:
        stmt = stmt.where(Paper.anchored_domain_key == domain_key)
    return list(session.exec(stmt.order_by(Paper.id.desc()).offset(offset).limit(limit)))


def list_papers_with_positions(
    session: Session, limit: int = 2000, domain_key: str | None = None
) -> list[tuple[Paper, PaperPosition | None]]:
    """论文 + 位置一次联查(避免 N+1)。"""
    stmt = (
        select(Paper, PaperPosition)
        .join(PaperPosition, PaperPosition.paper_id == Paper.id, isouter=True)
        .order_by(Paper.id.desc())
        .limit(limit)
    )
    if domain_key:
        stmt = stmt.where(Paper.anchored_domain_key == domain_key)
    return list(session.exec(stmt))


def search_papers(session: Session, query: str, limit: int = 50) -> list[PaperSearchHit]:
    """标题/摘要/作者 LIKE 全文搜索,附带锚定位置信息。"""
    like = f"%{query.strip()}%"
    stmt = (
        select(Paper, PaperPosition)
        .join(PaperPosition, PaperPosition.paper_id == Paper.id, isouter=True)
        .where(
            or_(
                Paper.title.like(like),
                Paper.abstract.like(like),
                Paper.authors.like(like),
            )
        )
        .limit(limit)
    )
    hits: list[PaperSearchHit] = []
    for paper, pos in session.exec(stmt):
        hits.append(
            PaperSearchHit(
                id=paper.id,
                title=paper.title,
                source=paper.source,
                url=paper.url,
                domain_key=paper.anchored_domain_key,
                domain_name=paper.anchored_domain_name,
                qw=pos.qw if pos else 0.0,
                qx=pos.qx if pos else 0.0,
                qy=pos.qy if pos else 0.0,
                qz=pos.qz if pos else 0.0,
            )
        )
    return hits


# ---------------------------------------------------------------------------
# Classification / Position / DomainNode
# ---------------------------------------------------------------------------
def add_classification(session: Session, cls: Classification) -> None:
    session.add(cls)
    session.commit()


def clear_classifications(session: Session, paper_id: int) -> None:
    for c in session.exec(select(Classification).where(Classification.paper_id == paper_id)):
        session.delete(c)
    session.commit()


def list_classifications(session: Session, paper_id: int) -> list[Classification]:
    return list(session.exec(select(Classification).where(Classification.paper_id == paper_id)))


# ---------------------------------------------------------------------------
# PaperTag
# ---------------------------------------------------------------------------
def add_tag(session: Session, tag: PaperTag) -> None:
    session.add(tag)
    session.commit()


def clear_tags(session: Session, paper_id: int) -> None:
    for t in session.exec(select(PaperTag).where(PaperTag.paper_id == paper_id)):
        session.delete(t)
    session.commit()


def list_tags(session: Session, paper_id: int) -> list[PaperTag]:
    return list(session.exec(select(PaperTag).where(PaperTag.paper_id == paper_id)))


def tag_stats(session: Session, limit: int = 30) -> list[dict]:
    """研究方向热度:tag 出现次数 Top N(用于整体研究方向展示)。"""
    from sqlalchemy import func

    rows = session.exec(
        select(PaperTag.tag, func.count(PaperTag.id).label("cnt"))
        .group_by(PaperTag.tag)
        .order_by(func.count(PaperTag.id).desc())
        .limit(limit)
    ).all()
    return [{"tag": tag, "count": cnt} for tag, cnt in rows]


def category_stats(session: Session, limit: int = 12) -> list[dict]:
    """学科维度分布:arXiv categories 出现次数 Top N。"""
    from sqlalchemy import func

    rows = session.exec(
        select(PaperTag.tag, func.count(PaperTag.id).label("cnt"))
        .where(PaperTag.source == "category")
        .group_by(PaperTag.tag)
        .order_by(func.count(PaperTag.id).desc())
        .limit(limit)
    ).all()
    return [{"key": tag, "name": _category_name(tag), "count": cnt} for tag, cnt in rows]


def _category_name(key: str) -> str:
    from app.domain.arxiv_taxonomy import ARXIV_CATEGORIES

    return ARXIV_CATEGORIES.get(key, key)


def upsert_position(session: Session, pos: PaperPosition) -> None:
    existing = session.exec(
        select(PaperPosition).where(PaperPosition.paper_id == pos.paper_id)
    ).first()
    if existing:
        existing.domain_key = pos.domain_key
        existing.qw, existing.qx, existing.qy, existing.qz = pos.qw, pos.qx, pos.qy, pos.qz
        existing.confidence = pos.confidence
        session.add(existing)
    else:
        session.add(pos)
    session.commit()


def get_positions_by_ids(session: Session, paper_ids: list[int]) -> dict[int, PaperPosition]:
    """按论文 id 集合一次查询位置(避免 N+1)。"""
    if not paper_ids:
        return {}
    rows = session.exec(
        select(PaperPosition).where(PaperPosition.paper_id.in_(paper_ids))
    ).all()
    return {pos.paper_id: pos for pos in rows}


def get_position(session: Session, paper_id: int) -> PaperPosition | None:
    return session.exec(
        select(PaperPosition).where(PaperPosition.paper_id == paper_id)
    ).first()


def upsert_domain_node(session: Session, node: DomainNode) -> DomainNode:
    existing = session.get(DomainNode, node.key)
    if existing:
        for field, value in node.model_dump(exclude={"key"}).items():
            setattr(existing, field, value)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


def get_domain_node(session: Session, key: str) -> DomainNode | None:
    return session.get(DomainNode, key)


def list_domain_nodes(session: Session) -> list[DomainNode]:
    return list(session.exec(select(DomainNode)))


def count_papers(session: Session) -> int:
    return session.exec(select(func.count(Paper.id))).one()


def count_domain_nodes(session: Session) -> int:
    return session.exec(select(func.count(DomainNode.key))).one()


# ---------------------------------------------------------------------------
# CrawlJob
# ---------------------------------------------------------------------------
def create_job(session: Session, source: str, query: str, max_pages: int = 1) -> CrawlJob:
    job = CrawlJob(source=source, query=query, max_pages=max_pages, status="pending")
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_job(session: Session, job_id: int) -> CrawlJob | None:
    return session.get(CrawlJob, job_id)


def list_jobs(session: Session, limit: int = 50) -> list[CrawlJob]:
    return list(session.exec(select(CrawlJob).order_by(CrawlJob.id.desc()).limit(limit)))


def update_job(session: Session, job: CrawlJob, **fields) -> CrawlJob:
    from datetime import datetime, timezone

    for k, v in fields.items():
        setattr(job, k, v)
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def add_crawl_log(session: Session, job_id: int, message: str, level: str = "info") -> None:
    session.add(CrawlLog(job_id=job_id, message=message[:500], level=level))
    session.commit()


def list_crawl_logs(session: Session, job_id: int, limit: int = 200) -> list[CrawlLog]:
    return list(
        session.exec(
            select(CrawlLog).where(CrawlLog.job_id == job_id)
            .order_by(CrawlLog.id.desc()).limit(limit)
        )
    )


# ---------------------------------------------------------------------------
# SourceConfig(数据源管理)
# ---------------------------------------------------------------------------
DEFAULT_SOURCES = [
    {"name": "arxiv", "display_name": "arXiv", "source_type": "open",
     "config": {"api_url": "https://export.arxiv.org/api/query"}},
    {"name": "ieee", "display_name": "IEEE Xplore", "source_type": "library", "config": {}},
    {"name": "acm", "display_name": "ACM DL", "source_type": "library", "config": {}},
    {"name": "cnki", "display_name": "知网 CNKI", "source_type": "library", "config": {}},
]


def init_default_sources(session: Session) -> None:
    """写入默认数据源(幂等)。"""
    import json

    for item in DEFAULT_SOURCES:
        if session.get(SourceConfig, item["name"]) is None:
            session.add(
                SourceConfig(
                    name=item["name"],
                    display_name=item["display_name"],
                    source_type=item["source_type"],
                    config=json.dumps(item["config"], ensure_ascii=False),
                )
            )
    session.commit()


def list_sources(session: Session) -> list[SourceConfig]:
    return list(session.exec(select(SourceConfig).order_by(SourceConfig.name)))


def get_source(session: Session, name: str) -> SourceConfig | None:
    return session.get(SourceConfig, name)


def upsert_source(session: Session, source: SourceConfig) -> SourceConfig:
    existing = session.get(SourceConfig, source.name)
    if existing:
        for field, value in source.model_dump(exclude={"name", "created_at"}).items():
            setattr(existing, field, value)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def delete_source(session: Session, name: str) -> bool:
    existing = session.get(SourceConfig, name)
    if existing is None:
        return False
    session.delete(existing)
    session.commit()
    return True
