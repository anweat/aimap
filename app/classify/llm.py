"""第三层:LLM 分类器(需 Provider;Mock 下亦可用)。

Prompt 要求模型输出 JSON:
  {domain_key, domain_name, confidence, summary, keywords,
   create_new, parent_key, description}

create_new=true 表示论文代表一个**新的研究方向**(现有领域均不贴切),
需给出:名称(domain_name)、挂载父领域(parent_key,取候选列表中的 key)、
一句话描述(description)。AnchorAgent 将据此动态创建领域并锚定。
"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.classify.base import BaseClassifier, ClassifierResult
from app.llm.base import BaseProvider, ChatMessage, extract_json
from app.llm.registry import get_provider


class LlmOutput(BaseModel):
    domain_key: str = ""
    domain_name: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)
    # 学科维度(arXiv 来源用元数据,其他来源由 LLM 补):如 cs.CL / stat.ML
    arxiv_category: str = ""
    # 新领域建议(可选)
    create_new: bool = False
    parent_key: str = ""
    description: str = ""


def _domain_list_prompt() -> str:
    """从领域树生成候选列表。静态内置,避免每篇论文查询 DB。"""
    return (
        "models.llm.arch(模型架构), models.llm.scaling(规模法则), "
        "models.llm.align(对齐与安全), models.vlm(多模态模型), "
        "models.slm(小模型与蒸馏), models.rl(强化学习), models.reasoning(推理与思维链), "
        "algorithm.opt(优化), algorithm.training(训练策略), algorithm.sampling(采样解码), "
        "algorithm.retrieval(检索增强RAG), algorithm.eval(评测基准), algorithm.agent(智能体), "
        "algorithm.gen(生成与扩散), infra.training(训练系统), infra.inference(推理系统), "
        "infra.gpu(异构计算), infra.scheduling(调度), infra.data(数据工程), "
        "infra.net(网络存储), infra.serving(MLOps)"
    )


class LlmClassifier(BaseClassifier):
    layer = "llm"

    def __init__(self, provider: BaseProvider | None = None):
        self._provider = provider or get_provider()

    def classify(self, title: str, abstract: str, *, metadata: dict | None = None) -> ClassifierResult | None:
        prompt = (
            "你是一名 AI 研究领域的论文分类专家。请判断下面这篇论文最属于哪个细分领域。\n\n"
            f"可选领域(domain_key 必须严格取其中之一):\n{_domain_list_prompt()}\n\n"
            "如果论文代表一个现有领域都没有覆盖的新研究方向,"
            "请设置 create_new=true,并在 parent_key 中给出最贴近的父领域 key,"
            "domain_name 给新领域名称(简短、专有名词优先),description 给一句话描述。\n\n"
            "另外请判断论文最接近的 arXiv 学科分类(arxiv_category,"
            "如 cs.CL/cs.LG/cs.CV/cs.DC/cs.AI/stat.ML 等)。\n\n"
            f"论文标题: {title}\n论文摘要: {abstract[:3000]}\n\n"
            '只输出 JSON: {"domain_key": "...", "domain_name": "...", '
            '"confidence": 0.0~1.0, "summary": "一句话概括", "keywords": ["..."], '
            '"arxiv_category": "cs.LG", "create_new": false, "parent_key": "", "description": ""}'
        )
        try:
            content = self._provider.chat_structured(
                [ChatMessage(role="user", content=prompt)],
                temperature=0.0,
            )
            data = json.loads(extract_json(content))
            out = LlmOutput(**data)
        except Exception as e:
            return ClassifierResult(
                layer=self.layer,
                domain_key="",
                confidence=0.0,
                evidence=f"LLM 解析失败: {e}",
            )

        if out.create_new:
            # 新领域建议:domain_key 置空,交由 AnchorAgent 动态创建
            return ClassifierResult(
                layer=self.layer,
                domain_key="",
                domain_name=out.domain_name,
                confidence=out.confidence,
                evidence=f"LLM 建议新领域: {out.domain_name} (父: {out.parent_key or '自动'})",
                extra={
                    "create_new": True,
                    "parent_key": out.parent_key,
                    "description": out.description,
                    "keywords": out.keywords,
                    "summary": out.summary,
                    "arxiv_category": out.arxiv_category,
                },
            )
        return ClassifierResult(
            layer=self.layer,
            domain_key=out.domain_key,
            domain_name=out.domain_name or out.domain_key.rsplit(".", 1)[-1],
            confidence=out.confidence,
            evidence=f"LLM: {out.summary}"[:200],
            extra={"keywords": out.keywords, "arxiv_category": out.arxiv_category},
        )
