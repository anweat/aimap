"""领域基准规则与动态注册(增量领域生长)。

设计:
  - 基准规则 = 种子领域树(三大根 + 已知细分,见 seed.py)+ 如下创建规则;
  - 领域不是写死的:AI 分析论文时若发现新的研究方向,可动态创建领域节点,
    自动获得:
      * 稳定的 key(slug + 序号,同父下同名称复用不重复建);
      * 四元数坐标(父四元数 × 兄弟旋转,与种子领域同一嵌入规则,
        新领域自然落在父领域附近的世界坐标上);
      * 描述与关键词(由 AI 生成);
      * created_by='ai' 标记(区别于种子领域)。
  - 约束:新领域必须挂载到已存在的父领域(默认三大根),防止失控生长;
  - 去重:同父下名称归一化后相同 → 复用已有节点(不重复建)。
"""
from __future__ import annotations

import math
import re
from typing import Iterable

from sqlmodel import Session, select

from app.db import upsert_domain_node
from app.domain.seed import DOMAIN_TREE_SEED
from app.models.entities import DomainNode
from app.quaternion.core import Quaternion, axis_angle_for_branch

# 新领域默认挂载点(父领域缺失时)
FALLBACK_PARENTS = ["models", "algorithm", "infra"]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """名称 → 稳定 slug(小写字母数字,连字符分隔)。"""
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")


def normalize_domain_name(name: str) -> str:
    return slugify(name)


def _child_keys(session: Session, parent_key: str) -> list[str]:
    rows = session.exec(
        select(DomainNode.key).where(DomainNode.parent_key == parent_key)
    ).all()
    return list(rows)


def find_domain(session: Session, name: str, parent_key: str | None = None) -> DomainNode | None:
    """按名称(归一化)查找领域;parent_key 提供时限定父子关系。"""
    norm = normalize_domain_name(name)
    stmt = select(DomainNode)
    if parent_key is not None:
        stmt = stmt.where(DomainNode.parent_key == parent_key)
    for node in session.exec(stmt):
        if normalize_domain_name(node.name) == norm:
            return node
    return None


def create_domain(
    session: Session,
    name: str,
    parent_key: str | None = None,
    *,
    description: str = "",
    keywords: Iterable[str] = (),
    created_by: str = "ai",
) -> DomainNode:
    """创建(或复用)领域节点,自动分配四元数坐标。

    规则:
      1. 名称归一化去重:同父下同名直接复用;
      2. 父领域必须存在;缺省/无效时挂到基准根(三大根之一,按已有兄弟数轮换);
      3. key = f"{parent_key}.{slug}",与既有兄弟冲突时追加序号;
      4. 坐标 = 父四元数 × 兄弟旋转(与种子嵌入规则一致)。
    """
    name = name.strip()
    if not name:
        raise ValueError("领域名称不能为空")

    # 1. 去重
    if parent_key:
        existing = find_domain(session, name, parent_key)
        if existing:
            return existing

    # 2. 父领域解析
    parent: DomainNode | None = None
    if parent_key:
        parent = session.get(DomainNode, parent_key)
    if parent is None:
        # 挂到基准根:选择兄弟数最少的根,保持平衡
        roots = [
            n for n in session.exec(select(DomainNode).where(DomainNode.parent_key.is_(None)))
        ] or _ensure_seed_roots(session)
        if not roots:
            raise ValueError("领域树为空,无法创建领域(请先初始化种子领域)")
        parent = min(roots, key=lambda r: len(_child_keys(session, r.key)))
        parent_key = parent.key

    # 3. 唯一 key
    base_key = f"{parent.key}.{slugify(name)}"
    key = base_key
    seq = 2
    while session.get(DomainNode, key) is not None:
        key = f"{base_key}-{seq}"
        seq += 1

    # 4. 四元数坐标:父 × 兄弟旋转
    siblings = _child_keys(session, parent_key)
    index = len(siblings)  # 新节点排在兄弟末尾
    level = parent.level + 1
    axis, angle = axis_angle_for_branch(index, len(siblings) + 1, spread=math.pi / 2 / (level + 1))
    q = (
        Quaternion(parent.qw, parent.qx, parent.qy, parent.qz)
        * Quaternion.from_axis_angle(axis, angle)
    ).normalized()

    node = DomainNode(
        key=key,
        name=name,
        parent_key=parent_key,
        level=level,
        keywords=__import__("json").dumps(list(keywords), ensure_ascii=False),
        qw=q.w,
        qx=q.x,
        qy=q.y,
        qz=q.z,
        description=description,
        created_by=created_by,
    )
    upsert_domain_node(session, node)
    return node


def _ensure_seed_roots(session: Session) -> list[DomainNode]:
    """兜底:领域树完全为空时,从种子数据恢复三大根。"""
    from app.domain.builder import build_domain_tree

    nodes = build_domain_tree(session)
    return [nodes[k] for k in ("models", "algorithm", "infra") if k in nodes]


def domain_prompt_catalog(session: Session, max_items: int = 60) -> str:
    """当前领域目录(供 LLM 选择/挂载):key(名称) 列表。"""
    nodes = session.exec(select(DomainNode)).all()
    nodes.sort(key=lambda n: (n.level, n.key))
    items = [f"{n.key}({n.name})" for n in nodes[:max_items]]
    return ", ".join(items)


def recent_ai_domains(session: Session, limit: int = 20) -> list[DomainNode]:
    """最近由 AI 创建的领域(研究方向增量),按创建时间倒序。"""
    rows = session.exec(
        select(DomainNode)
        .where(DomainNode.created_by == "ai")
        .order_by(DomainNode.created_at.desc())
        .limit(limit)
    ).all()
    return list(rows)
