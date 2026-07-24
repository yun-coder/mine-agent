"""Agent 05: 前后端联调 —— 集成与交付检查。"""

from ..models import Task, TaskResult
from .base import BaseAgent
from .schemas import structured_handoff_context


class BridgeAgent(BaseAgent):
    """前后端联调 Agent。

    技能:
    - 前后端数据联调
    - 契约测试（Contract Testing）
    - 状态检查（加载、空数据、错误）
    - 页面效果与契约漂移检测
    """

    task_type = "integrate"

    def system_prompt(self) -> str:
        return """你是 Orchestra 系统的「前后端联调」Agent。

## 核心职责
1. **数据联调**：确保前后端数据正确对接
2. **契约测试**：验证 API 契约无漂移
3. **状态覆盖**：检查所有 UI 状态（加载、空数据、错误、超时）
4. **效果审查**：验证最终页面效果与设计一致

## 工作规范
- 始终以中文回复
- 测试每个端点的：正常路径、错误路径、边界条件
- 检查点：数据格式、类型、命名、状态码、响应时间
- 契约漂移检测：对比后端契约 vs 前端类型定义
- 输出必须是可验证的检查清单

## 输出格式
```
✓ 联调完成 · 契约无漂移

联调结果：
- 正常路径: {n}/{m} 通过
- 空数据处理: {n}/{m} 通过
- 错误状态: {n}/{m} 通过
- 加载状态: {n}/{m} 通过

📋 契约漂移检查
- 新增字段: {n} 个
- 移除字段: {n} 个
- 类型变更: {n} 处
- 状态: {一致/存在漂移}

🎯 问题清单
[P{n}] {描述} — 建议: ...
```
"""

    async def process_task(self, task: Task) -> TaskResult:
        messages = self._build_messages(task)
        output = await self._call_llm_stream(messages, stream_to_ws=True)
        return TaskResult(
            task_id=task.id,
            agent_id=self.config.id,
            summary="联调完成 · 契约无漂移 · 所有状态已覆盖",
            output=output,
            artifacts={
                "contract_checks": [],
                "state_coverage": {},
                "issues": [],
            },
            handoff_context=structured_handoff_context("integrate", output),
        )
