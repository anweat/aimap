"""多来源去重测试:标题归一化、同源/跨源重复判定。"""
import pytest
from sqlmodel import select

from app.crawler.dedup import find_duplicate, normalize_title, upsert_paper_dedup
from app.db import get_session, init_db, upsert_paper
from app.models.entities import Paper


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        yield s


def test_normalize_title():
    assert normalize_title("Attention Is All You Need") == normalize_title("attention is all you need.")
    assert normalize_title("  A  B  ") == "a b"
    # arXiv 版本号噪声剥离
    assert normalize_title("LoRA: Low-Rank Adaptation (arXiv:2106.09685)") == "lora low rank adaptation"
    # 大小写/标点
    assert normalize_title("RAG: Retrieval-Augmented Generation!") == "rag retrieval augmented generation"


def test_find_same_source(session):
    p1 = Paper(source="arxiv", source_id="2301.00001", title="Same Source Paper")
    upsert_paper(session, p1)
    p2 = Paper(source="arxiv", source_id="2301.00001", title="Same Source Paper v2")
    dup, reason = find_duplicate(session, p2)
    assert dup is not None and reason == "same_source"


def test_find_cross_source_title(session):
    p1 = Paper(source="arxiv", source_id="2301.00002", title="Flash Attention is Fast and Memory Efficient")
    upsert_paper(session, p1)
    # 来自 IEEE 的同一篇论文(标题大小写/标点不同)
    p2 = Paper(source="ieee", source_id="ieee-12345", title="flash attention is fast and memory efficient!")
    dup, reason = find_duplicate(session, p2)
    assert dup is not None and reason == "same_title"
    assert dup.source == "arxiv"


def test_find_same_url(session):
    p1 = Paper(source="arxiv", source_id="2301.00003", title="Unique Title A",
               url="https://doi.org/10.1234/xyz")
    upsert_paper(session, p1)
    p2 = Paper(source="acm", source_id="acm-99", title="Unique Title B (different)",
               url="https://doi.org/10.1234/xyz")
    dup, reason = find_duplicate(session, p2)
    assert dup is not None and reason == "same_url"


def test_no_duplicate(session):
    p = Paper(source="acm", source_id="acm-100", title="Completely Different Paper 2024")
    dup, reason = find_duplicate(session, p)
    assert dup is None and reason == ""


def test_upsert_dedup_results(session):
    # 新论文 → saved
    p1 = Paper(source="acm", source_id="acm-200", title="Dedup Test Paper Alpha")
    saved, result = upsert_paper_dedup(session, p1)
    assert result == "saved"

    # 同源再抓 → updated
    p2 = Paper(source="acm", source_id="acm-200", title="Dedup Test Paper Alpha (v2)")
    _, result = upsert_paper_dedup(session, p2)
    assert result == "updated"

    # 跨源重复 → duplicate_same_title
    p3 = Paper(source="cnki", source_id="cnki-200", title="dedup test paper alpha!")
    _, result = upsert_paper_dedup(session, p3)
    assert result == "duplicate_same_title"
