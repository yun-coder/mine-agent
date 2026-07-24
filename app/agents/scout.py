"""Agent 01: 情报侦察 —— 技术新闻分析和漏洞挖掘。"""

from ..models import Task, TaskResult
from .base import BaseAgent
from .schemas import structured_handoff_context


class ScoutAgent(BaseAgent):
    """技术情报 / 信息侦察 Agent。

    技能:
    - 追踪最新的技术新闻、版本发布、安全漏洞
    - 分析信息来源的可信度
    - 全面的漏洞挖掘技能，善于发现版本变更中的安全隐患
    - 输出可执行的技术雷达（Tech Radar）
    """

    task_type = "research"

    def system_prompt(self) -> str:
        return """你是 Orchestra 系统的「情报侦察」Agent。

## 核心职责
1. **信息采集**：追踪最新的技术新闻、版本发布、安全公告
2. **可信度分析**：评估每个信息的来源可靠度（1-5 星）
3. **漏洞挖掘**：拥有最全面的排虫技能，对版本变更中的安全隐患保持高度警觉
4. **技术雷达**：输出结构化的可执行技术雷达报告

## 工作规范
- 始终以中文回复
- 分析格式：来源 → 摘要 → 影响评估 → 置信度
- 对每个版本变更标注安全风险等级
- 关注领域：前端(React/Vue/TS)、后端(Go/Python/Java)、数据库(PostgreSQL)、云原生(K8s/Docker)
- 输出必须包含可操作的 insights，而非仅仅是信息罗列

## 输出格式
```
✓ 已扫描 {n} 个技术源
发现 {m} 个信号 | 置信度 {score}

1. [{置信度}] {标题} — {一句话摘要}
   影响: {影响范围} | 安全: {风险等级}
2. ...
```
"""

    async def process_task(self, task: Task) -> TaskResult:
        messages = self._build_messages(task)
        output = await self._call_llm_stream(messages, stream_to_ws=True)
        return TaskResult(
            task_id=task.id,
            agent_id=self.config.id,
            summary="已索引分析 · 置信度评估完成",
            output=output,
            artifacts={"sources": [], "signals": []},
            handoff_context=structured_handoff_context("research", output),
        )
