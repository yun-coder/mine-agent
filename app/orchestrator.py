"""MasterOrchestrator —— 系统主控编排器。

职责:
1. 管理流水线生命周期（启动、暂停、继续、停止）
2. Agent 间的任务分发与交接
3. 前端 WebSocket 命令路由
4. 系统状态广播与审计事件
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from .agents.base import BaseAgent
from .agents.registry import AgentRegistry
from .models import (
    AgentStatus,
    AuditEvent,
    Task,
    TaskResult,
    WSMessage,
)
from .ws_manager import WSManager
from .memory.store import HandoffLog
from .memory.bus import MessageBus
from .pipeline.engine import PipelineEngine, PipelineNode


class MasterOrchestrator:
    """主控编排器 —— 所有 Agent 的上级管理者。"""

    def __init__(
        self,
        registry: AgentRegistry,
        ws_manager: WSManager,
        handoff_log: Optional[HandoffLog] = None,
        message_bus: Optional[MessageBus] = None,
    ):
        self.registry = registry
        self.ws = ws_manager
        self.pipeline = PipelineEngine(registry)
        self.handoff_log = handoff_log or HandoffLog()
        self.message_bus = message_bus or MessageBus()
        self._running = False
        self._paused = False
        self._stop_requested = False
        self._current_task: Optional[Task] = None

    # ── 属性 ──────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def system_event_count(self) -> int:
        return len(self.handoff_log.all())

    # ── 初始化 ────────────────────────────────────────────

    def register_default_agents(self) -> None:
        """注册 6 个默认 Agent。"""
        self.registry.register_defaults()

    async def add_custom_agent(self, name: str, role: str, model: str, task: str) -> BaseAgent:
        """添加自定义 Agent 并广播状态。"""
        agent = self.registry.add_custom(name, role, model, task)
        await self.emit_event(
            agent_id="master",
            agent_name="主控",
            title=f"新建窗口: {agent.config.code} / {name}",
            detail=f"角色: {role} · 模型: {model}",
            event_type="info",
        )
        await self.broadcast_state()
        return agent

    # ── 流水线控制 ────────────────────────────────────────

    async def start_pipeline(self, task_description: str = "") -> None:
        """启动流水线执行。"""
        if self._running:
            await self.emit_event(
                "master", "主控",
                "流水线已在运行中", "请先停止当前流水线", "error",
            )
            return

        self._running = True
        self._paused = False
        self._stop_requested = False

        await self.emit_event(
            "master", "主控",
            "流水线已启动",
            task_description or "主控正在分发任务图",
            "success",
        )
        await self.broadcast_pipeline_status()

        # 编译执行图
        self.pipeline.compile(task_description or "执行默认工作流")

        # 执行
        try:
            await self._execute_pipeline()
        except Exception as e:
            await self.emit_event(
                "master", "主控",
                "流水线执行异常",
                f"错误: {e}",
                "error",
            )
        finally:
            self._running = False
            await self.emit_event(
                "master", "主控",
                "流水线已完成",
                "所有已启用窗口都返回了可审查结果",
                "success",
            )
            await self.broadcast_pipeline_status()

    async def _execute_pipeline(self) -> None:
        """遍历流水线执行组（支持并发组）。"""
        for group in self.pipeline.execution_groups:
            if self._stop_requested:
                break

            if len(group) > 1:
                # 并行组：asyncio.gather 实现真正并发
                tasks = [self._run_node(node) for node in group]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for node, result in zip(group, results):
                    if isinstance(result, Exception):
                        await self.emit_event(
                            agent_id=node.agent_id,
                            agent_name=self.registry.get_config(node.agent_id).name
                            if self.registry.get_config(node.agent_id) else "",
                            title=f"Agent {node.agent_id} 并行执行异常",
                            detail=str(result),
                            event_type="error",
                        )
            else:
                # 串行组：逐个执行
                await self._run_node(group[0])

    async def _run_node(self, node: PipelineNode) -> None:
        """执行单个流水线节点。"""
        if self._stop_requested:
            return

        # 暂停等待
        while self._paused and not self._stop_requested:
            await asyncio.sleep(0.5)

        if self._stop_requested:
            return

        agent = self.registry.get(node.agent_id)
        if agent is None:
            return

        # 构建任务 —— 携带前置上下文
        task = Task(
            type=node.task_type,
            description=node.description,
            payload=node.payload,
            source_agent_id="master",
            target_agent_id=node.agent_id,
            handoff_context=self.pipeline.collect_context(node.agent_id),
        )

        self._current_task = task

        # 激活 Agent
        await self.emit_event(
            agent_id=agent.config.id,
            agent_name=agent.config.name,
            title=f"Agent {agent.config.code} / {agent.config.name} 已激活",
            detail=f"模型：{agent.config.model} · 模式：{agent.config.mode}",
            event_type="info",
        )

        try:
            result = await agent.run(task)
            self.pipeline.store_result(node.agent_id, result)

            # 记录交接
            if node.next_agent_id:
                next_config = self.registry.get_config(node.next_agent_id)
                next_name = next_config.name if next_config else ""
                self.handoff_log.record(
                    from_agent_id=node.agent_id,
                    from_agent_name=agent.config.name,
                    to_agent_id=node.next_agent_id,
                    to_agent_name=next_name,
                    task_id=task.id,
                    result_summary=result.summary,
                    token_used=result.token_used,
                )
                await self.emit_event(
                    agent_id=agent.config.id,
                    agent_name=agent.config.name,
                    title="交接结果已发出",
                    detail=f"handoff://{agent.config.name}/result-{node.agent_id}",
                    event_type="handoff",
                )
        except Exception as e:
            await self.emit_event(
                agent_id=agent.config.id,
                agent_name=agent.config.name,
                title=f"Agent {agent.config.name} 执行失败",
                detail=str(e),
                event_type="error",
            )

    async def pause_pipeline(self) -> None:
        if not self._running:
            return
        self._paused = True
        await self.emit_event("master", "主控", "流水线已暂停", "任务队列已由操作员暂停", "info")
        await self.broadcast_pipeline_status()

    async def resume_pipeline(self) -> None:
        if not self._running:
            return
        self._paused = False
        await self.emit_event("master", "主控", "流水线已继续", "任务队列已恢复分发", "info")
        await self.broadcast_pipeline_status()

    async def stop_pipeline(self) -> None:
        self._stop_requested = True
        self._paused = False
        # 重置正在运行的 Agent 状态
        for agent in self.registry.enabled_agents():
            if agent.state.status == AgentStatus.running:
                agent.state.status = AgentStatus.idle
        await self.emit_event("master", "主控", "流水线已停止", "操作员手动终止", "info")
        await self.broadcast_pipeline_status()

    # ── 命令路由 ──────────────────────────────────────────

    async def handle_command(self, agent_id: str, command: str) -> None:
        """处理来自前端的命令。"""
        if agent_id == "master":
            await self._handle_master_command(command)
        else:
            agent = self.registry.get(agent_id)
            if agent is None:
                await self._send_error(f"Agent {agent_id} 不存在")
                return

            # 创建一个轻量任务来执行命令
            task = Task(
                type="command",
                description=command,
                source_agent_id="master",
                target_agent_id=agent_id,
            )
            try:
                await agent.run(task)
            except Exception as e:
                await self._send_error(f"Agent {agent_id} 命令执行失败: {e}")

    async def _handle_master_command(self, command: str) -> None:
        """处理主控终端命令。"""
        parts = command.strip().split()
        if not parts:
            return

        cmd = parts[0].lower()
        args = parts[1:]

        match cmd:
            case "run":
                await self.start_pipeline(" ".join(args))
            case "pause":
                await self.pause_pipeline()
            case "resume" | "continue":
                await self.resume_pipeline()
            case "stop" | "halt":
                await self.stop_pipeline()
            case "status":
                await self.broadcast_state()
            case "help":
                await self._send_help()
            case "reset":
                self.pipeline = PipelineEngine(self.registry)
                self.handoff_log = HandoffLog()
                self._running = False
                self._paused = False
                for agent in self.registry._agents.values():
                    agent.state.status = AgentStatus.idle
                    agent.state.output = ""
                await self.emit_event("master", "主控", "工作区已重置", "所有 Agent 状态已恢复", "info")
                await self.broadcast_state()
            case _:
                await self._send_error(f"未知命令: {command}")

    async def _send_help(self) -> None:
        text = (
            "可用命令：\n"
            "  run <描述>    — 启动流水线\n"
            "  pause         — 暂停流水线\n"
            "  resume        — 继续流水线\n"
            "  stop          — 停止流水线\n"
            "  status        — 显示当前状态\n"
            "  reset         — 重置工作区\n"
            "  help          — 显示此帮助"
        )
        await self.ws.broadcast(
            WSMessage(
                type="output",
                agent_id="master",
                payload={"token": text, "done": True},
            ).model_dump()
        )

    async def _send_error(self, message: str) -> None:
        await self.emit_event("master", "主控", "命令错误", message, "error")

    # ── 配置更新 ──────────────────────────────────────────

    async def update_agent_config(self, agent_id: str, updates: dict) -> None:
        """更新 Agent 配置并广播新状态。"""
        try:
            config = self.registry.update_config(agent_id, updates)
            await self.emit_event(
                agent_id=agent_id,
                agent_name=config.name,
                title="配置已保存",
                detail=f"{config.role} · {config.model} · {'已启用' if config.enabled else '已停用'}",
                event_type="info",
            )
            await self.broadcast_state()
        except ValueError as e:
            await self._send_error(str(e))

    # ── 状态广播 ──────────────────────────────────────────

    async def broadcast_state(self) -> None:
        """发送完整系统状态给所有客户端。"""
        manifests = [m.model_dump() for m in self.registry.all_manifests()]
        await self.ws.broadcast(
            WSMessage(
                type="state_sync",
                payload={
                    "agents": manifests,
                    "running": self._running,
                    "paused": self._paused,
                },
            ).model_dump()
        )

    async def broadcast_pipeline_status(self) -> None:
        """发送流水线状态。"""
        await self.ws.broadcast(
            WSMessage(
                type="pipeline",
                payload={"running": self._running, "paused": self._paused},
            ).model_dump()
        )

    # ── 事件 ──────────────────────────────────────────────

    async def emit_event(
        self,
        agent_id: str,
        agent_name: str,
        title: str,
        detail: str,
        event_type: str = "info",
    ) -> None:
        """广播审计事件。"""
        event = AuditEvent(
            agent_id=agent_id,
            agent_name=agent_name,
            title=title,
            detail=detail,
            event_type=event_type,
        )
        await self.ws.broadcast(
            WSMessage(
                type="event",
                agent_id=agent_id,
                payload=event.model_dump(),
            ).model_dump()
        )
