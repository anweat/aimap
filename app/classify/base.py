"""多层分类框架:每层输出「领域标签 + 置信度 + 证据」,由集成层合并。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class ClassifierResult(BaseModel):
    """单层分类输出。"""

    layer: str
    domain_key: str
    domain_name: str = ""
    confidence: float = 0.0          # 0~1
    evidence: str = ""               # 人类可读/可追溯的证据描述
    extra: dict = Field(default_factory=dict)


class BaseClassifier(ABC):
    """分类器接口:输入论文文本,输出带置信度的领域标签。"""

    layer: str = "base"

    @abstractmethod
    def classify(self, title: str, abstract: str, *, metadata: dict | None = None) -> ClassifierResult | None:
        """返回 None 表示本层无法判断(弃权),由集成层处理。"""
