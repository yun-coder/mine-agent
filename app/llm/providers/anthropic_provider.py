"""Anthropic Claude API 提供商 (httpx 直连)。"""

from __future__ import annotations

from typing import AsyncIterator

import httpx

from ..base import BaseProvider


class AnthropicProvider(BaseProvider):
    """适配 Anthropic Claude API 的提供商。"""

    provider_name = "anthropic"

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com"):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def chat(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        system, message_list = self._convert_messages(messages)
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self._base_url}/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "system": system or None,
                    "messages": message_list,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                block["text"]
                for block in data.get("content", [])
                if block.get("type") == "text"
            )

    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        system, message_list = self._convert_messages(messages)
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "system": system or None,
                    "messages": message_list,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    import json
                    try:
                        data = json.loads(data_str)
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield delta.get("text", "")
                    except json.JSONDecodeError:
                        continue

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """将 OpenAI 格式消息转换为 Anthropic 格式。

        返回: (system_prompt, messages_without_system)
        """
        system = ""
        converted: list[dict] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system = content
            else:
                converted.append({
                    "role": "user" if role == "user" else "assistant",
                    "content": content,
                })
        return system, converted
