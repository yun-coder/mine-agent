"""LangGraph 智能体编排 — 混合架构（StateGraph + create_react_agent）。

Outer graph (StateGraph): 意图路由 + RAG/代码预处理
  START → intent_router → conditional_router
                               ├─ rag_fetch → agent_orchestrator ─┐
                               ├─ code_search → agent_orchestrator ─┤
                               └─ tool/general → agent_orchestrator ─┘
                                       ↓
                               format_response → END

Inner ReACT subgraph (create_react_agent):
  由 LangGraph 1.x 的 create_react_agent 管理，
  自动处理 agent ↔ tool 调用循环，内置 ToolNode。

图流程 / Graph flow:
  1. intent_router — 启发式关键词分类意图
  2. conditional_router — 根据意图路由到 rag_fetch / code_search / agent_orchestrator
  3. rag_fetch / code_search — 预处理，注入上下文到 messages
  4. agent_orchestrator — 委托给 create_react_agent 子图
  5. format_response — 格式化最终答案，附加引用和日志
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# 配置 Langfuse OTel 以记录输入/输出内容
# 必须在 instrument() 调用前设置，启用完整内容追踪
TRACELOOP_TRACE_CONTENT = "TRACELOOP_TRACE_CONTENT"

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from langgraph.types import Send
from loguru import logger

from src.config import settings
from src.agent.tools import AGENT_TOOLS, TOOL_DEFINITIONS
from src.tdai_client import get_client, TDAIMemoryClient

# ------------------------------------------------------------------
# Langfuse 集成 / Langfuse integration (optional observability)
# ------------------------------------------------------------------
# Langfuse 4.x uses OpenTelemetry-based auto-instrumentation.
# Once the Langfuse client is initialized, it automatically captures
# spans from LangGraph invoke()/astream_events() without any callbacks.

_langfuse_initialized = False


def _init_langfuse():
    """初始化 Langfuse OTel 追踪（仅初始化一次）。

    配置 OTel SDK 导出器 + 激活 Langchain 自动插桩，
    使所有 LangGraph/LangChain 调用自动生成 span 并发送到 Langfuse。
    """
    global _langfuse_initialized
    if _langfuse_initialized:
        return

    if settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            # 构建 Basic Auth: base64(public_key:secret_key)
            api_key = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}"
            encoded = base64.b64encode(api_key.encode()).decode()

            # 创建 OTLP exporter → Langfuse 的 OpenTelemetry 端点
            exporter = OTLPSpanExporter(
                endpoint=f"{settings.langfuse_host}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {encoded}"},
            )

            # 注册到全局 TracerProvider
            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)

            # 激活 Langchain 自动插桩 — 捕获所有 runnable 调用
            try:
                # 确保捕获完整的输入/输出内容
                import os
                os.environ["TRACELOOP_TRACE_CONTENT"] = "true"
                LangchainInstrumentor().instrument()
                logger.debug("[Langfuse] LangchainInstrumentor 已激活")
            except Exception as exc2:
                logger.debug("[Langfuse] LangchainInstrumentor 未安装，仅使用手动追踪")

            _langfuse_initialized = True
            logger.info(f"[Langfuse] OTel 追踪已配置 → {settings.langfuse_host}")
        except Exception as exc:
            logger.warning(f"[Langfuse] 初始化失败 ({exc})，跳过")
    else:
        logger.debug("[Langfuse] 未配置 LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY，跳过")


def _is_langfuse_enabled() -> bool:
    """检查 Langfuse 是否已启用。"""
    return _langfuse_initialized


# 全局一次性初始化 —— 在应用启动时调用
# 注：实际初始化已移至 main.py（模块导入前），这里仅保留延迟初始化作为后备
_init_langfuse()

# ------------------------------------------------------------------
# 状态定义 / State
# ------------------------------------------------------------------


class AgentState(MessagesState, total=False):
    """LangGraph 智能体的扩展状态 / Extended state for the LangGraph agent."""

    # 意图分类 / Intent classification
    intent_category: str  # "rag" | "code" | "tool" | "general"
    intent_confidence: float

    # 检索结果（由 rag_fetch 写入）/ Retrieval results (written by rag_fetch)
    retriever_context: str
    retriever_sources: list[str]
    retriever_scores: list[float]

    # 代码搜索结果 / Code search results
    code_results: list[dict]

    # 工具执行 / Tool execution
    pending_tool_calls: list[dict]
    tool_results: list[dict]

    # 最终输出 / Final output
    final_answer: str
    tool_log: list[dict]

    # 迭代计数 / Iteration counter (for outer graph tracing)
    iteration_count: int


# ------------------------------------------------------------------
# 常量 / Constants
# ------------------------------------------------------------------

_MAX_ITERATIONS = settings.max_iterations

# 按意图过滤的工具定义（减少上下文窗口占用）/ Tool definitions filtered per intent
_TOOLS_BY_INTENT: dict[str, list[dict]] = {
    "rag": [t for t in TOOL_DEFINITIONS if t["function"]["name"] == "rag_query"],
    "code": [
        t for t in TOOL_DEFINITIONS
        if t["function"]["name"] in ("code_search", "code_read", "file_tree", "get_current_time")
    ],
    "tool": [
        t for t in TOOL_DEFINITIONS
        if t["function"]["name"] in (
            "terminal_execute", "system_info", "calculate", "get_current_time",
        )
    ],
    "general": TOOL_DEFINITIONS,
}

# 按意图区分的系统提示词 / Intent-specific system prompts
_SYSTEM_PROMPTS: dict[str, str] = {
    "rag": (
        "你是企业知识库助手。用户的问题需要查阅公司文档才能回答。"
        "请使用 rag_query 工具搜索知识库，并在回答中标注引用来源。"
        "如果知识库中没有相关信息，如实告知用户。"
    ),
    "code": (
        "你是代码助手。用户的问题涉及代码搜索、文件读取或目录浏览。"
        "使用 code_search、code_read、file_tree 工具协助用户。"
    ),
    "tool": (
        "你是系统助手。用户需要执行命令或获取系统信息。"
        "使用 terminal_execute、system_info、calculate 工具。"
        "注意：终端命令受到安全沙箱保护，危险操作会被拦截。"
    ),
    "general": (
        "你是综合智能助手。可以根据用户意图调用合适的工具回答问题。"
        "你有以下工具可用：\n"
        "- rag_query: 搜索企业知识库\n"
        "- code_search: 搜索代码仓库\n"
        "- code_read: 读取代码文件\n"
        "- file_tree: 浏览目录结构\n"
        "- terminal_execute: 执行终端命令（受沙箱保护）\n"
        "- system_info: 获取系统信息\n"
        "- calculate: 安全数学计算\n"
        "- get_current_time: 获取当前时间\n\n"
        "请根据问题类型选择合适的工具。每次回答必须基于工具返回的结果。"
    ),
}

# ------------------------------------------------------------------
# 意图路由器 — 启发式分类 / Intent router — heuristic classification
# ------------------------------------------------------------------

_CODE_KEYWORDS = [
    r"\b(code|代码|python|javascript|java|go|rust|c\+\+|typescript)\b",
    r"\b(function|class|method|api|接口|模块)\b",
    r"\b(search|find|locate|搜索|查找|定位)\b.*(file|代码|\.py|\.js|\.ts)",
    r"\b(refactor|debug|fix|修复|重构|审查)\b",
]

_RAG_KEYWORDS = [
    r"\b(knowledge|knowledge base|知识库|文档|手册|policy|制度)\b",
    r"\b(document|论文|报告|article|faq|常见问题)\b",
    r"\b(rag|检索|recall|召回|similar|相似|semantic)\b",
]

_TOOL_KEYWORDS = [
    r"\b(run|exec|execute|运行|执行|terminal|shell|command|命令)\b",
    r"\b(ls|cd|mkdir|rm|cp|mv|git|docker|ps|top)\b",
    r"\b(system|cpu|memory|disk|系统|内存|磁盘)\b",
]


def _heuristic_intent(query: str) -> tuple[str, float]:
    """使用关键词匹配分类意图。返回 (类别, 置信度)。"""
    q = query.lower()
    scores = {"rag": 0, "code": 0, "tool": 0}
    for kw in _CODE_KEYWORDS:
        if re.search(kw, q):
            scores["code"] += 1
    for kw in _RAG_KEYWORDS:
        if re.search(kw, q):
            scores["rag"] += 1
    for kw in _TOOL_KEYWORDS:
        if re.search(kw, q):
            scores["tool"] += 1

    total = sum(scores.values())
    if total == 0:
        return "general", 0.0

    best = max(scores, key=scores.get)
    confidence = scores[best] / total
    return best, confidence


def intent_router(state: AgentState) -> AgentState:
    """分类用户意图并路由到相应的子流程。"""
    query = state.get("query", "")
    if not query:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                query = msg.content
                break

    category, confidence = _heuristic_intent(query)
    state["intent_category"] = category
    state["intent_confidence"] = confidence

    logger.debug(f"[Router] intent={category} confidence={confidence:.2f}")
    return state


# ------------------------------------------------------------------
# 条件路由器 — 基于意图的 Send 路由 / Conditional router via Send
# ------------------------------------------------------------------

def conditional_router(state: AgentState) -> list[Send]:
    """根据分类的意图，使用 Send 并发路由到相应节点。"""
    intent = state.get("intent_category", "general")
    if intent == "rag":
        return [Send("rag_fetch", state)]
    elif intent == "code":
        return [Send("code_search", state)]
    else:
        # tool / general 直接进 orchestrator
        return [Send("agent_orchestrator", state)]


# ------------------------------------------------------------------
# RAG 检索节点 / RAG fetch node
# ------------------------------------------------------------------

def rag_fetch(state: AgentState) -> AgentState:
    """执行 RAG 检索并将上下文注入消息。"""
    query = state.get("query", "")
    if not query:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                query = msg.content
                break

    from src.rag.qdrant_client import QdrantConnector

    conn = QdrantConnector(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        collection=settings.qdrant_collection,
    )
    results = conn.search(query, top_k=settings.top_k_rag)

    if not results:
        state["retriever_context"] = ""
        state["retriever_sources"] = []
        state["retriever_scores"] = []
        return state

    context_parts: list[str] = []
    sources: list[str] = []
    scores: list[float] = []
    for i, r in enumerate(results, 1):
        meta = r.get("metadata", {})
        fname = meta.get("filename", meta.get("source", "unknown"))
        sources.append(fname)
        scores.append(r.get("score", 0))
        context_parts.append(
            f"[{i}] 来源: {fname} (score={r.get('score', 0):.3f})\n{r['text'][:500]}"
        )

    state["retriever_context"] = "\n\n".join(context_parts)
    state["retriever_sources"] = sources
    state["retriever_scores"] = scores

    ctx_msg = SystemMessage(
        content=(
            "以下是从知识库中检索到的相关资料，请在回答时引用这些资料：\n\n"
            f"--- 检索结果 ---\n{state['retriever_context']}\n"
            "--- 结束 ---"
        )
    )
    messages = list(state.get("messages", []))
    messages.append(ctx_msg)
    state["messages"] = messages
    state["tool_log"] = state.get("tool_log", []) + [
        {"step": "rag_fetch", "tool": "rag_query", "results": len(results)}
    ]
    return state


# ------------------------------------------------------------------
# 代码搜索节点 / Code search node
# ------------------------------------------------------------------

def code_search_node(state: AgentState) -> AgentState:
    """执行代码搜索并注入结果。"""
    query = state.get("query", "")
    if not query:
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                query = msg.content
                break

    results_str = _exec_code_search(query, max_results=settings.top_k_code)

    state["code_results"] = results_str
    ctx_msg = SystemMessage(content=f"代码搜索结果:\n{results_str}")
    messages = list(state.get("messages", []))
    messages.append(ctx_msg)
    state["messages"] = messages
    state["tool_log"] = state.get("tool_log", []) + [
        {"step": "code_search", "tool": "code_search", "results_count": results_str.count('"file"')}
    ]
    return state


def _exec_code_search(query: str, max_results: int = 20) -> str:
    """执行代码搜索（从 tools.py 复用）。"""
    from src.agent.tools import _exec_code_search as _inner
    return _inner(query, max_results=max_results)


# ------------------------------------------------------------------
# ReACT 子图构建 / ReACT subgraph builder
# ------------------------------------------------------------------

_REACT_AGENT_CACHE: CompiledStateGraph | None = None


def _build_react_agent() -> CompiledStateGraph:
    """懒编译 ReACT agent 子图。/ Lazy-compile the ReACT agent subgraph."""
    global _REACT_AGENT_CACHE
    if _REACT_AGENT_CACHE is not None:
        return _REACT_AGENT_CACHE

    from langgraph.runtime import Runtime

    # 动态模型构建器 — 根据意图选择工具子集
    # Signature: (state, runtime) -> BaseChatModel
    def model_fn(state: dict, runtime: Runtime) -> ChatOllama:
        intent = state.get("intent_category", "general")
        tools = _TOOLS_BY_INTENT.get(intent, _TOOLS_BY_INTENT["general"])

        llm = ChatOllama(
            model=settings.llm_model,
            base_url=settings.ollama_base_url,
            temperature=0.1,
            num_ctx=8192,
        )
        if tools:
            return llm.bind_tools(tools)
        return llm

    # 动态系统提示词 — 返回消息列表
    def prompt_fn(state: dict) -> list:
        intent = state.get("intent_category", "general")
        system_prompt = _SYSTEM_PROMPTS.get(intent, _SYSTEM_PROMPTS["general"])
        messages = list(state.get("messages", []))
        max_history = 10
        if len(messages) > max_history:
            messages = messages[-max_history:]
        return [SystemMessage(content=system_prompt)] + messages

    _REACT_AGENT_CACHE = create_react_agent(
        model=model_fn,
        tools=AGENT_TOOLS,
        prompt=prompt_fn,
        checkpointer=None,  # 外层图负责检查点
        name="react_agent",
    )
    return _REACT_AGENT_CACHE


def agent_orchestrator(state: AgentState) -> AgentState:
    """委托给 ReACT 子图进行工具调用循环。"""
    react_agent = _build_react_agent()

    # 准备输入
    react_input: dict[str, Any] = {
        "messages": list(state.get("messages", [])),
        "intent_category": state.get("intent_category", "general"),
    }

    # 检查迭代次数上限
    iteration_count = state.get("iteration_count", 0)
    if iteration_count >= _MAX_ITERATIONS:
        # 达到上限，强制直接回答
        answer_msg = AIMessage(
            content="已达到最大迭代次数，以下是我的回答："
        )
        state["messages"] = list(state.get("messages", [])) + [answer_msg]
        state["final_answer"] = answer_msg.content
        return state

    # 调用 ReACT 子图（Langfuse 4.x 通过 OTel 自动捕获，无需 callbacks）
    try:
        result = react_agent.invoke(react_input)
    except Exception as exc:
        logger.error(f"[Orchestrator] ReACT agent 调用失败: {exc}")
        # 回退：安全提示，不暴露内部细节 / Fallback: safe message, no internal details leaked
        fallback_msg = AIMessage(content="工具调用过程中发生错误，请稍后重试。/ An error occurred during tool execution. Please try again.")
        messages = list(state.get("messages", []))
        messages.append(fallback_msg)
        state["messages"] = messages
        state["final_answer"] = fallback_msg.content
        return state

    # 提取最终答案
    messages = result.get("messages", [])
    final_answer = ""
    for msg in reversed(messages):
        if hasattr(msg, "content") and msg.content:
            final_answer = msg.content
            break

    state["messages"] = messages
    state["final_answer"] = final_answer
    state["tool_log"] = state.get("tool_log", []) + [
        {"step": "orchestrator", "direct_answer": bool(final_answer)}
    ]
    return state


# ------------------------------------------------------------------
# 格式化回复节点 / Format response node
# ------------------------------------------------------------------

def format_response(state: AgentState) -> AgentState:
    """格式化最终答案，包含引用和工具日志。"""
    answer = state.get("final_answer", "")

    if not answer:
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                answer = msg.content
                break

    # 附加引用信息
    citations = state.get("retriever_sources", [])
    if citations:
        answer += f"\n\n--- 资料来源 ---\n" + "\n".join(f"• {s}" for s in citations)

    # 附加工具调用日志摘要
    tool_log = state.get("tool_log", [])
    if tool_log:
        answer += f"\n\n--- 工具调用 ({len(tool_log)} 步) ---"
        for entry in tool_log:
            step = entry.get("step", "?")
            if "tool" in entry:
                answer += f"\n  [{step}] {entry['tool']}"
            elif "direct_answer" in entry:
                answer += f"\n  [{step}] 直接回答"
            else:
                answer += f"\n  [{step}]"

    state["final_answer"] = answer
    return state


# ------------------------------------------------------------------
# 构建图 / Build the graph
# ------------------------------------------------------------------

def _build_checkpointer():
    """根据配置构建检查点保存器。/ Build checkpointer based on configuration."""
    if settings.pg_dsn:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            return PostgresSaver.from_conn_string(settings.pg_dsn)
        except ImportError:
            logger.warning("[Graph] langgraph-checkpoint-postgres 不可用，回退到 MemorySaver")
        except Exception as exc:
            logger.warning(f"[Graph] Postgres checkpointer 初始化失败 ({exc})，回退到 MemorySaver")

    # 默认：MemorySaver
    try:
        return MemorySaver()
    except Exception as exc:
        logger.warning(f"[Graph] MemorySaver 不可用: {exc}")
        return None


def build_graph(checkpoint: bool = True) -> StateGraph:
    """构建并编译 LangGraph StateGraph。"""

    builder = StateGraph(AgentState)

    # 注册节点 / Register nodes
    builder.add_node("intent_router", intent_router)
    builder.add_node("rag_fetch", rag_fetch)
    builder.add_node("code_search", code_search_node)
    builder.add_node("agent_orchestrator", agent_orchestrator)
    builder.add_node("format_response", format_response)

    # 入口点 / Entry point
    builder.set_entry_point("intent_router")

    # 从 intent_router 出发的条件边（使用 Send）
    builder.add_conditional_edges(
        "intent_router",
        conditional_router,
        {
            "rag_fetch": "rag_fetch",
            "code_search": "code_search",
            "agent_orchestrator": "agent_orchestrator",
        },
    )

    # 子节点反馈给编排器以生成最终答案
    builder.add_edge("rag_fetch", "agent_orchestrator")
    builder.add_edge("code_search", "agent_orchestrator")

    # 编排器 → format_response → END
    builder.add_edge("agent_orchestrator", "format_response")
    builder.add_edge("format_response", END)

    # 可选的检查点编译
    if checkpoint:
        cp = _build_checkpointer()
        if cp:
            builder.checkpointer = cp
        else:
            logger.warning("[Graph] 无检查点可用，无检查点运行")

    return builder


def get_compiled_graph(checkpoint: bool = True):
    """返回编译好的 LangGraph 图实例。"""
    graph_builder = build_graph(checkpoint=checkpoint)
    return graph_builder.compile()


# ------------------------------------------------------------------
# 公开运行辅助函数 / Public run helper
# ------------------------------------------------------------------

def run_agent(
    question: str,
    session_id: str = "",
    checkpoint: bool = True,
    messages: list = None,
) -> dict[str, Any]:
    """为单个问题运行智能体。返回 {answer, tool_log, trace}。"""
    graph = get_compiled_graph(checkpoint=checkpoint)

    initial_state: AgentState = {
        "messages": messages if messages else [HumanMessage(content=question)],
        "query": question,
        "session_id": session_id,
        "intent_category": "",
        "intent_confidence": 0.0,
        "retriever_context": "",
        "retriever_sources": [],
        "retriever_scores": [],
        "code_results": [],
        "pending_tool_calls": [],
        "tool_results": [],
        "final_answer": "",
        "tool_log": [],
        "error": "",
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
                # 将记忆上下文注入到 messages 开头，作为 SystemMessage
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
    # 调用图（Langfuse OTel 自动捕获内部 span，手动包装顶层）
    # ================================================================
    tracer = trace.get_tracer("langgraph-agent")
    with tracer.start_as_current_span("run_agent") as span:
        span.set_attribute("input", question)
        span.set_attribute("session_id", session_id)
        result = graph.invoke(initial_state)
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
        "intent": result.get("intent_category", ""),
        "sources": result.get("retriever_sources", []),
        "error": result.get("error", ""),
        "iteration_count": result.get("iteration_count", 0),
    }
