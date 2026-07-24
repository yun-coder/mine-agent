"""配置加载器 — 从 .env 和环境变量读取 / Configuration loader — reads from .env and environment variables."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ollama 配置 / Ollama
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:8b"

    # Qdrant 配置 / Qdrant
    qdrant_host: str = "127.0.0.1"
    qdrant_port: int = 6333
    qdrant_collection: str = "enterprise_kb"

    # 路径配置 / Paths（优先从环境变量读取 / Prefer env var, fallback to sensible default）
    docs_dir: Path = Path(os.environ.get("DOCS_DIR", "/app/assets"))
    project_root: Path = Path(os.environ.get("PROJECT_ROOT", "/app"))
    log_dir: Path = Path(os.environ.get("LOG_DIR", "/app/logs"))

    # 智能体调优参数 / Agent tuning
    max_iterations: int = 5
    max_agent_retries: int = 1        # 智能体重试次数 / Agent retry count
    top_k_rag: int = 10
    stream_chunk_size: int = 50

    # 嵌入模型 / Embedding model
    embed_model: str = "bge-m3"          # Ollama 嵌入模型名称
    embed_dim: int = 1024                # 嵌入向量维度

    # RAG 检索增强 / RAG retrieval enhancement
    rag_use_hybrid_search: bool = True   # 开启向量+BM25+RRF混合检索
    rag_use_reranker: bool = False       # 开启BGE Reranker重排序
    rag_use_query_rewrite: bool = False  # 开启LLM查询改写
    rag_rewrite_model: str = ""          # 查询改写使用的模型(空=与主模型一致)
    rag_rerank_top_k: int = 5            # 重排序后保留的结果数
    rag_rrf_k: int = 60                  # RRF融合参数
    rag_bm25_path: str = ""              # BM25索引缓存路径(空=自动)
    rag_rewrite_timeout: float = 10.0    # 查询改写超时(秒)

    # Langfuse 配置 / Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://127.0.0.1:3001"

    # Postgres 检查点 / Postgres checkpointer
    pg_dsn: str = ""

    # ============================================================
    # TencentDB Agent Memory (TDAI) 配置
    # ============================================================
    # TDAI Gateway 地址（Docker 内：tdai-memory:8420，本地：127.0.0.1:8420）
    tdai_gateway_url: str = "http://tdai-memory:8420"
    # 启用记忆存取（关闭可跳过，不影响现有流程）
    tdai_enabled: bool = True
    # 记忆召回的 top_k
    tdai_recall_top_k: int = 5
    # 保存会话时使用的 user_id
    tdai_user_id: str = "kb-agent-default"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
