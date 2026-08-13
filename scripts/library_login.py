"""图书馆模拟浏览器登录:登录 IEEE / ACM / CNKI 并保存会话。

用法:
  python scripts/library_login.py --source ieee --manual    # 手动确认模式(推荐:SSO 跳转学校认证)
  python scripts/library_login.py --source ieee             # 自动检测模式(登录后自动保存)
  python scripts/library_login.py --source acm --auto       # 自动填表(需站点选择器配置)
  python scripts/library_login.py --source cnki --status    # 查看会话状态
  python scripts/library_login.py --source ieee --verify    # 校验会话是否仍有效

两种登录模式:
  --manual 手动确认:弹出浏览器,你完成登录(含跳转到学校统一认证),回到终端按回车
            即保存会话。不依赖自动检测,最稳,适合机构 SSO / CARSI 场景。
  默认(自动检测):弹出浏览器,自动检测登录成功信号后保存(已排除游客态 cookie)。

会话(cookie)保存于 data/sessions/<source>.json,后续爬虫复用,无需重复登录。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.crawler.auth import LoginTimeout, PlaywrightLoginManager, PlaywrightUnavailable


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="图书馆登录会话管理")
    parser.add_argument("--source", required=True, choices=["ieee", "acm", "cnki"])
    parser.add_argument("--auto", action="store_true", help="自动填表模式(需配置站点选择器)")
    parser.add_argument("--manual", action="store_true", help="手动确认模式:登录完成后回车保存(推荐 SSO 场景)")
    parser.add_argument("--headless", action="store_true", help="无头模式(默认有头,便于人工登录)")
    parser.add_argument("--status", action="store_true", help="查看会话状态")
    parser.add_argument("--verify", action="store_true", help="校验会话是否仍有效")
    parser.add_argument("--timeout", type=int, default=600, help="登录等待超时(秒,仅自动检测模式)")
    args = parser.parse_args()

    try:
        manager = PlaywrightLoginManager(args.source)
    except PlaywrightUnavailable as e:
        raise SystemExit(f"[auth] {e}")

    if args.status:
        meta = manager.session_meta()
        if not meta:
            print(f"[auth:{args.source}] 无登录会话,请运行: python scripts/library_login.py --source {args.source}")
            return
        print(f"[auth:{args.source}] 会话状态:")
        print(f"  保存时间: {meta['saved_at']}")
        print(f"  过期时间: {meta['expires_at']}")
        print(f"  Cookie  : {meta['cookie_count']} 个")
        print(f"  状态    : {'已过期,请重新登录' if meta['expired'] else '有效(未过期,建议 --verify 实测)'}")
        return

    if args.verify:
        print(f"[auth:{args.source}] 正在回访 {manager.site['home']} 校验会话…")
        result = manager.verify_session()
        if result.get("valid"):
            print(f"[auth:{args.source}] 会话有效 ✓ (URL={result.get('url', '')[:60]} "
                  f"会话类 cookie={result.get('auth_cookies')})")
        else:
            print(f"[auth:{args.source}] 会话无效 ✗ ({result.get('error', '未检测到登录态')})")
            print(f"  请重新登录: python scripts/library_login.py --source {args.source}")
            raise SystemExit(1)
        return

    cred = settings.library_credentials[args.source]
    print(f"[auth:{args.source}] 账号配置: {'已配置' if cred['account'] else '未配置(可纯人工登录)'}")

    if args.manual:
        meta = manager.manual_login(headless=args.headless, verify=True)
        if not meta.get("saved", True):
            raise SystemExit(0)
        print(f"[auth:{args.source}] 完成,校验结果: "
              f"{'通过' if meta.get('verified') else '未通过(会话已保存,可 --verify 复查或直接采集实测)'}")
        return

    try:
        meta = manager.login(
            account=cred["account"],
            password=cred["password"],
            headless=args.headless,
            auto=args.auto,
            timeout_s=args.timeout,
        )
        print(f"[auth:{args.source}] 完成,校验结果: {'通过' if meta.get('verified') else '未通过(见上方警告)'}")
    except LoginTimeout as e:
        print(f"[auth] 登录未完成: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
