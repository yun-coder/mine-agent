"""配置加载,统一入口"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ollama
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:8b"
    embed_model: str = "bge-m3"

    # Qdrant
    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    qdrant_collection: str = "enterprise_kb"

    # 数据路径（优先从环境变量读取 / Prefer env var）
    docs_dir: Path = Path(os.environ.get("DOCS_DIR", "/app/output"))
    qdrant_data_dir: Path = Path(os.environ.get("QDRANT_DATA_DIR", "/data/qdrant"))
    hf_cache_dir: Path = Path(os.environ.get("HF_CACHE_DIR", "/data/hf_cache"))
    log_dir: Path = Path(os.environ.get("LOG_DIR", "/app/logs"))

    # 分块
    chunk_size: int = 512
    chunk_overlap: int = 64

    # 检索
    top_k_vector: int = 20
    top_k_bm25: int = 20
    top_k_rrf: int = 10
    top_k_rerank: int = 5
    rrf_k: int = 60
    max_context_tokens: int = 4000
    use_reranker: bool = False
    use_query_rewrite: bool = False  # 默认关闭, 避免 LLM 改写耗时

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
settings.log_dir.mkdir(parents=True, exist_ok=True)
settings.docs_dir.mkdir(parents=True, exist_ok=True)
