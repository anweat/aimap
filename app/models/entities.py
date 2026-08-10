"""数据模型:论文、分类结果、领域节点、论文位置(四元数锚定)。

全部使用 SQLModel(基于 SQLAlchemy + Pydantic),表结构随代码同步,
启动时自动建表。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 论文
# ---------------------------------------------------------------------------
class Paper(SQLModel, table=True):
    """爬取到的论文元数据。source 标识来源,source_id 为来源侧 ID。"""

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)          # arxiv | ieee | acm | cnki | manual
    source_id: str = Field(index=True)       # 来源侧唯一 ID
    title: str
    title_norm: str = Field(default="", index=True)  # 归一化标题(跨源去重用)
    abstract: str = Field(default="")
    authors: str = Field(default="")         # 逗号分隔
    categories: str = Field(default="")      # 来源侧分类(逗号分隔)
    url: str = Field(default="")
    published_at: datetime | None = Field(default=None)
    crawled_at: datetime = Field(default_factory=_now)

    # 最终锚定结果(由多层 agent 分析产出)
    anchored_domain_key: str | None = Field(default=None, index=True)
    anchored_domain_name: str | None = Field(default=None)
    anchored_confidence: float = Field(default=0.0)
    analyzed_at: datetime | None = Field(default=None)


# ---------------------------------------------------------------------------
# 多层分类结果(每层一条,保留证据链)
# ---------------------------------------------------------------------------
class Classification(SQLModel, table=True):
    """某一层分类器对某篇论文的输出。

    layer 取值: rules | stats | llm | ensemble(集成层)
    """

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(index=True, foreign_key="paper.id")
    layer: str = Field(index=True)
    domain_key: str
    domain_name: str
    confidence: float = Field(default=0.0)   # 0~1
    evidence: str = Field(default="")        # JSON 字符串:命中词、相似度等
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# 领域树节点
# ---------------------------------------------------------------------------
class DomainNode(SQLModel, table=True):
    """领域树节点,携带四元数坐标(单位四元数,w,x,y,z)。

    created_by: seed(基准规则种子)/ ai(分析过程中动态创建的研究方向增量)。
    """

    key: str = Field(primary_key=True)       # 如 models.llm.arch
    name: str
    parent_key: str | None = Field(default=None, index=True)
    level: int = Field(default=0)
    keywords: str = Field(default="")         # JSON 数组字符串
    description: str = Field(default="")      # AI 生成的研究方向描述
    created_by: str = Field(default="seed", index=True)
    # 四元数坐标(单位四元数,位于 S3 球面)
    qw: float = Field(default=1.0)
    qx: float = Field(default=0.0)
    qy: float = Field(default=0.0)
    qz: float = Field(default=0.0)
    paper_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# 论文标签(增量 tag:一篇文章多个研究方向标签)
# ---------------------------------------------------------------------------
class PaperTag(SQLModel, table=True):
    """论文的研究方向标签(主标签 anchored 决定地图位置,附加标签丰富画像)。

    source: rules | stats | llm | category | manual;domain_key 为关联领域(可为空=自由词)。
    """

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(index=True, foreign_key="paper.id")
    tag: str = Field(index=True)
    domain_key: str | None = Field(default=None, index=True)
    source: str = Field(default="llm")
    confidence: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# 数据源配置(面板可编辑/添加)
# ---------------------------------------------------------------------------
class SourceConfig(SQLModel, table=True):
    """数据源配置:启用状态、端点、凭据状态、最近采集统计。

    source_type: open(公开 API,如 arXiv) | library(需登录,如 IEEE/ACM/CNKI)。
    config 为 JSON:{"api_url": "...", "delay": 3.0, "max_results": 200}
    """

    name: str = Field(primary_key=True)        # arxiv | ieee | acm | cnki | custom-x
    display_name: str = ""
    source_type: str = Field(default="open")   # open | library
    enabled: bool = Field(default=True)
    config: str = Field(default="{}")          # JSON
    last_crawl_at: datetime | None = Field(default=None)
    last_crawl_stats: str = Field(default="{}")  # JSON: {fetched, saved, duplicates, failed}
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# 采集任务日志(每来源进度/日志显示)
# ---------------------------------------------------------------------------
class CrawlLog(SQLModel, table=True):
    """采集任务的过程日志(进度、重试、错误),供前端逐条展示。"""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(index=True, foreign_key="crawljob.id")
    ts: datetime = Field(default_factory=_now)
    level: str = Field(default="info")         # info | warn | error
    message: str = Field(default="")


# ---------------------------------------------------------------------------
# 论文位置(四元数锚定结果,可独立于 Paper 更新)
# ---------------------------------------------------------------------------
class PaperPosition(SQLModel, table=True):
    """论文在地图上的位置:单位四元数 + 锚定领域。"""

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(index=True, foreign_key="paper.id", unique=True)
    domain_key: str = Field(index=True)
    # 单位四元数坐标
    qw: float
    qx: float
    qy: float
    qz: float
    confidence: float = Field(default=0.0)
    updated_at: datetime = Field(default_factory=_now)


class PaperSearchHit(SQLModel):
    """搜索命中(视图模型,非表)。"""

    id: int
    title: str
    source: str
    url: str
    domain_key: str | None = None
    domain_name: str | None = None
    qw: float = 0.0
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    score: float = 0.0


# ---------------------------------------------------------------------------
# 爬取任务(持久化状态,支持断点续爬)
# ---------------------------------------------------------------------------
class CrawlJob(SQLModel, table=True):
    """一次采集任务的完整状态机。

    status: pending → running → done | failed | stopped
    失败时记录 next_retry_at,可稍后 resume 续爬(cursor 为分页游标)。
    """

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)            # arxiv | ieee | acm | cnki
    query: str = Field(index=True)             # 检索式
    status: str = Field(default="pending", index=True)
    total_fetched: int = Field(default=0)      # 抓取总数(去重前)
    total_saved: int = Field(default=0)        # 新入库数(去重后)
    total_duplicates: int = Field(default=0)   # 重复过滤数
    total_failed: int = Field(default=0)       # 失败页数
    cursor: int = Field(default=0)             # 分页游标(已完成的页数)
    max_pages: int = Field(default=1)          # 计划页数
    last_error: str = Field(default="")
    next_retry_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
