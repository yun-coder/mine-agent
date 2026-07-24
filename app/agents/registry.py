"""Agent Registry —— 管理所有 Agent 实例的生命周期。"""

from __future__ import annotations

from typing import Optional

from ..models import AgentConfig, AgentManifest, TaskResult
from ..ws_manager import WSManager
from .base import BaseAgent
from .scout import ScoutAgent
from .architect import ArchitectAgent
from .backend_dev import BackendEngineerAgent
from .frontend_dev import FrontendEngineerAgent
from .bridge import BridgeAgent
from .tester import TesterAgent

# ─── 默认 Agent 模板 ──────────────────────────────────────────

AGENT_TEMPLATES: list[AgentConfig] = [
    AgentConfig(
        id="a1", code="01", name="情报侦察", role="技术情报 / 信息侦察",
        model="DeepSeek V3.2", provider="deepseek",
        task="获取每日技术文章，解析可信度、版本变化与网站消息，输出可执行的技术雷达。",
        temperature=0.2, mode="Parallel research",
    ),
    AgentConfig(
        id="a2", code="02", name="系统架构", role="架构设计 / 技术选型",
        model="GPT-5", provider="openai",
        task="根据需求和技术雷达，产出新项目的基础架构、边界、依赖与演进路线。",
        temperature=0.3, mode="Sequential handoff",
    ),
    AgentConfig(
        id="a3", code="03", name="后端工程", role="后端工程 / 数据建模",
        model="Claude Sonnet 4", provider="anthropic",
        task="实现 Go、Python、Java 服务与 PostgreSQL 数据层，提供可测试的 API 契约。",
        temperature=0.2, mode="Sequential handoff",
    ),
    AgentConfig(
        id="a4", code="04", name="前端工作台", role="前端工程 / 体验设计",
        model="GPT-5", provider="openai",
        task="使用 Vue、React、TypeScript、Next.js 构建高质量界面，并检查审美、响应式和可访问性。",
        temperature=0.5, mode="Sequential handoff",
    ),
    AgentConfig(
        id="a5", code="05", name="前后端联调", role="前后端联调 / 交付检查",
        model="DeepSeek V3.2", provider="deepseek",
        task="完成前后端数据联调，核对加载、空数据、错误状态，检查最终页面效果与契约漂移。",
        temperature=0.3, mode="Sequential handoff",
    ),
    AgentConfig(
        id="a6", code="06", name="回归测试", role="测试 / 复盘学习",
        model="Qwen 3 235B", provider="generic",
        task="覆盖最终产品测试与回归，总结前序漏检问题，沉淀可复用的测试策略与失败模式。",
        temperature=0.1, mode="Review only",
    ),
]


def _agent_class_for_role(role: str):
    """根据角色名称匹配对应的 Agent 类。"""
    mapping = [
        ("情报", ScoutAgent),
        ("架构", ArchitectAgent),
        ("后端", BackendEngineerAgent),
        ("前端", FrontendEngineerAgent),
        ("联调", BridgeAgent),
        ("测试", TesterAgent),
    ]
    for keyword, cls in mapping:
        if keyword in role:
            return cls
    return ScoutAgent


class AgentRegistry:
    """Agent 注册表：创建、查询、更新所有 Agent 实例。"""

    def __init__(self, ws_manager: Optional[WSManager] = None, message_bus=None):
        self._agents: dict[str, BaseAgent] = {}
        self._configs: dict[str, AgentConfig] = {}
        self._ws = ws_manager
        self._message_bus = message_bus

    @property
    def message_bus(self):
        return self._message_bus

    def register_defaults(self) -> None:
        """注册 6 个默认 Agent。"""
        for template in AGENT_TEMPLATES:
            self._configs[template.id] = template.model_copy()
            agent_cls = _agent_class_for_role(template.role)
            self._agents[template.id] = agent_cls(
                config=template.model_copy(),
                ws_manager=self._ws,
                message_bus=self._message_bus,
            )

    def get(self, agent_id: str) -> Optional[BaseAgent]:
        return self._agents.get(agent_id)

    def get_config(self, agent_id: str) -> Optional[AgentConfig]:
        return self._configs.get(agent_id)

    def update_config(self, agent_id: str, updates: dict) -> AgentConfig:
        """更新 Agent 配置并重新创建实例。"""
        config = self._configs.get(agent_id)
        if config is None:
            raise ValueError(f"Agent {agent_id} 不存在")

        for key, value in updates.items():
            if hasattr(config, key):
                setattr(config, key, value)

        # 使用新配置重建 Agent 实例
        agent_cls = _agent_class_for_role(config.role)
        old_agent = self._agents.get(agent_id)
        self._agents[agent_id] = agent_cls(
            config=config.model_copy(),
            ws_manager=self._ws,
            message_bus=self._message_bus,
        )
        # 复制旧状态（除状态外）
        if old_agent:
            self._agents[agent_id].state = old_agent.state
        return config

    def all_manifests(self) -> list[AgentManifest]:
        return [
            AgentManifest(config=self._configs[aid], state=self._agents[aid].state)
            for aid in self._configs
            if aid in self._agents
        ]

    def enabled_agents(self) -> list[BaseAgent]:
        return [
            agent
            for aid, agent in self._agents.items()
            if self._configs.get(aid, agent).enabled
        ]

    def add_custom(
        self,
        name: str = "自定义",
        role: str = "通用角色",
        model: str = "GPT-4o",
        task: str = "",
    ) -> BaseAgent:
        """添加一个自定义 Agent（来自前端"新建窗口"）。"""
        existing_ids = [int(c.code) for c in self._configs.values() if c.code.isdigit()]
        next_code = max(existing_ids) + 1 if existing_ids else 7
        agent_id = f"a{next_code}"
        config = AgentConfig(
            id=agent_id,
            code=str(next_code).zfill(2),
            name=name,
            role=role,
            model=model,
            task=task or "描述这个窗口要完成的具体任务。",
            temperature=0.3,
            enabled=True,
            mode="Sequential handoff",
        )
        self._configs[agent_id] = config
        agent_cls = _agent_class_for_role(role)
        self._agents[agent_id] = agent_cls(
            config=config.model_copy(),
            ws_manager=self._ws,
            message_bus=self._message_bus,
        )
        return self._agents[agent_id]

    def remove(self, agent_id: str) -> None:
        """移除自定义 Agent。"""
        if agent_id in ("a1", "a2", "a3", "a4", "a5", "a6"):
            # 默认 Agent 不可删除
            return
        self._agents.pop(agent_id, None)
        self._configs.pop(agent_id, None)
