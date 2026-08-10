"""LLM Provider 抽象:所有大模型调用经由统一接口。

设计目标:
  - 配置缺失时自动回落 MockProvider,保证框架无 key 可全链路运行/测试;
  - 后续在 .env 填入 API key 并设置 AIMAP_LLM_PROVIDER = openai|deepseek 即切换真实模型,
    上层(分类器 / Agent)代码零改动;
  - 所有 Provider 输出为结构化 JSON(通过 Pydantic 模型校验)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str  # system | user | assistant
    content: str


class ChatResult(BaseModel):
    content: str
    raw: dict = Field(default_factory=dict)


class ProviderError(Exception):
    """LLM 调用失败。"""


class BaseProvider(ABC):
    """LLM Provider 接口。"""

    name: str = "base"

    @abstractmethod
    def chat(self, messages: list[ChatMessage], *, temperature: float = 0.2, max_tokens: int = 2048) -> ChatResult:
        """多轮对话,返回文本结果。"""

    def chat_structured(self, messages: list[ChatMessage], *, temperature: float = 0.0, max_tokens: int = 2048) -> str:
        """对话并尽力提取 JSON(默认实现:原样返回,由调用方解析)。"""
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens).content


def extract_json(text: str) -> str:
    """从 LLM 输出中提取 JSON 块(容忍 ```json 围栏、前后杂讯、对象/数组)。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # 优先整体可解析
    try:
        import json as _json

        _json.loads(text)
        return text
    except Exception:
        pass
    # 数组提取:第一个 [ 到最后一个 ]
    s, e = text.find("["), text.rfind("]")
    if s != -1 and e != -1 and e > s:
        return text[s : e + 1]
    # 对象提取:第一个 { 到最后一个 }
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return text[s : e + 1]
    return text
