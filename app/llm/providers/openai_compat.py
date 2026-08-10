"""OpenAI 兼容 Provider(OpenAI / DeepSeek 均适用,仅 base_url 不同)。

使用 httpx 直接调用 chat completions 接口,无 SDK 依赖。
配置方式(.env):
  AIMAP_LLM_PROVIDER = openai
  OPENAI_API_KEY = sk-...
  或
  AIMAP_LLM_PROVIDER = deepseek
  DEEPSEEK_API_KEY = sk-...
"""
from __future__ import annotations

import httpx

from app.config import settings
from app.llm.base import BaseProvider, ChatMessage, ChatResult, ProviderError


class OpenAICompatProvider(BaseProvider):
    """OpenAI 兼容接口的通用实现。"""

    name = "openai-compat"

    def __init__(self, api_key: str, base_url: str, model: str, name: str = "openai-compat"):
        if not api_key:
            raise ProviderError(f"[{name}] 缺少 API key,请在 .env 中配置")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = name

    def chat(self, messages: list[ChatMessage], *, temperature: float = 0.2, max_tokens: int = 2048) -> ChatResult:
        payload = {
            "model": self.model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise ProviderError(f"[{self.name}] 请求失败: {e}") from e
        content = data["choices"][0]["message"]["content"]
        return ChatResult(content=content, raw=data)


class OpenAiProvider(OpenAICompatProvider):
    def __init__(self):
        super().__init__(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.llm_model,
            name="openai",
        )


class DeepSeekProvider(OpenAICompatProvider):
    def __init__(self):
        # 优先从 SecretVault 读取加密凭据(推荐),其次环境变量/.env
        from app.security import VaultError, get_vault

        try:
            api_key = get_vault().get("deepseek_api_key")
        except VaultError:
            api_key = settings.deepseek_api_key
        if not api_key:
            raise ProviderError(
                "[deepseek] 缺少 API key:请先运行 scripts/import_secret.py 导入,"
                "或在 .env 中设置 DEEPSEEK_API_KEY"
            )
        # llm_model 默认值是通用占位(gpt-4o-mini),对 DeepSeek 无效;
        # 仅当用户显式配置了 AIMAP_LLM_MODEL 时才沿用
        model = settings.llm_model if _explicit("AIMAP_LLM_MODEL") else "deepseek-chat"
        super().__init__(
            api_key=api_key,
            base_url=settings.deepseek_base_url,
            model=model,
            name="deepseek",
        )


def _explicit(env_name: str) -> bool:
    """判断某配置是否被用户显式设置且非空(.env 或环境变量)。"""
    import os

    from app.config import PROJECT_ROOT

    if env_name in os.environ and os.environ[env_name].strip():
        return True
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == env_name and value.strip():
                return True
    return False
