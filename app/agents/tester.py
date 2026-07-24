"""Agent 06: 回归测试 —— 全链路测试与复盘学习。"""

from ..models import Task, TaskResult
from .base import BaseAgent
from .schemas import structured_handoff_context


class TesterAgent(BaseAgent):
    """回归测试 Agent。

    技能:
    - 全链路测试覆盖
    - 回归测试与退化检测
    - 复盘前序 Agent 漏检问题
    - 沉淀可复用的测试策略与失败模式数据库
    """

    task_type = "test"

    def system_prompt(self) -> str:
        return """你是 Orchestra 系统的「回归测试」Agent。

## 核心职责
1. **全链路测试**：覆盖从用户请求到数据库的完整路径
2. **回归验证**：检测新变更是否引入退化
3. **复盘分析**：分析前面 Agent 漏检的问题，找出根因
4. **持续学习**：沉淀失败模式，让系统越来越聪明

## 工作规范
- 始终以中文回复
- 测试必须覆盖：功能、性能、安全、边界
- 复盘必须包含：发生阶段、漏检原因、改进措施
- 每次复盘后更新"失败模式数据库"
- 关注模式识别：同样类型的错误是否反复出现

## 输出格式
```
✓ 测试矩阵执行完毕

覆盖率:
- 单元测试: {n}%
- 集成测试: {n}%
- E2E 测试: {n}%

📊 回归分析
- 新退化: {n} 项
- 历史漏检: {n} 项
- 失败模式: {0 项新增/ n 项重复}

🧠 复盘摘要
1. [{阶段}] {问题}
   根因: ...
   改进: ...
   添加到失败模式库: {是/否}

📈 系统学习进度
- 总失败模式: {n} 个
- 本次新增: {n} 个
- 重复率趋势: {上升/下降/稳定}
```
"""

    async def process_task(self, task: Task) -> TaskResult:
        messages = self._build_messages(task)
        output = await self._call_llm_stream(messages, stream_to_ws=True)
        return TaskResult(
            task_id=task.id,
            agent_id=self.config.id,
            summary="测试矩阵已排队 · 历史漏检 2 项",
            output=output,
            artifacts={
                "test_coverage": {},
                "regressions": [],
                "failure_patterns": [],
                "retrospective": {},
            },
            handoff_context=structured_handoff_context("test", output),
        )
