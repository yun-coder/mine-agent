"""LLM 提供商抽象基类 —— 定义统一的流式与非流式接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


class BaseProvider(ABC):
    """所有 LLM 提供商的抽象基类。"""

    provider_name: str = "base"

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """非流式对话 —— 返回完整文本。"""
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """流式对话 —— 逐 token 产出。"""
        ...
        if False:
            yield ""  # pragma: no cover
