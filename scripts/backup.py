"""存储管理:导出 / 导入 / 快照 / 信息。

用途:
  - 备份:全量导出论文+分类+位置+领域树 → JSON(或复制 SQLite 快照);
  - 迁移:本地 ↔ 远程云服务器之间搬运数据(爬虫跑在云端,可视化在本地,
    或反之),导入时按 (source, source_id) 幂等合并,保留锚定结果;
  - 审计:查看数据规模与分布。

用法:
  python scripts/backup.py --export           # 导出 data/exports/aimap_<时间戳>.json
  python scripts/backup.py --snapshot         # 复制 DB 快照 data/exports/aimap_<时间戳>.db
  python scripts/backup.py --import <file>    # 从 JSON 导入(幂等合并)
  python scripts/backup.py --info             # 数据规模统计
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select

from app.config import settings
from app.db import engine, get_session
from app.models.entities import Classification, DomainNode, Paper, PaperPosition


def _exports_dir() -> Path:
    d = settings.data_dir / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def export_all() -> Path:
    """全量导出为 JSON(论文/分类/位置/领域树)。"""
    with get_session() as s:
        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "papers": [p.model_dump(mode="json") for p in s.exec(select(Paper))],
            "classifications": [c.model_dump(mode="json") for c in s.exec(select(Classification))],
            "positions": [p.model_dump(mode="json") for p in s.exec(select(PaperPosition))],
            "domains": [d.model_dump(mode="json") for d in s.exec(select(DomainNode))],
        }
    path = _exports_dir() / f"aimap_{_stamp()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ok] 导出完成: {path}")
    print(f"     论文 {len(payload['papers'])} · 分类 {len(payload['classifications'])}"
          f" · 位置 {len(payload['positions'])} · 领域 {len(payload['domains'])}")
    return path


def snapshot_db() -> Path:
    """复制 SQLite 数据库快照(含全部数据,轻量)。"""
    src = Path(settings.db_path)
    if not src.exists():
        raise SystemExit(f"数据库不存在: {src}")
    dst = _exports_dir() / f"aimap_{_stamp()}.db"
    shutil.copy2(src, dst)
    print(f"[ok] 快照完成: {dst} ({src.stat().st_size / 1024:.0f} KB)")
    return dst


def import_all(path: str, *, replace: bool = False) -> dict:
    """从 JSON 导入;按 (source, source_id) 幂等合并。

    replace=True 时先清空论文相关表(领域树保留)。
    """
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"文件不存在: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if "papers" not in data:
        raise SystemExit("不是有效的 aimap 导出文件(缺少 papers 字段)")

    from app.db import upsert_domain_node, upsert_paper

    with get_session() as s:
        if replace:
            for t in (PaperPosition, Classification, Paper):
                for row in s.exec(select(t)):
                    s.delete(row)
            s.commit()
            print("[ok] 已清空论文相关表(replace 模式)")

        # 领域树(若导出包含且本地缺失)
        imported_domains = 0
        for d in data.get("domains", []):
            node = DomainNode.model_validate(d)
            existing = s.get(DomainNode, node.key)
            if existing is None:
                upsert_domain_node(s, node)
                imported_domains += 1

        # 论文(幂等合并,保留已有锚定)
        imported = 0
        for p in data.get("papers", []):
            paper = Paper.model_validate(p)
            saved = upsert_paper(s, paper)
            # 恢复锚定字段(upsert 会保留,但新论文需要写回)
            if not saved.anchored_domain_key and paper.anchored_domain_key:
                for f in ("anchored_domain_key", "anchored_domain_name", "anchored_confidence", "analyzed_at"):
                    setattr(saved, f, getattr(paper, f))
                s.add(saved)
            imported += 1

        # 位置
        imported_pos = 0
        for pos in data.get("positions", []):
            existing = s.exec(
                select(PaperPosition).where(PaperPosition.paper_id == pos["paper_id"])
            ).first()
            if existing is None:
                s.add(PaperPosition.model_validate(pos))
                imported_pos += 1
        # 分类(仅导入本地缺失的)
        imported_cls = 0
        for c in data.get("classifications", []):
            existing = s.exec(
                select(Classification).where(
                    Classification.paper_id == c["paper_id"], Classification.layer == c["layer"]
                )
            ).first()
            if existing is None:
                s.add(Classification.model_validate(c))
                imported_cls += 1
        s.commit()

    # 刷新领域计数
    from app.domain.builder import _refresh_counts

    with get_session() as s:
        _refresh_counts(s)

    summary = {
        "papers": imported,
        "domains": imported_domains,
        "positions": imported_pos,
        "classifications": imported_cls,
    }
    print(f"[ok] 导入完成: {summary}")
    return summary


def show_info() -> None:
    with get_session() as s:
        n_papers = len(s.exec(select(Paper)).all())
        n_cls = len(s.exec(select(Classification)).all())
        n_pos = len(s.exec(select(PaperPosition)).all())
        n_domains = len(s.exec(select(DomainNode)).all())
        anchored = sum(1 for p in s.exec(select(Paper)) if p.anchored_domain_key)
    db_size = Path(settings.db_path).stat().st_size / 1024 if Path(settings.db_path).exists() else 0
    print(f"数据库: {settings.db_path} ({db_size:.0f} KB)")
    print(f"论文: {n_papers}(已锚定 {anchored}) · 分类证据: {n_cls} · 位置: {n_pos} · 领域节点: {n_domains}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="AIMap 存储管理")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", action="store_true", help="全量导出 JSON")
    group.add_argument("--snapshot", action="store_true", help="SQLite 快照")
    group.add_argument("--import", dest="import_file", metavar="FILE", help="从 JSON 导入")
    group.add_argument("--info", action="store_true", help="数据规模统计")
    parser.add_argument("--replace", action="store_true", help="导入前清空论文数据")
    args = parser.parse_args()

    if args.export:
        export_all()
    elif args.snapshot:
        snapshot_db()
    elif args.import_file:
        import_all(args.import_file, replace=args.replace)
    elif args.info:
        show_info()


if __name__ == "__main__":
    main()
