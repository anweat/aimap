"""CLI:按检索式采集 arXiv 论文并触发多层分析。

用法:
  python scripts/crawl.py "large language model" --max 20
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.orchestrator import OrchestratorAgent
from app.crawler.arxiv import ArxivCrawler
from app.db import get_session, init_db, upsert_paper


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="采集 arXiv 论文并分析")
    parser.add_argument("query", help="arXiv 检索式,如 'large language model'")
    parser.add_argument("--max", type=int, default=20, help="最大数量")
    parser.add_argument("--no-analyze", action="store_true", help="仅采集不分析")
    args = parser.parse_args()

    init_db()
    crawler = ArxivCrawler()
    print(f"[crawl] arXiv 检索: {args.query} (max={args.max})")
    fetched = crawler.search(args.query, max_results=args.max)
    print(f"[crawl] 命中 {len(fetched)} 篇")

    with get_session() as s:
        saved = [upsert_paper(s, p) for p in fetched]
        if not args.no_analyze:
            orchestrator = OrchestratorAgent(s)
            for p in saved:
                r = orchestrator.analyze_paper(p.id)
                print(f"  - [{r.status}] {p.title[:60]} → {r.artifacts.get('domain_key', '')}")
    print("[done]")


if __name__ == "__main__":
    main()
