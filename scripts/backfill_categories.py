"""回填存量论文的学科维度 tag(source='category')。

arXiv 来源论文的 categories 元数据在爬取时已入库,但升级到
"三维度正交分类"之前的论文还没有学科 tag。本脚本一次性补齐:

  python scripts/backfill_categories.py

后续新爬取的论文在分析时自动写入学科 tag,无需再跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select

from app.db import add_tag, get_session
from app.domain.arxiv_taxonomy import split_categories
from app.models.entities import Paper, PaperTag


def main() -> None:
    with get_session() as s:
        papers = s.exec(select(Paper)).all()
        backfilled = 0
        for p in papers:
            cats = split_categories(p.categories or "")
            if not cats:
                continue
            existing = {t.tag for t in s.exec(
                select(PaperTag).where(PaperTag.paper_id == p.id, PaperTag.source == "category")
            )}
            for cat in cats:
                if cat in existing:
                    continue
                add_tag(s, PaperTag(paper_id=p.id, tag=cat, source="category",
                                    domain_key=None, confidence=1.0))
                backfilled += 1
        print(f"[ok] 回填完成: {backfilled} 个学科 tag"
              f"({len(papers)} 篇论文,{sum(1 for p in papers if p.categories)} 篇带元数据)")


if __name__ == "__main__":
    main()
