"""arXiv 爬虫:使用官方 export API(无需 API key),遵循统一资源获取规范。

接口: <AIMAP_ARXIV_API>?search_query=<query>&start=0&max_results=N
行为: 限流(默认 3s/次) + 429/5xx 指数退避重试 + 连续失败熔断,
      全部由 app/crawler/policy.py 的 FetchPolicy 统一管理。
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from app.config import settings
from app.crawler.base import BaseCrawler, CrawlerError
from app.crawler.policy import FetchPolicy, RetriesExhausted, get_fetch_policy
from app.models.entities import Paper

ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
USER_AGENT = "aimap/0.2 (research domain map; contact: local deployment)"


class ArxivCrawler(BaseCrawler):
    source = "arxiv"

    def __init__(self, api_url: str | None = None, policy: FetchPolicy | None = None):
        self._api_url = api_url or settings.arxiv_api
        self._policy = policy or get_fetch_policy()

    # -- 带策略的请求 -------------------------------------------------------
    def _get(self, params: dict) -> httpx.Response:
        def do_request() -> tuple[int | None, float | None, httpx.Response]:
            # 超时/网络错误直接冒泡:policy 依据异常类型判定是否可恢复重试
            resp = httpx.get(
                self._api_url,
                params=params,
                timeout=30.0,
                headers={"User-Agent": USER_AGENT},
            )
            retry_after = None
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    retry_after = float(ra)
                except ValueError:
                    retry_after = None
            return resp.status_code, retry_after, resp

        try:
            resp = self._policy.execute(
                self.source,
                do_request,
                on_retry=lambda a, s, msg: print(f"[arxiv] 重试 {a}: 状态 {s} — {msg}"),
            )
            return resp
        except RetriesExhausted as e:
            if e.status == 429:
                raise CrawlerError("arXiv 持续限流(429),已按策略停止本次采集,请稍后重试") from e
            raise CrawlerError(f"arXiv 请求失败: {e}") from e

    # -- 实现 ------------------------------------------------------------
    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        max_results = min(max_results, settings.arxiv_max_results)
        resp = self._get({"search_query": query, "start": 0, "max_results": max_results})
        return self._parse(resp.text)

    def fetch_by_id(self, source_id: str) -> Paper | None:
        resp = self._get({"id_list": source_id})
        papers = self._parse(resp.text)
        return papers[0] if papers else None

    def fetch_page(self, query: str, start: int, max_results: int = 50) -> list[Paper]:
        """分页抓取一页(供断点续爬使用)。"""
        max_results = min(max_results, settings.arxiv_max_results)
        resp = self._get({"search_query": query, "start": start, "max_results": max_results})
        return self._parse(resp.text)

    # -- 解析 ------------------------------------------------------------
    @staticmethod
    def _parse(xml_text: str) -> list[Paper]:
        root = ET.fromstring(xml_text)
        papers: list[Paper] = []
        for entry in root.findall("a:entry", ATOM_NS):
            title = _text(entry, "a:title").replace("\n", " ").strip()
            abstract = _text(entry, "a:summary").replace("\n", " ").strip()
            authors = ", ".join(
                a.findtext("a:name", default="", namespaces=ATOM_NS).strip()
                for a in entry.findall("a:author", ATOM_NS)
            )
            published = _text(entry, "a:published")
            entry_id = _text(entry, "a:id")
            url = entry_id
            source_id = entry_id.rsplit("/abs/", 1)[-1] if "/abs/" in entry_id else entry_id
            categories = ", ".join(c.get("term", "") for c in entry.findall("a:category", ATOM_NS))
            papers.append(
                Paper(
                    source="arxiv",
                    source_id=source_id,
                    title=title,
                    abstract=abstract,
                    authors=authors,
                    categories=categories,
                    url=url,
                    published_at=_parse_date(published),
                )
            )
        return papers


def _text(elem: ET.Element, tag: str) -> str:
    node = elem.find(tag, ATOM_NS)
    return node.text if node is not None and node.text else ""


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
