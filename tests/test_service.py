"""采集服务测试:CrawlJob 状态机、断点续爬、去重统计(mock 网络)。"""
import pytest
from sqlmodel import select

from app.crawler.dedup import upsert_paper_dedup
from app.crawler.service import CrawlService
from app.db import get_job, get_session, init_db
from app.models.entities import CrawlJob, Paper


class FakeArxiv:
    """模拟 arXiv 分页:每页 3 篇,可指定失败页。"""

    def __init__(self, fail_on_page: int | None = None, title_prefix: str = "Fake Paper",
                 sid_prefix: str = "fake"):
        self.fail_on_page = fail_on_page
        self.title_prefix = title_prefix
        self.sid_prefix = sid_prefix

    def fetch_page(self, query, start, max_results=50):
        from app.crawler import service as service_mod

        page = start // service_mod.PAGE_SIZE
        if self.fail_on_page is not None and page == self.fail_on_page:
            raise RuntimeError("simulated network failure")
        return [
            Paper(source="arxiv", source_id=f"{self.sid_prefix}-{page}-{i}",
                  title=f"{self.title_prefix} {page}-{i}",
                  abstract="abstract", categories="cs.LG",
                  url=f"https://arxiv.org/abs/{self.sid_prefix}-{page}-{i}")
            for i in range(3)
        ]


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _page_size(monkeypatch):
    """分页大小调小,便于快速测试。"""
    import app.crawler.service as service_mod

    monkeypatch.setattr(service_mod, "PAGE_SIZE", 3)


def test_job_done_with_stats(session, monkeypatch):
    monkeypatch.setattr("app.crawler.service.ArxivCrawler", FakeArxiv)
    with get_session() as s:
        job = CrawlService(s).create("arxiv", "test query", max_results=6, analyze=False)
        assert job.status == "done"
        assert job.total_fetched == 6
        assert job.total_saved == 6
        assert job.total_duplicates == 0
        assert job.max_pages == 2
        assert job.cursor == 2


def test_job_failed_then_resume(session, monkeypatch):
    """第 2 页失败 → job failed + 游标停在 1;续爬从游标继续并完成。"""
    monkeypatch.setattr("app.crawler.service.ArxivCrawler", lambda: FakeArxiv(fail_on_page=1))
    with get_session() as s:
        job = CrawlService(s).create("arxiv", "resume query", max_results=6, analyze=False)
        assert job.status == "failed"
        assert job.cursor == 1            # 第 1 页完成,第 2 页失败
        assert job.total_saved == 3
        assert job.next_retry_at is not None
        assert "simulated" in job.last_error

        # 修复网络后续爬:从 cursor=1 继续
        monkeypatch.setattr("app.crawler.service.ArxivCrawler", FakeArxiv)
        job2 = CrawlService(s).resume_job(job.id, analyze=False)
        assert job2.status == "done"
        assert job2.cursor == 2
        assert job2.total_fetched == 6
        assert job2.total_saved == 6


def test_job_duplicates_counted(session, monkeypatch):
    """预置一篇论文,使抓取页中的一篇成为跨源重复(用独立标题体系避免测试间干扰)。"""
    with get_session() as s:
        upsert_paper_dedup(
            s,
            Paper(source="manual", source_id="manual-dup-1-0", title="Dup Series 1-0"),
        )
    monkeypatch.setattr(
        "app.crawler.service.ArxivCrawler",
        lambda: FakeArxiv(title_prefix="Dup Series", sid_prefix="dup"),
    )
    with get_session() as s:
        job = CrawlService(s).create("arxiv", "dup query", max_results=6, analyze=False)
        assert job.status == "done"
        assert job.total_duplicates >= 1
        assert job.total_saved == job.total_fetched - job.total_duplicates


def test_resume_invalid_state(session, monkeypatch):
    monkeypatch.setattr("app.crawler.service.ArxivCrawler", FakeArxiv)
    with get_session() as s:
        job = CrawlService(s).create("arxiv", "state query", max_results=3, analyze=False)
        assert job.status == "done"
        with pytest.raises(ValueError):
            CrawlService(s).resume_job(job.id)  # done 状态不可续爬


def test_job_persistence(session, monkeypatch):
    """任务落库:列表可查。"""
    monkeypatch.setattr("app.crawler.service.ArxivCrawler", FakeArxiv)
    with get_session() as s:
        before = len(s.exec(select(CrawlJob)).all())
        CrawlService(s).create("arxiv", "persist query", max_results=3, analyze=False)
        after = len(s.exec(select(CrawlJob)).all())
        assert after == before + 1
