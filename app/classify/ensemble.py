"""集成层:合并多层分类结果,产出最终锚定标签。

策略(可配置):
  1. 每层结果按层权重加权投票(置信度 × 权重);
  2. 得票最高的领域获胜;LLM 层在配置为真实 provider 时权重更高;
  3. 若所有层均弃权 → 返回 None(论文不入地图)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.classify.base import ClassifierResult

# 各层默认权重:LLM 层判断力最强;规则层精确但覆盖面窄;统计层覆盖面广
DEFAULT_LAYER_WEIGHTS: dict[str, float] = {
    "rules": 1.0,
    "stats": 0.8,
    "llm": 1.5,
}


@dataclass
class EnsembleResult:
    domain_key: str
    domain_name: str
    confidence: float
    votes: dict[str, float] = field(default_factory=dict)   # layer -> 权重分
    detail: list[ClassifierResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domain_key": self.domain_key,
            "domain_name": self.domain_name,
            "confidence": round(self.confidence, 3),
            "votes": {k: round(v, 3) for k, v in self.votes.items()},
        }


class EnsembleClassifier:
    def __init__(self, layer_weights: dict[str, float] | None = None):
        self.weights = layer_weights or DEFAULT_LAYER_WEIGHTS

    def combine(self, results: list[ClassifierResult | None]) -> EnsembleResult | None:
        valid = [r for r in results if r and r.domain_key]
        if not valid:
            return None
        votes: dict[str, float] = {}
        names: dict[str, str] = {}
        for r in valid:
            weight = self.weights.get(r.layer, 1.0)
            votes[r.domain_key] = votes.get(r.domain_key, 0.0) + r.confidence * weight
            names[r.domain_key] = r.domain_name or r.domain_key
        winner = max(votes, key=votes.get)
        total = sum(votes.values())
        confidence = votes[winner] / total if total > 0 else 0.0
        return EnsembleResult(
            domain_key=winner,
            domain_name=names[winner],
            confidence=confidence,
            votes=votes,
            detail=valid,
        )
