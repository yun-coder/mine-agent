"""工具定义和执行器 — 基于 langchain_core.tools 的 @tool 装饰器。

每个工具遵循 OpenAI 函数调用格式，可直接用于 Ollama 的工具调用 API。
Agentic RAG 模式下，Agent 拥有全部工具，自主决定何时调用 rag_query。

Architecture change 2026-07-09:
  Removed TOOL_DEFINITIONS / _tool_to_dict (was for old intent-filtered routing).
  Agent now uses AGENT_TOOLS directly via bind_tools().
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
from langchain_core.tools import tool
from loguru import logger

from src.config import settings
from src.agent.toolkit.sanitizer import terminal_sanitizer

# ------------------------------------------------------------------
# RAG 工具 / RAG tool
# ------------------------------------------------------------------


def _call_llm(prompt: str, temperature: float = 0.3, max_tokens: int = 300) -> str:
    """调用 Ollama 生成辅助说明（非工具调用）。"""
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
            )
            r.raise_for_status()
            return r.json()["response"].strip()
    except Exception as exc:
        logger.debug(f"[RAG] LLM 辅助调用失败 / LLM helper call failed: {exc}")
        return ""


def _rewrite_query(query: str, num_queries: int = 3) -> list[str]:
    """LLM 改写查询为多个子查询，提升召回率。失败时降级返回原查询。"""
    prompt = (
        f"将以下问题改写为{num_queries}个更利于知识库检索的查询。\n"
        "要求：\n"
        "1. 每个查询简洁明确，不超过 30 字\n"
        "2. 只返回 JSON 数组格式，不要其他内容\n"
        "3. 覆盖原问题的不同角度\n\n"
        f"原问题: {query}\n\n"
        '输出格式示例: ["查询1", "查询2", "查询3"]'
    )
    response = _call_llm(prompt, temperature=0.3, max_tokens=300)
    if not response:
        return [query]

    # 从响应中提取 JSON 数组
    try:
        queries = json.loads(response)
    except json.JSONDecodeError:
        start = response.find("[")
        end = response.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                queries = json.loads(response[start:end])
            except (json.JSONDecodeError, ValueError):
                queries = None
        else:
            queries = None

    if isinstance(queries, list) and all(isinstance(q, str) for q in queries) and len(queries) > 1:
        # 去重 + 确保原查询在结果中
        seen = set()
        results = [query]
        for q in queries:
            if q not in seen and q != query:
                seen.add(q)
                results.append(q)
        return results[:num_queries]
    return [query]


def _check_result_quality(query: str, results: list[dict]) -> tuple[bool, str | None]:
    """检查检索结果质量。如果结果不足，返回 (False, rewritten_query)；否则返回 (True, None)。"""
    if not results:
        return False, query  # 无结果，重试

    # 简单启发式：分数过低说明不相关
    avg_score = sum(r.get("score", 0) for r in results) / len(results)
    if avg_score < 0.3:
        # 尝试用关键词风格改写查询再搜
        rewritten = _rewrite_query(query, num_queries=2)
        if len(rewritten) > 1:
            return False, rewritten[1]  # 用第二个子查询重试
        return False, query

    return True, None


def _exec_rag_query(
    query: str,
    top_k: int = 10,
    rewrite_query: bool = False,
) -> str:
    from src.rag.qdrant_client import QdrantConnector

    conn = QdrantConnector(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        collection=settings.qdrant_collection,
    )

    # Phase 2: 可选的多查询改写 / Optional query rewriting
    queries = [query]
    if rewrite_query or settings.rag_use_query_rewrite:
        rewritten = _rewrite_query(query)
        if rewritten:
            queries = rewritten
            logger.debug(f"[RAG] 查询改写 / query rewritten: {query} → {queries}")

    # 多查询检索 + 去重 / Multi-query retrieval + dedup
    seen_ids: set[str] = set()
    all_results: list[dict] = []
    for sq in queries:
        results = conn.search(sq, top_k=top_k)
        for r in results:
            rid = r.get("id", "")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                all_results.append(r)
        if len(all_results) >= top_k:
            break

    # 结果不足时尝试重写再检索一次 / Fallback: rewrite and retry if results are thin
    if len(all_results) < 3 and not rewrite_query and not settings.rag_use_query_rewrite:
        quality_ok, rewritten = _check_result_quality(query, all_results)
        if not quality_ok and rewritten and rewritten != query:
            logger.debug(f"[RAG] 质量不足，改写重试 / quality low, retry with: {rewritten}")
            retry_results = conn.search(rewritten, top_k=top_k)
            for r in retry_results:
                rid = r.get("id", "")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    all_results.append(r)

    # 格式化输出 / Format output
    if not all_results:
        return "知识库中未找到匹配信息。请确认文档已索引，或换一种表述。/ Knowledge base has no matching info. Please confirm documents are indexed, or rephrase."

    parts: list[str] = []
    for i, r in enumerate(all_results[:top_k], 1):
        meta = r.get("metadata", {})
        fname = meta.get("filename", "unknown")
        page = meta.get("page", "?")
        source = meta.get("source", "")
        text = r.get("text", "")
        parts.append(
            f"[{i}] 来源: {fname} (第 {page} 页) | 路径: {source} | "
            f"相关度: {r['score']:.3f}\n{text[:500]}"
        )
    return "\n\n".join(parts)


@tool
def rag_query(query: str, top_k: int = 10, rewrite_query: bool = False) -> str:
    """搜索知识库（Qdrant 向量检索 + BM25 混合检索）。用于回答基于已有文档、笔记、知识库的事实性问题。
    Search the knowledge base (Qdrant + BM25 hybrid retrieval).
    Use for factual questions about company documents, policies, procedures, notes, or any stored knowledge.

    Args:
        query: The question or search query.
        top_k: Number of results to return (default 10).
        rewrite_query: Whether to expand the query into sub-queries for better recall (default False).
    """
    return _exec_rag_query(query, top_k, rewrite_query)


# ------------------------------------------------------------------
# 代码搜索工具 / Code search tool
# ------------------------------------------------------------------


def _exec_code_search(pattern: str, root_dir: str = None, max_results: int = 20) -> str:
    root = Path(root_dir) if root_dir else settings.project_root
    matches: list[dict] = []
    for ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".cpp", ".c", ".h"):
        for f in root.rglob(f"*{ext}"):
            try:
                content = f.read_text(errors="ignore")
                if pattern.lower() in content.lower():
                    matches.append({
                        "file": str(f),
                        "language": ext.lstrip("."),
                        "snippet": content[:300],
                    })
                    if len(matches) >= max_results:
                        break
            except OSError:
                continue
        if len(matches) >= max_results:
            break
    return json.dumps(matches, ensure_ascii=False, indent=2)


@tool
def code_search(pattern: str, root_dir: str = None, max_results: int = 20) -> str:
    """在代码库中搜索匹配模式的文件。支持 glob 模式如 '*.py', 'src/**/*.ts'。
    Search the codebase for files matching a pattern. Supports glob patterns.

    Args:
        pattern: File pattern or keyword to search for.
        root_dir: Root directory to search (default: project root).
        max_results: Max results to return (default 20).
    """
    return _exec_code_search(pattern, root_dir, max_results)


# ------------------------------------------------------------------
# 代码读取工具 / Code read tool
# ------------------------------------------------------------------


def _exec_code_read(path: str, max_lines: int = 200) -> str:
    fp = Path(path)
    if not fp.exists():
        return f"文件不存在 / File not found: {path}"
    size = fp.stat().st_size
    if size > 50 * 1024:
        return f"文件过大 ({size / 1024:.0f} KB)，最大支持 50 KB / File too large, max 50 KB"
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()[:max_lines]
        numbered = "\n".join(f"{i + 1:>5} | {l}" for i, l in enumerate(lines))
        return f"--- {fp.name} ({len(lines)} 行 / lines) ---\n{numbered}"
    except PermissionError:
        return f"权限被拒绝 / Permission denied: {path}"
    except Exception as exc:
        return f"读取失败 / Read failed: {exc}"


@tool
def code_read(path: str, max_lines: int = 200) -> str:
    """读取源代码文件并返回带行号的内容。
    Read a source code file and return its content with line numbers.

    Args:
        path: Absolute or relative path to the file.
        max_lines: Maximum lines to return (default 200).
    """
    return _exec_code_read(path, max_lines)


# ------------------------------------------------------------------
# 文件树工具 / File tree tool
# ------------------------------------------------------------------


def _walk_tree(parent: Path, lines: list[str], current_depth: int, max_depth: int) -> None:
    if current_depth > max_depth:
        return
    try:
        items = sorted(parent.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return
    prefix = "    " * current_depth
    for item in items:
        marker = "[DIR] " if item.is_dir() else "[FILE] "
        lines.append(f"{prefix}{marker}{item.name}")
        if item.is_dir() and current_depth < max_depth:
            _walk_tree(item, lines, current_depth + 1, max_depth)


def _exec_file_tree(directory: str, depth: int = 2) -> str:
    dp = Path(directory)
    if not dp.is_dir():
        return f"目录不存在 / Directory not found: {directory}"
    lines: list[str] = [str(dp)]
    _walk_tree(dp, lines, current_depth=1, max_depth=depth)
    return "\n".join(lines)


@tool
def file_tree(directory: str, depth: int = 2) -> str:
    """列出目录树结构。
    List directory tree structure.

    Args:
        directory: Directory path to list.
        depth: Max recursion depth (default 2).
    """
    return _exec_file_tree(directory, depth)


# ------------------------------------------------------------------
# 终端执行工具（沙箱隔离）/ Terminal execute tool (sandboxed)
# ------------------------------------------------------------------


def _exec_terminal(command: str, timeout: int = 30) -> str:
    result = terminal_sanitizer(command)
    if not result["safe"]:
        return f"已拦截 / BLOCKED: {result['reason']}"

    safe_cmd = result["command"]

    # 判断操作系统 / Determine OS
    is_windows = os.name == "nt" or "windir" in os.environ
    if is_windows:
        shell_cmd = ["powershell", "-NoProfile", "-Command", safe_cmd]
        cwd = str(settings.project_root)
    else:
        shell_cmd = ["/bin/bash", "-c", safe_cmd]
        cwd = str(settings.project_root)

    try:
        proc = subprocess.run(
            shell_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            shell=False,
        )
        output = ""
        if proc.stdout:
            output += f"标准输出 / stdout:\n{proc.stdout[:5000]}\n"
        if proc.stderr:
            output += f"标准错误 / stderr:\n{proc.stderr[:5000]}\n"
        output += f"返回码 / return_code: {proc.returncode}"
        return output
    except subprocess.TimeoutExpired:
        return f"[超时 / TIMEOUT] 命令执行超时 {timeout} 秒"
    except FileNotFoundError:
        return "[错误 / ERR] 命令不存在 / Command not found"
    except Exception as exc:
        return f"[错误 / ERR] 执行失败 / Execution failed: {exc}"


@tool
def terminal_execute(command: str, timeout: int = 30) -> str:
    """在沙箱环境中执行 Shell 命令。支持只读命令。破坏性命令已被拦截。
    Execute a shell command in a sandboxed environment. Supports read-only commands.
    Destructive commands are blocked.

    Args:
        command: The shell command to execute.
        timeout: Timeout in seconds (default 30).
    """
    return _exec_terminal(command, timeout)


# ------------------------------------------------------------------
# 系统信息工具 / System info tool
# ------------------------------------------------------------------


def _exec_system_info() -> str:
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disks = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
            disks.append(
                f"  {p.mountpoint}: 已使用 {u.percent}% "
                f"({u.used // 1024**3}GB / {u.total // 1024**3}GB)"
            )
        except PermissionError:
            pass
    disk_str = "\n".join(disks) if disks else "磁盘 / Disk: N/A"
    return (
        f"CPU 使用率: {cpu}%\n"
        f"内存: 已使用 {mem.percent}% (可用 {mem.available // 1024**3}GB / 总计 {mem.total // 1024**3}GB)\n"
        f"磁盘:\n{disk_str}"
    )


@tool
def system_info() -> str:
    """获取系统信息：CPU、内存、磁盘使用情况。
    Get system information: CPU, memory, disk usage.
    """
    return _exec_system_info()


# ------------------------------------------------------------------
# 计算器工具 / Calculate tool
# ------------------------------------------------------------------


def _exec_calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        allowed = {
            ast.Constant: True,
            ast.Expression: True,
            ast.UnaryOp: True,
            ast.BinOp: True,
            ast.Add: True,
            ast.Sub: True,
            ast.Mult: True,
            ast.Div: True,
            ast.USub: True,
            ast.UAdd: True,
        }
        for node in ast.walk(tree):
            if type(node) not in allowed:
                return f"[错误 / ERR] 表达式中存在不安全操作: {type(node).__name__}"
        code = compile(tree, "<calc>", "eval")
        result = eval(code, {"__builtins__": {}}, {})
        return str(result)
    except Exception as exc:
        return f"[错误 / ERR] 计算失败 / Calculation failed: {exc}"


@tool
def calculate(expression: str) -> str:
    """安全地计算数学表达式。
    Safely evaluate a mathematical expression.

    Args:
        expression: Mathematical expression, e.g. '2 + 3 * 4'.
    """
    return _exec_calculate(expression)


# ------------------------------------------------------------------
# 时间工具 / Time tool
# ------------------------------------------------------------------


def _exec_get_current_time() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@tool
def get_current_time() -> str:
    """获取当前日期和时间。
    Get the current date and time.
    """
    return _exec_get_current_time()


# ------------------------------------------------------------------
# 工具注册表 / Registry
# ------------------------------------------------------------------

# BaseTool 实例列表 — 供 create_react_agent + ToolNode 使用
AGENT_TOOLS: list[Any] = [
    rag_query,
    code_search,
    code_read,
    file_tree,
    terminal_execute,
    system_info,
    calculate,
    get_current_time,
]
