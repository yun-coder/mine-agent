"""BaseAgent —— 所有 Agent 的抽象基类，带工具调用能力。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from ..models import (
    AgentConfig,
    AgentManifest,
    AgentState,
    AgentStatus,
    Task,
    TaskResult,
    WSMessage,
)
from ..llm.registry import get_provider
from ..tools import ToolRegistry, FileSystemTools, ToolResult
from ..tools.engine import ToolExecutionEngine
from ..ws_manager import WSManager
from ..config import OrchestraSettings
from ..memory.bus import MessageBus, Envelope
from . import schemas


class BaseAgent(ABC):
    """Agent 抽象基类。

    子类只需实现:
    - system_prompt() → 返回角色系统提示词
    - process_task(task) → 执行任务并返回结果

    可选覆盖:
    - task_type → 返回任务类型（research/design/implement/frontend/integrate/test），
      自动启用结构化输出
    """

    # 子类可重写此属性以启用结构化输出
    task_type: str = "generic"

    def __init__(
        self,
        config: AgentConfig,
        ws_manager: Optional[WSManager] = None,
        tool_registry: Optional[ToolRegistry] = None,
        message_bus: Optional[MessageBus] = None,
    ):
        self.config = config
        self.state = AgentState()
        self._ws = ws_manager
        self._settings = OrchestraSettings()
        self.message_bus = message_bus or MessageBus()

        # 工具系统初始化
        if tool_registry is None:
            fs_tools = FileSystemTools()
            tool_registry = ToolRegistry(fs_tools=fs_tools)
        self._tool_registry = tool_registry
        self._tool_engine = ToolExecutionEngine(tool_registry)

    @abstractmethod
    def system_prompt(self) -> str:
        """返回角色特定的系统提示词。"""
        ...

    async def run(self, task: Task) -> TaskResult:
        """公开入口：设置状态 → 执行 → 返回结构化结果。"""
        self.state.status = AgentStatus.running
        self.state.started_at = datetime.now().isoformat()
        await self._emit_status()
        await self._emit_event(
            title=f"Agent {self.config.code} / {self.config.name} 已激活",
            detail=f"模型：{self.config.model} · 模式：{self.config.mode}",
            event_type="info",
        )
        await self._bus_publish("agent:start", {
            "agent_id": self.config.id,
            "agent_name": self.config.name,
            "task_type": task.type,
        })

        try:
            result = await self.process_task(task)
            self.state.status = AgentStatus.done
            self.state.completed_at = datetime.now().isoformat()
            self.state.output = result.summary
            await self._emit_status()
            await self._bus_publish("agent:complete", {
                "agent_id": self.config.id,
                "agent_name": self.config.name,
                "summary": result.summary,
                "confidence": result.confidence,
            })
            return result
        except Exception as e:
            self.state.status = AgentStatus.error
            self.state.error = str(e)
            self.state.completed_at = datetime.now().isoformat()
            await self._emit_status()
            await self._emit_event(
                title=f"Agent {self.config.name} 执行失败",
                detail=str(e),
                event_type="error",
            )
            await self._bus_publish("agent:error", {
                "agent_id": self.config.id,
                "agent_name": self.config.name,
                "error": str(e),
            })
            raise

    @abstractmethod
    async def process_task(self, task: Task) -> TaskResult:
        """执行任务。子类必须实现此方法。"""
        ...

    async def handle_command(self, command: str) -> AsyncIterator[str]:
        """处理前端的交互式命令输入，包含工具调用循环。"""
        messages = self._build_messages_from_cmd(command)
        provider = get_provider(self.config.provider)
        if provider is None:
            yield "错误：未配置 LLM 提供商"
            return

        # 工具调用主循环
        max_iterations = 10
        for iteration in range(max_iterations):
            # 调用 LLM
            response = await provider.chat(
                messages=messages,
                model=self.config.model,
                temperature=self.config.temperature,
                max_tokens=4096,
            )

            # 检查是否有工具调用
            tool_calls = self._tool_engine.parse_tool_calls_from_text(response)
            analysis = self._tool_engine.strip_tool_calls_from_text(response)

            if analysis:
                yield analysis

            if not tool_calls:
                # LLM 完成推理，不再需要调用工具
                break

            # 执行工具
            results = []
            for call in tool_calls:
                tool_name = call.get("name", "")
                arguments = call.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass

                await self._emit_event(
                    title=f"调用工具: {tool_name}",
                    detail=f"参数: {json.dumps(arguments, ensure_ascii=False)[:200]}",
                    event_type="info",
                )

                result = await self._tool_registry.execute(tool_name, arguments)
                results.append(result)

                yield f"\n[工具: {tool_name} → {'✓' if result.success else '✗'}]\n"
                if result.output:
                    yield result.output[:500] + ("\n..." if len(result.output) > 500 else "") + "\n"
                if result.error:
                    yield f"错误: {result.error}\n"

            # 将工具执行结果注入对话上下文
            tool_context = self._tool_engine.format_result_as_context(results)
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": tool_context + "\n\n请基于以上结果继续你的分析和任务。如果已经完成，请总结输出。如果需要更多信息，请继续调用工具。"})

        else:
            yield "\n[已达到最大工具调用次数，强制结束]\n"

    async def _call_llm_stream(
        self,
        messages: list[dict],
        stream_to_ws: bool = True,
    ) -> str:
        """调用 LLM 并推送流式 token 到 WebSocket。包含工具调用循环。"""
        provider = get_provider(self.config.provider)
        if provider is None:
            text = self._mock_response()
            await self._emit_output(text)
            return text

        collected: list[str] = []
        max_iterations = 15

        for iteration in range(max_iterations):
            # 调用 LLM（流式）
            current_response = ""
            async for token in provider.chat_stream(
                messages=messages,
                model=self.config.model,
                temperature=self.config.temperature,
            ):
                current_response += token
                collected.append(token)
                if stream_to_ws and self._ws:
                    await self._ws.broadcast(
                        WSMessage(
                            type="output",
                            agent_id=self.config.id,
                            payload={"token": token, "done": False},
                        ).model_dump()
                    )

            # 检查是否有工具调用
            tool_calls = self._tool_engine.parse_tool_calls_from_text(current_response)

            if not tool_calls:
                # 没有工具调用，推理完成
                break

            # 有工具调用：将 LLM 输出添加到上下文
            messages.append({"role": "assistant", "content": current_response})

            # 执行每个工具
            for call in tool_calls:
                tool_name = call.get("name", "")
                arguments = call.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        pass

                tool_msg = f"\n━━━ 调用工具: {tool_name} ━━━\n"
                collected.append(tool_msg)
                if stream_to_ws and self._ws:
                    await self._ws.broadcast(
                        WSMessage(
                            type="output",
                            agent_id=self.config.id,
                            payload={"token": tool_msg, "done": False},
                        ).model_dump()
                    )

                result = await self._tool_registry.execute(tool_name, arguments)

                result_text = f"[工具: {tool_name} → {'✓' if result.success else '✗'}]\n"
                if result.output:
                    result_text += result.output[:1000]
                    if len(result.output) > 1000:
                        result_text += "\n...(输出已截断)"
                if result.error:
                    result_text += f"\n错误: {result.error}"

                collected.append(result_text + "\n")
                if stream_to_ws and self._ws:
                    await self._ws.broadcast(
                        WSMessage(
                            type="output",
                            agent_id=self.config.id,
                            payload={"token": result_text + "\n", "done": False},
                        ).model_dump()
                    )

                # 将工具结果作为 user 消息注入
                messages.append({
                    "role": "user",
                    "content": f"工具 {tool_name} 执行结果:\n{result.output[:1500] if result.output else '无输出'}\n\n请基于以上结果继续你的分析和任务。",
                })
        else:
            done_msg = "\n[已达到最大工具调用次数]\n"
            collected.append(done_msg)
            if stream_to_ws and self._ws:
                await self._ws.broadcast(
                    WSMessage(
                        type="output",
                        agent_id=self.config.id,
                        payload={"token": done_msg, "done": True},
                    ).model_dump()
                )
            return "".join(collected)

        text = "".join(collected)
        if stream_to_ws and self._ws:
            await self._ws.broadcast(
                WSMessage(
                    type="output",
                    agent_id=self.config.id,
                    payload={"token": "", "done": True},
                ).model_dump()
            )
        return text

    def _build_messages(self, task: Task) -> list[dict]:
        """构建 LLM 消息列表：系统提示 + 上下文 + 任务。"""
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt_with_tools()}
        ]
        for ctx in task.handoff_context:
            messages.append(ctx)
        messages.append({
            "role": "user",
            "content": task.description + "\n\n附加上下文:\n" + str(task.payload),
        })

        # 如果配置了结构化输出，追加格式要求
        if self.task_type in schemas.SCHEMA_MAP:
            schema_fields = schemas.SCHEMA_MAP[self.task_type].model_json_schema()
            field_descriptions = {
                k: v.get("description", k)
                for k, v in schema_fields.get("properties", {}).items()
                if k != "raw_text"
            }
            format_hint = (
                "\n\n## 输出要求\n"
                "你的输出应在最后附带一个 JSON 格式的结构化摘要，便于下游 Agent 解析。\n"
                "请用 ```json ... ``` 代码块包裹 JSON，包含以下字段：\n"
            )
            for field_name, desc in field_descriptions.items():
                format_hint += f"- `{field_name}`: {desc}\n"
            format_hint += "\n注意：你生成的分析文本（中文）保持原样即可，JSON 仅作为附加的结构化摘要。"
            messages[0]["content"] += format_hint

        return messages

    def _system_prompt_with_tools(self) -> str:
        """在原始 system_prompt 后追加工具调用说明。"""
        base_prompt = self.system_prompt()
        tools_prompt = f"""

