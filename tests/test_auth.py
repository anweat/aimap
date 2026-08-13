"""图书馆登录会话管理测试(不启动真实浏览器,覆盖信号检测纯逻辑)。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.crawler.auth import PlaywrightLoginManager, SessionExpired


@pytest.fixture
def manager(tmp_path):
    """隔离会话目录:临时 data_dir。"""
    old_dir = settings.data_dir
    settings.data_dir = tmp_path
    m = PlaywrightLoginManager("ieee")
    yield m
    settings.data_dir = old_dir


# ---------- 登录信号检测(纯逻辑,无需浏览器) ----------

def test_cookie_auth_detection(manager):
    # 真正的登录态 cookie
    assert manager._cookie_is_auth({"name": "auth_token"}) is True
    assert manager._cookie_is_auth({"name": "SESSION_TOKEN"}) is True
    assert manager._cookie_is_auth({"name": "XPLORE_AUTH"}) is True
    # 游客态也会有的 cookie(应用会话 ID / 跟踪 / CSRF),不代表登录
    assert manager._cookie_is_auth({"name": "sessionid"}) is False
    assert manager._cookie_is_auth({"name": "ASP.NET_SessionId"}) is False
    assert manager._cookie_is_auth({"name": "JSESSIONID"}) is False
    assert manager._cookie_is_auth({"name": "_ga"}) is False
    assert manager._cookie_is_auth({"name": "csrftoken"}) is False
    assert manager._cookie_is_auth({"name": "gad_clientid"}) is False


def test_login_path_detection(manager):
    assert manager._is_login_path("https://ieeexplore.ieee.org/signin") is True
    assert manager._is_login_path("https://dl.acm.org/sso/login") is True
    assert manager._is_login_path("https://ieeexplore.ieee.org/document/123") is False


def test_in_site_domain(manager):
    assert manager._in_site_domain("https://ieeexplore.ieee.org/document/1") is True
    assert manager._in_site_domain("https://idp.ieee.org/") is True  # 同主域
    assert manager._in_site_domain("https://www.google.com/") is False


def test_is_logged_in_combined(manager):
    # 全部信号满足 → 已登录
    ok = {"in_site": True, "auth_cookies": 2, "on_login_path": False}
    assert manager._is_logged_in(ok) is True
    # 在登录页 → 未登录
    assert manager._is_logged_in({**ok, "on_login_path": True}) is False
    # 无会话 cookie → 未登录
    assert manager._is_logged_in({**ok, "auth_cookies": 0}) is False
    # 不在站内 → 未登录(如 SSO 跳转到第三方)
    assert manager._is_logged_in({**ok, "in_site": False}) is False


# ---------- 会话存储 ----------

def test_no_session_raises(manager):
    assert manager.has_session() is False
    with pytest.raises(SessionExpired):
        manager.load_state()


def test_save_and_load_roundtrip(manager):
    """storage_state 落盘后能读回(通过 _save 模拟登录成功路径)。"""
    fake_state = {
        "cookies": [{"name": "sessionid", "value": "abc123", "domain": "ieeexplore.ieee.org",
                     "path": "/", "expires": -1, "httpOnly": False, "secure": False,
                     "sameSite": "Lax"}],
        "origins": [],
    }
    meta = manager._save(fake_state)
    assert meta["source"] == "ieee"
    assert manager.has_session() is True
    assert manager.session_meta()["expired"] is False
    assert manager.session_meta()["cookie_count"] == 1

    loaded = manager.load_state()
    assert loaded["cookies"][0]["value"] == "abc123"


def test_expired_session(manager):
    manager._save({"cookies": [], "origins": []})
    meta = json.loads(manager.session_file.read_text(encoding="utf-8"))
    meta["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    manager.session_file.write_text(json.dumps(meta), encoding="utf-8")
    assert manager.session_meta()["expired"] is True
    with pytest.raises(SessionExpired):
        manager.load_state()


def test_unknown_source():
    with pytest.raises(ValueError):
        PlaywrightLoginManager("unknown")
