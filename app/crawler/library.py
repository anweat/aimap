"""图书馆数据源(预留接口):IEEE / ACM / 知网(CNKI)。

当前状态:框架已就位,凭据从 .env 读取(LIB_*_ACCOUNT / LIB_*_PASSWORD)。
在账号提供之前:
  - 调用任何 search/fetch 都会抛出 LibraryCredentialsMissing,明确提示;
  - 提供凭据后,按各库协议实现登录与检索(通常需要会话保持 / 浏览器自动化,
    建议后续在 crawler/ 下为每个库单独建模块,复用本类的会话与凭据管理)。
"""
from __future__ import annotations

from app.config import settings
from app.crawler.base import BaseCrawler, LibraryCredentialsMissing
from app.models.entities import Paper


class LibraryCrawler(BaseCrawler):
    """图书馆数据源基类:管理凭据与可用性。

    子类必须定义:
      source : "ieee" | "acm" | "cnki"
      _search_impl(query, max_results, auth)  : 凭据就绪后的实际检索逻辑
      _fetch_impl(source_id, auth)            : 单篇抓取逻辑
    """

    def __init__(self, account: str = "", password: str = ""):
        self._account = account
        self._password = password

    # -- 凭据 ------------------------------------------------------------
    def is_available(self) -> bool:
        return bool(self._account and self._password)

    def _auth(self) -> dict[str, str]:
        if not self.is_available():
            raise LibraryCredentialsMissing(
                f"[{self.source}] 图书馆账号未配置。请在 .env 中设置 "
                f"LIB_{self.source.upper()}_ACCOUNT / LIB_{self.source.upper()}_PASSWORD"
            )
        return {"account": self._account, "password": self._password}

    # -- 接口 ------------------------------------------------------------
    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        auth = self._auth()
        return self._search_impl(query, max_results, auth)

    def fetch_by_id(self, source_id: str) -> Paper | None:
        auth = self._auth()
        return self._fetch_impl(source_id, auth)

    # -- 子类实现(未实现时明确报错,保证框架可运行、可测试)---------------
    def _search_impl(self, query: str, max_results: int, auth: dict[str, str]) -> list[Paper]:
        # 真实采集路径:先确认登录会话,再按各库站点协议实现检索
        from app.crawler.auth import PlaywrightLoginManager, SessionExpired

        manager = PlaywrightLoginManager(self.source)
        if not manager.has_session():
            raise SessionExpired(
                f"[{self.source}] 尚未登录:请先运行 "
                f"python scripts/library_login.py --source {self.source} 完成浏览器登录"
            )
        raise NotImplementedError(
            f"[{self.source}] 检索逻辑尚未实现:登录会话已就绪,"
            f"请按该库站点协议在 crawler/library.py 的 _search_impl 中"
            f"使用 manager.authenticated_browser() 发起检索"
        )

    def _fetch_impl(self, source_id: str, auth: dict[str, str]) -> Paper | None:
        raise NotImplementedError(
            f"[{self.source}] 单篇抓取逻辑尚未实现:登录会话就绪后,"
            f"请按该库站点协议实现 _fetch_impl"
        )


class IeeeCrawler(LibraryCrawler):
    source = "ieee"

    def __init__(self):
        cred = settings.library_credentials["ieee"]
        super().__init__(cred["account"], cred["password"])


class AcmCrawler(LibraryCrawler):
    source = "acm"

    def __init__(self):
        cred = settings.library_credentials["acm"]
        super().__init__(cred["account"], cred["password"])


class CnkiCrawler(LibraryCrawler):
    source = "cnki"

    def __init__(self):
        cred = settings.library_credentials["cnki"]
        super().__init__(cred["account"], cred["password"])


def get_library_crawler(source: str) -> LibraryCrawler:
    """工厂:按来源名返回图书馆爬虫实例。"""
    crawlers: dict[str, type[LibraryCrawler]] = {
        "ieee": IeeeCrawler,
        "acm": AcmCrawler,
        "cnki": CnkiCrawler,
    }
    if source not in crawlers:
        raise ValueError(f"未知图书馆数据源: {source}")
    return crawlers[source]()
