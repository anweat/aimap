"""Web 场景下的「手动登录」:后台弹出浏览器,用户登录后在前端点「确认保存」。

与 CLI 的 input() 回车不同,Web 无终端,改为两步:
  1. POST /sources/{name}/manual-login          → 后台线程弹出有头浏览器并等待;
  2. POST /sources/{name}/manual-login/confirm  → 用户在前端点「确认保存」,
     后台线程保存 storage_state 并回访校验。

适用图书馆跳转到学校统一认证(CARSI / Shibboleth / CAS)的场景:用户在浏览器里
走完整条 SSO 跳转后点确认,保存的是最终生效的图书馆域名会话 cookie。
"""
from __future__ import annotations

import threading
import time

from app.crawler.auth import PlaywrightLoginManager, PlaywrightUnavailable

# 等待前端确认的超时(秒);超时后浏览器自动关闭
_CONFIRM_TIMEOUT = 15 * 60

# name -> 进行中的手动登录会话(event / holder / started_at)
_ACTIVE: dict[str, dict] = {}
_LOCK = threading.Lock()


def start(source: str) -> dict:
    """启动手动登录(后台线程)。已在进行的来源直接拒绝,避免多开浏览器。"""
    try:
        manager = PlaywrightLoginManager(source)
    except PlaywrightUnavailable as e:
        return {"started": False, "detail": str(e)}

    with _LOCK:
        if source in _ACTIVE:
            return {"started": False, "detail": f"{source} 已有进行中的手动登录,请先确认或等待超时"}
        evt = threading.Event()
        holder: dict = {"result": None, "error": None, "done": False}
        _ACTIVE[source] = {"event": evt, "holder": holder, "started_at": time.monotonic()}

    threading.Thread(target=_worker, args=(source, manager, evt, holder), daemon=True).start()
    return {"started": True, "timeout_s": _CONFIRM_TIMEOUT}


def _worker(source: str, manager: PlaywrightLoginManager, evt: threading.Event, holder: dict) -> None:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(manager.site["login_entry"], timeout=60_000)
            except Exception:
                pass  # 首页加载慢/超时不阻断,用户仍可手动操作
            # 阻塞等待前端确认(或超时)
            evt.wait(_CONFIRM_TIMEOUT)
            if not evt.is_set():
                holder["error"] = f"等待确认超时({_CONFIRM_TIMEOUT // 60} 分钟),已放弃"
            else:
                state = context.storage_state()
                meta = manager._save(state)
                meta["cookie_count"] = len(state.get("cookies", []))
                holder["result"] = meta
            browser.close()
    except Exception as e:  # noqa: BLE001
        holder["error"] = f"手动登录异常: {e}"
    finally:
        holder["done"] = True
        with _LOCK:
            _ACTIVE.pop(source, None)


def confirm(source: str, verify: bool = True) -> dict:
    """前端确认保存:触发 worker 保存会话,并(可选)回访校验。"""
    with _LOCK:
        active = _ACTIVE.get(source)
    if not active:
        return {"confirmed": False, "status": "error", "detail": "无进行中的手动登录,请先点击「手动登录」"}

    active["event"].set()
    holder = active["holder"]
    deadline = time.monotonic() + 30
    while not holder["done"] and time.monotonic() < deadline:
        time.sleep(0.2)

    if holder.get("error"):
        return {"confirmed": True, "status": "error", "detail": holder["error"]}
    meta = holder.get("result")
    if not meta:
        return {"confirmed": True, "status": "error", "detail": "会话保存失败"}

    verified = None
    if verify:
        try:
            verified = PlaywrightLoginManager(source).verify_session().get("valid")
        except Exception:
            verified = None
    return {
        "confirmed": True,
        "status": "ok",
        "source": source,
        "expires_at": meta.get("expires_at"),
        "cookie_count": meta.get("cookie_count", 0),
        "verified": verified,
    }


def status(source: str) -> dict:
    """查询该来源是否有进行中的手动登录。"""
    with _LOCK:
        return {"active": source in _ACTIVE}
