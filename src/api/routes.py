"""REST API 路由 / REST API routes for the LangGraph Agent."""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from pydantic import BaseModel, Field, field_validator

from src.agent.graph import run_agent, get_compiled_graph, AgentState
from src.config import settings
from src.api.auth import api_key_auth
from src.api.rate_limit import check_rate_limit
from langchain_core.messages import HumanMessage

router = APIRouter(prefix="/api/v1")

# OpenAI-compatible router mounted at root level
openai_router = APIRouter()

# ------------------------------------------------------------------
# 请求 / 响应模型 / Request / Response models
# ------------------------------------------------------------------


class QuestionRequest(BaseModel):
    question: str = Field(..., description="用户问题 / User question")
    session_id: str = Field("", description="会话标识 / Session identifier")
    top_k: int = Field(10, ge=1, le=50, description="RAG 返回结果数 / Number of RAG results to return")

    @field_validator("question")
    @classmethod
    def validate_question_length(cls, v: str) -> str:
        if len(v) > 4000:
            raise ValueError("问题不能超过 4000 个字符 / Question cannot exceed 4000 characters")
        return v.strip()


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    qdrant: str = "unknown"
    ollama_llm: str = "unknown"
    ollama_embed: str = "unknown"
    langfuse: str = "unknown"


class AgentResponse(BaseModel):
    answer: str
    tool_log: list[dict]
    session_id: str
    elapsed_ms: int


# ------------------------------------------------------------------
# 健康检查 / Health
# ------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
def health_check():
    """检查所有下游服务的连接状态 / Check connectivity to all downstream services.

    Note: This endpoint is exempt from API key auth (it's a health check).
    """
    health = {
        "status": "ok",
        "version": "1.0.0",
        "qdrant": "disconnected",
        "ollama_llm": "disconnected",
        "ollama_embed": "disconnected",
        "langfuse": "disconnected",
    }

    # 检查 Qdrant / Check Qdrant
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=5)
        count = client.count(settings.qdrant_collection)
        health["qdrant"] = f"connected (points={count.count})"
    except Exception as exc:
        health["qdrant"] = f"error: {exc}"
        health["status"] = "degraded"

    # 检查 Ollama LLM / Check Ollama LLM
    try:
        import httpx
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{settings.ollama_base_url}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            llm_model = models[0] if models else None
            health["ollama_llm"] = f"connected (models={models})" if llm_model else "connected (no models)"
    except Exception as exc:
        health["ollama_llm"] = f"error: {exc}"
        health["status"] = "degraded"

    # 检查 Ollama Embed / Check Ollama Embedder
    try:
        with httpx.Client(timeout=5) as c:
            r = c.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={"model": "bge-m3", "prompt": "health check"},
            )
            if r.status_code == 200:
                health["ollama_embed"] = "connected"
            else:
                health["ollama_embed"] = f"error: {r.status_code}"
                health["status"] = "degraded"
    except Exception as exc:
        health["ollama_embed"] = f"error: {exc}"
        health["status"] = "degraded"

    # 检查 Langfuse / Check Langfuse
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{settings.langfuse_host}/api/public/health")
            if r.status_code == 200:
                health["langfuse"] = "connected"
            else:
                health["langfuse"] = f"error: {r.status_code}"
    except Exception:
        health["langfuse"] = "error: unreachable"

    return HealthResponse(**health)


# ------------------------------------------------------------------
# 智能体问答（非流式）/ Agent Q&A (non-streaming)
# ------------------------------------------------------------------


@router.post("/agent/ask", response_model=AgentResponse, dependencies=[Depends(api_key_auth)])
async def agent_ask(req: QuestionRequest, request: Request):
    """运行 LangGraph 智能体回答问题（非流式）。"""
    # 速率限制 / Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    allowed, limit, remaining = await check_rate_limit("/api/v1/agent/ask", key=client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"请求过于频繁 / Too many requests. Limit: {limit}/min, Remaining: {remaining}",
            headers={"Retry-After": "60"},
        )

    if not req.question.strip():
        raise HTTPException(400, "问题不能为空 / Question cannot be empty")

    t0 = time.time()
    tracer = trace.get_tracer("langgraph-api")
    with tracer.start_as_current_span("agent_ask") as span:
        # 设置标准 input/output attribute，Langfuse exporter 会自动识别
        span.set_attribute("input", req.question)
        span.set_attribute("session_id", req.session_id)
        try:
            result = run_agent(
                question=req.question,
                session_id=req.session_id,
                checkpoint=False,
            )
        except Exception as exc:
            span.set_attribute("error", str(exc))
            raise HTTPException(500, f"智能体执行失败 / Agent execution failed: {exc}")
        answer = result.get("final_answer", result.get("answer", ""))
        span.set_attribute("output", answer)
        span.set_attribute("answer_length", len(answer))
        span.set_attribute("tools_used", len(result.get("tool_log", [])))
    elapsed = int((time.time() - t0) * 1000)

    return AgentResponse(
        answer=result.get("final_answer", result.get("answer", "")),
        tool_log=result.get("tool_log", []),
        session_id=req.session_id,
        elapsed_ms=elapsed,
    )


