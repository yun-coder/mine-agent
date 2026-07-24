"""FastAPI 应用工厂 —— 创建和配置应用实例。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import OrchestraSettings
from .ws_manager import WSManager
from .agents.registry import AgentRegistry
from .orchestrator import MasterOrchestrator
from .memory.store import HandoffLog
from .memory.context import ContextStore
from .memory.bus import MessageBus
from .web.routes import router, init_app_state
from .llm.registry import init_ws_manager as init_llm_ws_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = OrchestraSettings()
    ws_manager = WSManager()
    handoff_log = HandoffLog()
    context_store = ContextStore(max_tokens=settings.max_context_tokens)
    message_bus = MessageBus()
    agent_registry = AgentRegistry(ws_manager=ws_manager, message_bus=message_bus)
    orchestrator = MasterOrchestrator(
        agent_registry, ws_manager, handoff_log, message_bus=message_bus,
    )

    # 注入 WebSocket 管理器到 LLM 工厂（供 RouterProvider 发送回退事件）
    init_llm_ws_manager(ws_manager)

    orchestrator.register_default_agents()

    # 注入全局引用（供 WebSocket 路由使用）
    init_app_state(
        ws_manager=ws_manager,
        orchestrator=orchestrator,
        agent_registry=agent_registry,
        handoff_log=handoff_log,
    )

    app.state.settings = settings
    app.state.ws_manager = ws_manager
    app.state.context_store = context_store
    app.state.handoff_log = handoff_log
    app.state.agent_registry = agent_registry
    app.state.orchestrator = orchestrator

    yield


PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Orchestra",
        description="多智能体协同控制台",
        version="0.2.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API 和 WebSocket 路由
    app.include_router(router)

    # 静态文件路由 (SPA)
    if PUBLIC_DIR.exists():
        index_path = PUBLIC_DIR / "index.html"

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str):
            """提供 SPA 文件，支持前端路由。"""
            # 首先尝试精确文件匹配
            file_path = PUBLIC_DIR / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))

            # 如果是 API 路径，跳过（已在 router 中处理）
            if full_path.startswith("api/") or full_path.startswith("ws"):
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=404, content={"detail": "Not found"})

            # 返回 index.html（SPA fallback）
            if index_path.exists():
                return FileResponse(str(index_path))

            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"detail": "Not found"})

    return app
