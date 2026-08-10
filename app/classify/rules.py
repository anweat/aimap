"""第一层:规则分类器(关键词锚定)。

从领域树节点读取关键词,对标题/摘要做大小写不敏感的子串匹配。
命中即输出该领域;命中多个领域时取得分最高者(得分 = 命中次数加权)。
"""
from __future__ import annotations

import json

from sqlmodel import Session

from app.classify.base import BaseClassifier, ClassifierResult
from app.db import list_domain_nodes


class RulesClassifier(BaseClassifier):
    layer = "rules"

    def __init__(self, session: Session):
        # 预加载领域节点 → 关键词
        self._rules: list[dict] = []
        for node in list_domain_nodes(session):
            keywords = json.loads(node.keywords or "[]")
            if keywords:
                self._rules.append(
                    {
                        "key": node.key,
                        "name": node.name,
                        "keywords": [k.lower() for k in keywords],
                    }
                )

    def classify(self, title: str, abstract: str, *, metadata: dict | None = None) -> ClassifierResult | None:
        text = f"{title}\n{abstract}".lower()
        best: tuple[float, dict, list[str]] | None = None
        for rule in self._rules:
            hits = [k for k in rule["keywords"] if k in text]
            if hits:
                score = len(hits) + 0.5 * (rule["key"].count(".") + 1)  # 深层级略有加成
                if best is None or score > best[0]:
                    best = (score, rule, hits)
        if best is None:
            return None
        score, rule, hits = best
        confidence = min(0.95, 0.45 + 0.12 * len(hits))
        return ClassifierResult(
            layer=self.layer,
            domain_key=rule["key"],
            domain_name=rule["name"],
            confidence=round(confidence, 3),
            evidence=f"命中关键词: {', '.join(hits[:6])}",
        )
