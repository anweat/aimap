"""领域演化引擎测试:热度分级、热门细分建议、冷门标记、自动创建。"""
import json

import pytest

from app.db import get_session, init_db, upsert_paper
from app.domain.builder import build_domain_tree
from app.domain.evolution import (
    cold_domains,
    domain_stats,
    evolve,
    hot_domains,
    suggest_subdivisions,
)
from app.domain.policy import create_domain
from app.models.entities import DomainNode, Paper


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        build_domain_tree(s)
        yield s


def _seed_hot_domain(session, domain_key: str, n: int, prefix: str):
    """在指定领域下造 n 篇论文,使其成为热门。"""
    with get_session() as s:
        for i in range(n):
            upsert_paper(
                s,
                Paper(source="test", source_id=f"{prefix}-{i}",
                      title=f"{prefix} Paper {i}", abstract="x",
                      categories="cs.LG"),
            )
            p = s.exec(
                __import__("sqlmodel").select(Paper).where(
                    Paper.source_id == f"{prefix}-{i}")
            ).first()
            p.anchored_domain_key = domain_key
            s.add(p)
        s.commit()


def test_domain_stats_heat(session):
    _seed_hot_domain(session, "infra.inference", 14, "hot-inf")
    stats = domain_stats(session)
    by_key = {st.key: st for st in stats}
    assert by_key["infra.inference"].heat == "hot"
    assert by_key["infra.inference"].paper_count >= 14
    assert by_key["infra.inference"].parent_key == "infra"
    # 冷门:0 论文的种子领域
    cold = [st for st in stats if st.heat == "cold"]
    assert cold


def test_hot_and_cold_lists(session):
    hots = hot_domains(session, limit=3)
    assert hots and all(h.heat == "hot" for h in hots)
    colds = cold_domains(session)
    assert all(c["paper_count"] <= 1 for c in colds)


class FakeSplitProvider:
    """模拟 LLM:对热门领域输出 2 个细分方向。"""

    name = "fake"

    def chat_structured(self, messages, **kwargs):
        return json.dumps([
            {"name": "PagedAttention", "description": "分页 KV cache 管理", "confidence": 0.9},
            {"name": "Speculative Decoding", "description": "推测解码加速", "confidence": 0.8},
        ], ensure_ascii=False)


def test_suggest_subdivisions(session):
    suggestions = suggest_subdivisions(session, provider=FakeSplitProvider(), limit=1)
    assert suggestions, "应有热门领域建议"
    item = suggestions[0]
    assert "suggestions" in item
    assert len(item["suggestions"]) == 2
    assert item["suggestions"][0]["name"] == "PagedAttention"


def test_evolve_auto_create(session):
    report = evolve(session, auto_create=True, limit=1, provider=FakeSplitProvider())
    assert report.suggested
    assert report.created or report.reused
    keys = [c["key"] for c in report.created]
    # 新建的子领域挂在热门领域下
    for k in keys:
        node = session.get(DomainNode, k)
        assert node is not None and node.parent_key == "infra.inference"
        assert node.created_by == "ai"
    # 二次演化 → 复用不重复建
    report2 = evolve(session, auto_create=True, limit=1, provider=FakeSplitProvider())
    assert report2.created == [] or all(
        c["key"] not in keys for c in report2.created
    ) or report2.reused


def test_evolve_no_auto_create(session):
    report = evolve(session, auto_create=False, limit=1, provider=FakeSplitProvider())
    assert report.suggested
    assert report.created == []  # 只建议不创建
