"""图书馆检索与凭据层测试(纯逻辑,不启动真实浏览器)。"""
from datetime import datetime

import pytest

from app.config import settings
from app.crawler.auth import PlaywrightLoginManager
from app.crawler.library import (
    parse_acm_html,
    parse_cnki_html,
    parse_ieee_records,
    get_library_crawler,
)


# ---------------------------------------------------------------------------
# 解析器(纯函数)
# ---------------------------------------------------------------------------
def test_parse_ieee_records():
    records = [
        {
            "articleNumber": "1234567",
            "title": "Large Language Models",
            "abstract": "We study LLMs.",
            "authors": [{"preferredName": "Alice"}, {"preferredName": "Bob"}],
            "publicationYear": 2023,
            "publicationTitle": "IEEE TPAMI",
            "documentLink": "https://ieeexplore.ieee.org/document/1234567",
        }
    ]
    papers = parse_ieee_records(records)
    assert len(papers) == 1
    p = papers[0]
    assert p.source == "ieee"
    assert p.source_id == "1234567"
    assert p.title == "Large Language Models"
    assert p.authors == "Alice, Bob"
    assert p.categories == "IEEE TPAMI"
    assert p.published_at == datetime(2023, 1, 1)
    assert "document/1234567" in p.url


def test_parse_ieee_records_skips_empty_title():
    assert parse_ieee_records([{"articleNumber": "9", "title": ""}]) == []


def test_parse_acm_html():
    html = (
        '<li class="search__item">'
        '<a href="/doi/10.1145/999999">Attention Is All You Need</a>'
        '<div class="issue-item__abstract">Transformer architecture.</div>'
        "</li>"
    )
    papers = parse_acm_html(html)
    assert len(papers) == 1
    p = papers[0]
    assert p.source == "acm"
    assert p.source_id == "10.1145/999999"
    assert p.title == "Attention Is All You Need"
    assert "Transformer" in p.abstract
    assert p.url == "https://dl.acm.org/doi/10.1145/999999"


def test_parse_cnki_html():
    html = '<a href="/kns8s/defaultresult/index?filename=ABC123">基于大语言模型的领域地图构建</a>'
    papers = parse_cnki_html(html)
    assert len(papers) == 1
    p = papers[0]
    assert p.source == "cnki"
    assert "大语言模型" in p.title
    assert p.url.startswith("https://kns.cnki.net/")


def test_get_library_crawler_factory():
    assert get_library_crawler("ieee").source == "ieee"
    assert get_library_crawler("acm").source == "acm"
    assert get_library_crawler("cnki").source == "cnki"
    with pytest.raises(ValueError):
        get_library_crawler("unknown")


# ---------------------------------------------------------------------------
# 凭据层(SecretVault 优先)
# ---------------------------------------------------------------------------
def test_store_and_read_credential(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.store_library_credential("ieee", account="alice@univ", password="s3cret")
    cred = settings.library_credentials["ieee"]
    assert cred["account"] == "alice@univ"
    assert cred["password"] == "s3cret"
    assert settings.has_library_credential("ieee") is True
    assert settings.masked_library_account("ieee") == "ali···iv"


def test_clear_credential(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.store_library_credential("acm", account="bob", password="pw")
    settings.store_library_credential("acm", account="", password="")
    assert settings.has_library_credential("acm") is False
    assert settings.library_credentials["acm"]["account"] == ""


def test_store_unknown_source_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with pytest.raises(ValueError):
        settings.store_library_credential("unknown", account="x", password="y")


# ---------------------------------------------------------------------------
# 登录信号增强(纯逻辑)
# ---------------------------------------------------------------------------
def test_site_specific_cookie_hint():
    m = PlaywrightLoginManager("ieee")
    assert m._cookie_is_auth({"name": "XPLORE_AUTH"}) is True  # 站点特定 hint
    assert m._cookie_is_auth({"name": "csrftoken"}) is False


def test_logged_in_via_page_marker_or_local_storage():
    m = PlaywrightLoginManager("ieee")
    base = {"in_site": True, "on_login_path": False}
    # 无任何登录信号 → 未登录
    assert m._is_logged_in({**base, "auth_cookies": 0, "local_storage_hits": 0, "page_marker": False}) is False
    # 页面出现登录成功标记 → 已登录(即使无 auth cookie)
    assert m._is_logged_in({**base, "auth_cookies": 0, "local_storage_hits": 0, "page_marker": True}) is True
    # localStorage 命中 → 已登录
    assert m._is_logged_in({**base, "auth_cookies": 0, "local_storage_hits": 1, "page_marker": False}) is True


def test_in_site_domain_boundary():
    m = PlaywrightLoginManager("acm")
    assert m._in_site_domain("https://dl.acm.org/doi/10.1145/1") is True
    assert m._in_site_domain("https://idp.acm.org/") is True            # 同注册域子域
    assert m._in_site_domain("https://dl.acm.org.evil.com/x") is False  # 前缀欺骗,注册域不同
    assert m._in_site_domain("https://www.google.com/") is False
