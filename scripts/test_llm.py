"""LLM 连接实测:验证凭据、探测可用模型、单次对话测试。

用法:
  python scripts/test_llm.py                 # 用配置的 provider/模型
  python scripts/test_llm.py --provider deepseek --model deepseek-flash
  python scripts/test_llm.py --probe         # 探测 DeepSeek 可用模型列表

输出脱敏:不打印完整 key。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


def probe_deepseek_models(api_key: str) -> list[str]:
    """列出 DeepSeek 账号可用的模型名(OpenAI 兼容 /models 端点)。"""
    resp = httpx.get(
        "https://api.deepseek.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", [])]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="LLM 连接实测")
    parser.add_argument("--provider", default=None, help="deepseek | openai | mock(默认取配置)")
    parser.add_argument("--model", default=None, help="覆盖模型名")
    parser.add_argument("--probe", action="store_true", help="探测 DeepSeek 可用模型")
    parser.add_argument("--no-mask", action="store_true", help="打印完整 key(仅调试,慎用)")
    args = parser.parse_args()

    from app.config import settings
    from app.llm.base import ChatMessage
    from app.llm.registry import get_provider
    from app.security import VaultError, get_vault

    # 凭据来源展示(脱敏)
    try:
        key = get_vault().get("deepseek_api_key")
        print(f"[key] 来源: SecretVault(加密存储) 摘要: {key[:4]}···{key[-2:]} (长度 {len(key)})")
    except VaultError:
        key = settings.deepseek_api_key
        print(f"[key] 来源: 环境变量/.env 摘要: {key[:4]}···{key[-2:]} (长度 {len(key)})" if key else "[key] 未配置")

    if args.probe:
        if not key:
            raise SystemExit("未找到 DeepSeek key,无法探测")
        try:
            models = probe_deepseek_models(key)
        except Exception as e:
            raise SystemExit(f"探测失败: {e}")
        print(f"[probe] DeepSeek 可用模型({len(models)}):")
        for m in models:
            print(f"   - {m}")
        return

    provider = get_provider(args.provider)
    model = args.model or settings.llm_model
    print(f"[llm] provider={provider.name} model={model}")
    try:
        result = provider.chat(
            [ChatMessage(role="user", content="请回复 OK 两个字,不要其他内容。")],
            temperature=0.0,
            max_tokens=32,
        )
    except Exception as e:
        print(f"[FAIL] 调用失败: {type(e).__name__}: {e}")
        if "model" in str(e).lower() or "not exist" in str(e).lower():
            print("提示:模型名可能不正确,运行 --probe 查看可用模型后重试")
        raise SystemExit(1)
    print(f"[ok] 返回内容: {result.content[:120]!r}")
    print("[ok] LLM 链路可用")


if __name__ == "__main__":
    main()
