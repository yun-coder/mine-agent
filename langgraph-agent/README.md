# LangGraph Agent Platform / LangGraph 智能体平台

A production-grade local agent platform built on **LangGraph 1.x** with **OpenWebUI** frontend, connecting to your enterprise knowledge base (Qdrant) and supporting code search, terminal sandbox, and tool orchestration.

基于 **LangGraph 1.x** 构建、以 **OpenWebUI** 为前端的工业级本地智能体平台，连接企业知识库（Qdrant），支持代码搜索、终端沙箱和工具编排。

---

## Architecture / 架构

```
User (OpenWebUI / CLI / REST API)
  │
  ▼
FastAPI Server (:8000)
  ├─ /api/v1/agent/ask     — 同步问答 / Synchronous Q&A
  ├─ /api/v1/agent/stream  — SSE 流式输出 / SSE streaming
  ├─ /chat/completions     — OpenAI 兼容接口 / OpenAI-compatible
  └─ /health               — 健康检查 / Health check
  │
  ▼
LangGraph StateGraph (Hybrid)
  intent_router ──▶ rag_fetch ──┐
          │              code_search ──┐
          └── tool/general ────────────┤
                                       ▼
                            create_react_agent (subgraph)
                                       │
                              intent-filtered tools
                                       ▼
                              format_response
  │
  ▼
Qdrant (:6333) ←→ Ollama (:11434) ←→ Langfuse (:3001)
```

## Features / 功能特性

| Feature / 功能 | Description / 说明 |
|---|---|
| **意图路由** / Intent Routing | 启发式关键词分类：rag / code / tool / general |
| **企业 RAG** / Enterprise RAG | Qdrant 语义检索 + Ollama 嵌入，支持 ACL/部门过滤 |
| **代码助手** / Code Assistant | 代码搜索、文件读取、目录浏览 |
| **沙箱终端** / Sandboxed Terminal | 三层防御（黑名单 + 白名单 + 执行隔离） |
| **SSE 流式** / Streaming | 基于 `astream_events()` 的实时 Token 输出 |
| **可观测性** / Observability | Langfuse 4.x OTel 自动追踪 |
| **OpenWebUI 集成** / Integration | 兼容 Open WebUI 自定义 API 端点 |

## Quick Start / 快速开始

### Prerequisites / 前置条件

- Python 3.11+
- Ollama running (`ollama serve`)
- Qdrant running
- Documents indexed in Qdrant

### Install / 安装

```bash
pip install -r requirements.txt
```

### Configure / 配置

复制模板并编辑：

```bash
cp .env.example .env
```

```env
# Ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
LLM_MODEL=qwen3:8b

# Qdrant
QDRANT_HOST=127.0.0.1
QDRANT_PORT=6333
QDRANT_COLLECTION=enterprise_kb

# Paths
PROJECT_ROOT=F:/projects/langgraph-agent
DOCS_DIR=F:/data/docs

# Agent settings
MAX_ITERATIONS=5
TOP_K_RAG=10
TOP_K_CODE=20
STREAM_CHUNK_SIZE=4

# Langfuse (optional)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://127.0.0.1:3001

# PostgreSQL checkpointer (optional)
PG_DSN=postgresql://user:pass@localhost:5432/langgraph
```

### Run / 运行

```bash
# 连通性测试
python -m src.cli test

# 启动 API 服务
python -m src.cli serve

# 或直接运行
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

### Docker Compose / Docker 部署

```bash
docker compose up -d
```

启动：`agent-platform` (:8001) + `open-webui` (:3000)

## API Endpoints / API 接口

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | 服务信息 / Service info |
| GET | `/health` | 健康检查（Qdrant + Ollama） |
| POST | `/api/v1/agent/ask` | 同步问答 |
| POST | `/api/v1/agent/stream` | SSE 流式输出 |
| POST | `/chat/completions` | OpenAI 兼容接口 |

## Project Structure / 项目结构

```
langgraph-agent/
├── src/
│   ├── __main__.py          # python -m src 入口
│   ├── cli.py               # 命令行接口
│   ├── config.py            # 配置加载器
│   ├── main.py              # FastAPI 应用工厂
│   ├── agent/
│   │   ├── graph.py         # 混合 StateGraph + ReACT 子图
│   │   ├── tools.py         # 8 个 @tool 装饰器工具
│   │   └── toolkit/
│   │       └── sanitizer.py # 终端命令沙箱
│   ├── api/
│   │   └── routes.py        # FastAPI 路由
│   └── rag/
│       └── qdrant_client.py # Qdrant 连接器 + Ollama 嵌入器
├── tests/
│   └── test_sanitizer.py    # 沙箱测试（25 用例）
├── .env.example             # 配置模板
├── compose.yaml             # Docker Compose
├── Dockerfile               # 容器镜像
├── pyproject.toml           # 项目元数据
├── requirements.txt         # 依赖
└── README.md
```

## Tools / 工具

| Tool | Description | Safety |
|------|-------------|--------|
| `rag_query` | 搜索企业知识库 | 只读 |
| `code_search` | 搜索代码库 | 只读 |
| `code_read` | 读取源文件（最大 50KB） | 只读 |
| `file_tree` | 列出目录结构 | 只读 |
| `terminal_execute` | 执行 Shell 命令 | 三层沙箱 |
| `system_info` | CPU/内存/磁盘监控 | 只读 |
| `calculate` | 安全数学计算 | AST 验证 |
| `get_current_time` | 当前时间 | 安全 |

## Safety / 安全机制

终端沙箱采用**三层防御**：

1. **命令黑名单** — 拦截 `rm -rf`、`sudo`、`chmod 777`、`eval`、shell 逃逸等 30+ 模式
2. **路径白名单** — 仅允许在配置的目录内操作
3. **执行隔离** — 超时控制（默认 30 秒）+ `shell=False`

## Extending / 扩展工具

添加新工具只需两步：

```python
from langchain_core.tools import tool

@tool
def my_tool(param: str) -> str:
    """工具描述，LLM 会根据此理解何时调用。"""
    return result

# 添加到 src/agent/tools.py 的 AGENT_TOOLS 列表
AGENT_TOOLS = [..., my_tool]
```

`TOOL_DEFINITIONS` 会自动从 `@tool` 实例生成，无需手动维护。

## Checkpointing / 持久化检查点

| Backend | Config | 用途 |
|---------|--------|------|
| `MemorySaver` | 默认 | 开发环境 |
| `PostgresSaver` | 设置 `PG_DSN` | 生产环境 |

## Observability / 可观测性

Langfuse 4.x 通过 OpenTelemetry 自动追踪，无需额外配置：

1. 启动 Langfuse：`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_SECRET_KEY` 填入 `.env`
2. 每次智能体调用自动记录追踪（意图路由、工具调用、LLM 交互）

## License / 许可证

MIT
