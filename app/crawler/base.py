"""爬虫抽象基类:所有数据源统一实现该接口。

接口约定:
  search(query, max_results) -> list[Paper]
  fetch_by_id(source_id)      -> Paper | None

新增数据源(如 IEEE / ACM / CNKI)只需继承 BaseCrawler 并实现这两个方法,
上层(API / 编排器)无需改动。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.entities import Paper


class CrawlerError(Exception):
    """爬虫层错误基类。"""


class LibraryCredentialsMissing(CrawlerError):
    """图书馆账号未配置。"""


class BaseCrawler(ABC):
    source: str = "base"

    @abstractmethod
    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        """按查询词搜索论文。"""

    @abstractmethod
    def fetch_by_id(self, source_id: str) -> Paper | None:
        """按来源侧 ID 抓取单篇论文。"""

    def is_available(self) -> bool:
        """该数据源当前是否可用(凭据、网络等)。"""
        return True
