"""OpenAI 格式兼容的 LLM 提供商（GPT, DeepSeek, Qwen 等）。"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from ..base import BaseProvider


class OpenAIProvider(BaseProvider):
    """适配 OpenAI API 格式的提供商。

    直接兼容: GPT-4o, GPT-5
    也兼容任何 OpenAI 兼容 API（如 DeepSeek、零一万物等）。
    """

    provider_name = "openai"

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return response.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content
