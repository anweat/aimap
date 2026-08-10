"""API 集成测试:通过 FastAPI TestClient 覆盖全部路由。"""
import pytest
from fastapi.testclient import TestClient

from app.db import get_session, init_db, upsert_paper
from app.domain.builder import build_domain_tree
from app.main import app
from app.models.entities import Paper

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _setup():
    init_db()
    with get_session() as s:
        build_domain_tree(s)
        upsert_paper(
            s,
            Paper(
                source="test",
                source_id="api-1",
                title="DeepSpeed: System Optimizations Enable Training Deep Learning Models",
                abstract="We enable extreme-scale model training with ZeRO optimization.",
                authors="Microsoft",
                categories="cs.LG cs.DC",
                url="https://example.com/api-1",
            ),
        )
    yield


def test_status():
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "arxiv" in body["sources"]
    assert body["domain_nodes"] > 0


def test_tree():
    r = client.get("/api/tree")
    assert r.status_code == 200
    nodes = r.json()["nodes"]
    keys = {n["key"] for n in nodes}
    assert {"models", "algorithm", "infra"} <= keys
    # 每个节点带四元数与 3D 投影
    n = nodes[0]
    assert set(n["position"]) == {"qw", "qx", "qy", "qz"}
    assert len(n["xyz"]) == 3


def test_analyze_then_detail():
    # 触发多层分析(动态取第一篇论文,避免硬编码 id)
    pid = _first_paper_id()
    r = client.post(f"/api/papers/{pid}/analyze")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"

    # 详情含证据链与位置
    r = client.get(f"/api/papers/{pid}")
    assert r.status_code == 200
    d = r.json()
    assert d["anchored"]["domain_key"]
    assert len(d["classifications"]) >= 2  # rules + llm(甚至 stats)
    assert d["anchored"]["position"]["qw"] != 0


def _first_paper_id() -> int:
    r = client.get("/api/papers")
    rows = r.json()
    assert rows, "前置数据缺失"
    return rows[0]["id"]


def test_map_nodes():
    r = client.get("/api/map/nodes")
    assert r.status_code == 200
    body = r.json()
    assert len(body["domains"]) > 0
    for d in body["domains"]:
        assert len(d["q"]) == 4
        assert len(d["xyz"]) == 3


def test_search_hit():
    r = client.get("/api/search?q=DeepSpeed")
    assert r.status_code == 200
    hits = r.json()["hits"]
    assert len(hits) >= 1
    assert "DeepSpeed" in hits[0]["title"]


def test_search_miss():
    r = client.get("/api/search?q=zzzzz_no_such_thing")
    assert r.status_code == 200
    assert r.json()["hits"] == []


def test_papers_list():
    r = client.get("/api/papers")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert "position" in rows[0]


def test_analyze_missing_paper_404():
    r = client.post("/api/papers/999999/analyze")
    assert r.status_code == 404


def test_frontend_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "AIMap" in r.text
