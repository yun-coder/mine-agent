"""Agent 系统 —— 多角色智能体实现和注册表。"""

from .base import BaseAgent
from .scout import ScoutAgent
from .architect import ArchitectAgent
from .backend_dev import BackendEngineerAgent
from .frontend_dev import FrontendEngineerAgent
from .bridge import BridgeAgent
from .tester import TesterAgent
from .registry import AgentRegistry, AGENT_TEMPLATES

__all__ = [
    "BaseAgent",
    "ScoutAgent",
    "ArchitectAgent",
    "BackendEngineerAgent",
    "FrontendEngineerAgent",
    "BridgeAgent",
    "TesterAgent",
    "AgentRegistry",
    "AGENT_TEMPLATES",
]
