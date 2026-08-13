"""图书馆数据源:IEEE / ACM / CNKI 的真实检索(登录态复用)。

设计:
  - 登录会话由 app.crawler.auth.PlaywrightLoginManager 管理(cookie 落盘、回访校验);
  - 检索时用 manager.authenticated_browser() 复用登录态:
      * IEEE Xplore 走其内部 REST 接口(https://ieeexplore.ieee.org/rest/search,返回 JSON),
        经 context.request 携带登录 cookie 发起,结构稳定;
      * ACM / CNKI 无公开 API,用浏览器打开搜索结果页并解析 HTML(best-effort,
        站点结构变化时需更新对应解析器,解析失败返回空列表而非抛异常);
  - 解析器为纯函数(接受 JSON/HTML 文本),便于单测且不依赖浏览器;
  - 凭据从 app.config.settings.library_credentials 读取(SecretVault 优先,.env 回退)。
"""
from __future__ import annotations

import html as _html
import json
import re
import urllib.parse
from datetime import datetime

from app.config import settings
from app.crawler.base import BaseCrawler, LibraryCredentialsMissing
from app.models.entities import Paper

# 检索默认上限(每来源单次抓取,避免过度打扰站点)
DEFAULT_MAX_RESULTS = 50


def _to_paper(source: str, source_id: str, title: str, *, abstract: str = "",
              authors: str = "", categories: str = "", url: str = "",
              published_at: datetime | None = None) -> Paper:
    return Paper(
        source=source,
        source_id=source_id,
        title=title.strip(),
        abstract=abstract.strip(),
        authors=authors.strip(),
        categories=categories.strip(),
        url=url.strip(),
        published_at=published_at,
    )


# ---------------------------------------------------------------------------
# IEEE Xplore(JSON REST,可靠)
# ---------------------------------------------------------------------------
def parse_ieee_records(records: list[dict]) -> list[Paper]:
    """解析 IEEE Xplore /rest/search 返回的 records 列表 → Paper。"""
    papers: list[Paper] = []
    for r in records or []:
        title = (r.get("title") or "").strip()
        arn = str(r.get("articleNumber") or r.get("arnumber") or "").strip()
        if not title:
            continue
        authors = ", ".join(
            a.get("preferredName") or a.get("name") or ""
            for a in (r.get("authors") or [])
            if (a.get("preferredName") or a.get("name"))
        )
        year = r.get("publicationYear") or r.get("pubYear")
        published = None
        if year:
            try:
                published = datetime(int(year), 1, 1)
            except (TypeError, ValueError):
                published = None
        url = (r.get("documentLink") or r.get("htmlLink") or "").strip()
        if not url and arn:
            url = f"https://ieeexplore.ieee.org/document/{arn}"
        source_id = arn or url.rsplit("/", 1)[-1]
        papers.append(
            _to_paper(
                "ieee", source_id, title,
                abstract=(r.get("abstract") or "").strip(),
                authors=authors,
                categories=(r.get("publicationTitle") or r.get("displayPublicationTitle") or "").strip(),
                url=url,
                published_at=published,
            )
        )
    return papers


