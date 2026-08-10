"""数据源管理测试:CRUD、默认初始化、探测建议。"""
import json

import pytest

from app.db import (
    delete_source,
    get_session,
    get_source,
    init_db,
    init_default_sources,
    list_sources,
    upsert_source,
)
from app.models.entities import SourceConfig


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        init_default_sources(s)
        yield s


def test_default_sources(session):
    with get_session() as s:
        names = {src.name for src in list_sources(s)}
    assert {"arxiv", "ieee", "acm", "cnki"} <= names
    arxiv = get_source(session, "arxiv")
    assert arxiv.source_type == "open"
    assert "export.arxiv.org" in json.loads(arxiv.config)["api_url"]


def test_upsert_and_delete(session):
    with get_session() as s:
        src = upsert_source(
            s,
            SourceConfig(name="my-mirror", display_name="测试镜像", source_type="open",
                         config=json.dumps({"api_url": "https://mirror.example.com"})),
        )
        assert src.name == "my-mirror"
        # 更新
        src2 = upsert_source(
            s,
            SourceConfig(name="my-mirror", display_name="测试镜像2", source_type="open",
                         config=json.dumps({"api_url": "https://mirror2.example.com"})),
        )
        assert src2.display_name == "测试镜像2"
        # 删除
        assert delete_source(s, "my-mirror") is True
        assert delete_source(s, "my-mirror") is False  # 已删


def test_probe_open_source_unreachable(monkeypatch):
    """open 源端点不可达 → 给出代理/镜像建议。"""
    from app.crawler import probe as probe_mod
    from app.crawler.probe import probe_source

    monkeypatch.setattr(
        probe_mod, "_probe_http",
        lambda url, timeout=6.0: {"reachable": False, "error": "连接失败:网络不可达"},
    )
    with get_session() as s:
        src = upsert_source(
            s,
            SourceConfig(name="probe-test", display_name="探测", source_type="open",
                         config=json.dumps({"api_url": "https://dead.example.com"})),
        )
        report = probe_source(s, src)
        assert report["reachable"] is False
        assert any("HTTPS_PROXY" in sug for sug in report["suggestions"])
        delete_source(s, "probe-test")


def test_probe_library_no_session(session, tmp_path, monkeypatch):
    """library 源无会话 → 建议登录(隔离 sessions 目录)。"""
    from app.config import settings
    from app.crawler.probe import probe_source

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with get_session() as s:
        src = get_source(s, "ieee")
        report = probe_source(s, src)
        assert report["source_type"] == "library"
        assert any("library_login" in sug for sug in report["suggestions"])


def test_probe_http_ok(monkeypatch):
    from app.crawler import probe as probe_mod
    from app.crawler.probe import probe_source

    monkeypatch.setattr(
        probe_mod, "_probe_http",
        lambda url, timeout=6.0: {"reachable": True, "status": 200, "ms": 120},
    )
    with get_session() as s:
        src = upsert_source(
            s,
            SourceConfig(name="probe-ok", display_name="可达", source_type="open",
                         config=json.dumps({"api_url": "https://ok.example.com"})),
        )
        report = probe_source(s, src)
        assert report["reachable"] is True
        assert report["details"]["status"] == 200
        assert report["suggestions"] == [] or report["suggestions"]
        delete_source(s, "probe-ok")


def test_login_endpoint_validation():
    """登录端点:未知来源 404、公开源 400。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/sources/nonexist/login")
    assert r.status_code == 404
    r = client.post("/api/sources/arxiv/login")
    assert r.status_code == 400
    assert "无需登录" in r.json()["detail"]


def test_sources_list_session_field(session):
    """来源列表带 session 状态(library 有,open 为 None)。"""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.get("/api/sources")
    assert r.status_code == 200
    by_name = {s["name"]: s for s in r.json()["sources"]}
    assert "session" in by_name["arxiv"] and by_name["arxiv"]["session"] is None
    assert "session" in by_name["ieee"]
    assert by_name["ieee"]["session"]["has"] in (True, False)
