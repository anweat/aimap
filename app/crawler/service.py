"""采集服务:CrawlJob 状态机 + 断点续爬 + 多来源调度 + 异步执行。

设计:
  - 每次采集 = 一个 CrawlJob(持久化于 SQLite):记录状态、分页游标、统计;
  - run_job 按分页抓取,每页经 upsert_paper_dedup 去重入库,过程日志写入 CrawlLog;
  - start_async 后台线程执行:API 立即返回,前端轮询 job 状态/日志/进度;
  - 失败(网络/429)不丢进度:job 标记 failed + next_retry_at + 游标,resume 续爬;
  - 完成后把统计回写数据源配置(last_crawl_stats),供来源面板展示。
"""
from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from app.crawler.arxiv import ArxivCrawler
from app.crawler.base import CrawlerError
from app.crawler.dedup import upsert_paper_dedup
from app.db import add_crawl_log, create_job, get_job, get_source, update_job, upsert_source
from app.models.entities import CrawlJob, SourceConfig

# 每页抓取量(arXiv API 单次上限附近,兼顾限流开销)
PAGE_SIZE = 50

# 失败后可重试延迟(秒)
RETRY_DELAY_SECONDS = 300


class CrawlService:
    """采集服务:创建/执行/续爬任务(同步或后台线程)。"""

    def __init__(self, session: Session):
        self._session = session

    # ------------------------------------------------------------------
    # 任务创建
    # ------------------------------------------------------------------
    def create(self, source: str, query: str, max_results: int = 20, analyze: bool = True,
               async_run: bool = False) -> CrawlJob:
        """创建任务;async_run=True 时后台线程执行并立即返回。"""
        job = create_job(self._session, source, query, max_pages=max(1, math.ceil(max_results / PAGE_SIZE)))
        add_crawl_log(self._session, job.id, f"任务创建: {source} 检索 '{query}' (最多 {max_results} 篇)")
        if async_run:
            threading.Thread(target=self._run_job_bg, args=(job.id, analyze), daemon=True).start()
            job.status = "running"
            update_job(self._session, job, status="running")
            return job
        return self.run_job(job.id, analyze=analyze)

    def _run_job_bg(self, job_id: int, analyze: bool) -> None:
        """后台线程执行:独立 session(线程安全,SQLite WAL 已启用)。"""
        from app.db import get_session

        with get_session() as s:
            CrawlService(s).run_job(job_id, analyze=analyze)

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def run_job(self, job_id: int, *, analyze: bool = True) -> CrawlJob:
        """执行任务:从当前 cursor 开始抓取,直到完成或失败。"""
        job = get_job(self._session, job_id)
        if job is None:
            raise ValueError(f"任务不存在: {job_id}")
        if job.status == "running":
            return job  # 已在执行,避免并发重复

        update_job(self._session, job, status="running")
        add_crawl_log(self._session, job_id, "开始执行")
        try:
            if job.source == "arxiv":
                self._crawl_arxiv(job, analyze=analyze)
            else:
                self._crawl_library(job, analyze=analyze)
            update_job(self._session, job, status="done", last_error="", next_retry_at=None)
            add_crawl_log(self._session, job_id,
                          f"完成: 抓取 {job.total_fetched} · 入库 {job.total_saved}"
                          f" · 重复过滤 {job.total_duplicates} · 失败 {job.total_failed}")
            self._update_source_stats(job)
        except Exception as e:
            update_job(
                self._session, job,
                status="failed",
                last_error=str(e)[:500],
                next_retry_at=datetime.now(timezone.utc) + timedelta(seconds=RETRY_DELAY_SECONDS),
            )
            add_crawl_log(self._session, job_id, f"任务失败: {e}", level="error")
        return job

    def resume_job(self, job_id: int, *, analyze: bool = True) -> CrawlJob:
        """断点续爬:从上次 cursor 继续(仅允许 failed/stopped 状态)。"""
        job = get_job(self._session, job_id)
        if job is None:
            raise ValueError(f"任务不存在: {job_id}")
        if job.status not in ("failed", "stopped"):
            raise ValueError(f"任务状态为 {job.status},不可续爬")
        add_crawl_log(self._session, job_id, f"断点续爬(游标 {job.cursor}/{job.max_pages})")
        return self.run_job(job_id, analyze=analyze)

    # ------------------------------------------------------------------
    # 来源实现
    # ------------------------------------------------------------------
    def _crawl_arxiv(self, job: CrawlJob, *, analyze: bool) -> None:
        crawler = ArxivCrawler()
        base_cursor = job.cursor  # 快照:循环期间 cursor 会被 update_job 推进
        remaining_pages = job.max_pages - base_cursor

        for page in range(remaining_pages):
            start = (base_cursor + page) * PAGE_SIZE
            add_crawl_log(self._session, job.id,
                          f"抓取第 {base_cursor + page + 1}/{job.max_pages} 页 (start={start})")
            try:
                papers = crawler.fetch_page(job.query, start=start, max_results=PAGE_SIZE)
            except Exception as e:
                job.total_failed += 1
                add_crawl_log(self._session, job.id, f"第 {start} 条起抓取失败: {e}", level="error")
                update_job(self._session, job, total_failed=job.total_failed)
                raise  # 由 run_job 统一标记 failed
            if not papers:
                add_crawl_log(self._session, job.id, "无更多结果,提前结束")
                break

            self._ingest_papers(job, papers, analyze=analyze)

            # 每页完成即推进游标(断点续爬的基础)
            update_job(self._session, job, cursor=base_cursor + page + 1,
                       total_fetched=job.total_fetched, total_saved=job.total_saved,
                       total_duplicates=job.total_duplicates, total_failed=job.total_failed)
            add_crawl_log(self._session, job.id,
                          f"第 {base_cursor + page + 1} 页完成: 累计抓取 {job.total_fetched}"
                          f" · 入库 {job.total_saved} · 重复 {job.total_duplicates}")

    # ------------------------------------------------------------------
    # 图书馆采集(单次检索,复用登录态)
    # ------------------------------------------------------------------
    def _crawl_library(self, job: CrawlJob, *, analyze: bool) -> None:
        from app.crawler.library import get_library_crawler

        crawler = get_library_crawler(job.source)
        if not crawler.is_available():
            raise CrawlerError(f"[{job.source}] 账号未配置:请在数据源面板(📡 数据源 → 编辑)填写账号密码")
        max_results = job.max_pages * PAGE_SIZE
        add_crawl_log(self._session, job.id,
                      f"[{job.source}] 检索 '{job.query}' (最多 {max_results} 篇,复用登录态)")
        papers = crawler.search(job.query, max_results=max_results)
        if not papers:
            add_crawl_log(self._session, job.id, f"[{job.source}] 无结果或解析为空(站点结构可能变化)", level="warn")
        else:
            add_crawl_log(self._session, job.id, f"[{job.source}] 检索到 {len(papers)} 篇,开始入库")
        self._ingest_papers(job, papers, analyze=analyze)
        update_job(self._session, job, cursor=job.max_pages,
                   total_fetched=job.total_fetched, total_saved=job.total_saved,
                   total_duplicates=job.total_duplicates, total_failed=job.total_failed)

    def _ingest_papers(self, job: CrawlJob, papers: list, *, analyze: bool) -> None:
        """去重入库 + 计数 + 可选多层分析(arxiv 与图书馆共用)。"""
        for p in papers:
            saved, result = upsert_paper_dedup(self._session, p)
            if result in ("saved", "updated"):
                job.total_saved += 1  # 同源更新也算有效入库
                if analyze and not (result == "updated" and saved.anchored_domain_key):
                    from app.agents.orchestrator import OrchestratorAgent

                    OrchestratorAgent(self._session).analyze_paper(saved.id)
            else:
                job.total_duplicates += 1
            job.total_fetched += 1

    # ------------------------------------------------------------------
    # 来源统计回写
    # ------------------------------------------------------------------
    def _update_source_stats(self, job: CrawlJob) -> None:
        source = get_source(self._session, job.name if hasattr(job, "name") else job.source)
        if source is None:
            return
        stats = {"fetched": job.total_fetched, "saved": job.total_saved,
                 "duplicates": job.total_duplicates, "failed": job.total_failed}
        source.last_crawl_stats = json.dumps(stats, ensure_ascii=False)
        source.last_crawl_at = datetime.now(timezone.utc)
        upsert_source(self._session, source)


def job_to_dict(job: CrawlJob) -> dict:
    return {
        "id": job.id,
        "source": job.source,
        "query": job.query,
        "status": job.status,
        "total_fetched": job.total_fetched,
        "total_saved": job.total_saved,
        "total_duplicates": job.total_duplicates,
        "total_failed": job.total_failed,
        "cursor": job.cursor,
        "max_pages": job.max_pages,
        "last_error": job.last_error,
        "next_retry_at": job.next_retry_at.isoformat() if job.next_retry_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
