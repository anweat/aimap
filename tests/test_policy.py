"""动态领域注册测试:基准规则、AI 建领域、坐标分配、去重。"""
import math

import pytest

from app.db import get_session, init_db
from app.domain.builder import build_domain_tree
from app.domain.policy import create_domain, find_domain, recent_ai_domains
from app.models.entities import DomainNode
from app.quaternion.core import Quaternion


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        build_domain_tree(s)
        yield s


def test_create_domain_basic(session):
    node = create_domain(session, "Agentic RAG", parent_key="algorithm.retrieval",
                         description="RAG 与智能体结合的研究方向", keywords=["agentic rag", "tool use"])
    assert node.parent_key == "algorithm.retrieval"
    assert node.created_by == "ai"
    assert node.level == 2  # algorithm(0) → retrieval(1) → 新领域(2)
    assert node.description.startswith("RAG")
    # 单位四元数
    norm = math.sqrt(node.qw**2 + node.qx**2 + node.qy**2 + node.qz**2)
    assert norm == pytest.approx(1.0, abs=1e-6)
    # 与父领域有一定夹角(坐标在父附近但不重合)
    parent = session.get(DomainNode, "algorithm.retrieval")
    qn = Quaternion(node.qw, node.qx, node.qy, node.qz)
    qp = Quaternion(parent.qw, parent.qx, parent.qy, parent.qz)
    assert 0.05 < qn.angle_to(qp) < math.pi / 2


def test_create_domain_dedup_same_parent(session):
    n1 = create_domain(session, "Agentic RAG", parent_key="algorithm.retrieval")
    n2 = create_domain(session, "agentic rag", parent_key="algorithm.retrieval")
    assert n1.key == n2.key  # 同父下名称归一化去重


def test_create_domain_same_name_diff_parent(session):
    n1 = create_domain(session, "Eval Suite", parent_key="algorithm.eval")
    n2 = create_domain(session, "Eval Suite", parent_key="algorithm.training")
    assert n1.key != n2.key


def test_create_domain_key_unique_with_sequence(session):
    # 同父下刻意制造同名不同规范名,验证序号生成
    a = create_domain(session, "Test Domain X", parent_key="models")
    b = create_domain(session, "Test Domain X-2", parent_key="models")  # slug 不同
    assert a.key != b.key
    c = create_domain(session, "Test Domain X-2", parent_key="models")
    assert c.key == b.key  # 复用


def test_create_domain_fallback_parent(session):
    """父领域不存在时自动挂到兄弟最少的根。"""
    node = create_domain(session, "Quantum ML", parent_key="nonexistent.parent")
    assert node.parent_key in ("models", "algorithm", "infra")


def test_create_domain_empty_name(session):
    with pytest.raises(ValueError):
        create_domain(session, "   ")


def test_find_domain(session):
    create_domain(session, "Federated Learning", parent_key="models.llm.arch")
    found = find_domain(session, "federated learning", "models.llm.arch")
    assert found is not None
    assert find_domain(session, "federated learning", "models") is None  # 限定父


def test_recent_ai_domains(session):
    domains = recent_ai_domains(session)
    assert all(d.created_by == "ai" for d in domains)
    assert len(domains) >= 1
