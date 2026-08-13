"""导入浏览器导出的 cookie 为图书馆登录会话(免 playwright 登录)。

用法:
  python scripts/import_cookies.py --source ieee --file cookies.txt
  python scripts/import_cookies.py --source ieee --file cookies.json --verify

如何导出 cookie(在已登录目标库的浏览器里):
  - Chrome/Edge 扩展 "Get cookies.txt"  → 导出 Netscape 格式(.txt);
  - 扩展 "EditThisCookie"                → 导出 JSON 数组;
  - DevTools → Application → Cookies → 选中目标站域名 → 手动复制为 JSON。

脚本会:
  1. 解析 cookie(兼容 Netscape / JSON 两种格式);
  2. 过滤出目标站域名相关的 cookie(忽略第三方/无关站点);
  3. 转换为 playwright storage_state,写入 data/sessions/<source>.json,
     与 playwright 登录产出同格式,后续爬虫/校验/面板状态无缝复用;
  4. 可选 --verify 回访目标站实测登录态。

注意:大学图书馆账号多为机构 SSO(CARSI/Shibboleth)登录,请导出的是
     **目标库自身域名**(如 ieeexplore.ieee.org / dl.acm.org / kns.cnki.net)
     下的会话 cookie,而非机构身份提供方(idp.xxx)的 cookie。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crawler.auth import PlaywrightLoginManager, PlaywrightUnavailable


# ---------------------------------------------------------------------------
# 解析:浏览器导出 → playwright storage_state cookie 列表
# ---------------------------------------------------------------------------
def parse_netscape(text: str) -> list[dict]:
    """Netscape cookies.txt 格式 → playwright cookie 列表。"""
    cookies: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        if not name or not domain:
            continue
        try:
            exp = int(float(expires))
        except (TypeError, ValueError):
            exp = -1
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain.lstrip("."),
            "path": path or "/",
            "expires": exp if exp > 0 else -1,
            "httpOnly": False,  # Netscape 格式不含 httpOnly,统一视为 False
            "secure": secure.strip().upper() == "TRUE",
            "sameSite": "Lax",
        })
    return cookies


def parse_json(text: str) -> list[dict]:
    """JSON 数组(EditThisCookie / 手抄)或 storage_state 对象 → cookie 列表。"""
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("cookies", [data]) if "cookies" in data else [data]
    if not isinstance(data, list):
        return []

    cookies: list[dict] = []
    for c in data:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        # expires:兼容 expires / expirationDate / session 三套字段
        raw_exp = c.get("expires", c.get("expirationDate", -1))
        try:
            exp = int(float(raw_exp))
        except (TypeError, ValueError):
            exp = -1
        if c.get("session"):
            exp = -1
        same_site = _norm_same_site(c.get("sameSite", "Lax"))
        cookies.append({
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": str(c.get("domain", "")).lstrip("."),
            "path": c.get("path", "/") or "/",
            "expires": exp if exp > 0 else -1,
            "httpOnly": bool(c.get("httpOnly", c.get("http_only", False))),
            "secure": bool(c.get("secure", False)),
            "sameSite": same_site,
        })
    return cookies


def _norm_same_site(value) -> str:
    s = str(value).lower()
    if s in ("no_restriction", "none"):
        return "None"
    if s == "strict":
        return "Strict"
    return "Lax"


def filter_related(cookies: list[dict], site_host: str) -> list[dict]:
    """只保留目标站域名相关的 cookie(相同 / 子域 / 同注册域)。"""
    host = (site_host or "").lower()
    out: list[dict] = []
    for c in cookies:
        d = str(c.get("domain", "")).lstrip(".").lower()
        if not d or not host:
            continue
        if d == host or d.endswith("." + host) or d.split(".")[-2:] == host.split(".")[-2:]:
            out.append(c)
    return out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="导入浏览器 cookie 为图书馆登录会话")
    parser.add_argument("--source", required=True, choices=["ieee", "acm", "cnki"])
    parser.add_argument("--file", required=True, help="浏览器导出的 cookie 文件(.txt Netscape 或 .json)")
    parser.add_argument("--verify", action="store_true", help="导入后回访目标站校验登录态")
    args = parser.parse_args()

    try:
        manager = PlaywrightLoginManager(args.source)
    except PlaywrightUnavailable as e:
        raise SystemExit(f"[import] {e}")

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"[import] 文件不存在: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")

    # 格式判定:先试 JSON,失败回退 Netscape
    try:
        cookies = parse_json(text)
        fmt = "JSON"
    except json.JSONDecodeError:
        cookies = parse_netscape(text)
        fmt = "Netscape"
    if not cookies:
        raise SystemExit(f"[import] 未从文件解析到任何 cookie(格式: {fmt})")

    site_host = urlparse(manager.site["home"]).hostname or ""
    related = filter_related(cookies, site_host)
    skipped = len(cookies) - len(related)
    if not related:
        raise SystemExit(
            f"[import] 解析到 {len(cookies)} 个 cookie,但都不属于 {site_host} 域名。\n"
            f"  请确认导出的是目标库自身域名的 cookie(而非 SSO/机构 idp 域名)。"
        )

    meta = manager.import_storage_state({"cookies": related, "origins": []})
    print(f"[import:{args.source}] 已导入 {len(related)} 个 cookie"
          f"{f'(忽略无关 {skipped} 个)' if skipped else ''} → {manager.session_file}")
    print(f"  有效期至: {meta['expires_at']}")

    if args.verify:
        print(f"[import:{args.source}] 回访 {manager.site['home']} 校验…")
        result = manager.verify_session()
        if result.get("valid"):
            print(f"[import:{args.source}] 会话有效 ✓ (会话类 cookie={result.get('auth_cookies')})")
        else:
            print(f"[import:{args.source}] 会话无效 ✗ ({result.get('error', '未检测到登录态')})")
            print("  可能原因:cookie 已失效 / 导出的是 SSO 而非目标站 cookie / 站点需二次验证")
            raise SystemExit(1)
    else:
        print("  提示:加 --verify 可实测登录态是否真的有效")


if __name__ == "__main__":
    main()
