"""全局配置 / 环境变量管理"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class OrchestraSettings(BaseSettings):
    """Orchestra 全局配置，从 .env 文件加载 LLM API Keys 等。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Provider API Keys
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""

    # 自定义 base URL（用于代理或兼容 API）
    openai_base_url: Optional[str] = None
    anthropic_base_url: str = "https://api.anthropic.com"
    deepseek_base_url: str = "https://api.deepseek.com"

    # 服务配置
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"

    # LLM 默认参数
    default_model: str = "GPT-4o"
    max_context_tokens: int = 16000
    pipeline_timeout_seconds: int = 300

    # 数据目录
    data_dir: str = str(Path(__file__).resolve().parent.parent / "data")

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def anthropic_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def any_llm_configured(self) -> bool:
        return self.openai_configured or self.anthropic_configured or self.deepseek_configured
