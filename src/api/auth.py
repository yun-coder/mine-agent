"""API 认证中间件 — 基于 API Key 的认证 / API Key-based authentication middleware.

Usage:
    # In .env:
    API_KEY=your-secret-key-here

    # Protect specific endpoints by adding the auth dependency:
    @router.post("/agent/ask", dependencies=[Depends(api_key_auth)])
    def agent_ask(req: QuestionRequest):
        ...

    # Or protect an entire router:
    router.dependencies.append(Depends(api_key_auth))
"""

from __future__ import annotations

import hmac
import os
from fastapi import Header, HTTPException, status

# API Key 从环境变量读取 / Read API key from environment variable
# 每次请求时动态读取，支持测试时修改 / Read dynamically per request for testability
def _get_api_key():
    return os.environ.get("API_KEY", "").strip()

def _is_auth_enabled():
    return bool(_get_api_key())

# HTTP Header 名称 / HTTP Header name
API_KEY_HEADER = "Authorization"


async def api_key_auth(authorization: str = Header(default=None, alias="Authorization")):
    """验证 API Key / Validate API Key."""
    if not _is_auth_enabled():
        return None

    api_key = _get_api_key()

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 API Key / API key missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key = authorization
    if key.startswith("Bearer "):
        key = key[7:]

    if not hmac.compare_digest(key, api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key / Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return key


async def optional_api_key(
    authorization: str = Header(default=None, alias="Authorization"),
):
    """可选认证 — 返回 API Key 或 None / Optional auth — returns API key or None."""
    if not _is_auth_enabled():
        return None
    if not authorization:
        return None
    key = authorization
    if key.startswith("Bearer "):
        key = key[7:]
    if key != _get_api_key():
        return None
    return key
