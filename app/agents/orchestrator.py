"""L3 编排 Agent:调度 L2 Agent,管理持久化与批处理。

流程(analyze_paper):
  paper → AnchorAgent(分类+锚定) + ProfileAgent(画像)
        → 各层 Classification 落库 → PaperPosition 落库
        → Paper 锚定字段更新 → 领域树论文计数刷新
"""
from __future__ import annotations

from sqlmodel import Session

from app.agents.anchor import AnchorAgent, ProfileAgent
from app.agents.base import AgentResult, AgentTask, BaseAgent
from app.classify.base import ClassifierResult
from app.db import (
    add_classification,
    add_tag,
    clear_classifications,
    clear_tags,
    get_paper,
    upsert_position,
)
from app.domain.builder import _refresh_counts
from app.domain.position import anchor_paper_position
from app.llm.base import BaseProvider
from app.llm.registry import get_provider
from app.models.entities import Classification, Paper, PaperTag


class OrchestratorAgent(BaseAgent):
    """编排 Agent:驱动「采集 → 分析 → 锚定 → 落库」全流程。"""

    name = "orchestrator"

    def __init__(self, session: Session, provider: BaseProvider | None = None):
        self._session = session
        self._provider = provider or get_provider()
        self._anchor = AnchorAgent(session, provider)
        self._profile = ProfileAgent(provider)

    # -- 单篇分析 ----------------------------------------------------------
    def analyze_paper(self, paper_id: int) -> AgentResult:
        paper = get_paper(self._session, paper_id)
        if paper is None:
            return AgentResult(task_type="orchestrate", status="error", message=f"论文 {paper_id} 不存在")

        snapshot = {
            "id": paper.id,
            "source": paper.source,
            "source_id": paper.source_id,
            "title": paper.title,
            "abstract": paper.abstract,
        }

        # 1. 锚定(多层分类 + 位置)
        anchor_result = self._anchor.run(AgentTask(task_type="anchor", paper=snapshot))
        if anchor_result.status == "skipped":
            return anchor_result

        # 2. 画像(失败不影响锚定)
        profile_result = self._profile.run(AgentTask(task_type="profile", paper=snapshot))

        # 3. 持久化
        self._persist(paper, anchor_result)

        return AgentResult(
            task_type="orchestrate",
            status="ok",
            message=f"分析完成: {anchor_result.message}",
            artifacts={
                "anchor": anchor_result.artifacts,
                "profile": profile_result.artifacts.get("profile"),
            },
        )

    # -- 批量分析 ----------------------------------------------------------
    def analyze_all_unanchored(self, limit: int = 100) -> list[AgentResult]:
        """对未锚定论文批量执行分析(用于离线补全)。"""
        from sqlmodel import select

        papers = self._session.exec(
            select(Paper).where(Paper.anchored_domain_key.is_(None)).limit(limit)
        ).all()
        return [self.analyze_paper(p.id) for p in papers]

    # -- 持久化 ------------------------------------------------------------
    def _persist(self, paper: Paper, anchor_result: AgentResult) -> None:
        artifacts = anchor_result.artifacts
        domain_key = artifacts["domain_key"]

        # 各层分类结果
        clear_classifications(self._session, paper.id)
        for layer_dict in artifacts.get("layers", []):
            if not layer_dict or not layer_dict.get("domain_key"):
                continue
            add_classification(
                self._session,
                Classification(
                    paper_id=paper.id,
                    layer=layer_dict["layer"],
                    domain_key=layer_dict["domain_key"],
                    domain_name=layer_dict.get("domain_name", ""),
                    confidence=layer_dict.get("confidence", 0.0),
                    evidence=layer_dict.get("evidence", ""),
                ),
            )

        # 增量 tag(主标签 + LLM 关键词)
        clear_tags(self._session, paper.id)
        for t in artifacts.get("tags", []):
            add_tag(
                self._session,
                PaperTag(
                    paper_id=paper.id,
                    tag=t["tag"],
                    domain_key=t.get("domain_key"),
                    source=t.get("source", "llm"),
                    confidence=t.get("confidence", 0.0),
                ),
            )

        # 位置
        pos = anchor_paper_position(
            self._session, paper, domain_key, artifacts.get("confidence", 0.0)
        )
        upsert_position(self._session, pos)

        # 论文锚定字段
        paper.anchored_domain_key = domain_key
        paper.anchored_domain_name = artifacts.get("domain_name", domain_key)
        paper.anchored_confidence = artifacts.get("confidence", 0.0)
        from datetime import datetime, timezone

        paper.analyzed_at = datetime.now(timezone.utc)
        self._session.add(paper)
        self._session.commit()

        # 领域计数
        _refresh_counts(self._session)

    def run(self, task: AgentTask) -> AgentResult:
        """BaseAgent 接口:task_type = analyze_paper | analyze_all。"""
        if task.task_type == "analyze_paper" and task.paper_id:
            return self.analyze_paper(task.paper_id)
        if task.task_type == "analyze_all":
            results = self.analyze_all_unanchored(task.payload.get("limit", 100))
            return AgentResult(
                task_type="orchestrate",
                status="ok",
                message=f"批量分析完成: {len(results)} 篇",
                artifacts={"results": [r.model_dump() for r in results]},
            )
        return AgentResult(task_type=task.task_type, status="error", message=f"未知任务类型: {task.task_type}")
