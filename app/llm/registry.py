"""Provider 注册表:按名称获取,配置缺失自动回落 Mock。"""
from __future__ import annotations

from app.config import settings
from app.llm.base import BaseProvider
from app.llm.providers.mock import MockProvider
from app.llm.providers.openai_compat import DeepSeekProvider, OpenAiProvider


def get_provider(name: str | None = None) -> BaseProvider:
    """返回指定(或配置的)Provider;无法实例化时回落 Mock 并记录。"""
    name = name or settings.llm_provider
    try:
        if name == "mock":
            return MockProvider()
        if name == "openai":
            return OpenAiProvider()
        if name == "deepseek":
            return DeepSeekProvider()
        raise ValueError(f"未知 provider: {name}")
    except Exception as e:  # 缺少 key 等 → 回落 mock,保证可用
        print(f"[llm] provider '{name}' 不可用({e}),回落 MockProvider")
        return MockProvider()


def provider_status() -> dict:
    """当前 Provider 状态(供 API / 前端展示)。"""
    try:
        provider = get_provider()
        return {"configured": settings.llm_provider, "active": provider.name, "model": settings.llm_model}
    except Exception as e:  # pragma: no cover
        return {"configured": settings.llm_provider, "active": "mock", "error": str(e)}
