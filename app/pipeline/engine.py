"""流水线引擎 —— 编排 Agent 执行顺序与数据传递。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..agents.registry import AgentRegistry
from ..models import TaskResult


@dataclass
class PipelineNode:
    """流水线中的一步。"""
    agent_id: str
    task_type: str = "generic"
    description: str = ""
    payload: dict = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    next_agent_id: Optional[str] = None
    result: Optional[TaskResult] = None


class PipelineEngine:
    """流水线执行引擎。

    支持的执行模式:
    - Sequential handoff: Agent 顺序执行，前一个完成后交接给下一个
    - Parallel research: 多个 Agent 并发研究
    - Review only: 审查模式，在末尾作为质量关卡
    """

    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.nodes: dict[str, PipelineNode] = {}
        self.execution_order: list[PipelineNode] = []
        self._description: str = ""

    def compile(self, task_description: str) -> None:
        """根据启用的 Agent 配置编译执行顺序。"""
        self._description = task_description
        self.nodes.clear()
        self.execution_order.clear()

        agents = self.registry.enabled_agents()
        agents.sort(key=lambda a: a.config.code)

        # 按模式分组
        sequential = [a for a in agents if a.config.mode == "Sequential handoff"]
        parallel = [a for a in agents if a.config.mode == "Parallel research"]
        review = [a for a in agents if a.config.mode == "Review only"]

        # Sequential: 用 next_agent_id 串联
        for i, agent in enumerate(sequential):
            next_id = sequential[i + 1].config.id if i + 1 < len(sequential) else None
            node = PipelineNode(
                agent_id=agent.config.id,
                task_type=self._infer_task_type(agent.config.role),
                description=agent.config.task,
                next_agent_id=next_id,
            )
            self.nodes[agent.config.id] = node
            self.execution_order.append(node)

        # 先执行 parallel research 组，再执行 sequential，最后 review
        prepend_nodes = []
        for agent in parallel:
            node = PipelineNode(
                agent_id=agent.config.id,
                task_type="research",
                description=agent.config.task,
                next_agent_id=sequential[0].config.id if sequential else None,
            )
            self.nodes[agent.config.id] = node
            prepend_nodes.append(node)

        append_nodes = []
        for agent in review:
            node = PipelineNode(
                agent_id=agent.config.id,
                task_type="test",
                description=agent.config.task,
                next_agent_id=None,
                dependencies=[n.agent_id for n in self.execution_order],
            )
            self.nodes[agent.config.id] = node
            append_nodes.append(node)

        self.execution_order = prepend_nodes + self.execution_order + append_nodes

    # ── 并行执行分组 ──────────────────────────────────────────

    @property
    def execution_groups(self) -> list[list[PipelineNode]]:
        """返回按并发性分组的执行顺序。

        每组是一个可以并行执行的节点列表：
        - "Parallel research" → 全部在一个并发组里
        - "Sequential handoff" → 每个节点单独一组（必须串行）
        - "Review only" → 全部在一个并发组里（在所有串行之后）
        """
        if not self.nodes:
            return []

        parallel_group: list[PipelineNode] = []
        sequential_groups: list[list[PipelineNode]] = []
        review_group: list[PipelineNode] = []

        mode_map = {}
        for agent in self.registry.enabled_agents():
            mode_map[agent.config.id] = agent.config.mode

        # 按 execution_order 遍历，保持原有顺序
        for node in self.execution_order:
            agent_mode = mode_map.get(node.agent_id, "Sequential handoff")
            if agent_mode == "Parallel research":
                parallel_group.append(node)
            elif agent_mode == "Sequential handoff":
                sequential_groups.append([node])
            elif agent_mode == "Review only":
                review_group.append(node)

        groups: list[list[PipelineNode]] = []
        if parallel_group:
            groups.append(parallel_group)
        groups.extend(sequential_groups)
        if review_group:
            groups.append(review_group)
        return groups

    def store_result(self, agent_id: str, result: TaskResult) -> None:
        if agent_id in self.nodes:
            self.nodes[agent_id].result = result
        if agent_id in self.nodes:
            self.nodes[agent_id].result = result

    def collect_context(self, target_agent_id: str) -> list[dict]:
        """收集所有前置 Agent 的上下文摘要。"""
        excerpts: list[dict] = []
        for node in self.execution_order:
            if node.agent_id == target_agent_id:
                break
            if node.result:
                excerpts.extend(node.result.handoff_context)
        return excerpts

    def _infer_task_type(self, role: str) -> str:
        if "情报" in role:
            return "research"
        if "架构" in role:
            return "design"
        if "后端" in role:
            return "implement"
        if "前端" in role:
            return "implement"
        if "联调" in role:
            return "integrate"
        if "测试" in role:
            return "test"
        return "generic"
