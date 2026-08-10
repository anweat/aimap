"""保密导入 API key 到 SecretVault(加密存储,明文不落盘、不输出)。

用法:
  python scripts/import_secret.py --source <密钥文件路径> --name deepseek_api_key
  python scripts/import_secret.py --prompt deepseek_api_key     # 交互式输入

说明:
  - 源文件内容不会被打印(仅显示脱敏摘要:长度与前 4 后 2 字符);
  - Windows 下主密钥由系统 DPAPI 保护(绑定当前用户);
  - 导入后可运行 scripts/test_llm.py 验证。
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import SecretVault, VaultError, get_vault


def mask(value: str) -> str:
    """脱敏显示:sk-abc...xy。"""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}···{value[-2:]}"


def read_from_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"源文件不存在: {p}")
    content = p.read_text(encoding="utf-8", errors="replace").strip()
    # 兼容 "KEY=xxx" / "xxx" 两种文件格式
    if "=" in content.splitlines()[0] and len(content.splitlines()) == 1:
        content = content.split("=", 1)[1].strip().strip('"').strip("'")
    if not content:
        raise SystemExit(f"源文件内容为空: {p}")
    return content


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="保密导入密钥到 SecretVault")
    parser.add_argument("--source", help="密钥文件路径(如 F:/Desktop/token/deepseek-api.txt)")
    parser.add_argument("--name", default="deepseek_api_key", help="凭据名称")
    parser.add_argument("--vault-dir", help="自定义凭据库目录(默认 data/secrets)")
    args = parser.parse_args()

    if args.source:
        value = read_from_file(args.source)
    else:
        value = getpass.getpass("粘贴密钥(输入不回显): ").strip()
    if not value:
        raise SystemExit("密钥为空,已取消")

    vault = SecretVault(args.vault_dir) if args.vault_dir else get_vault()
    vault.set(args.name, value)

    print(f"[ok] 凭据已加密存入 SecretVault: {args.name}")
    print(f"     位置: {vault.vault_dir} (主密钥受系统保护,明文不落盘)")
    print(f"     摘要: {mask(value)} (长度 {len(value)})")
    if args.name == "deepseek_api_key":
        print("提示:运行 scripts/test_llm.py 验证 DeepSeek 连接")


if __name__ == "__main__":
    main()
