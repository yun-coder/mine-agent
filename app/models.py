"""Pydantic 数据模型 —— 所有类型定义集中在一次"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ─── Agent 状态 ───────────────────────────────────────────────

class AgentStatus(str, Enum):
    idle = "idle"
    running = "running"
    waiting = "waiting"
    done = "done"
    error = "error"

    @property
    def label(self) -> str:
        return {
            "idle": "就绪",
            "running": "运行中",
            "waiting": "等待中",
            "done": "已完成",
            "error": "已阻塞",
        }[self.value]


# ─── Agent 配置 ╱ 状态 ────────────────────────────────────────

class AgentConfig(BaseModel):
    """用户可配置的 Agent 参数，跨会话持久化。"""
    id: str
    code: str = "00"
    name: str = "未命名"
    role: str = "通用角色"
    model: str = "GPT-4o"
    provider: str = "openai"
    task: str = ""
    temperature: float = 0.3
    enabled: bool = True
    mode: str = "Sequential handoff"


class AgentState(BaseModel):
    """Agent 运行时状态，易失（不持久化）。"""
    status: AgentStatus = AgentStatus.idle
    output: str = ""
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class AgentManifest(BaseModel):
    """通过 WebSocket 下发给前端的完整 Agent 视图。"""
    config: AgentConfig
    state: AgentState


# ─── 任务 ╱ 任务结果 ──────────────────────────────────────────

class Task(BaseModel):
    """分配给一个 Agent 的工作单元。"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: str = "generic"  # research | design | implement | integrate | test | review | command
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source_agent_id: str = "master"
    target_agent_id: str = ""
    handoff_context: list[dict] = Field(default_factory=list)
    status: str = "pending"


class TaskResult(BaseModel):
    """Agent 完成任务的输出。"""
    task_id: str
    agent_id: str
    summary: str = ""
    output: str = ""
    artifacts: dict[str, Any] = Field(default_factory=dict)
    handoff_context: list[dict] = Field(default_factory=list)
    confidence: float = 1.0
    token_used: int = 0


# ─── 审计事件 ──────────────────────────────────────────────────

def _event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:8]}"


def _now_hhmmss() -> str:
    return datetime.now().strftime("%H:%M:%S")


class AuditEvent(BaseModel):
    """审计日志中的一条事件。"""
    id: str = Field(default_factory=_event_id)
    time: str = Field(default_factory=_now_hhmmss)
    agent_id: str = "master"
    agent_name: str = "主控"
    title: str = ""
    detail: str = ""
    event_type: str = "info"  # info | handoff | error | success
    live: bool = True


# ─── WebSocket 消息信封 ──────────────────────────────────────

class WSMessage(BaseModel):
    """所有 WebSocket 消息使用此信封。"""
    type: str  # command | output | status | event | handoff | config | pipeline | state_sync | error
    agent_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# ─── 交接记录 ──────────────────────────────────────────────────

class HandoffRecord(BaseModel):
    """Agent 之间的交接记录。"""
    id: str = Field(default_factory=lambda: f"h_{uuid.uuid4().hex[:6]}")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    from_agent_id: str = ""
    from_agent_name: str = ""
    to_agent_id: str = ""
    to_agent_name: str = ""
    task_id: str = ""
    result_summary: str = ""
    token_used: int = 0


# ─── 模型 → Provider 映射 ────────────────────────────────────

MODEL_PROVIDER_MAP: dict[str, str] = {
    "GPT-5": "openai",
    "GPT-4o": "openai",
    "GPT-4o-mini": "openai",
    "Claude Sonnet 4": "anthropic",
    "Claude Opus 4": "anthropic",
    "Claude Haiku 4": "anthropic",
    "DeepSeek V3": "deepseek",
    "DeepSeek V3.2": "deepseek",
    "DeepSeek R1": "deepseek",
    "Qwen 3 235B": "generic",
    "Qwen 3 Max": "generic",
}


def resolve_provider(model_name: str) -> str:
    """根据模型名称返回 provider key。"""
    return MODEL_PROVIDER_MAP.get(model_name, "openai")
