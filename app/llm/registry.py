"""LLM 工厂 —— 根据 provider 名称创建缓存实例（带 RouterProvider 回退链包装）。"""

from __future__ import annotations

from typing import Optional

from .base import BaseProvider
from .providers.openai_provider import OpenAIProvider
from .providers.anthropic_provider import AnthropicProvider
from .providers.generic_provider import GenericProvider
from .router import RouterProvider
from ..config import OrchestraSettings
from ..ws_manager import WSManager


_instances: dict[str, BaseProvider] = {}
_ws_manager: Optional[WSManager] = None


def init_ws_manager(ws_manager: WSManager) -> None:
    """注入 WebSocket 管理器（供 RouterProvider 发送回退事件）。"""
    global _ws_manager
    _ws_manager = ws_manager


def get_provider(provider_name: str) -> Optional[BaseProvider]:
    """获取缓存的 provider 实例，不存在则创建。

    返回的实例由 RouterProvider 包装，支持自动回退链。
    """
    if provider_name not in _instances:
        # 创建真正的 provider 实例
        real = _create(provider_name)
        # 用 RouterProvider 包装，除非已经是 mock
        if real is not None and getattr(real, 'provider_name', '') == 'mock':
            _instances[provider_name] = real
        else:
            _instances[provider_name] = RouterProvider(
                primary=provider_name,
                ws_manager=_ws_manager,
            )
    return _instances.get(provider_name)


def _create(provider_name: str) -> Optional[BaseProvider]:
    """根据 provider 名称创建底层实例（不做 Router 包装）。"""
    settings = OrchestraSettings()

    match provider_name:
        case "openai":
            if not settings.openai_configured:
                return None  # 让 RouterProvider 走回退链
            return OpenAIProvider(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )
        case "anthropic":
            if not settings.anthropic_configured:
                return None
            return AnthropicProvider(
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
            )
        case "deepseek":
            if not settings.deepseek_configured:
                return None
            return GenericProvider(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
            )
        case "generic":
            # 用 openai API key 或通用 key
            key = settings.openai_api_key or settings.deepseek_api_key
            if not key:
                return None
            return GenericProvider(api_key=key)
        case "mock":
            return _MockProvider("mock")
        case _:
            return None


class LLMFactory:
    """静态工厂，等价于 get_provider()。"""

    @staticmethod
    def create(provider_name: str) -> BaseProvider:
        provider = get_provider(provider_name)
        assert provider is not None
        return provider
