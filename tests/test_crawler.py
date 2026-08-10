"""爬虫层测试:arXiv 解析(离线样例 XML)+ 图书馆凭据/会话门禁。"""
import pytest

from app.crawler.arxiv import ArxivCrawler
from app.crawler.auth import PlaywrightUnavailable, SessionExpired
from app.crawler.base import LibraryCredentialsMissing
from app.crawler.library import LibraryCrawler

SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <title>Attention Is All You Need</title>
    <summary>  The dominant sequence transduction models are based on complex
    recurrent or convolutional neural networks.  </summary>
    <published>2017-06-12T00:00:00Z</published>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <category term="cs.CL"/>
    <category term="cs.LG"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2303.08774v1</id>
    <title>GPT-4 Technical Report</title>
    <summary>We report the development of GPT-4.</summary>
    <published>2023-03-15T00:00:00Z</published>
    <author><name>OpenAI</name></author>
    <category term="cs.CL"/>
  </entry>
</feed>
"""


def test_parse_atom_feed():
    papers = ArxivCrawler._parse(SAMPLE_ATOM)
    assert len(papers) == 2

    p = papers[0]
    assert p.source == "arxiv"
    assert p.source_id == "1706.03762v7"
    assert "Attention Is All You Need" in p.title
    assert "recurrent" in p.abstract
    assert "Ashish Vaswani" in p.authors and "Noam Shazeer" in p.authors
    assert "cs.CL" in p.categories
    assert p.published_at is not None
    assert p.published_at.year == 2017


def test_parse_empty_feed():
    empty = '<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    assert ArxivCrawler._parse(empty) == []


def test_library_crawler_requires_credentials():
    crawler = LibraryCrawler(account="", password="")
    assert crawler.is_available() is False
    with pytest.raises(LibraryCredentialsMissing):
        crawler.search("attention")


def test_library_crawler_with_credentials_not_implemented():
    crawler = LibraryCrawler(account="user", password="pass")
    crawler.source = "ieee"  # 模拟 IEEE 数据源
    assert crawler.is_available() is True
    # 凭据就绪:未装 playwright → 提示安装;已装但无会话 → SessionExpired;
    # 会话就绪但实现缺失 → NotImplementedError
    with pytest.raises((NotImplementedError, PlaywrightUnavailable, SessionExpired)):
        crawler.search("attention")
