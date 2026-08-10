"""AnchorAgent 动态建领域与增量 tag 集成测试。"""
import math

import pytest

from app.agents.anchor import AnchorAgent
from app.agents.base import AgentTask
from app.classify.base import ClassifierResult
from app.db import get_session, init_db, list_tags, upsert_paper
from app.domain.builder import build_domain_tree
from app.models.entities import DomainNode, Paper


class FakeNewDomainProvider:
    """模拟 LLM:输出新领域建议(Agentic RAG)。"""

    name = "fake"

    def chat_structured(self, messages, **kwargs):
        import json

        return json.dumps(
            {
                "domain_key": "",
                "domain_name": "Agentic RAG",
                "confidence": 0.85,
                "summary": "智能体驱动的检索增强生成",
                "keywords": ["agentic rag", "tool use", "retrieval"],
                "create_new": True,
                "parent_key": "algorithm.retrieval",
                "description": "RAG 与智能体规划/工具调用结合的新研究方向",
            },
            ensure_ascii=False,
        )


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        build_domain_tree(s)
        yield s


def test_anchor_creates_new_domain(session):
    paper = upsert_paper(
        session,
        Paper(source="test", source_id="anchor-new-1",
              title="Agentic Retrieval-Augmented Generation with Tool Use",
              abstract="We combine agent planning with retrieval augmented generation."),
    )
    agent = AnchorAgent(session, provider=FakeNewDomainProvider())
    result = agent.run(AgentTask(task_type="anchor", paper={
        "id": paper.id, "source": "test", "source_id": "anchor-new-1",
        "title": paper.title, "abstract": paper.abstract,
    }))

    assert result.status == "ok"
    assert result.artifacts["created_domain"] is not None
    created = result.artifacts["created_domain"]
    assert created["key"] == "algorithm.retrieval.agentic-rag"
    assert created["parent"] == "algorithm.retrieval"

    # 新领域落库且坐标单位
    node = session.get(DomainNode, created["key"])
    assert node is not None
    assert node.created_by == "ai"
    norm = math.sqrt(node.qw**2 + node.qx**2 + node.qy**2 + node.qz**2)
    assert norm == pytest.approx(1.0, abs=1e-6)

    # 论文锚定到新领域
    assert result.artifacts["domain_key"] == created["key"]


def test_anchor_writes_tags(session):
    paper = upsert_paper(
        session,
        Paper(source="test", source_id="anchor-tag-1",
              title="Scaling Laws for Agentic Systems",
              abstract="Empirical scaling analysis of autonomous agent systems."),
    )
    agent = AnchorAgent(session, provider=FakeNewDomainProvider())
    result = agent.run(AgentTask(task_type="anchor", paper={
        "id": paper.id, "source": "test", "source_id": "anchor-tag-1",
        "title": paper.title, "abstract": paper.abstract,
    }))
    assert result.status == "ok"
    tags = result.artifacts["tags"]
    assert tags, "应产生增量 tag"
    # 主标签存在
    assert any(t["primary"] for t in tags)
    # LLM 关键词成为自由词 tag
    assert any(t["tag"] == "agentic rag" and not t["domain_key"] for t in tags)


def test_new_domain_low_confidence_ignored(session):
    """置信度不足时不应创建新领域。"""
    class LowConfProvider(FakeNewDomainProvider):
        def chat_structured(self, messages, **kwargs):
            import json

            return json.dumps(
                {"domain_key": "", "domain_name": "Doubtful Field", "confidence": 0.5,
                 "summary": "", "keywords": [], "create_new": True,
                 "parent_key": "models", "description": ""},
                ensure_ascii=False,
            )

    paper = upsert_paper(
        session,
        Paper(source="test", source_id="anchor-low-1", title="Doubtful Field Paper",
              abstract="Some uncertain research."),
    )
    agent = AnchorAgent(session, provider=LowConfProvider())
    result = agent.run(AgentTask(task_type="anchor", paper={
        "id": paper.id, "source": "test", "source_id": "anchor-low-1",
        "title": paper.title, "abstract": paper.abstract,
    }))
    # 低置信度:不建新领域(LLM 层 domain_key 为空 → 不参与集成,由其他层决定)
    assert result.artifacts.get("created_domain") is None
    assert session.get(DomainNode, "models.doubtful-field") is None


def test_repeat_analysis_reuses_domain(session):
    """同一名称的新领域建议 → 复用已有节点,不重复建。"""
    paper = upsert_paper(
        session,
        Paper(source="test", source_id="anchor-new-2",
              title="Agentic RAG for Knowledge Intensive Tasks",
              abstract="Tool use with retrieval augmentation."),
    )
    agent = AnchorAgent(session, provider=FakeNewDomainProvider())
    result = agent.run(AgentTask(task_type="anchor", paper={
        "id": paper.id, "source": "test", "source_id": "anchor-new-2",
        "title": paper.title, "abstract": paper.abstract,
    }))
    assert result.artifacts["created_domain"]["key"] == "algorithm.retrieval.agentic-rag"
