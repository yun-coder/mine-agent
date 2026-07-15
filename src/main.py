"""FastAPI 应用工厂 / FastAPI application factory."""

from __future__ import annotations

import os

# ⚠️ Langfuse OTel 追踪必须在任何 langchain/langgraph 导入前初始化
# 因为 langchain 会自动设置 NoopTracerProvider，导致后面注册的 exporter 无效
from src.agent.graph import _init_langfuse

_init_langfuse()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.shutdown import setup_graceful_shutdown
from src.config_validate import validate_dependencies
from src.metrics import init_metrics
from src.api.routes import router as api_router, openai_router, health_check


def create_app() -> FastAPI:
    app = FastAPI(
        title="LangGraph Agent Platform",
        description="Production-grade local Agent with LangGraph + OpenWebUI",
        version="1.0.0",
    )

    # CORS — 仅允许配置的源 / Only allow configured origins
    allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # 挂载 API 路由 / Mount API routes
    app.include_router(api_router)
    app.include_router(openai_router)

    # 初始化 Prometheus 指标 / Initialize Prometheus metrics
    init_metrics(app)

    # 注册优雅关闭 / Register graceful shutdown
    setup_graceful_shutdown()

    @app.get("/")
    def index():
        return {
            "service": "LangGraph Agent Platform",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/health",
            "agent_ask": "POST /api/v1/agent/ask",
            "agent_stream": "POST /api/v1/agent/stream",
        }

    @app.get("/health", include_in_schema=False)
    async def root_health():
        return await health_check()

    @app.get("/health/live", include_in_schema=False)
    async def liveness():
        return {"status": "ok"}

    # OpenAI 兼容的 models 端点 — 供 OpenWebUI 发现模型
    # OpenWebUI 请求的是 /models（不带 /v1 前缀）
    @app.get("/models")
    @app.get("/v1/models")
    def openai_models():
        return {
            "object": "list",
            "data": [
                {
                    "id": "langgraph-agent",
                    "object": "model",
                    "created": 1719000000,
                    "owned_by": "langgraph",
                    "name": "LangGraph Agent",
                    "description": "LangGraph 智能体平台 — 支持意图路由、RAG、代码搜索和工具调用",
                },
                {
                    "id": "qwen3:8b",
                    "object": "model",
                    "created": 1719000000,
                    "owned_by": "ollama",
                    "name": "Qwen 3 8B",
                    "description": "Ollama 本地模型",
                },
            ],
        }

    return app


app = create_app()


def create_and_validate_app() -> FastAPI:
    """创建应用并验证依赖 / Create app and validate dependencies."""
    app = create_app()
    logger.info("=" * 60)
    logger.info("正在验证服务依赖... / Validating service dependencies...")
    logger.info("=" * 60)
    try:
        validate_dependencies(required=False)
    except Exception as exc:
        logger.warning(f"依赖验证失败 (应用仍可启动): {exc}")
    logger.info("=" * 60)
    return app
