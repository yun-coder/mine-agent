"""LangGraph 智能体编排 — 纯 Agent 架构（Agentic RAG）。

图流程 / Graph flow:
  START → agent_orchestrator (ReACT 子图, 自主调用工具)
         → format_response → END

Agent 拥有全部工具（rag_query, code_search, terminal_execute 等），
自主决定何时调用、调用哪个、是否多次调用。

Architecture change 2026-07-09:
  Removed: intent_router, conditional_router, rag_fetch, code_search_node
  Reason:  Agent should decide when/if to use RAG, not hard-coded keyword routing.
"""

from __future__ import annotations

import base64
import asyncio
import re
import threading
import time
import uuid
from typing import Any

import httpx
from opentelemetry import trace

# Optional: Langfuse / OTel auto-instrumentation
try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    _HAS_OTEL_SDK = True
except ImportError:
    _HAS_OTEL_SDK = False

try:
    from opentelemetry.instrumentation.langchain import LangchainInstrumentor
    _HAS_LANGCHAIN_OTEL = True
except ImportError:
    _HAS_LANGCHAIN_OTEL = False

# 配置 Langfuse OTel 以记录输入/输出内容
# 必须在 instrument() 调用前设置，启用完整内容追踪
TRACELOOP_TRACE_CONTENT = "TRACELOOP_TRACE_CONTENT"

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from loguru import logger

from src.config import settings
from src.agent.tools import AGENT_TOOLS
from src.tdai_client import get_client, TDAIMemoryClient

# ------------------------------------------------------------------
# Langfuse 集成 / Langfuse integration (optional observability)
# ------------------------------------------------------------------

_langfuse_initialized = False


def _init_langfuse():
    """初始化 Langfuse OTel 追踪（仅初始化一次）。"""
    global _langfuse_initialized
    if _langfuse_initialized:
        return

    if not _HAS_OTEL_SDK:
        logger.debug("[Langfuse] opentelemetry-sdk 未安装，跳过")
        return

    if settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            api_key = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
            encoded = base64.b64encode(api_key.encode()).decode()

            exporter = OTLPSpanExporter(
                endpoint=f"{settings.langfuse_host}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {encoded}"},
            )

            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)

            if _HAS_LANGCHAIN_OTEL:
                try:
                    import os
                    os.environ["TRACELOOP_TRACE_CONTENT"] = "true"
                    LangchainInstrumentor().instrument()
                    logger.debug("[Langfuse] LangchainInstrumentor 已激活")
                except Exception as exc2:
                    logger.debug("[Langfuse] LangchainInstrumentor 调用失败: {exc2}")
            else:
                logger.debug("[Langfuse] opentelemetry-instrumentation-langchain 未安装，仅使用手动追踪")

            _langfuse_initialized = True
            logger.info(f"[Langfuse] OTel 追踪已配置 → {settings.langfuse_host}")
        except Exception as exc:
            logger.warning(f"[Langfuse] 初始化失败 ({exc})，跳过")
    else:
        logger.debug("[Langfuse] 未配置 LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY，跳过")


def _is_langfuse_enabled() -> bool:
    return _langfuse_initialized


_init_langfuse()

# ------------------------------------------------------------------
# 状态定义 / State
# ------------------------------------------------------------------


class AgentState(MessagesState, total=False):
    """LangGraph 智能体状态 — 简化版（不含硬编码路由字段）。"""

    # 查询信息 / Query info
    query: str
    session_id: str

    # 最终输出 / Final output
    final_answer: str
    tool_log: list[dict]

    # 迭代计数 / Iteration counter
    iteration_count: int


_MAX_ITERATIONS = settings.max_iterations

SYSTEM_PROMPT = (
    "你是智能助手，可以根据用户的问题类型自主选择合适的工具来回答。\n"
    "你有以下工具可用：\n"
    "- rag_query: 搜索知识库（Qdrant 向量检索 + BM25 混合检索）。"
    "用于回答基于已有文档、笔记、知识库的事实性问题，例如公司制度、技术资料等。\n"
    "- code_search: 搜索代码仓库中的文件。\n"
    "- code_read: 读取源代码文件内容（带行号）。\n"
    "- file_tree: 浏览目录结构。\n"
    "- terminal_execute: 执行终端命令（受安全沙箱保护，危险操作被拦截）。\n"
    "- system_info: 获取系统信息（CPU、内存、磁盘）。\n"
    "- calculate: 安全数学计算。\n"
    "- get_current_time: 获取当前日期和时间。\n\n"
    "请根据问题类型选择合适的工具。如果引用了知识库内容，请在回答中标注来源。\n"
    "当工具返回的结果不足以回答问题时，可以修改查询再次调用，或结合多个工具的结果综合分析。"
)


