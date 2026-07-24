"""FastAPI 路由 —— REST API + WebSocket 端点。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from ..models import WSMessage, resolve_provider, AuditEvent
from ..ws_manager import WSManager
from ..orchestrator import MasterOrchestrator
from ..agents.registry import AgentRegistry
from ..memory.store import HandoffLog
from ..pipeline.engine import PipelineEngine

router = APIRouter()

# ── 全局引用（由 lifespan 注入） ──────────────────────────────

_ws_manager: WSManager = None
_orchestrator: MasterOrchestrator = None
_agent_registry: AgentRegistry = None
_handoff_log: HandoffLog = None


def init_app_state(
    ws_manager: WSManager,
    orchestrator: MasterOrchestrator,
    agent_registry: AgentRegistry,
    handoff_log: HandoffLog,
) -> None:
    """在应用启动时注入全局服务引用。"""
    global _ws_manager, _orchestrator, _agent_registry, _handoff_log
    _ws_manager = ws_manager
    _orchestrator = orchestrator
    _agent_registry = agent_registry
    _handoff_log = handoff_log


# ── WebSocket 端点 ─────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """唯一的 WebSocket 端点。

    浏览器建立此连接后，所有消息通过 agent_id 字段多路复用。
    """
    ws_mgr = _ws_manager
    orch = _orchestrator
    registry = _agent_registry
    log = _handoff_log

    await ws_mgr.connect(ws)

    # 连接时发送完整状态
    try:
        await orch.broadcast_state()
    except Exception:
        pass

    # 心跳 ping
    ping_task = asyncio.create_task(_ping_loop(ws, ws_mgr))

    try:
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)
            message = WSMessage(**data)

            match message.type:
                case "command":
                    cmd = message.payload.get("command", "")
                    agent_id = message.agent_id or "master"
                    await orch.handle_command(agent_id, cmd)

                case "agent:config":
                    agent_id = message.agent_id
                    if agent_id:
                        updates = message.payload.get("config", {})
                        await orch.update_agent_config(agent_id, updates)

                case "pipeline:start":
                    desc = message.payload.get("description", "")
                    await orch.start_pipeline(desc)

                case "pipeline:pause":
                    await orch.pause_pipeline()

                case "pipeline:resume":
                    await orch.resume_pipeline()

                case "pipeline:stop":
                    await orch.stop_pipeline()

                case "agent:add":
                    cfg = message.payload.get("config", {})
                    await orch.add_custom_agent(
                        name=cfg.get("name", "自定义"),
                        role=cfg.get("role", "通用角色"),
                        model=cfg.get("model", "GPT-4o"),
                        task=cfg.get("task", ""),
                    )

                case "agent:remove":
                    agent_id = message.agent_id
                    if agent_id:
                        registry.remove(agent_id)
                        await orch.broadcast_state()

                case "system:reset":
                    # 重置所有
                    orch.pipeline = PipelineEngine(registry)
                    log.clear()
                    for agent in registry._agents.values():
                        agent.state.status = "idle"
                        agent.state.output = ""
                    orch._running = False
                    orch._paused = False
                    await orch.emit_event(
                        "master", "主控", "工作区已重置", "所有 Agent 状态已恢复", "info"
                    )
                    await orch.broadcast_state()

                case "ping":
                    await ws_mgr.send_to(
                        ws, WSMessage(type="pong", payload={}).model_dump()
                    )

    except WebSocketDisconnect:
        ping_task.cancel()
        await ws_mgr.disconnect(ws)
    except Exception:
        ping_task.cancel()
        await ws_mgr.disconnect(ws)


async def _ping_loop(ws: WebSocket, ws_manager: WSManager):
    """WebSocket 心跳保活。"""
    try:
        while True:
            await asyncio.sleep(25)
            try:
                await ws.send_json({"type": "ping"})
            except Exception:
                break
    except asyncio.CancelledError:
        pass


# ── REST API（回退/调试用） ────────────────────────────────

@router.get("/api/agents")
async def get_agents(request: Request):
    """获取所有 Agent 状态。"""
    manifests = _agent_registry.all_manifests()
    return {"agents": [m.model_dump() for m in manifests]}


@router.get("/api/events")
async def get_events(request: Request):
    """获取最近的审计事件。"""
    entries = _handoff_log.recent(50)
    return {
        "events": [
            {
                "id": e.id,
                "time": e.timestamp,
                "from": e.from_agent_name,
                "to": e.to_agent_name,
                "summary": e.result_summary,
            }
            for e in entries
        ]
    }


@router.get("/api/status")
async def get_status(request: Request):
    """获取系统状态。"""
    return {
        "running": _orchestrator.is_running,
        "paused": _orchestrator.is_paused,
        "agent_count": len(_agent_registry.all_manifests()),
        "event_count": _orchestrator.system_event_count,
    }
