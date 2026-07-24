"""RouterProvider —— 多提供商自动回退链包装器。

当主提供商不可用时，按预设链自动降级到其他提供商，
仅在全部失败后才降级到 MockProvider。
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Optional

from ..config import OrchestraSettings
from ..ws_manager import WSManager
from .base import BaseProvider

logger = logging.getLogger(__name__)

# 回退链：主提供商 → 备选提供商 → Mock
FALLBACK_CHAINS: dict[str, list[str]] = {
    "openai": ["openai", "deepseek", "anthropic", "mock"],
    "anthropic": ["anthropic", "openai", "deepseek", "mock"],
    "deepseek": ["deepseek", "openai", "anthropic", "mock"],
    "generic": ["generic", "openai", "deepseek", "mock"],
}

# 默认排除 mock 配额外的 fallback（留给 _create 自动判断是否返回 MockProvider）
_KNOWN_PROVIDERS = {"openai", "anthropic", "deepseek", "generic", "mock"}


def _get_settings() -> OrchestraSettings:
    return OrchestraSettings()


class RouterProvider(BaseProvider):
    """多提供商回退路由包装器。

    通过缓存的 provider 池和回退链，自动在主提供商失败时切换。
    全程不会将异常抛给调用方（除了全部提供商都失败）。

    提醒: 此包装器不缓存自身的 provider 实例 —— 它假设外部调用方
    （即 _instances 缓存）已经做了单例化。
    """

    provider_name = "router"

    def __init__(
        self,
        primary: str,
        ws_manager: Optional[WSManager] = None,
    ):
        self._primary = primary
        self._chain = FALLBACK_CHAINS.get(primary, [primary, "mock"])
        self._ws = ws_manager
        self._settings = _get_settings()
        # 懒初始化 provider 实例
        self._providers: dict[str, BaseProvider] = {}

    def _get_or_create(self, name: str) -> Optional[BaseProvider]:
        """获取或创建一个 provider 实例。"""
        if name == "mock":
            return None  # mock 由 _create 处理，不包装

        if name not in self._providers:
            provider = self._create_single(name)
            if provider is not None:
                self._providers[name] = provider
        return self._providers.get(name)

    def _create_single(self, name: str) -> Optional[BaseProvider]:
        """创建单个 provider（不包装 RouterProvider）。"""
        # 延迟导入打破循环依赖
        from .registry import _create

        return _create(name)

    def _available(self, name: str) -> bool:
        """检查 provider 是否可访问（已配置）。"""
        match name:
            case "openai":
                return self._settings.openai_configured
            case "anthropic":
                return self._settings.anthropic_configured
            case "deepseek":
                return self._settings.deepseek_configured
            case "generic":
                return bool(self._settings.openai_api_key or self._settings.deepseek_api_key)
            case "mock":
                return True  # mock 始终可用
            case _:
                return False

    async def _emit_fallback(self, provider_name: str, error: Exception) -> None:
        """广播回退事件到 WebSocket。"""
        logger.warning(f"Provider fallback: {self._primary} → {provider_name}: {error}")
        if self._ws:
            from ..models import WSMessage
            await self._ws.broadcast(
                WSMessage(
                    type="event",
                    agent_id="master",
                    payload={
                        "title": f"Provider 自动回退: {self._primary} → {provider_name}",
                        "detail": f"错误: {str(error)[:200]}",
                        "event_type": "info",
                        "agent_name": "主控",
                    },
                ).model_dump()
            )

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """沿回退链逐个尝试非流式调用。"""
        last_error: Optional[Exception] = None

        for name in self._chain:
            if not self._available(name):
                continue

            if name == "mock":
                # mock 不需要包装
                from .registry import _create
                mock = _create("mock")
                if mock:
                    return await mock.chat(messages, model, temperature, max_tokens)
                continue

            provider = self._get_or_create(name)
            if provider is None:
                continue

            try:
                return await provider.chat(messages, model, temperature, max_tokens)
            except Exception as e:
                last_error = e
                await self._emit_fallback(name, e)
                continue

        # 全部回退链都失败
        error_msg = f"LLM 调用失败：所有提供商均不可用"
        if last_error:
            error_msg += f" (最后错误: {last_error})"
        raise RuntimeError(error_msg)

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """沿回退链逐个尝试流式调用。

        如果某个提供商成功，stream 直接产生 token 并返回。
        仅在全部失败时产生错误消息。
        """
        last_error: Optional[Exception] = None

        for name in self._chain:
            if not self._available(name):
                continue

            if name == "mock":
                from .registry import _create
                mock = _create("mock")
                if mock:
                    async for token in mock.chat_stream(messages, model, temperature, max_tokens):
                        yield token
                    return
                continue

            provider = self._get_or_create(name)
            if provider is None:
                continue

            try:
                async for token in provider.chat_stream(messages, model, temperature, max_tokens):
                    yield token
                return  # 成功完成
            except Exception as e:
                last_error = e
                await self._emit_fallback(name, e)
                continue

        # 全部失败
        yield f"\n[LLM 调用失败：所有提供商均不可用"
        if last_error:
            yield f" (最后错误: {last_error})"
        yield "]\n"