# ======================================================================
# ReACT 子图构建 / ReACT subgraph builder
# ======================================================================

_REACT_AGENT_CACHE: CompiledStateGraph | None = None


class AgentExecutionError(RuntimeError):
    """Raised when the model/tool execution path cannot produce an answer."""


@wrap_model_call
def _trim_agent_history(request, handler):
    """Limit model context without mutating the persisted conversation state."""
    max_history = 20
    messages = request.messages
    if len(messages) > max_history:
        request = request.override(messages=messages[-max_history:])
    return handler(request)


def _build_react_agent() -> CompiledStateGraph:
    """懒编译 ReACT agent 子图。"""
    global _REACT_AGENT_CACHE
    if _REACT_AGENT_CACHE is not None:
        return _REACT_AGENT_CACHE

    llm = ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0.1,
        num_ctx=8192,
    )

    _REACT_AGENT_CACHE = create_agent(
        model=llm,
        tools=AGENT_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        middleware=[_trim_agent_history],
        checkpointer=None,
        name="react_agent",
    )
    return _REACT_AGENT_CACHE


def agent_orchestrator(state: AgentState) -> AgentState:
    """委托给 ReACT 子图进行工具调用循环。"""
    react_agent = _build_react_agent()

    react_input: dict[str, Any] = {
        "messages": list(state.get("messages", [])),
    }

    iteration_count = state.get("iteration_count", 0)
    if iteration_count >= _MAX_ITERATIONS:
        answer_msg = AIMessage(
            content="已达到最大迭代次数，以下是我的回答："
        )
        state["messages"] = list(state.get("messages", [])) + [answer_msg]
        state["final_answer"] = answer_msg.content
        return state

    result = None
    execution_error: Exception | None = None
    try:
        result = react_agent.invoke(react_input)
    except Exception as exc:
        execution_error = exc
        # 对可恢复异常尝试重试 / Retry once for recoverable errors
        _RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError)
        if isinstance(exc, _RETRYABLE) and settings.max_agent_retries > 0:
            logger.warning(f"[Orchestrator] ReACT agent 调用失败，{settings.max_agent_retries} 次重试后重试: {exc}")
            time.sleep(1.0)
            try:
                result = react_agent.invoke(react_input)
            except Exception as exc2:
                logger.error(f"[Orchestrator] 重试仍然失败: {exc2}")
                execution_error = exc2
                result = None

        if result is None:
            logger.error(
                f"[Orchestrator] ReACT agent 调用失败: {execution_error}"
            )
            raise AgentExecutionError(
                "Agent model/tool execution failed"
            ) from execution_error

    messages = result.get("messages", [])
    final_answer = ""
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.content:
            final_answer = msg.content
            break

    state["messages"] = messages
    state["final_answer"] = final_answer
    state["iteration_count"] = iteration_count + 1
    state["tool_log"] = state.get("tool_log", []) + [
        {"step": "orchestrator", "direct_answer": bool(final_answer)}
    ]
    return state


# ======================================================================
# 格式化回复节点 / Format response node
# ======================================================================


def format_response(state: AgentState) -> AgentState:
    """确保最终答案已正确提取。"""
    answer = state.get("final_answer", "")

    if not answer:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                answer = msg.content
                break

    state["final_answer"] = answer
    return state


# ======================================================================
# 构建图 / Build the graph
# ======================================================================


_CHECKPOINTER = None
_CHECKPOINTER_CONTEXT = None
_CHECKPOINTER_LOCK = threading.Lock()
_COMPILED_GRAPH_CACHE: dict[bool, CompiledStateGraph] = {}
_ASYNC_CHECKPOINTER = None
_ASYNC_CHECKPOINTER_CONTEXT = None
_ASYNC_CHECKPOINTER_LOCK = asyncio.Lock()
_ASYNC_COMPILED_GRAPH_CACHE: dict[bool, CompiledStateGraph] = {}


