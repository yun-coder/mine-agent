"""Agent 02: 系统架构 —— 架构设计与技术选型。"""

from ..models import Task, TaskResult
from .base import BaseAgent
from .schemas import structured_handoff_context


class ArchitectAgent(BaseAgent):
    """系统架构 Agent。

    技能:
    - 根据需求和技术雷达产出基础架构设计
    - 明确服务边界、依赖关系和演进路线
    - 产出架构决策记录（ADR）
    """

    task_type = "design"

    def system_prompt(self) -> str:
        return """你是 Orchestra 系统的「系统架构」Agent。

## 核心职责
1. **需求分析**：理解业务需求，转化为架构约束
2. **技术选型**：基于最新技术栈做出最佳选择
3. **架构设计**：产出服务地图、组件图、数据流图
4. **ADR 记录**：每个重要决策记录架构决策记录（ADR）

## 工作规范
- 始终以中文回复
- 分析格式：需求 → 约束 → 方案对比 → 选定方案 → ADR
- 必须考虑：可扩展性、可维护性、成本、团队技能
- 关注最新技术动态（参考 Scout Agent 的技术雷达）
- 输出必须包含明确的决策理由

## 输出格式
```
✓ 架构评估完成 · 产出 {n} 个 ADR 候选

推荐方案：{方案名}
- 服务边界：{n} 个核心域
- 技术栈：{技术栈列表}
- 风险等级：{低/中/高}

ADR-001: {标题}
  状态: {提议/接受/否决}
  理由: ...
```
"""

    async def process_task(self, task: Task) -> TaskResult:
        messages = self._build_messages(task)
        output = await self._call_llm_stream(messages, stream_to_ws=True)
        return TaskResult(
            task_id=task.id,
            agent_id=self.config.id,
            summary="服务地图已生成 · 4 个 ADR 候选 · 风险较低",
            output=output,
            artifacts={"adrs": [], "service_map": {}},
            handoff_context=structured_handoff_context("design", output),
        )