# ------------------------------------------------------------------
# 智能体问答（LangGraph 原生流式）/ Agent Q&A (native streaming)
# ------------------------------------------------------------------


@router.post("/agent/stream", dependencies=[Depends(api_key_auth)])
async def agent_stream(req: QuestionRequest, request: Request):
    """以 LangGraph 原生 astream() 流式输出运行智能体。"""
    # 速率限制 / Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    allowed, limit, remaining = await check_rate_limit("/api/v1/agent/stream", key=client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"请求过于频繁 / Too many requests. Limit: {limit}/min, Remaining: {remaining}",
            headers={"Retry-After": "60"},
        )
    if not req.question.strip():
        raise HTTPException(400, "问题不能为空 / Question cannot be empty")

    async def event_generator() -> AsyncGenerator[str, None]:
        t0 = time.time()

        # 构建初始状态
        initial: AgentState = {
            "messages": [HumanMessage(content=req.question)],
            "query": req.question,
            "session_id": req.session_id,
            "final_answer": "",
            "tool_log": [],
            "iteration_count": 0,
        }

        # 编译图
        graph = get_compiled_graph(checkpoint=False)

        final_answer = ""
        chunks_buffer: list[str] = []
        tool_log: list[dict] = []

        try:
            # langgraph 1.2.9+ 的 astream_events 返回 dict，不是 tuple
            async for event in graph.astream_events(initial, version="v2"):
                event_type = event.get("event", "")
                event_data = event.get("data", {})

                if event_type == "on_chat_model_stream":
                    chunk = event_data.get("chunk", None)
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        chunks_buffer.append(chunk.content)
                        yield f"data: {json.dumps({'type': 'token', 'data': chunk.content}, ensure_ascii=False)}\n\n"

                elif event_type == "on_chat_model_end":
                    msg = event_data.get("output", None)
                    if msg and hasattr(msg, "content") and msg.content:
                        final_answer = msg.content

                elif event_type == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    yield f"data: {json.dumps({'type': 'tool_start', 'tool': tool_name}, ensure_ascii=False)}\n\n"

                elif event_type == "on_tool_end":
                    tool_name = event.get("name", "")
                    yield f"data: {json.dumps({'type': 'tool_end', 'tool': tool_name}, ensure_ascii=False)}\n\n"

                elif event_type == "on_chain_end":
                    output = event_data.get("output", {})
                    if isinstance(output, dict):
                        fa = output.get("final_answer", "")
                        if fa:
                            final_answer = fa

        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            return

        # 如果流式事件没有捕获到 final_answer，从累积的 chunk 拼接
        if not final_answer and chunks_buffer:
            final_answer = "".join(chunks_buffer).strip()

        if final_answer:
            chunk_size = settings.stream_chunk_size
            for i in range(0, len(final_answer), chunk_size):
                chunk = final_answer[i : i + chunk_size]
                yield f"data: {json.dumps({'type': 'token', 'data': chunk}, ensure_ascii=False)}\n\n"

        meta = {
            "type": "metadata",
            "tool_log": tool_log,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
        yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# 旧版兼容路由 / Legacy alias
# ------------------------------------------------------------------


@router.post("/agent")
def agent_legacy(req: QuestionRequest):
    """旧版端点 — 重定向到 /agent/ask。"""
    return agent_ask(req)


# ------------------------------------------------------------------
# OpenAI 兼容层 / OpenAI-compatible API for OpenWebUI integration
# ------------------------------------------------------------------


class OpenAIChatMessage(BaseModel):
    role: str
    content: str


class OpenAIChatCompletionRequest(BaseModel):
    model: str = "qwen3:8b"
    messages: list[OpenAIChatMessage]
    stream: bool = False


class OpenAIChoice(BaseModel):
    index: int = 0
    message: OpenAIChatMessage
    finish_reason: str = "stop"


class OpenAIUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: OpenAIUsage


async def _openai_chat_completions_stream(
    req: OpenAIChatCompletionRequest,
    lc_messages: list,
) -> StreamingResponse:
    """OpenAI 兼容的流式 SSE 响应 — 供 OpenWebUI 使用。"""

    async def event_generator() -> AsyncGenerator[str, None]:
        initial: AgentState = {
            "messages": lc_messages,
            "query": lc_messages[-1].content if lc_messages else "",
            "session_id": "",
            "final_answer": "",
            "tool_log": [],
            "iteration_count": 0,
        }

        graph = get_compiled_graph(checkpoint=False)
        full_answer = ""
        chunks_buffer: list[str] = []

        try:
            async for event in graph.astream_events(initial, version="v2"):
                event_type = event.get("event", "")

                if event_type == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk", None)
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        chunks_buffer.append(chunk.content)
                        # OpenAI 流式格式：data: {"choices": [{"delta": {"content": "..."}, "index": 0}]}
                        payload = {
                            "choices": [{"delta": {"content": chunk.content}, "index": 0}],
                        }
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

                elif event_type == "on_chat_model_end":
                    msg = event.get("data", {}).get("output", None)
                    if msg and hasattr(msg, "content") and msg.content:
                        full_answer = msg.content

                elif event_type == "on_chain_end":
                    output = event.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        fa = output.get("final_answer", "")
                        if fa:
                            full_answer = fa

        except Exception as exc:
            logger = __import__("loguru").logger
            logger.error(f"[OpenAI Stream] Agent error: {exc}")
            yield f"data: {json.dumps({'choices': [{'delta': {'content': ''}, 'finish_reason': 'stop', 'index': 0}]})}\n\n"
            yield "data: [DONE]\n\n"
            return

        if not full_answer and chunks_buffer:
            full_answer = "".join(chunks_buffer).strip()

        if full_answer:
            chunk_size = settings.stream_chunk_size
            for i in range(0, len(full_answer), chunk_size):
                chunk = full_answer[i : i + chunk_size]
                payload = {
                    "choices": [{"delta": {"content": chunk}, "index": 0}],
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        # 结束标记
        yield f"data: {json.dumps({'choices': [{'delta': {'content': ''}, 'finish_reason': 'stop', 'index': 0}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@openai_router.post("/chat/completions", dependencies=[Depends(api_key_auth)])
async def openai_chat_completions(req: OpenAIChatCompletionRequest):
    """OpenAI 兼容的 chat completions 端点 — 供 OpenWebUI 调用。

    支持 stream=true 的 SSE 流式响应和 stream=false 的 JSON 响应。
    """
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

    # 转换 OpenAI 消息格式为 LangChain 消息
    lc_messages = []
    for msg in req.messages:
        if msg.role == "user":
            lc_messages.append(HumanMessage(content=msg.content))
        elif msg.role == "assistant":
            lc_messages.append(AIMessage(content=msg.content))
        elif msg.role == "system":
            lc_messages.append(SystemMessage(content=msg.content))
        elif msg.role == "tool":
            lc_messages.append(ToolMessage(content=msg.content, tool_call_id=""))

    if req.stream:
        return await _openai_chat_completions_stream(req, lc_messages)

    # 非流式：直接返回 JSON
    result = run_agent(
        question=req.messages[-1].content if req.messages else "",
        session_id="",
        checkpoint=False,
        messages=lc_messages,
    )
    answer = result.get("final_answer", result.get("answer", ""))

    return OpenAIChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
        created=int(time.time()),
        model=req.model,
        choices=[
            OpenAIChoice(
                message=OpenAIChatMessage(role="assistant", content=answer),
            )
        ],
        usage=OpenAIUsage(),
    )


@openai_router.post("/v1/chat/completions", dependencies=[Depends(api_key_auth)])
async def openai_chat_completions_v1(req: OpenAIChatCompletionRequest):
    """OpenAI 兼容的 chat completions 端点（带 /v1 前缀）。"""
    return await openai_chat_completions(req)