def _normalized_pg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


def _build_checkpointer():
    """Build one process-wide checkpointer and keep its DB context alive."""
    global _CHECKPOINTER, _CHECKPOINTER_CONTEXT
    if _CHECKPOINTER is not None:
        return _CHECKPOINTER

    with _CHECKPOINTER_LOCK:
        if _CHECKPOINTER is not None:
            return _CHECKPOINTER
        if settings.pg_dsn:
            try:
                from langgraph.checkpoint.postgres import PostgresSaver

                context = PostgresSaver.from_conn_string(
                    _normalized_pg_dsn(settings.pg_dsn)
                )
                saver = context.__enter__()
                saver.setup()
                _CHECKPOINTER_CONTEXT = context
                _CHECKPOINTER = saver
                logger.info("[Graph] PostgreSQL checkpointer enabled")
                return saver
            except Exception as exc:
                if _CHECKPOINTER_CONTEXT is not None:
                    _CHECKPOINTER_CONTEXT.__exit__(type(exc), exc, exc.__traceback__)
                    _CHECKPOINTER_CONTEXT = None
                message = f"PostgreSQL checkpointer initialization failed: {exc}"
                if settings.checkpoint_required:
                    raise RuntimeError(message) from exc
                logger.warning(f"[Graph] {message}; using MemorySaver")

        if settings.checkpoint_required:
            raise RuntimeError("Checkpointing is required but PG_DSN is not configured")
        _CHECKPOINTER = MemorySaver()
        logger.warning("[Graph] Using in-memory checkpointer; state is not durable")
        return _CHECKPOINTER


def close_checkpointer() -> None:
    global _CHECKPOINTER, _CHECKPOINTER_CONTEXT
    with _CHECKPOINTER_LOCK:
        if _CHECKPOINTER_CONTEXT is not None:
            _CHECKPOINTER_CONTEXT.__exit__(None, None, None)
        _CHECKPOINTER_CONTEXT = None
        _CHECKPOINTER = None
        _COMPILED_GRAPH_CACHE.clear()
        _ASYNC_COMPILED_GRAPH_CACHE.clear()


async def _build_async_checkpointer():
    """Build an async checkpointer for LangGraph streaming execution."""
    global _ASYNC_CHECKPOINTER, _ASYNC_CHECKPOINTER_CONTEXT
    if _ASYNC_CHECKPOINTER is not None:
        return _ASYNC_CHECKPOINTER

    async with _ASYNC_CHECKPOINTER_LOCK:
        if _ASYNC_CHECKPOINTER is not None:
            return _ASYNC_CHECKPOINTER
        if settings.pg_dsn:
            context = None
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                context = AsyncPostgresSaver.from_conn_string(
                    _normalized_pg_dsn(settings.pg_dsn)
                )
                saver = await context.__aenter__()
                await saver.setup()
                _ASYNC_CHECKPOINTER_CONTEXT = context
                _ASYNC_CHECKPOINTER = saver
                logger.info("[Graph] Async PostgreSQL checkpointer enabled")
                return saver
            except Exception as exc:
                if context is not None:
                    await context.__aexit__(type(exc), exc, exc.__traceback__)
                message = f"Async PostgreSQL checkpointer initialization failed: {exc}"
                if settings.checkpoint_required:
                    raise RuntimeError(message) from exc
                logger.warning(f"[Graph] {message}; using async in-memory checkpointer")

        if settings.checkpoint_required:
            raise RuntimeError("Checkpointing is required but PG_DSN is not configured")
        _ASYNC_CHECKPOINTER = MemorySaver()
        logger.warning("[Graph] Using async in-memory checkpointer; state is not durable")
        return _ASYNC_CHECKPOINTER


async def get_compiled_graph_async(checkpoint: bool = True):
    """Return a graph compiled with a checkpointer compatible with async streaming."""
    if checkpoint in _ASYNC_COMPILED_GRAPH_CACHE:
        return _ASYNC_COMPILED_GRAPH_CACHE[checkpoint]
    graph_builder = build_graph(checkpoint=checkpoint)
    checkpointer = await _build_async_checkpointer() if checkpoint else False
    compiled = graph_builder.compile(checkpointer=checkpointer)
    _ASYNC_COMPILED_GRAPH_CACHE[checkpoint] = compiled
    return compiled