# ---------------------------------------------------------------------------
# ACM Digital Library(HTML,best-effort)
# ---------------------------------------------------------------------------
_ACM_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>/doi/[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_ACM_ABSTRACT_RE = re.compile(
    r'<div[^>]+class="[^"]*(?:issue-item__abstract|hlFld-Abstract|abstract)[^"]*"[^>]*>(?P<abs>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_ACM_AUTHORS_RE = re.compile(
    r'<[^>]+class="[^"]*(?:author|hlFld-Author)[^"]*"[^>]*>(?P<authors>.*?)</(?:span|div|li)>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_acm_html(html: str) -> list[Paper]:
    """解析 ACM DL 搜索结果页 HTML → Paper(结构变化时优雅降级为空)。"""
    papers: list[Paper] = []
    seen: set[str] = set()
    for m in _ACM_LINK_RE.finditer(html):
        href = m.group("href").strip()
        title = _clean(m.group("title"))
        if not title or href in seen:
            continue
        seen.add(href)
        source_id = href.split("/doi/", 1)[-1].strip("/")
        papers.append(
            _to_paper(
                "acm", source_id, title,
                abstract=_extract_nearby(html, m.end(), _ACM_ABSTRACT_RE),
                authors=_extract_authors(html, m.end()),
                url=f"https://dl.acm.org{href}",
            )
        )
    return papers


def parse_cnki_html(html: str) -> list[Paper]:
    """解析 CNKI 搜索结果页 HTML → Paper(best-effort,反爬强时需人工登录配合)。"""
    papers: list[Paper] = []
    seen: set[str] = set()
    # CNKI 结果链接常见形态:/kns8s/defaultresult/… 或 detail?dbcode=…&filename=…
    link_re = re.compile(
        r'<a[^>]+href="(?P<href>[^"]*(?:detail|filename|GetUrl)[^"]*)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for m in link_re.finditer(html):
        href = m.group("href").strip()
        title = _clean(m.group("title"))
        if not title or len(title) < 4 or title in seen:
            continue
        seen.add(title)
        papers.append(
            _to_paper(
                "cnki", href, title,
                abstract="",
                url=urllib.parse.urljoin("https://kns.cnki.net/", href),
            )
        )
    return papers


def _clean(text: str) -> str:
    """去标签、反转义、压缩空白。"""
    return re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", text))).strip()


def _extract_nearby(html: str, pos: int, pattern: re.Pattern) -> str:
    """在 pos 之后就近匹配 pattern 捕获组,返回清理后文本。"""
    m = pattern.search(html, pos)
    if not m:
        return ""
    return _clean(m.group(1))


def _extract_authors(html: str, pos: int) -> str:
    m = _ACM_AUTHORS_RE.search(html, pos)
    if not m:
        return ""
    return _clean(m.group(1))


# ---------------------------------------------------------------------------
# 基类与子类
# ---------------------------------------------------------------------------
class LibraryCrawler(BaseCrawler):
    """图书馆数据源基类:凭据 + 登录会话 + 检索。

    子类必须定义:
      source             : "ieee" | "acm" | "cnki"
      _search_with_browser(manager, query, max_results) -> list[Paper]
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
                f"[{self.source}] 图书馆账号未配置。请在数据源面板(📡 数据源 → 编辑)填写账号密码,"
                f"或在 .env 设置 LIB_{self.source.upper()}_ACCOUNT / LIB_{self.source.upper()}_PASSWORD"
            )
        return {"account": self._account, "password": self._password}

    # -- 登录会话 ---------------------------------------------------------
    def _require_session(self):
        from app.crawler.auth import PlaywrightLoginManager, SessionExpired

        manager = PlaywrightLoginManager(self.source)
        if not manager.has_session():
            raise SessionExpired(
                f"[{self.source}] 尚未登录:请先运行 "
                f"python scripts/library_login.py --source {self.source} 完成浏览器登录"
            )
        return manager

    # -- 接口 ------------------------------------------------------------
    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        self._auth()  # 先校验凭据,给出明确提示
        manager = self._require_session()
        return self._search_with_browser(manager, query, max_results)

    def fetch_by_id(self, source_id: str) -> Paper | None:
        self._auth()
        return self._fetch_impl(source_id)

    # -- 子类实现(未实现时明确报错)-------------------------------------
    def _search_with_browser(self, manager, query: str, max_results: int) -> list[Paper]:
        raise NotImplementedError(f"[{self.source}] 检索逻辑尚未实现")

    def _fetch_impl(self, source_id: str) -> Paper | None:
        raise NotImplementedError(f"[{self.source}] 单篇抓取尚未实现")


class IeeeCrawler(LibraryCrawler):
    source = "ieee"

    def __init__(self):
        cred = settings.library_credentials["ieee"]
        super().__init__(cred["account"], cred["password"])

    def _search_with_browser(self, manager, query: str, max_results: int) -> list[Paper]:
        rows = min(max_results or DEFAULT_MAX_RESULTS, 100)
        payload = {
            "newsearch": True,
            "queryText": query,
            "highlight": True,
            "returnFacets": ["ALL"],
            "returnType": "SEARCH",
            "matchPubs": True,
            "pageNumber": 1,
            "rowsPerPage": rows,
        }
        with manager.authenticated_browser() as context:
            resp = context.request.post(
                "https://ieeexplore.ieee.org/rest/search",
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=60_000,
            )
            if not resp.ok:
                raise RuntimeError(f"[ieee] 检索失败 HTTP {resp.status}")
            data = resp.json()
        return parse_ieee_records(data.get("records") or [])

    def _fetch_impl(self, source_id: str) -> Paper | None:
        manager = self._require_session()
        with manager.authenticated_browser() as context:
            resp = context.request.get(
                f"https://ieeexplore.ieee.org/rest/document/{source_id}",
                timeout=60_000,
            )
            if not resp.ok:
                return None
            data = resp.json()
        return parse_ieee_records([data])[0] if data else None


class AcmCrawler(LibraryCrawler):
    source = "acm"

    def __init__(self):
        cred = settings.library_credentials["acm"]
        super().__init__(cred["account"], cred["password"])

    def _search_with_browser(self, manager, query: str, max_results: int) -> list[Paper]:
        page_size = min(max_results or DEFAULT_MAX_RESULTS, 50)
        url = (
            "https://dl.acm.org/action/doSearch"
            f"?AllField={urllib.parse.quote(query)}&startPage=0&pageSize={page_size}"
        )
        with manager.authenticated_browser() as context:
            page = context.new_page()
            page.goto(url, timeout=60_000)
            page.wait_for_load_state("domcontentloaded")
            html = page.content()
        return parse_acm_html(html)


class CnkiCrawler(LibraryCrawler):
    source = "cnki"

    def __init__(self):
        cred = settings.library_credentials["cnki"]
        super().__init__(cred["account"], cred["password"])

    def _search_with_browser(self, manager, query: str, max_results: int) -> list[Paper]:
        # CNKI 新版检索为 SPA,等待结果容器出现;结构反爬较强,best-effort。
        url = f"https://kns.cnki.net/kns8s/defaultresult/index?kw={urllib.parse.quote(query)}"
        with manager.authenticated_browser() as context:
            page = context.new_page()
            page.goto(url, timeout=60_000)
            try:
                page.wait_for_selector("a[href*='detail'],a[href*='filename'],.result-table-list", timeout=15_000)
            except Exception:
                pass  # 结果容器未出现也继续抓取当前 DOM
            page.wait_for_load_state("domcontentloaded")
            html = page.content()
        return parse_cnki_html(html)


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
