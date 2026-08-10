"""数据源汇总:统一获取/注册爬虫。"""
from __future__ import annotations

from app.crawler.arxiv import ArxivCrawler
from app.crawler.base import BaseCrawler
from app.crawler.library import LibraryCrawler, get_library_crawler


def all_crawlers() -> dict[str, BaseCrawler]:
    """返回全部数据源实例:arxiv 恒可用,图书馆按凭据配置状态。"""
    crawlers: dict[str, BaseCrawler] = {"arxiv": ArxivCrawler()}
    for lib in ("ieee", "acm", "cnki"):
        crawlers[lib] = get_library_crawler(lib)
    return crawlers


def available_sources() -> list[str]:
    """当前可用的数据源列表(供 API 状态查询)。"""
    return [name for name, c in all_crawlers().items() if c.is_available()]
