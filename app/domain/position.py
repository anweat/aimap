"""论文位置锚定:在领域树四元数坐标基础上,叠加论文特征扰动。

公式:
  q_paper = normalize( q_domain × q_feature )

  q_domain   : 论文锚定领域的节点四元数(论文在树中的"主干位置")
  q_feature  : 由论文标题+摘要确定性生成的小扰动四元数(论文在领域内的"个体偏移")

特性:
  - 确定性:同一篇论文多次计算得到相同位置;
  - 聚簇性:同领域论文共享 q_domain,彼此距离小;
  - 全地图可导:论文位置仅依赖领域锚定 + 文本特征,无需外部服务。
"""
from __future__ import annotations

import math
import random

from sqlmodel import Session

from app.db import get_domain_node
from app.models.entities import Paper, PaperPosition
from app.quaternion.core import Quaternion

# 特征扰动幅度:角度上限(弧度),控制领域内论文的离散程度
FEATURE_SPREAD = 0.35


def anchor_paper_position(session: Session, paper: Paper, domain_key: str, confidence: float) -> PaperPosition:
    """计算并保存论文的四元数位置。"""
    node = get_domain_node(session, domain_key)
    q_domain = Quaternion(node.qw, node.qx, node.qy, node.qz) if node else Quaternion.identity()

    # 特征扰动:由论文标识确定性生成小角度旋转(领域内离散,跨领域保持聚簇)
    feature_seed = f"{paper.source}:{paper.source_id}:{paper.title[:200]}"
    rng = random.Random(feature_seed)
    theta = FEATURE_SPREAD * rng.random()
    axis = (rng.random() - 0.5, rng.random() - 0.5, rng.random() - 0.5)
    q_feature = Quaternion.from_axis_angle(axis, theta)

    q_paper = (q_domain * q_feature).normalized()
    pos = PaperPosition(
        paper_id=paper.id,
        domain_key=domain_key,
        qw=q_paper.w,
        qx=q_paper.x,
        qy=q_paper.y,
        qz=q_paper.z,
        confidence=confidence,
    )
    return pos