def build_graph(checkpoint: bool = True) -> StateGraph:
    """构建并编译 LangGraph StateGraph — 纯 Agent 架构。"""
    builder = StateGraph(AgentState)

    builder.add_node("agent_orchestrator", agent_orchestrator)
    builder.add_node("format_response", format_response)

    builder.set_entry_point("agent_orchestrator")
    builder.add_edge("agent_orchestrator", "format_response")
    builder.add_edge("format_response", END)

    return builder


def get_compiled_graph(checkpoint: bool = True):
    if checkpoint in _COMPILED_GRAPH_CACHE:
        return _COMPILED_GRAPH_CACHE[checkpoint]
    graph_builder = build_graph(checkpoint=checkpoint)
    checkpointer = _build_checkpointer() if checkpoint else False
    compiled = graph_builder.compile(checkpointer=checkpointer)
    _COMPILED_GRAPH_CACHE[checkpoint] = compiled
    return compiled


# ======================================================================
# 公开运行辅助函数 / Public run helper
# ======================================================================


def run_agent(
    question: str,
    session_id: str = "",
    checkpoint: bool = True,
    messages: list = None,
) -> dict[str, Any]:
    """为单个问题运行智能体。返回 {answer, tool_log}。"""
    graph = get_compiled_graph(checkpoint=checkpoint)

    initial_state: AgentState = {
        "messages": list(messages) if messages else [HumanMessage(content=question)],
        "query": question,
        "session_id": session_id,
        "final_answer": "",
        "tool_log": [],
        "iteration_count": 0,
    }

    # ================================================================
    # TDAI Memory: 召回历史记忆并注入上下文
    # ================================================================
    tdai: TDAIMemoryClient = get_client()
    if tdai.enabled:
        try:
            memory_result = tdai.sync_recall(
                query=question,
                session_key=session_id or "default",
            )
            system_ctx = memory_result.get("appendSystemContext", "")
            memory_count = memory_result.get("memory_count", 0)
            if system_ctx:
                memory_msg = SystemMessage(
                    content=(
                        "以下是与用户相关的历史记忆，请参考这些信息回答：\n\n"
                        f"{system_ctx}"
                    )
                )
                initial_state["messages"].insert(0, memory_msg)
                initial_state["tool_log"] = [
                    {"step": "tdai_recall", "memory_count": memory_count}
                ]
                logger.info(f"[TDAI] 注入 {memory_count} 条历史记忆到会话 / Injected {memory_count} memories")
        except Exception as exc:
            logger.warning(f"[TDAI] 召回失败（不影响主流程）/ Recall failed (non-fatal): {exc}")

    # ================================================================
    # 调用图（Langfuse OTel 自动捕获内部 span）
    # ================================================================
    tracer = trace.get_tracer("langgraph-agent")
    thread_id = session_id.strip() or f"request-{uuid.uuid4().hex}"
    run_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": max(10, _MAX_ITERATIONS * 4),
    }
    with tracer.start_as_current_span("run_agent") as span:
        span.set_attribute("input", question)
        span.set_attribute("session_id", session_id)
        result = graph.invoke(initial_state, config=run_config)
        span.set_attribute("output", result.get("final_answer", ""))
        span.set_attribute("answer_length", len(result.get("final_answer", "")))

    # ================================================================
    # TDAI Memory: 保存本轮对话到记忆系统
    # ================================================================
    final_answer = result.get("final_answer", "")
    if tdai.enabled and final_answer:
        try:
            tdai.sync_capture(
                user_content=question,
                assistant_content=final_answer,
                session_key=session_id or "default",
                session_id=session_id,
            )
            logger.debug("[TDAI] 对话已保存到记忆系统 / Conversation captured to memory")
        except Exception as exc:
            logger.debug(f"[TDAI] 保存失败（不影响主流程）/ Capture failed (non-fatal): {exc}")

    return {
        "answer": final_answer,
        "tool_log": result.get("tool_log", []),
        "iteration_count": result.get("iteration_count", 0),
        "session_id": thread_id,
    }
