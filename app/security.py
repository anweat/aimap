"""安全凭据库(SecretVault):API key 等敏感信息加密存储,明文不落盘。

设计:
  - 密文存放于 <data_dir>/secrets/<name>.enc(Fernet 加密);
  - 主密钥(master key)不落明文:
      * Windows: 由系统 DPAPI(CryptProtectData)加密为 .master.dpapi,
        仅当前 Windows 用户可解密 —— 密钥与用户账户绑定,复制到其他
        机器/账户无法解出;
      * Linux:   存为 .master.key 且权限 600(部署到远程云时生效);
  - 启动时按需解密,日志与调试输出中不出现明文;
  - 换机器/换用户导致解密失败时抛 VaultError,提示重新导入。

用法:
  vault = get_vault()
  vault.set("deepseek_api_key", "sk-...")   # 导入(一次性)
  key = vault.get("deepseek_api_key")        # 运行时读取
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_DPAPI = sys.platform == "win32"


class VaultError(Exception):
    """凭据库错误(解密失败 / 未找到等)。"""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    @classmethod
    def from_bytes(cls, data: bytes) -> "_DataBlob":
        buf = ctypes.create_string_buffer(data, len(data))
        return cls(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def to_bytes(self) -> bytes:
        return ctypes.string_at(self.pbData, self.cbData)


def _dpapi_protect(data: bytes) -> bytes:
    blob_in = _DataBlob.from_bytes(data)
    blob_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise VaultError(f"DPAPI 加密失败 (error {ctypes.get_last_error()})")
    try:
        return blob_out.to_bytes()
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    blob_in = _DataBlob.from_bytes(data)
    blob_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise VaultError("DPAPI 解密失败:凭据与当前 Windows 用户/机器不匹配,请重新导入")
    try:
        return blob_out.to_bytes()
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


class SecretVault:
    """基于 Fernet 的凭据库,主密钥由平台能力保护。"""

    def __init__(self, vault_dir: Path):
        self.vault_dir = Path(vault_dir)
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self._key_bytes: bytes | None = None

    # ---- 主密钥 ----------------------------------------------------------
    def _master_key(self) -> bytes:
        if self._key_bytes is not None:
            return self._key_bytes

        if _DPAPI:
            dpapi_file = self.vault_dir / ".master.dpapi"
            if not dpapi_file.exists():
                key = Fernet.generate_key()
                dpapi_file.write_bytes(_dpapi_protect(key))
                try:
                    dpapi_file.chmod(0o600)
                except OSError:
                    pass
            self._key_bytes = _dpapi_unprotect(dpapi_file.read_bytes())
        else:
            key_file = self.vault_dir / ".master.key"
            if not key_file.exists():
                key_file.write_bytes(Fernet.generate_key())
                key_file.chmod(0o600)
            self._key_bytes = key_file.read_bytes()
        return self._key_bytes

    # ---- 增删查 ----------------------------------------------------------
    def set(self, name: str, value: str) -> None:
        """加密存储;value 为明文,仅存在于内存。"""
        if not value:
            raise VaultError("拒绝存储空凭据")
        token = Fernet(self._master_key()).encrypt(value.encode("utf-8"))
        (self.vault_dir / f"{name}.enc").write_bytes(token)

    def get(self, name: str) -> str:
        """解密读取;失败(密钥不匹配等)抛 VaultError。"""
        path = self.vault_dir / f"{name}.enc"
        if not path.exists():
            raise VaultError(f"凭据 {name} 不存在,请运行 scripts/import_secret.py 导入")
        try:
            raw = Fernet(self._master_key()).decrypt(path.read_bytes())
        except InvalidToken as e:
            raise VaultError(f"凭据 {name} 解密失败:主密钥不匹配(跨用户/跨机器),请重新导入") from e
        return raw.decode("utf-8")

    def has(self, name: str) -> bool:
        return (self.vault_dir / f"{name}.enc").exists()

    def delete(self, name: str) -> None:
        path = self.vault_dir / f"{name}.enc"
        if path.exists():
            path.unlink()

    def list_names(self) -> list[str]:
        return sorted(p.name[: -len(".enc")] for p in self.vault_dir.glob("*.enc"))


def get_vault() -> SecretVault:
    """获取全局凭据库实例(目录由配置决定)。"""
    from app.config import settings

    return SecretVault(settings.data_dir / "secrets")
