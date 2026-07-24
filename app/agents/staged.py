"""StagedAgent —— 多阶段状态机 Agent。

允许 Agent 在执行过程中切换角色（system prompt + 工具集），
实现类似"需求分析 → 设计 → 实现 → 审查"的流程。

参考学习项目的 staged-system-prompt 模式，但适配 Orchestra 现有架构。

使用方式:
    stages = [
        Stage("requirements", "你是需求分析师...", tools_1,
              transition=lambda text: "complete_requirements" in text),
        Stage("implementation", "你是软件工程师...", tools_2,
              transition=lambda text: "submit_for_review" in text),
        Stage("review", "你是代码审查员...", tools_3,
              transition=lambda text: "approve_code" in text),
    ]
    agent = StagedAgent(config, ws, stages=stages)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Optional

from ..config import OrchestraSettings
from ..models import AgentConfig, Task, TaskResult
from ..ws_manager import WSManager
from ..llm.registry import get_provider
from ..memory.bus import MessageBus
from ..tools import ToolRegistry, FileSystemTools
from ..tools.engine import ToolExecutionEngine
from .base import BaseAgent
from . import schemas


@dataclass
class Stage:
    """一个执行阶段。"""
    name: str                                         # 阶段名称（如 "requirements"）
    system_prompt: str                                # 阶段专属系统提示词
    tool_names: list[str] = field(default_factory=list)  # 本阶段可用的工具名
    transition_check: Optional[Callable[[str], bool]] = None  # 是否应转到下一阶段
    max_iterations: int = 10                           # 本阶段最大 LLM 调用次数


class StagedAgent(BaseAgent):
    """支持多阶段状态转换的 Agent。

    StagedAgent 持有多个 Stage，执行时从 stage 0 开始，
    每个阶段用独立的 system_prompt 和工具配置调用 LLM。
    当 LLM 输出包含阶段转换信号时，自动进入下一阶段。

    所有历史消息跨阶段共享，实现上下文连续。
    """

    def __init__(
        self,
        config: AgentConfig,
        stages: list[Stage],
        ws_manager: Optional[WSManager] = None,
        tool_registry: Optional[ToolRegistry] = None,
        message_bus: Optional[MessageBus] = None,
    ):
        super().__init__(config, ws_manager, tool_registry, message_bus)
        self._stages = stages
        self._current_stage_idx = 0
        self._stage_history: list[dict] = []  # 跨阶段共享
        self._stage_logs: list[dict] = []     # 每个阶段的执行日志

    @property
    def current_stage(self) -> Stage:
        """当前阶段。"""
        return self._stages[self._current_stage_idx]

    @property
    def current_stage_name(self) -> str:
        return self.current_stage.name

    @property
    def is_completed(self) -> bool:
        """是否所有阶段都已完成。"""
        return self._current_stage_idx >= len(self._stages)

    async def process_task(self, task: Task) -> TaskResult:
        """执行所有阶段（从 0 开始依次执行）。"""
        if not self._stages:
            # 没有阶段配置，回退到父类行为
            messages = self._build_messages(task)
            output = await self._call_llm_stream(messages, stream_to_ws=True)
            return TaskResult(
                task_id=task.id, agent_id=self.config.id,
                summary="处理完成", output=output,
                artifacts={},
                handoff_context=schemas.structured_handoff_context(
                    self.task_type, output,
                ),
            )

        # 阶段 0 开始时，注入任务描述到历史
        self._stage_history = [
            {"role": "user", "content": task.description},
        ]

        while self._current_stage_idx < len(self._stages):
            stage = self.current_stage

            await self._emit_event(
                title=f"阶段 [{stage.name}] 开始",
                detail=f"第 {self._current_stage_idx + 1}/{len(self._stages)} 阶段",
                event_type="info",
            )
            await self._bus_publish("stage:start", {
                "agent_id": self.config.id,
                "stage": stage.name,
                "stage_idx": self._current_stage_idx,
            })

            # 执行本阶段
            stage_output = await self._execute_stage(stage, task)

            # 检查阶段转换
            if stage.transition_check and stage.transition_check(stage_output):
                self._log_stage(stage.name, stage_output, transitioned=True)
                self._current_stage_idx += 1
                if self._current_stage_idx < len(self._stages):
                    await self._emit_event(
                        title=f"阶段转换: {stage.name} → {self.current_stage.name}",
                        detail="上下文已连续传递到下一阶段",
                        event_type="handoff",
                    )
            else:
                # 没有转换信号：本阶段完成，结束
                self._log_stage(stage.name, stage_output, transitioned=False)
                break

        full_output = "\n\n".join(
            log["output"] for log in self._stage_logs
        )

        await self._bus_publish("stage:complete", {
            "agent_id": self.config.id,
            "completed_stages": [s.name for s in self._stages[:self._current_stage_idx + 1]],
        })

        return TaskResult(
            task_id=task.id, agent_id=self.config.id,
            summary=f"完成 {self._current_stage_idx + 1}/{len(self._stages)} 个阶段",
            output=full_output,
            artifacts={"stage_logs": list(self._stage_logs)},
            handoff_context=schemas.structured_handoff_context(
                self.task_type, full_output,
            ),
        )

    async def _execute_stage(self, stage: Stage, task: Task) -> str:
        """执行单个阶段。"""
        provider = get_provider(self.config.provider)
        if provider is None:
            return ""

        # 构建消息：本次新增 system prompt + 跨阶段历史
        messages = [
            {"role": "system", "content": self._stage_system_prompt(stage)},
        ]
        messages.extend(self._stage_history)

        collected: list[str] = []

        for iteration in range(stage.max_iterations):
            response = ""
            async for token in provider.chat_stream(
                messages=messages,
                model=self.config.model,
                temperature=self.config.temperature,
            ):
                response += token
                collected.append(token)
                if self._ws:
                    await self._ws.broadcast(
                        self._ws_msg(output_token=token, done=False)
                    )

            # 检查工具调用
            tool_calls = self._tool_engine.parse_tool_calls_from_text(response)

            if tool_calls:
                messages.append({"role": "assistant", "content": response})
                for call in tool_calls:
                    result = await self._tool_registry.execute(
                        call.get("name"), call.get("arguments", {}),
                    )
                    result_text = (
                        f"[工具: {call.get('name')} → {'✓' if result.success else '✗'}]\n"
                        f"{result.output[:1000] if result.output else ''}"
                    )
                    messages.append({"role": "user", "content": result_text})
                    collected.append(result_text)
            else:
                # 无工具调用，阶段推理结束
                break

        output = "".join(collected)
        self._stage_history.append({"role": "assistant", "content": output})
        return output

    def _stage_system_prompt(self, stage: Stage) -> str:
        """为阶段生成 system prompt（含工具说明）。"""
        prompt = stage.system_prompt
        if stage.tool_names:
            tools_desc = "\n".join(
                f"- {name}" for name in stage.tool_names
            )
            prompt += f"\n\n## 当前可用工具\n{tools_desc}\n\n"

        # 所有阶段都加上工具调用说明（如果有文件系统工具）
        prompt += """

### 如何调用工具
```tool_call
{"name": "工具名称", "arguments": {"参数1": "值1"}}
```
"""
        return prompt

    def _log_stage(self, name: str, output: str, transitioned: bool) -> None:
        self._stage_logs.append({
            "stage": name,
            "output": output[:500],
            "transitioned": transitioned,
        })

    def _ws_msg(self, output_token: str, done: bool) -> dict:
        from ..models import WSMessage
        return WSMessage(
            type="output", agent_id=self.config.id,
            payload={"token": output_token, "done": done},
        ).model_dump()

    # ── 下面覆盖 BaseAgent 的 system_prompt 和 _build_messages ──

    def system_prompt(self) -> str:
        if self._stages:
            return self._stage_system_prompt(self.current_stage)
        return ""

    def _build_messages(self, task: Task) -> list[dict]:
        """StagedAgent 用 _stage_history 替代直接拼接。"""
        if self._stages:
            return [
                {"role": "system", "content": self._stage_system_prompt(self.current_stage)},
                *self._stage_history,
            ]
        return super()._build_messages(task)
