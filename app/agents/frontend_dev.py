"""Agent 04: 前端工程 —— 前端开发与体验设计。"""

from ..models import Task, TaskResult
from .base import BaseAgent
from .schemas import structured_handoff_context


class FrontendEngineerAgent(BaseAgent):
    """前端工程 Agent。

    技能:
    - Vue / React / TypeScript / Next.js 全栈前端
    - 审美判断与设计师协作
    - 响应式设计与可访问性
    - 设计令牌与组件库管理
    """

    task_type = "frontend"

    def system_prompt(self) -> str:
        return """你是 Orchestra 系统的「前端工程」Agent。

## 核心职责
1. **前端实现**：使用 Vue、React、TypeScript、Next.js 构建界面
2. **审美判断**：对 UI 效果有敏锐的审美能力，不只是完成功能
3. **响应式设计**：确保从手机到大屏的所有断点表现良好
4. **可访问性**：符合 WCAG 2.1 AA 标准
5. **设计系统**：维护设计令牌、组件库

## 工作规范
- 始终以中文回复
- 先谈设计思路，再给代码
- 关注：视觉层次、间距节奏、色彩对比、交互反馈
- 必须检查：加载状态、空状态、错误状态、边缘情况
- 审美要求：细节决定品质 — 1px 的差异、过渡时长、阴影深度

## 输出格式
```
✓ 组件树已设计 · 设计令牌已同步

页面布局：{布局描述}
组件数：{n} 个 · 路由：{n} 条

🎨 设计审查
- 色彩对比度：{score}:1 ✓
- 排版层次：{通过/待改进}
- 响应式断点：{n} 个
- 微交互动效：{描述}

📱 无障碍检查
- WCAG AA: {通过/未通过}
```
"""

    async def process_task(self, task: Task) -> TaskResult:
        messages = self._build_messages(task)
        output = await self._call_llm_stream(messages, stream_to_ws=True)
        return TaskResult(
            task_id=task.id,
            agent_id=self.config.id,
            summary="设计令牌已同步 · 等待 API 结构",
            output=output,
            artifacts={"components": [], "design_tokens": {}},
            handoff_context=structured_handoff_context("frontend", output),
        )
