"""第二层:统计分类器(词频-逆文档频率 + 余弦相似度)。

思路:每个领域节点由其关键词构造"伪文档",论文文本与所有伪文档
计算 TF-IDF 余弦相似度,取最相似领域。不依赖外部服务。
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

from sqlmodel import Session

from app.classify.base import BaseClassifier, ClassifierResult
from app.db import list_domain_nodes

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class StatsClassifier(BaseClassifier):
    layer = "stats"

    def __init__(self, session: Session):
        self._docs: dict[str, Counter] = {}   # domain_key -> 词频
        for node in list_domain_nodes(session):
            keywords = json.loads(node.keywords or "[]")
            # 关键词拆词 + 节点名拆词,构成领域伪文档
            words: list[str] = []
            for kw in keywords:
                words.extend(_tokens(kw))
            words.extend(_tokens(node.name))
            if words:
                self._docs[node.key] = Counter(words)
        # 全局 IDF(跨领域)
        self._df: Counter = Counter()
        for counter in self._docs.values():
            for word in counter:
                self._df[word] += 1
        self._n_docs = max(len(self._docs), 1)

    def _idf(self, word: str) -> float:
        return math.log((1 + self._n_docs) / (1 + self._df[word])) + 1.0

    def classify(self, title: str, abstract: str, *, metadata: dict | None = None) -> ClassifierResult | None:
        query = Counter(_tokens(f"{title} {abstract}"))
        if not query:
            return None
        best: tuple[float, str] | None = None
        for key, doc in self._docs.items():
            # TF-IDF 余弦相似度(向量均为非负,内积/范数即可)
            dot = sum(q * self._idf(w) * doc[w] * self._idf(w) for w, q in query.items() if w in doc)
            if dot <= 0:
                continue
            norm_doc = math.sqrt(sum(doc[w] * self._idf(w) ** 2 for w in doc))
            norm_query = math.sqrt(sum(q * self._idf(w) ** 2 for w, q in query.items()))
            if norm_doc <= 0 or norm_query <= 0:
                continue
            sim = dot / (norm_doc * norm_query)
            if best is None or sim > best[0]:
                best = (sim, key)
        if best is None:
            return None
        sim, key = best
        if sim < 0.05:  # 过低视为弃权
            return None
        return ClassifierResult(
            layer=self.layer,
            domain_key=key,
            domain_name=key.rsplit(".", 1)[-1],
            confidence=round(min(0.9, 0.3 + sim * 1.2), 3),
            evidence=f"TF-IDF 余弦相似度 {sim:.3f}",
        )
