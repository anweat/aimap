"""arXiv 学科目录与学科 tag 集成测试。"""
from sqlmodel import select

from app.agents.anchor import AnchorAgent
from app.agents.base import AgentTask
from app.db import get_session, init_db, list_tags, upsert_paper
from app.domain.arxiv_taxonomy import (
    ARXIV_CATEGORIES,
    category_label,
    parse_categories,
    split_categories,
)
from app.domain.builder import build_domain_tree
from app.models.entities import Paper, PaperTag


def test_split_categories():
    assert split_categories("cs.CL, cs.LG, cs.CL") == ["cs.CL", "cs.LG"]
    assert split_categories("cs.CL cs.LG") == ["cs.CL", "cs.LG"]
    assert split_categories("") == []
    assert split_categories(None) == []


def test_category_label():
    assert "计算语言学" in category_label("cs.CL")
    assert category_label("zz.XX") == "zz.XX"  # 未收录按原名


def test_parse_categories():
    items = parse_categories("cs.CL cs.LG")
    assert items[0] == {"key": "cs.CL", "name": "计算语言学/NLP"}
    assert len(items) == 2


def test_taxonomy_coverage():
    # 常用 AI 学科均在目录内
    for key in ("cs.AI", "cs.CL", "cs.LG", "cs.CV", "cs.DC", "stat.ML", "cs.CR"):
        assert key in ARXIV_CATEGORIES


def _seed():
    init_db()
    with get_session() as s:
        build_domain_tree(s)


def test_arxiv_paper_gets_category_tags():
    _seed()
    from app.llm.providers.mock import MockProvider

    with get_session() as s:
        paper = upsert_paper(
            s,
            Paper(source="arxiv", source_id="cat-1",
                  title="Transformers for Vision Tasks",
                  abstract="Vision transformers for image classification.",
                  categories="cs.CV cs.LG"),
        )
        agent = AnchorAgent(s, provider=MockProvider())
        result = agent.run(AgentTask(task_type="anchor", paper={
            "id": paper.id, "source": "arxiv", "source_id": "cat-1",
            "title": paper.title, "abstract": paper.abstract, "categories": "cs.CV cs.LG",
        }))
        assert result.status == "ok"
        # AnchorAgent 产出 tags(落库由 orchestrator 完成)
        cat_tags = {t["tag"] for t in result.artifacts["tags"] if t["source"] == "category"}
        assert {"cs.CV", "cs.LG"} <= cat_tags


def test_non_arxiv_paper_uses_llm_category():
    """非 arXiv 来源:学科来自 LLM 输出的 arxiv_category。"""
    _seed()

    class FakeProvider:
        name = "fake"

        def chat_structured(self, messages, **kwargs):
            import json

            return json.dumps(
                {"domain_key": "models.llm.arch", "domain_name": "模型架构",
                 "confidence": 0.9, "summary": "", "keywords": [],
                 "arxiv_category": "stat.ML", "create_new": False,
                 "parent_key": "", "description": ""},
                ensure_ascii=False,
            )

    with get_session() as s:
        paper = upsert_paper(
            s,
            Paper(source="ieee", source_id="ieee-cat-1",
                  title="Neural Architecture Search on IEEE",
                  abstract="Searching transformer architectures."),
        )
        agent = AnchorAgent(s, provider=FakeProvider())
        result = agent.run(AgentTask(task_type="anchor", paper={
            "id": paper.id, "source": "ieee", "source_id": "ieee-cat-1",
            "title": paper.title, "abstract": paper.abstract,
        }))
        assert result.status == "ok"
        tags = result.artifacts["tags"]
        assert any(t["tag"] == "stat.ML" and t["source"] == "category" for t in tags)
