"""端到端管线测试:领域树构建 → 论文入库 → 多层分析 → 锚定位置。"""
import math

import pytest
from sqlmodel import select

from app.agents.orchestrator import OrchestratorAgent
from app.db import (
    get_position,
    get_session,
    init_db,
    list_classifications,
    upsert_paper,
)
from app.domain.builder import build_domain_tree
from app.models.entities import DomainNode, Paper
from app.quaternion.core import Quaternion


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        build_domain_tree(s)
        yield s


def _make_paper(source_id: str, title: str, abstract: str) -> Paper:
    return Paper(
        source="test",
        source_id=source_id,
        title=title,
        abstract=abstract,
        authors="Test Author",
        categories="cs.LG",
        url=f"https://example.com/{source_id}",
    )


def test_tree_built_with_quaternions(session):
    nodes = session.exec(select(DomainNode)).all()
    assert len(nodes) >= 20  # 种子数据规模
    # 所有节点为单位四元数
    for n in nodes:
        norm = math.sqrt(n.qw**2 + n.qx**2 + n.qy**2 + n.qz**2)
        assert norm == pytest.approx(1.0, abs=1e-6)
    # 根领域彼此分离(不重叠于恒等四元数)
    roots = [n for n in nodes if n.parent_key is None]
    assert len(roots) == 3
    for i in range(len(roots)):
        for j in range(i + 1, len(roots)):
            qi = Quaternion(roots[i].qw, roots[i].qx, roots[i].qy, roots[i].qz)
            qj = Quaternion(roots[j].qw, roots[j].qx, roots[j].qy, roots[j].qz)
            assert qi.angle_to(qj) > 0.8  # 根间至少约 46° 分离
    # 子节点与父节点角度小于根间角度(逐层收敛)
    parent = {n.key: n.parent_key for n in nodes}
    for n in nodes:
        if n.parent_key is None:
            continue
        p = next(x for x in nodes if x.key == n.parent_key)
        qn = Quaternion(n.qw, n.qx, n.qy, n.qz)
        qp = Quaternion(p.qw, p.qx, p.qy, p.qz)
        assert qn.angle_to(qp) < 1.0


def test_full_pipeline_anchor(session):
    paper = _make_paper(
        "pipeline-1",
        "Efficient Training of Language Models with Tensor Parallelism",
        "We scale distributed training of large language models using data and tensor parallelism.",
    )
    saved = upsert_paper(session, paper)

    orchestrator = OrchestratorAgent(session)
    result = orchestrator.analyze_paper(saved.id)
    assert result.status == "ok", result.message

    # 论文锚定字段
    session.refresh(saved)
    assert saved.anchored_domain_key is not None
    assert "infra" in saved.anchored_domain_key
    assert saved.analyzed_at is not None

    # 位置已生成且为单位四元数
    pos = get_position(session, saved.id)
    assert pos is not None
    assert pos.domain_key == saved.anchored_domain_key
    norm = math.sqrt(pos.qw**2 + pos.qx**2 + pos.qy**2 + pos.qz**2)
    assert norm == pytest.approx(1.0, abs=1e-6)

    # 各层分类证据落库
    layers = {c.layer for c in list_classifications(session, saved.id)}
    assert "rules" in layers
    assert "llm" in layers

    # 领域计数更新
    node = session.get(DomainNode, saved.anchored_domain_key)
    assert node.paper_count >= 1


def test_pipeline_deterministic_position(session):
    """同一论文两次分析,位置一致(确定性)。"""
    paper = _make_paper(
        "pipeline-2",
        "Retrieval Augmented Generation for Knowledge Intensive Tasks",
        "RAG combines parametric memory with non-parametric retrieval.",
    )
    saved = upsert_paper(session, paper)

    orchestrator = OrchestratorAgent(session)
    orchestrator.analyze_paper(saved.id)
    pos1 = get_position(session, saved.id)

    orchestrator.analyze_paper(saved.id)
    pos2 = get_position(session, saved.id)

    assert pos1 is not None and pos2 is not None
    assert (pos1.qw, pos1.qx, pos1.qy, pos1.qz) == pytest.approx(
        (pos2.qw, pos2.qx, pos2.qy, pos2.qz)
    )


def test_analyze_missing_paper(session):
    orchestrator = OrchestratorAgent(session)
    result = orchestrator.analyze_paper(999999)
    assert result.status == "error"


def test_upsert_preserves_anchor(session):
    """重复采集同一论文不得覆盖已锚定的分析结果。"""
    paper = _make_paper(
        "upsert-1",
        "Tensor Parallelism for Efficient LLM Training",
        "We study distributed training with tensor parallelism for large models.",
    )
    saved = upsert_paper(session, paper)
    OrchestratorAgent(session).analyze_paper(saved.id)
    session.refresh(saved)
    anchored = saved.anchored_domain_key
    assert anchored

    # 模拟再次爬取同一来源论文
    duplicate = _make_paper(
        "upsert-1",
        "Tensor Parallelism for Efficient LLM Training",
        "We study distributed training with tensor parallelism for large models.",
    )
    saved2 = upsert_paper(session, duplicate)
    assert saved2.id == saved.id
    assert saved2.anchored_domain_key == anchored  # 锚定保留
    assert saved2.analyzed_at is not None
