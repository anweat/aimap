"""多层分类器测试:规则层 / 统计层 / LLM(mock)层 / 集成层。"""
import json

import pytest

from app.classify.ensemble import DEFAULT_LAYER_WEIGHTS, EnsembleClassifier
from app.classify.base import ClassifierResult
from app.classify.llm import LlmClassifier
from app.classify.rules import RulesClassifier
from app.classify.stats import StatsClassifier
from app.db import get_session, init_db, upsert_domain_node
from app.domain.builder import build_domain_tree
from app.llm.providers.mock import MockProvider
from app.models.entities import DomainNode


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        build_domain_tree(s)
        yield s


def test_rules_classifier_hits(session):
    c = RulesClassifier(session)
    r = c.classify("Scaling Laws for Neural Language Models", "We study empirical scaling laws.")
    assert r is not None
    assert r.layer == "rules"
    assert r.domain_key == "models.llm.scaling"
    assert r.confidence > 0.5
    assert "scaling" in r.evidence


def test_rules_classifier_abstain(session):
    c = RulesClassifier(session)
    r = c.classify("A Study of Quantum Entanglement", "Entanglement entropy in conformal field theory.")
    assert r is None


def test_stats_classifier(session):
    c = StatsClassifier(session)
    r = c.classify(
        "KV Cache Compression for Efficient LLM Inference",
        "We optimize inference serving throughput via paged attention and KV cache reuse.",
    )
    assert r is not None
    assert r.layer == "stats"
    assert "infra" in r.domain_key


def test_llm_classifier_mock(session):
    c = LlmClassifier(MockProvider())
    r = c.classify(
        "Retrieval-Augmented Generation for Knowledge-Intensive Tasks",
        "We introduce RAG combining retrieval with generation.",
    )
    assert r is not None
    assert r.layer == "llm"
    assert r.domain_key  # mock 至少给出一个领域
    assert 0.0 <= r.confidence <= 1.0


def test_ensemble_weights():
    e = EnsembleClassifier()
    results = [
        ClassifierResult(layer="rules", domain_key="models.llm", confidence=0.8),
        ClassifierResult(layer="stats", domain_key="models.llm", confidence=0.7),
        ClassifierResult(layer="llm", domain_key="algorithm.agent", confidence=0.9),
    ]
    out = e.combine(results)
    assert out is not None
    assert out.domain_key == "models.llm"  # 0.8+0.7 vs 0.9*1.5=1.35 → 1.5 > 1.35
    assert out.confidence > 0.5
    assert len(out.detail) == 3


def test_ensemble_all_abstain():
    e = EnsembleClassifier()
    assert e.combine([None, None]) is None
    assert e.combine([]) is None


def test_llm_output_json_parse(session):
    """验证 mock provider 输出可被 LlmClassifier 解析。"""
    c = LlmClassifier(MockProvider())
    r = c.classify("LoRA: Low-Rank Adaptation of Large Language Models", "Efficient fine-tuning.")
    assert r.domain_key
    assert r.evidence
