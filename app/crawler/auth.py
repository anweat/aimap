"""图书馆模拟浏览器认证:基于 playwright 的登录与会话(cookie)复用。

流程(scripts/library_login.py --source ieee):
  1. 检查 data/sessions/<source>.json 会话是否有效(--status / --verify);
  2. 无效 → 启动浏览器(默认有头模式):
       a. 自动模式(--auto):自动填入账号密码并提交(站点选择器可配置);
       b. 人工模式(默认):打开登录页,由用户手动完成登录(验证码/SSO);
  3. 登录成功判定(全自动,无需人工确认):
       - URL 处于目标站内页,且
       - 出现会话类 cookie(名称含 session/token/auth 等),且
       - 已离开登录入口页(login/signin/sso)
     —— 信号全部满足即自动保存会话;
  4. 保存后立即用会话回访目标站校验有效性(verify),失败则提示重登;
  5. 超时输出诊断信息(当前 URL / cookie 清单 / localStorage),便于排查。

会话文件含登录 cookie,属敏感数据,保存于 data/sessions/ 并 chmod 600;
账号密码本身不落盘,来自 .env 或 SecretVault。
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

try:  # playwright 为可选依赖:未安装时给出明确指引而非崩溃
    from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:  # pragma: no cover
    _HAS_PLAYWRIGHT = False

# 常见会话类 cookie 名关键字(命中即视为登录态信号)
_AUTH_COOKIE_HINTS = ("session", "auth", "login", "sid", "jwt")
# CSRF/XSRF 防护 cookie 不是登录态,需排除
_CSRF_HINTS = ("csrf", "xsrf")
# 登录入口页路径关键字(出现则视为"尚未登录")
_LOGIN_PATH_HINTS = ("login", "signin", "sign-in", "sso", "auth", "logon", "cas")


class PlaywrightUnavailable(Exception):
    pass


class SessionExpired(Exception):
    pass


class LoginTimeout(Exception):
    """登录等待超时:携带诊断信息。"""

    def __init__(self, source: str, diagnostics: dict):
        self.source = source
        self.diagnostics = diagnostics
        super().__init__(f"[{source}] 登录等待超时,诊断信息见 diagnostics")


def _sessions_dir() -> Path:
    d = settings.data_dir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


class PlaywrightLoginManager:
    """图书馆登录会话管理:登录 → 自动检测成功 → 保存 → 校验 → 复用。"""

    # 各库站点信息
    SITES: dict[str, dict] = {
        "ieee": {
            "name": "IEEE Xplore",
            "home": "https://ieeexplore.ieee.org/",
            "login_entry": "https://ieeexplore.ieee.org/",
        },
        "acm": {
            "name": "ACM Digital Library",
            "home": "https://dl.acm.org/",
            "login_entry": "https://dl.acm.org/",
        },
        "cnki": {
            "name": "中国知网 CNKI",
            "home": "https://kns.cnki.net/",
            "login_entry": "https://kns.cnki.net/",
        },
    }
    # 自动填表选择器(可选;未配置时自动模式回退人工模式)
    SELECTORS: dict[str, dict[str, str]] = {}

    def __init__(self, source: str):
        if not _HAS_PLAYWRIGHT:
            raise PlaywrightUnavailable(
                "playwright 未安装:请执行 "
                "pip install -e \".[library]\" && playwright install chromium"
            )
        if source not in self.SITES:
            raise ValueError(f"未知图书馆数据源: {source}")
        self.source = source
        self.site = self.SITES[source]
        self.session_file = _sessions_dir() / f"{source}.json"

    # ================= 会话状态 =================
    def has_session(self) -> bool:
        return self.session_file.exists()

    def session_meta(self) -> dict | None:
        if not self.session_file.exists():
            return None
        try:
            meta = json.loads(self.session_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return {
            "source": meta.get("source"),
            "saved_at": meta.get("saved_at"),
            "expires_at": meta.get("expires_at"),
            "cookie_count": len(meta.get("storage_state", {}).get("cookies", [])),
            "expired": self._is_expired(meta),
        }

    def _is_expired(self, meta: dict | None) -> bool:
        if not meta or not meta.get("expires_at"):
            return True
        try:
            expires = datetime.fromisoformat(meta["expires_at"])
            return datetime.now(timezone.utc) > expires
        except ValueError:
            return True

    def load_state(self) -> dict:
        """读取 storage_state(内部使用,不暴露给调用方日志)。"""
        if not self.has_session():
            raise SessionExpired(
                f"[{self.source}] 无登录会话,请先运行 "
                f"python scripts/library_login.py --source {self.source}"
            )
        meta = json.loads(self.session_file.read_text(encoding="utf-8"))
        if self._is_expired(meta):
            raise SessionExpired(
                f"[{self.source}] 登录会话已过期({meta.get('expires_at')}),请重新登录"
            )
        return meta["storage_state"]

    # ================= 登录信号检测 =================
    @staticmethod
    def _cookie_is_auth(cookie: dict) -> bool:
        name = cookie.get("name", "").lower()
        if any(h in name for h in _CSRF_HINTS):
            return False  # CSRF 防护 cookie 不代表登录态
        return any(hint in name for hint in _AUTH_COOKIE_HINTS)

    @staticmethod
    def _is_login_path(url: str) -> bool:
        path = (urlparse(url).path or "").lower()
        return any(hint in path for hint in _LOGIN_PATH_HINTS)

    def _in_site_domain(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        site_host = urlparse(self.site["home"]).hostname or ""
        return site_host in host or host in site_host or site_host.split(".")[-2:] == host.split(".")[-2:]

    def _collect_signals(self, context: "BrowserContext", page: "Page") -> dict:
        """采集登录信号(永不抛异常,失败字段置 None)。"""
        signals: dict = {"url": "", "in_site": False, "on_login_path": False,
                         "auth_cookies": 0, "total_cookies": 0, "local_storage": 0}
        try:
            signals["url"] = page.url
            signals["in_site"] = self._in_site_domain(page.url)
            signals["on_login_path"] = self._is_login_path(page.url)
        except Exception:
            pass
        try:
            cookies = context.cookies()
            signals["total_cookies"] = len(cookies)
            signals["auth_cookies"] = sum(1 for c in cookies if self._cookie_is_auth(c))
        except Exception:
            pass
        try:
            signals["local_storage"] = page.evaluate("() => localStorage.length")
        except Exception:
            pass
        return signals

    def _is_logged_in(self, signals: dict) -> bool:
        """综合判定登录成功:
        1. 已在目标站域名内;且
        2. 存在会话类 cookie(≥1);且
        3. 已离开登录入口页(不在 login/signin/sso 路径)。
        """
        return bool(
            signals.get("in_site")
            and signals.get("auth_cookies", 0) >= 1
            and not signals.get("on_login_path")
        )

    # ================= 登录流程 =================
    def login(self, account: str = "", password: str = "", *, headless: bool = False,
              auto: bool = False, timeout_s: int = 600) -> dict:
        """执行登录:自动检测成功 → 保存会话 → 回访校验。"""
        if not _HAS_PLAYWRIGHT:
            raise PlaywrightUnavailable("playwright 未安装")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context()
            page = context.new_page()
            entry = self.site["login_entry"]
            print(f"[auth:{self.source}] 打开站点: {entry}")
            try:
                page.goto(entry, timeout=60_000)
            except PWTimeout:
                print("[auth] 警告:站点打开超时,继续等待登录信号…")

            if auto and account and password:
                if not self._auto_fill(page, account, password):
                    print("[auth] 自动填表失败(站点结构变化),回退人工模式")

            print(f"[auth:{self.source}] 请在浏览器中完成登录(支持验证码/SSO/二次验证)…")
            signals, ok = self._wait_for_login(context, page, timeout_s)
            if not ok:
                browser.close()
                raise LoginTimeout(self.source, signals)

            # 保存 storage_state(含 cookie/localStorage)
            state = context.storage_state()
            meta = self._save(state)
            browser.close()
            print(f"[auth:{self.source}] 登录成功!会话已保存: {self.session_file}")
            print(f"          有效期至: {meta['expires_at']}")

            # 保存后立即校验:会话是否真实可用
            try:
                verify = self.verify_session()
            except Exception as e:
                verify = {"valid": False, "error": str(e)[:200]}
            if verify.get("valid"):
                print(f"[auth:{self.source}] 会话校验通过 ✓(回访 {self.site['home']} 确认登录态有效)")
            else:
                print(f"[auth:{self.source}] 警告:会话校验未通过({verify.get('error', '未知原因')}),"
                      f"请重新运行登录;若站点无 cookie 登录态可忽略")
            return {**meta, "verified": verify.get("valid")}

    def _auto_fill(self, page: "Page", account: str, password: str) -> bool:
        sel = self.SELECTORS.get(self.source, {})
        user_sel, pass_sel, submit_sel = sel.get("account"), sel.get("password"), sel.get("submit")
        if not (user_sel and pass_sel):
            return False
        try:
            page.fill(user_sel, account, timeout=10_000)
            page.fill(pass_sel, password, timeout=10_000)
            if submit_sel:
                page.click(submit_sel, timeout=10_000)
            print("[auth] 已自动填写账号密码并提交")
            return True
        except PWTimeout:
            return False

    def _wait_for_login(self, context: "BrowserContext", page: "Page", timeout_s: int):
        """轮询检测登录信号;每 15 秒打印一次进度;超时返回诊断。"""
        deadline = time.monotonic() + timeout_s
        last_report = 0.0
        while time.monotonic() < deadline:
            signals = self._collect_signals(context, page)
            if self._is_logged_in(signals):
                return signals, True
            now = time.monotonic()
            if now - last_report >= 15:
                print(f"[auth] 等待登录中… URL={signals['url'][:70] or '(空)'} "
                      f"cookies={signals['total_cookies']}(会话类 {signals['auth_cookies']}) "
                      f"localStorage={signals['local_storage']}")
                last_report = now
            try:
                page.wait_for_timeout(1500)
            except Exception:
                break
        # 超时:输出诊断信息
        diag = self._collect_signals(context, page)
        print("[auth] 登录超时。诊断信息:")
        print(f"  当前 URL    : {diag['url'] or '(无)'}")
        print(f"  站点域名内  : {diag['in_site']} | 处于登录页: {diag['on_login_path']}")
        print(f"  Cookie 总数 : {diag['total_cookies']}(会话类 {diag['auth_cookies']})")
        print(f"  localStorage: {diag['local_storage']} 项")
        print("  建议:确认浏览器中登录已完成;或检查站点是否改版/需要机构 SSO 跳转")
        return diag, False

    def _save(self, storage_state: dict) -> dict:
        meta = {
            "source": self.source,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)).isoformat(),
        }
        payload = {**meta, "storage_state": storage_state}
        self.session_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            self.session_file.chmod(0o600)
        except OSError:
            pass
        return meta

    # ================= 会话校验与复用 =================
    def verify_session(self, timeout_s: int = 45) -> dict:
        """用已保存会话回访目标站,确认登录态仍有效。"""
        if not self.has_session():
            return {"valid": False, "error": "无会话文件"}
        with self.authenticated_browser() as context:
            page = context.new_page()
            try:
                page.goto(self.site["home"], timeout=timeout_s * 1000)
                page.wait_for_timeout(3000)
                signals = self._collect_signals(context, page)
            except Exception as e:
                return {"valid": False, "error": f"回访失败: {e}"}
        valid = self._is_logged_in(signals)
        return {
            "valid": valid,
            "url": signals.get("url", ""),
            "auth_cookies": signals.get("auth_cookies", 0),
            "total_cookies": signals.get("total_cookies", 0),
            "on_login_path": signals.get("on_login_path", False),
        }

    @contextmanager
    def authenticated_browser(self):
        """上下文管理器:yield 带登录态的浏览器上下文(自动清理资源)。"""
        if not _HAS_PLAYWRIGHT:
            raise PlaywrightUnavailable("playwright 未安装")
        state = self.load_state()
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(storage_state=state)
        try:
            yield context
        finally:
            browser.close()
            pw.stop()


def get_login_manager(source: str) -> PlaywrightLoginManager:
    return PlaywrightLoginManager(source)
