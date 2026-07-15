"""配置验证 — 启动时检查所有必需服务 / Startup dependency validation.

在应用启动前验证所有下游服务的可达性。
Validates reachability of all downstream services before the app starts.
"""

from __future__ import annotations

from loguru import logger


def validate_dependencies(required: bool = True) -> dict[str, bool]:
    """验证所有必需服务的连通性 / Validate connectivity of all required services.

    Args:
        required: 如果为 True 且某服务不可达则抛出异常 / Raise if a service is unreachable

    Returns:
        各服务的连通性状态字典 / Dict of service connectivity status
    """
    from src.config import settings

    results = {}

    # 1. Ollama LLM / Ollama LLM
    try:
        import httpx
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{settings.ollama_base_url}/api/tags")
            if r.status_code == 200:
                models = r.json().get("models", [])
                llm_model = settings.llm_model
                if llm_model and any(llm_model in m.get("name", "") for m in models):
                    logger.info(f"[配置 / Config] Ollama LLM: 已连接 (模型={llm_model}) / connected (model={llm_model})")
                    results["ollama_llm"] = True
                else:
                    logger.warning(f"[配置 / Config] Ollama 已连接但缺少模型 {llm_model} / Connected but missing model {llm_model}")
                    results["ollama_llm"] = False
            else:
                results["ollama_llm"] = False
    except Exception as exc:
        logger.error(f"[配置 / Config] Ollama LLM 不可达 / unreachable: {exc}")
        results["ollama_llm"] = False

    # 2. Ollama Embedder / Ollama Embedder
    try:
        with httpx.Client(timeout=5) as c:
            r = c.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={"model": settings.embed_model, "prompt": "health check"},
            )
            results["ollama_embed"] = r.status_code == 200
            if results["ollama_embed"]:
                logger.info("[配置 / Config] Ollama Embedder: 已连接 / connected")
            else:
                logger.warning(f"[配置 / Config] Ollama Embedder: 不可达 / unreachable (status={r.status_code})")
    except Exception as exc:
        logger.error(f"[配置 / Config] Ollama Embedder 不可达 / unreachable: {exc}")
        results["ollama_embed"] = False

    # 3. Qdrant / Qdrant
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=5)
        client.get_collections()
        results["qdrant"] = True
        logger.info(f"[配置 / Config] Qdrant: 已连接 / connected ({settings.qdrant_host}:{settings.qdrant_port})")
    except Exception as exc:
        logger.error(f"[配置 / Config] Qdrant 不可达 / unreachable: {exc}")
        results["qdrant"] = False

    # 4. Langfuse (optional) / Langfuse (optional)
    if settings.langfuse_public_key:
        try:
            with httpx.Client(timeout=5) as c:
                r = c.get(f"{settings.langfuse_host}/api/public/health")
                results["langfuse"] = r.status_code == 200
                if results["langfuse"]:
                    logger.info("[配置 / Config] Langfuse: 已连接 / connected")
                else:
                    logger.warning(f"[配置 / Config] Langfuse: 不可达 / unreachable (status={r.status_code})")
        except Exception as exc:
            logger.warning(f"[配置 / Config] Langfuse 不可达 / unreachable: {exc}")
            results["langfuse"] = False
    else:
        logger.info("[配置 / Config] Langfuse: 未配置，跳过 / not configured, skipping")
        results["langfuse"] = True  # 可选 / Optional

    # 5. Postgres checkpointer (optional) / Postgres checkpointer (optional)
    if settings.pg_dsn:
        try:
            import psycopg2
            dsn = settings.pg_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
            conn = psycopg2.connect(dsn, connect_timeout=5)
            conn.close()
            results["postgres"] = True
            logger.info("[配置 / Config] PostgreSQL: 已连接 / connected")
        except Exception as exc:
            logger.warning(f"[配置 / Config] PostgreSQL 不可达 / unreachable: {exc}")
            results["postgres"] = False
    else:
        logger.info("[配置 / Config] PostgreSQL: 未配置，使用内存检查点 / not configured, using memory checkpointer")
        results["postgres"] = not settings.checkpoint_required

    # 总结 / Summary
    failed = [k for k, v in results.items() if not v]
    if failed:
        logger.warning(f"[配置 / Config] 以下服务不可用: {', '.join(failed)} / The following services are unavailable: {', '.join(failed)}")
        if required:
            raise RuntimeError(f"必需服务不可用: {', '.join(failed)} / Required services unavailable: {', '.join(failed)}")
    else:
        logger.info("[配置 / Config] 所有必需服务已连接 / All required services connected")

    return results
