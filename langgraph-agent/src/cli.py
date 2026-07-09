"""命令行入口 — LangGraph 智能体平台 CLI / CLI entry point for the LangGraph Agent Platform."""

import argparse
import json
import sys
from pathlib import Path

# 修复 Windows 控制台 UTF-8 编码 / Fix Windows console encoding for UTF-8
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保项目根目录在 sys.path 中 / Ensure project root is on sys.path
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.graph import run_agent
from src.config import settings


def cmd_ask(args):
    """运行智能体回答问题 / Run agent for a question."""
    result = run_agent(
        question=args.question,
        session_id=args.session or "",
        checkpoint=False,
    )
    print(f"\n{'='*60}")
    print(f"意图: {result.get('intent', 'unknown')}")
    print(f"{'='*60}")
    print(f"回答:\n{result.get('final_answer', result.get('answer', ''))}")
    print(f"\n{'='*60}")
    sources = result.get('sources', [])
    if sources:
        print(f"资料来源: {', '.join(sources[:5])}")
    print(f"工具调用: {len(result.get('tool_log', []))} 步")
    print(f"{'='*60}")


def cmd_stream(args):
    """运行智能体并流式输出 / Run agent with streaming output."""
    import asyncio
    import httpx

    async def main():
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                "http://127.0.0.1:8000/api/v1/agent/stream",
                json={"question": args.question, "session_id": args.session or ""},
            )
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    if data.get("type") == "token":
                        print(data["data"], end="", flush=True)
                    elif data.get("type") == "intent":
                        print(f"\n[意图分类: {data['category']}]", flush=True)
                    elif data.get("type") == "metadata":
                        print(f"\n[完成: {data['elapsed_ms']}ms, 意图: {data['intent']}]")
                        if data.get("sources"):
                            print(f"  资料来源: {', '.join(data['sources'][:5])}")

    asyncio.run(main())


def cmd_test(_args=None):
    """运行连通性测试 / Run a quick connectivity test."""
    print("=== 连通性测试 ===")

    # Qdrant
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, timeout=5)
        count = client.count(settings.qdrant_collection)
        print(f"[OK] Qdrant: connected (collection={settings.qdrant_collection}, points={count.count})")
    except Exception as exc:
        print(f"[FAIL] Qdrant: {exc}")

    # Ollama
    try:
        import httpx
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{settings.ollama_base_url}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            if models:
                print(f"[OK] Ollama: connected (models={', '.join(models)})")
            else:
                print("[OK] Ollama: connected (no models pulled — run 'ollama pull qwen3:8b')")
    except Exception as exc:
        print(f"[FAIL] Ollama: {exc}")

    # Agent graph
    try:
        from src.agent.graph import build_graph
        graph = build_graph(checkpoint=False)
        print("[OK] LangGraph: graph built successfully")
    except Exception as exc:
        print(f"[FAIL] LangGraph: {exc}")

    # Langfuse (optional)
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        try:
            import langfuse
            lf = langfuse.Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            print(f"[OK] Langfuse: connected (host={settings.langfuse_host})")
        except Exception as exc:
            print(f"[WARN] Langfuse: {exc}")
    else:
        print("[INFO] Langfuse: not configured (LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set)")


def main():
    parser = argparse.ArgumentParser(
        prog="langgraph-agent",
        description="LangGraph Agent Platform CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令 / Available commands")

    # ask
    ask_p = subparsers.add_parser("ask", help="向智能体提问 / Ask the agent a question")
    ask_p.add_argument("question", help="问题内容 / Question to ask")
    ask_p.add_argument("--session", "-s", default="", help="会话标识 / Session ID")

    # stream
    stream_p = subparsers.add_parser("stream", help="流式输出提问 / Ask with streaming output")
    stream_p.add_argument("question", help="问题内容 / Question to ask")
    stream_p.add_argument("--session", "-s", default="", help="会话标识 / Session ID")

    # test
    subparsers.add_parser("test", help="连通性测试 / Connectivity test")

    # serve
    serve_p = subparsers.add_parser("serve", help="启动 API 服务 / Start the API server")
    serve_p.add_argument("--host", default="0.0.0.0", help="绑定地址 / Bind address")
    serve_p.add_argument("--port", type=int, default=8000, help="绑定端口 / Bind port")

    args = parser.parse_args()

    if args.command == "ask":
        cmd_ask(args)
    elif args.command == "stream":
        cmd_stream(args)
    elif args.command == "test":
        cmd_test()
    elif args.command == "serve":
        import uvicorn
        uvicorn.run(
            "src.main:app",
            host=args.host,
            port=args.port,
            log_level="info",
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
