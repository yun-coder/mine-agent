"""通用的 OpenAI 兼容 API 提供商（DeepSeek, Qwen-API 等）。"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from .openai_provider import OpenAIProvider


class GenericProvider(OpenAIProvider):
    """通用 OpenAI 兼容 API 提供商。

    适用于任何提供 OpenAI 兼容 REST API 的服务：
    - DeepSeek API (api.deepseek.com)
    - 阿里云百炼 (dashscope.aliyuncs.com/compatible-mode)
    - 零一万物 (api.lingyiwanwu.com)
    - 月之暗面 (api.moonshot.cn)
    等等。
    """

    provider_name = "generic"

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        super().__init__(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        return await super().chat(messages, model, temperature, max_tokens)

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        async for token in super().chat_stream(messages, model, temperature, max_tokens):
            yield token
