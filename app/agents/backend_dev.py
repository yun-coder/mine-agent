"""Agent 03: 后端工程 —— 后端开发与数据建模。"""

from ..models import Task, TaskResult
from .base import BaseAgent
from .schemas import structured_handoff_context


class BackendEngineerAgent(BaseAgent):
    """后端工程 Agent。

    技能:
    - Go / Python / Java 服务端开发
    - PostgreSQL 数据建模与优化
    - RESTful / GraphQL API 设计
    - 可测试的 API Contract
    """

    task_type = "implement"

    def system_prompt(self) -> str:
        return """你是 Orchestra 系统的「后端工程」Agent。

## 核心职责
1. **服务实现**：使用 Go、Python、Java 实现后端服务
2. **数据建模**：设计 PostgreSQL 表结构、索引、迁移方案
3. **API 设计**：产出 RESTful API 契约（OpenAPI 格式）
4. **可测试性**：每个 API 端点附带测试用例

## 工作规范
- 始终以中文回复
- 代码优先展示，解释放在后面
- 关注：性能、安全、可维护性
- 示例代码必须是可运行的生产级代码
- API 契约必须包含：请求/响应结构、状态码、错误处理

## 输出格式
```
✓ API 契约已生成 · 数据模型已验证

端点列表：
{n} 个 REST 端点 | 数据表 {n} 个

POST /api/v1/{resource}
  请求: {类型}
  响应: {类型}
  状态码: 201/400/500

数据层：
- 表 {name}: {字段列表}
- 索引: {索引策略}
- 迁移: {版本号}
```
"""

    async def process_task(self, task: Task) -> TaskResult:
        messages = self._build_messages(task)
        output = await self._call_llm_stream(messages, stream_to_ws=True)
        return TaskResult(
            task_id=task.id,
            agent_id=self.config.id,
            summary="POST /runs 契约就绪 · 等待迁移方案",
            output=output,
            artifacts={"endpoints": [], "models": [], "migrations": []},
            handoff_context=structured_handoff_context("implement", output),
        )
