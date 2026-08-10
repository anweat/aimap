"""存储管理测试:导出 / 导入往返与幂等性。"""
import pytest
from sqlmodel import select

from app.agents.orchestrator import OrchestratorAgent
from app.db import get_session, init_db, upsert_paper
from app.domain.builder import build_domain_tree
from app.models.entities import Paper, PaperPosition
from scripts.backup import export_all, import_all


@pytest.fixture(scope="module")
def session():
    init_db()
    with get_session() as s:
        build_domain_tree(s)
        p = upsert_paper(
            s,
            Paper(
                source="test",
                source_id="bk-1",
                title="Flash Attention for Efficient Transformers",
                abstract="Fast and memory-efficient exact attention with IO-awareness.",
                authors="Dao et al.",
                categories="cs.LG",
            ),
        )
        OrchestratorAgent(s).analyze_paper(p.id)
        yield s


def test_export_import_roundtrip(session, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with get_session() as s:
        before = s.exec(select(Paper)).all()
        before_ids = {p.id: p.anchored_domain_key for p in before}

    path = export_all()
    assert path.exists()

    # 导入到空库(替换模式)
    with get_session() as s:
        for t in (PaperPosition, Paper):
            for row in s.exec(select(t)):
                s.delete(row)
        s.commit()
    summary = import_all(str(path))
    assert summary["papers"] >= 1

    with get_session() as s:
        after = s.exec(select(Paper)).all()
        assert len(after) == len(before)
        # 锚定结果保留
        for p in after:
            assert p.anchored_domain_key == before_ids[p.id]
        # 位置恢复
        positions = s.exec(select(PaperPosition)).all()
        assert len(positions) == len([k for k in before_ids.values() if k])


def test_import_idempotent(session, tmp_path, monkeypatch):
    """重复导入不产生重复论文。"""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    path = export_all()
    import_all(str(path))
    with get_session() as s:
        n1 = len(s.exec(select(Paper)).all())
    import_all(str(path))
    with get_session() as s:
        n2 = len(s.exec(select(Paper)).all())
    assert n1 == n2
