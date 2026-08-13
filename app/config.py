"""全局配置:从环境变量 / .env 文件读取。

所有 API key、账号密码均通过此处注入,代码中不出现任何密钥。
环境变量前缀为 AIMAP_(如 AIMAP_LLM_PROVIDER);OPENAI_API_KEY /
DEEPSEEK_API_KEY 为兼容惯例的别名,两种写法均有效。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="AIMAP_",
        extra="ignore",
    )

    # ---- 应用 ----
    db_path: Path = PROJECT_ROOT / "data" / "aimap.db"
    data_dir: Path = PROJECT_ROOT / "data"  # 数据目录(DB/凭据库/会话)
    cors_origins: str = "*"

    # ---- LLM ----
    llm_provider: str = "mock"  # mock | openai | deepseek
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENAI_API_KEY", "AIMAP_OPENAI_API_KEY")
    )
    openai_base_url: str = "https://api.openai.com/v1"
    deepseek_api_key: str = Field(
        default="", validation_alias=AliasChoices("DEEPSEEK_API_KEY", "AIMAP_DEEPSEEK_API_KEY")
    )
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # ---- 图书馆账号(预留)----
    lib_ieee_account: str = ""
    lib_ieee_password: str = ""
    lib_acm_account: str = ""
    lib_acm_password: str = ""
    lib_cnki_account: str = ""
    lib_cnki_password: str = ""

    # ---- 爬虫 ----
    arxiv_delay: float = 3.0
    arxiv_max_results: int = 200
    # arXiv API 端点(网络受限时可改用镜像/代理)
    arxiv_api: str = "https://export.arxiv.org/api/query"
    # 图书馆登录会话有效期(小时)
    session_ttl_hours: int = 24 * 7
    # 资源获取规范(所有数据源统一):限流/重试/熔断
    crawl_min_interval: float = 3.0      # 每来源最小请求间隔(秒)
    crawl_max_retries: int = 3           # 最大重试次数
    crawl_backoff_base: float = 2.0      # 退避基数(秒)
    crawl_backoff_max: float = 60.0      # 退避上限(秒)
    crawl_circuit_threshold: int = 5     # 熔断阈值(连续失败次数)
    crawl_circuit_cooldown: float = 300.0  # 熔断冷却(秒)

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # 图书馆数据源名(凭据/会话/检索均以此为键)
    LIBRARY_SOURCES: tuple[str, ...] = ("ieee", "acm", "cnki")

    def _env_credentials(self) -> dict[str, dict[str, str]]:
        """从 .env 读取的图书馆凭据(作为回退来源)。"""
        return {
            "ieee": {"account": self.lib_ieee_account, "password": self.lib_ieee_password},
            "acm": {"account": self.lib_acm_account, "password": self.lib_acm_password},
            "cnki": {"account": self.lib_cnki_account, "password": self.lib_cnki_password},
        }

    @property
    def library_credentials(self) -> dict[str, dict[str, str]]:
        """图书馆凭据汇总。

        优先级:SecretVault(数据源面板配置,加密存储)> .env(回退)。
        任一来源读不到明文(跨机器/密钥缺失)时静默回退 .env,不抛异常。
        """
        creds = self._env_credentials()
        try:
            from app.security import get_vault

            vault = get_vault()
            for lib in creds:
                for field in ("account", "password"):
                    name = f"lib_{lib}_{field}"
                    if vault.has(name):
                        creds[lib][field] = vault.get(name)
        except Exception:
            pass  # SecretVault 不可用时保持 .env 回退
        return creds

    def has_library_credential(self, source: str) -> bool:
        """某图书馆源是否已配置账号(SecretVault 或 .env)。"""
        try:
            from app.security import get_vault

            if get_vault().has(f"lib_{source}_account"):
                return True
        except Exception:
            pass
        return bool(self._env_credentials().get(source, {}).get("account"))

    def masked_library_account(self, source: str) -> str:
        """脱敏账号(前 3 后 2),供界面展示;未配置返回空串。"""
        account = self.library_credentials.get(source, {}).get("account", "") or ""
        if not account:
            return ""
        if len(account) <= 6:
            return account[0] + "*" * (len(account) - 1)
        return f"{account[:3]}···{account[-2:]}"

    def store_library_credential(self, source: str, account: str | None = None,
                                 password: str | None = None) -> None:
        """将图书馆凭据写入 SecretVault(加密存储,明文不落盘)。

        None 表示不修改;空字符串表示删除该字段。
        """
        if source not in self.LIBRARY_SOURCES:
            raise ValueError(f"未知图书馆数据源: {source}")
        from app.security import get_vault

        vault = get_vault()
        if account is not None:
            if account:
                vault.set(f"lib_{source}_account", account)
            else:
                vault.delete(f"lib_{source}_account")
        if password is not None:
            if password:
                vault.set(f"lib_{source}_password", password)
            else:
                vault.delete(f"lib_{source}_password")


settings = Settings()
