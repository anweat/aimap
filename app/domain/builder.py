"""领域树构建:从种子数据建树、分配四元数坐标、统计论文数。

四元数分配规则:
  根节点 = 恒等四元数 (1,0,0,0);
  子节点 = 父节点四元数 × 由兄弟序号决定的旋转(轴角 → 四元数);
  递归逐层嵌入 S3,树结构即映射为球面上的层次分布。
"""
from __future__ import annotations

import json
import math

from sqlmodel import Session, select

from app.db import upsert_domain_node
from app.domain.seed import DOMAIN_TREE_SEED
from app.models.entities import DomainNode, Paper
from app.quaternion.core import Quaternion, axis_angle_for_branch


def build_domain_tree(session: Session, seed: list[dict] | None = None) -> dict[str, DomainNode]:
    """从种子数据构建(或重建)领域树,分配四元数坐标。返回 key → node。"""
    seed = seed or DOMAIN_TREE_SEED
    nodes: dict[str, DomainNode] = {}

    # 先按 key 建立父子索引
    by_key: dict[str, dict] = {item["key"]: item for item in seed}

    def assign(key: str, parent_q: Quaternion, parent_level: int) -> Quaternion:
        item = by_key[key]
        level = parent_level + 1
        # 所有层级统一:子节点 = 父四元数 × 兄弟旋转。
        # 虚拟根(投影中心)= 恒等四元数;三大根领域以其为父旋转 90° 铺开,
        # 更深层级角度递减收敛。
        siblings = [k for k, v in by_key.items() if v["parent"] == item["parent"]]
        index = siblings.index(key)
        axis, angle = axis_angle_for_branch(index, len(siblings), spread=math.pi / 2 / (level + 1))
        q = (parent_q * Quaternion.from_axis_angle(axis, angle)).normalized()
        node = DomainNode(
            key=item["key"],
            name=item["name"],
            parent_key=item["parent"],
            level=level,
            keywords=json.dumps(item["keywords"], ensure_ascii=False),
            qw=q.w,
            qx=q.x,
            qy=q.y,
            qz=q.z,
        )
        upsert_domain_node(session, node)
        nodes[key] = node
        return q

    # 根节点
    roots = [k for k, v in by_key.items() if v["parent"] is None]
    for root_key in roots:
        assign(root_key, Quaternion.identity(), -1)

    # 非根节点按层数从浅到深分配(保证父先于子)
    remaining = [k for k in by_key if k not in nodes]
    for _ in range(len(remaining) + 1):
        progressed = False
        for key in list(remaining):
            parent_key = by_key[key]["parent"]
            if parent_key in nodes:
                assign(key, Quaternion(nodes[parent_key].qw, nodes[parent_key].qx, nodes[parent_key].qy, nodes[parent_key].qz), nodes[parent_key].level)
                remaining.remove(key)
                progressed = True
        if not progressed:
            break  # 孤儿节点(种子数据错误),跳过

    _refresh_counts(session)
    return nodes


def _refresh_counts(session: Session) -> None:
    """统计每个领域下的论文数(含子树)。"""
    rows = session.exec(
        select(Paper.anchored_domain_key, Paper.id)
    ).all()
    counts: dict[str, int] = {}
    for key, _ in rows:
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        parts = key.split(".")
        for i in range(1, len(parts)):
            ancestor = ".".join(parts[:i])
            counts[ancestor] = counts.get(ancestor, 0) + 1
    for node in session.exec(select(DomainNode)):
        node.paper_count = counts.get(node.key, 0)
        session.add(node)
    session.commit()