## 主机工具访问权限
你可以直接访问用户的电脑文件系统来完成任务！所有 Agent 都有以下权限：
- 📄 读取文件 (read_file)
- ✏️ 写入/创建文件 (write_file)
- 📝 追加文件内容 (append_file)
- 📂 列出目录内容 (list_directory)
- 🔍 搜索文件 (search_files)
- ℹ️ 获取文件信息 (file_info)
- 📁 创建目录 (create_directory)
- 🗑️ 删除文件 (delete_file)
- 💻 执行命令 (execute_command)

### 如何调用工具
当你需要操作用户电脑上的文件时，在回复中使用以下格式：

```tool_call
{{"name": "工具名称", "arguments": {{"参数1": "值1", "参数2": "值2"}}}}
```

工具执行结果会自动注入到对话中，你可以基于结果继续推理。
如果需要多次调用工具，可以连续使用多个工具调用。

请合理使用这些工具来完成任务。优先使用文件读写和命令执行来获取信息。
"""
        return base_prompt + tools_prompt

    def _build_messages_from_cmd(self, command: str) -> list[dict]:
        """从交互式命令构建消息列表（含工具提示）。"""
        base_content = self._system_prompt_with_tools()
        # 交互模式也加结构化提示
        if self.task_type in schemas.SCHEMA_MAP:
            base_content += (
                "\n\n你的最终回复请在末尾用 ```json ``` 包裹结构化 JSON 摘要。"
            )
        return [
            {"role": "system", "content": base_content},
            {"role": "user", "content": command},
        ]

    async def _emit_status(self) -> None:
        if self._ws:
            await self._ws.broadcast(
                WSMessage(
                    type="status",
                    agent_id=self.config.id,
                    payload={
                        "status": self.state.status.value,
                        "output": self.state.output,
                        "error": self.state.error,
                        "started_at": self.state.started_at,
                        "completed_at": self.state.completed_at,
                    },
                ).model_dump()
            )

    async def _emit_output(self, text: str) -> None:
        if self._ws:
            await self._ws.broadcast(
                WSMessage(
                    type="output",
                    agent_id=self.config.id,
                    payload={"token": text, "done": True},
                ).model_dump()
            )

    async def _emit_event(
        self, title: str, detail: str, event_type: str = "info"
    ) -> None:
        if self._ws:
            await self._ws.broadcast(
                WSMessage(
                    type="event",
                    agent_id=self.config.id,
                    payload={
                        "title": title,
                        "detail": detail,
                        "event_type": event_type,
                        "agent_name": self.config.name,
                    },
                ).model_dump()
            )

    def _mock_response(self) -> str:
        return (
            f"[{self.config.name}] 已收到任务并完成分析。\n"
            f"（此为模拟回复——配置 LLM API Key 后可获取真实输出并使用文件工具。）"
        )

    async def _bus_publish(self, event_type: str, payload: dict) -> None:
        """通过 MessageBus 发布事件。"""
        if hasattr(self, 'message_bus') and self.message_bus:
            await self.message_bus.publish_event(
                sender=self.config.id,
                type=event_type,
                payload=payload,
            )

    def to_manifest(self) -> AgentManifest:
        return AgentManifest(config=self.config, state=self.state)
