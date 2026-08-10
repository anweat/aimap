"""arXiv 学科分类目录(学科维度)。

三维度正交分类体系中的"学科维度":
  - 维度1 视角:models / algorithm / infra(用户定义,主坐标,定地图位置)
  - 维度2 学科:arXiv cs.* / stat.* 分类(本模块,论文自带元数据,零成本)
  - 维度3 主题:AI 动态增量领域(domain/policy.py)

学科标签以 PaperTag(source='category') 存储,提供:
  - 学科过滤/聚合(整体研究方向中的学科分布);
  - 跨视角对照(同一学科下的模型/算法/系统论文占比)。
"""
from __future__ import annotations

import re

# 常用学科 → 中文名(覆盖 AI 论文 90%+ 归属;未列出的按原名展示)
ARXIV_CATEGORIES: dict[str, str] = {
    "cs.AI": "人工智能",
    "cs.CL": "计算语言学/NLP",
    "cs.LG": "机器学习",
    "cs.CV": "计算机视觉",
    "cs.DC": "分布式并行计算",
    "cs.CR": "安全与密码",
    "cs.NE": "神经与进化计算",
    "cs.SE": "软件工程",
    "cs.DS": "数据结构与算法",
    "cs.IT": "信息论",
    "cs.MM": "多媒体",
    "cs.RO": "机器人学",
    "cs.AR": "硬件架构",
    "cs.PF": "性能分析",
    "cs.SY": "系统与控制",
    "cs.HC": "人机交互",
    "cs.IR": "信息检索",
    "cs.DM": "离散数学",
    "cs.DB": "数据库",
    "cs.GT": "博弈论",
    "cs.LO": "逻辑",
    "cs.PL": "编程语言",
    "cs.CY": "计算与社会",
    "stat.ML": "统计机器学习",
    "stat.AP": "应用统计",
    "eess.AS": "音频/语音",
    "eess.IV": "图像处理",
    "math.OC": "优化与控制",
}

_CSV_RE = re.compile(r"[,\s]+")


def split_categories(categories_str: str) -> list[str]:
    """'cs.CL, cs.LG' / 'cs.CL cs.LG' → ['cs.CL', 'cs.LG'](保持出现顺序去重)。"""
    if not categories_str:
        return []
    seen: list[str] = []
    for part in _CSV_RE.split(categories_str.strip()):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def category_label(key: str) -> str:
    """学科 key → 展示名:'cs.CL 计算语言学/NLP'。"""
    name = ARXIV_CATEGORIES.get(key)
    return f"{key} {name}" if name else key


def parse_categories(categories_str: str) -> list[dict]:
    """解析论文 categories 字段 → [{key, name}]。"""
    return [
        {"key": k, "name": ARXIV_CATEGORIES.get(k, k)}
        for k in split_categories(categories_str)
    ]
