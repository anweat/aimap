"""多来源重复资源过滤。

去重策略(按优先级):
  1. 同源精确:相同 (source, source_id) → 视为同一资源的更新(更新元数据);
  2. 跨源标题:title_norm(归一化标题)相同 → 重复,跳过;
  3. 跨源 URL :URL 相同 → 重复,跳过。

标题归一化:小写、去标点符号、合并空白、剥离常见版本/前缀噪声
(如 arXiv id、"(Technical Report)" 等),使 "Attention Is All You Need"
与 "attention is all you need." 判定为同一篇。
"""
from __future__ import annotations

import re

from sqlmodel import Session, select

from app.models.entities import Paper

# 剥离噪声:arXiv 版本号、圆括号/方括号后缀(版本/技术报告/会议标记)
_NOISE_PATTERNS = [
    re.compile(r"\b(arxiv|doi)\s*[:\s]*\S+", re.I),
    re.compile(r"\s*[\(\[]\s*[^)\]]*(technical report|preprint|version \d|v\d+)[^)\]]*[\)\]]\s*$", re.I),
    re.compile(r"\s*[\(\[]\s*(technical report|preprint|research report)\s*[\)\]]\s*$", re.I),
]
_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """归一化标题:小写 → 去噪声 → 去标点 → 合并空白。"""
    t = title.strip().lower()
    for pat in _NOISE_PATTERNS:
        t = pat.sub(" ", t)
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


def find_duplicate(session: Session, paper: Paper) -> tuple[Paper | None, str]:
    """查找重复论文。返回 (已存在的论文, 原因);无重复返回 (None, "")。

    reason: same_source(同源更新) | same_title(跨源标题重复) | same_url(URL 重复)
    """
    # 1. 同源精确
    if paper.source and paper.source_id:
        existing = session.exec(
            select(Paper).where(Paper.source == paper.source, Paper.source_id == paper.source_id)
        ).first()
        if existing:
            return existing, "same_source"

    title_norm = paper.title_norm or normalize_title(paper.title)
    if not title_norm:
        return None, ""

    # 2. 标题重复(任何来源;同源不同 source_id 的重复条目/版本也视为重复)
    existing = session.exec(
        select(Paper).where(Paper.title_norm == title_norm)
    ).first()
    if existing:
        return existing, "same_title"

    # 3. URL 相同(标题归一化后不同但链接一致)
    if paper.url:
        existing = session.exec(
            select(Paper).where(Paper.url == paper.url)
        ).first()
        if existing:
            return existing, "same_url"

    return None, ""


def upsert_paper_dedup(session: Session, paper: Paper) -> tuple[Paper, str]:
    """带去重的论文入库。返回 (论文, 处置结果)。

    处置结果:
      saved    —— 新论文入库;
      updated  —— 同源更新(新版本/元数据变化);
      duplicate_same_title / duplicate_same_url —— 跨源重复,已跳过。
    """
    from app.db import upsert_paper

    if not paper.title_norm:
        paper.title_norm = normalize_title(paper.title)

    duplicate, reason = find_duplicate(session, paper)
    if reason == "same_source":
        saved = upsert_paper(session, paper)
        return saved, "updated"
    if duplicate is not None:
        return duplicate, f"duplicate_{reason}"
    saved = upsert_paper(session, paper)
    return saved, "saved"
