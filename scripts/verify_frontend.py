"""前端真实浏览器验证:playwright 打开页面,收集日志/错误/渲染状态。

用法:
  python scripts/verify_frontend.py [--url http://localhost:8000] [--wait 6]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="前端浏览器验证")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--wait", type=float, default=6.0)
    args = parser.parse_args()

    # Windows GBK 控制台兼容
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    console_logs: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        page.on("console", lambda m: console_logs.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("requestfailed", lambda r: console_logs.append(f"[reqfail] {r.url} {r.failure}"))

        page.goto(args.url, timeout=30_000)
        page.wait_for_timeout(args.wait * 1000)

        status = page.text_content("#statusBar")
        banner_visible = page.is_visible("#errorBanner")
        canvas_count = page.locator("canvas").count()
        tree_nodes = page.locator(".tree-node").count()
        paper_rows = page.locator(".paper-row").count()
        log_lines = page.locator(".log-line").count()

        print("=" * 60)
        print(f"URL        : {args.url}")
        print(f"状态栏     : {status!r}")
        print(f"错误横幅   : {'可见 ❌' if banner_visible else '隐藏 ✓'}")
        print(f"3D canvas  : {canvas_count} 个")
        print(f"领域树节点 : {tree_nodes} 个")
        print(f"论文行     : {paper_rows} 行")
        print(f"日志行     : {log_lines} 行")
        print(f"页面错误   : {len(page_errors)}")
        for e in page_errors[:10]:
            print(f"  ❌ {e}")
        print(f"请求失败   : {sum(1 for l in console_logs if '[reqfail]' in l)}")
        print("--- 前端日志(最近 20 条)---")
        for l in console_logs[-20:]:
            print(" ", l[:150])
        browser.close()

    failed = bool(page_errors) or banner_visible
    print("=" * 60)
    print("结论:", "FAIL ❌" if failed else "PASS ✓ 前端初始化正常")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
