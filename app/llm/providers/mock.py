"""Mock Provider:无 API key 时的默认实现,用于测试与演示。

行为:
  - chat:按提示词中的关键词返回固定结构的 JSON(可被分类器/Agent 解析);
  - 保证全链路(爬取 → 分类 → 锚定 → 可视化)在没有外部服务时可用。
"""
from __future__ import annotations

import json
import random

from app.llm.base import BaseProvider, ChatMessage, ChatResult


class MockProvider(BaseProvider):
    name = "mock"

    def __init__(self):
        self._rng = random.Random(42)

    def chat(self, messages: list[ChatMessage], *, temperature: float = 0.2, max_tokens: int = 2048) -> ChatResult:
        user_text = "\n".join(m.content for m in messages if m.role == "user")
        title = _extract_title(user_text)
        # 从题目/摘要中尽力挑一个领域关键词(模拟 LLM 判断)
        candidate = _pick_domain(title)
        content = json.dumps(
            {
                "domain_key": candidate,
                "domain_name": candidate,
                "confidence": round(self._rng.uniform(0.55, 0.9), 3),
                "summary": f"[mock] 该论文涉及 {candidate} 方向",
                "keywords": _keywords(title),
            },
            ensure_ascii=False,
        )
        return ChatResult(content=content, raw={"provider": "mock", "model": "mock-1"})

    def chat_structured(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 2048) -> str:
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens).content


# -- 模拟"理解"的启发式 ---------------------------------------------------
_DOMAIN_WORDS = [
    ("models.llm.arch", ["transformer", "attention", "moe", "mamba", "architecture"]),
    ("models.llm.scaling", ["scaling", "emergent"]),
    ("models.llm.align", ["alignment", "safety", "rlhf", "jailbreak"]),
    ("models.vlm", ["vision", "multimodal", "image", "diffusion"]),
    ("models.slm", ["distillation", "quantization", "compression"]),
    ("models.rl", ["reinforcement", "reward"]),
    ("models.reasoning", ["reasoning", "chain-of-thought", "reasoning"]),
    ("algorithm.opt", ["optimization", "adam", "gradient"]),
    ("algorithm.training", ["fine-tuning", "instruction", "training"]),
    ("algorithm.sampling", ["decoding", "sampling", "beam"]),
    ("algorithm.retrieval", ["retrieval", "rag", "knowledge"]),
    ("algorithm.eval", ["benchmark", "evaluation", "benchmark"]),
    ("algorithm.agent", ["agent", "tool", "planning"]),
    ("algorithm.gen", ["generative", "diffusion", "gan"]),
    ("infra.training", ["distributed", "parallel", "fsdp", "deepspeed"]),
    ("infra.inference", ["inference", "serving", "vllm", "kv cache"]),
    ("infra.gpu", ["gpu", "cuda", "accelerator", "hardware"]),
    ("infra.scheduling", ["scheduler", "cluster", "kubernetes"]),
    ("infra.data", ["data pipeline", "dataset", "curation"]),
    ("infra.net", ["rdma", "network", "nvlink"]),
    ("infra.serving", ["mlops", "serving", "observability"]),
]


def _extract_title(user_text: str) -> str:
    for line in user_text.splitlines():
        line = line.strip()
        if line.startswith("Title:"):
            return line[len("Title:") :].strip()
    return user_text[:120]


def _pick_domain(text: str) -> str:
    lowered = text.lower()
    for key, words in _DOMAIN_WORDS:
        if any(w in lowered for w in words):
            return key
    return "models.llm"


def _keywords(text: str) -> list[str]:
    return [w for w in text.replace(",", " ").split()[:5]]
