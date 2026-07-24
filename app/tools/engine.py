"""工具执行引擎 —— 将 LLM 的函数调用请求路由到实际工具。"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator, Optional

from . import ToolRegistry, ToolResult


class ToolExecutionEngine:
    """管理 LLM 与工具之间的交互循环。

    工作流程:
    1. LLM 返回文本（可能包含工具调用标记）
    2. 解析工具调用
    3. 执行工具
    4. 将工具结果注入回 LLM 上下文
    5. LLM 继续推理
    """

    def __init__(self, tool_registry: ToolRegistry):
        self.registry = tool_registry
        self._tool_history: list[dict] = []

    def get_tool_definitions(self) -> list[dict]:
        """获取 LLM function-calling 格式的工具列表。"""
        return self.registry.to_llm_tools()

    def parse_tool_calls_from_text(self, text: str) -> list[dict]:
        """从 LLM 文本回复中解析工具调用（文本模式）。

        支持格式:
        [TOOL_CALL] {"name": "read_file", "arguments": {"path": "/tmp/test.txt"}}
        或 markdown 代码块:
        ```tool_call
        {"name": "read_file", "arguments": {"path": "/tmp/test.txt"}}
        ```
        """
        calls = []

        # 格式1: [TOOL_CALL] {...}
        for match in re.finditer(
            r'\[TOOL_CALL\]\s*(\{.*?\})(?=\n|$|\[TOOL_CALL\])',
            text, re.DOTALL
        ):
            try:
                call = json.loads(match.group(1))
                if "name" in call and "arguments" in call:
                    calls.append(call)
            except json.JSONDecodeError:
                continue

        # 格式2: ```tool_call ... ```
        for match in re.finditer(
            r'```tool_call\s*\n?(.*?)\n?```',
            text, re.DOTALL
        ):
            try:
                call = json.loads(match.group(1))
                if "name" in call and "arguments" in call:
                    calls.append(call)
            except json.JSONDecodeError:
                continue

        return calls

    def strip_tool_calls_from_text(self, text: str) -> str:
        """从文本中移除工具调用标记，只保留分析内容。"""
        text = re.sub(r'\[TOOL_CALL\]\s*\{.*?\}(?=\n|$)', '', text, flags=re.DOTALL)
        text = re.sub(r'```tool_call\s*\n?(.*?)\n?```', '', text, flags=re.DOTALL)
        # 清理多余空行
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        return text

    def format_result_as_context(self, results: list[ToolResult]) -> str:
        """将工具执行结果格式化为 LLM 上下文文本。"""
        parts = ["━━━ 工具执行结果 ━━━"]
        for i, r in enumerate(results):
            status = "✓" if r.success else "✗"
            parts.append(f"[工具结果 {i+1}] {status}")
            if r.output:
                # 截取过长输出
                output = r.output[:2000]
                if len(r.output) > 2000:
                    output += "\n... (输出已截断)"
                parts.append(output)
            if r.error:
                parts.append(f"错误: {r.error}")
        parts.append("━━━━━━━━━━━━━━━━")
        return "\n".join(parts)

    def format_text_as_stream(self, text: str) -> AsyncIterator[str]:
        """将文本转换为异步流（用于兼容流式接口）。"""
        async def _stream():
            for chunk in text:
                yield chunk
        return _stream()

    def get_tool_history(self) -> list[dict]:
        """获取工具调用历史。"""
        return list(self._tool_history)

    def clear_history(self) -> None:
        self._tool_history.clear()
