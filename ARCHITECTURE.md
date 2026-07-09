# 企业知识库平台 — 架构与数据流转说明

> 基于 **LangGraph + Qdrant + Open WebUI** 的企业级智能问答平台，全容器化部署，集成 **TDAI Memory** 四层长期记忆系统。

---

## 目录

- [一、系统总览](#一系统总览)
- [二、服务架构](#二服务架构)
- [三、核心模块详解](#三核心模块详解)
  - [3.1 LangGraph Agent（智能体 API 服务）](#31-langgraph-agent智能体-api-服务)
  - [3.2 RAG Pipeline（文档处理流水线）](#32-rag-pipeline文档处理流水线)
  - [3.3 TDAI Memory（四层长期记忆系统）](#33-tdai-memory四层长期记忆系统)
  - [3.4 Langfuse（可观测性平台）](#34-langfuse可观测性平台)
  - [3.5 Open WebUI（聊天前端）](#35-open-webui聊天前端)
- [四、完整数据流转全景图](#四完整数据流转全景图)
- [五、功能使用说明](#五功能使用说明)
- [六、部署指南](#六部署指南)
- [七、运维与监控](#七运维与监控)
- [八、安全机制](#八安全机制)

---

## 一、系统总览

本平台是一个**企业级本地智能问答系统**，零外部 API 依赖，所有模型在本地运行。

### 技术栈

| 层 | 技术 |
|---|---|
| **前端** | Open WebUI（聊天界面） |
| **后端 API** | FastAPI + LangGraph 1.x（智能体编排） |
| **文档处理** | Docling + tiktoken 分块 + Ollama 嵌入 |
| **向量存储** | Qdrant + BM25 混合索引 |
| **LLM 推理** | Ollama（qwen3:8b + bge-m3） |
| **长期记忆** | TDAI Memory（四层记忆：L0-L3） |
| **可观测性** | Langfuse 4.x（OTel 追踪） |
| **监控** | Prometheus + Grafana |
| **容器化** | Docker Compose（全服务容器化） |
| **认证** | API Key + 速率限制 + 熔断器 |

### 目录结构

```
D:\local-agent\                    # 项目根目录
├── langgraph-agent/               # [核心] 智能体 API 服务
│   ├── src/
│   │   ├── agent/
│   │   │   ├── graph.py            # LangGraph 图编排（含 TDAI 记忆集成）
│   │   │   ├── tools.py            # 8 个函数工具（RAG/代码/终端/计算等）
│   │   │   └── toolkit/
│   │   │       └── sanitizer.py    # 终端命令三层安全沙箱
│   │   ├── api/
│   │   │   ├── routes.py           # FastAPI 路由（同步/流式/OpenAI兼容）
│   │   │   ├── auth.py             # API Key 认证
│   │   │   ├── rate_limit.py       # 滑动窗口速率限制
│   │   │   ├── retry.py            # 指数退避重试
│   │   │   └── circuit_breaker.py  # 熔断器（防止雪崩）
│   │   ├── rag/
│   │   │   └── qdrant_client.py    # Qdrant 连接器 + Ollama 嵌入器
│   │   ├── tdai_client.py          # TDAI Memory HTTP 客户端
│   │   ├── config.py               # 配置加载（pydantic-settings）
│   │   ├── main.py                 # FastAPI 应用工厂
│   │   ├── cli.py                  # CLI 入口（ask/stream/test/serve）
│   │   ├── metrics.py              # Prometheus 指标
│   │   ├── logging_config.py       # 结构化日志（JSON）
│   │   └── shutdown.py             # 优雅关闭
│   ├── tests/                      # 单元测试
│   ├── Dockerfile                  # 容器构建
│   └── compose.yaml                # Docker Compose（含 Open WebUI）
│
├── rag-pipeline/                   # [独立] 文档处理流水线
│   ├── parser.py                   # 文档解析（Docling/pypdf）
│   ├── chunker.py                  # 语义分块（tiktoken）
│   ├── embedder.py                 # Ollama 批量嵌入
│   ├── qdrant_store.py             # Qdrant 写入/检索
│   ├── bm25_index.py               # BM25 倒排索引 + RRF 融合
│   ├── query_rewriter.py           # LLM 查询改写
│   ├── reranker.py                 # BGE 重排器（可选）
│   ├── rag_pipeline.py             # 检索→生成主流程
│   ├── api.py                      # FastAPI 服务（含 Web UI）
│   ├── ingest.py                   # 文档入库 CLI
│   ├── query.py                    # 命令行提问 CLI
│   ├── evaluate.py                 # RAGAS 离线评估
│   ├── pii_filter.py               # PII 检测与脱敏
│   ├── config.py                   # 配置加载
│   ├── Dockerfile                  # 容器构建
│   └── static/index.html           # 问答页面
│
├── langfuse/                       # 可观测性部署
│   └── docker-compose.yml          # Langfuse + PG + ClickHouse + Redis + MinIO
│
├── monitoring/                     # 监控配置
│   └── grafana-dashboard-langgraph-agent.json
│
├── data/                           # 本地数据（日志等）
│
├── README.md                       # 项目总 README
└── pyproject.toml                  # 项目元数据
```

---

## 二、服务架构

### 服务拓扑

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          用户入口层                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ Open WebUI   │  │ RAG Web UI   │  │ CLI (Python) │  │ REST Client │  │
│  │ :3000        │  │ :8001/       │  │ src.cli     │  │ curl/httpie  │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘  │
└─────────┼─────────────────┼─────────────────┼──────────────────┼─────────┘
          │                 │                 │                  │
          ▼                 ▼                 ▼                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        API 服务层                                        │
│  ┌──────────────────────┐    ┌──────────────────────┐                    │
│  │ LangGraph Agent      │◄──►│ RAG Pipeline         │                    │
│  │ FastAPI :8000         │    │ FastAPI :8001         │                    │
│  │                       │    │                       │                    │
│  │  /api/v1/agent/ask   │    │ /query               │                    │
│  │  /api/v1/agent/stream│    │ /query/stream        │                    │
│  │  /chat/completions   │    │ /ingest              │                    │
│  │  /health             │    │ /health              │                    │
│  └──────────┬───────────┘    └──────────┬───────────┘                    │
└─────────────┼────────────────────────────┼───────────────────────────────┘
              │                            │
              ▼                            ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        核心引擎层                                        │
│                                                                          │
│  ┌────────────────┐    ┌────────────────┐    ┌──────────────────┐       │
│  │ LangGraph      │    │ Qdrant         │    │ BM25 Index       │       │
│  │ StateGraph     │    │ Vector DB      │    │ (内存/本地文件)  │       │
│  │                │    │ :6333          │    │                   │       │
│  │ intent_router  │    │                │    │ rank-bm25        │       │
│  │  → rag_fetch   │    │ enterprise_kb  │    │ RRF Fusion       │       │
│  │  → code_search │    │ collection     │    │                   │       │
│  │  → ReACT 子图  │    │ bge-m3 1024d   │    │ bm25.pkl         │       │
│  │  → format_rsp  │    │                │    │                   │       │
│  └───────┬────────┘    └────────┬───────┘    └──────────────────┘       │
│          │                      │                                        │
│          ▼                      ▼                                        │
│  ┌──────────────────────────────────────────────────┐                    │
│  │               Ollama（本地 LLM）                   │                    │
│  │               :11434                              │                    │
│  │  ┌──────────┐  ┌──────────┐  ┌────────────────┐   │                    │
│  │  │ qwen3:8b │  │ bge-m3   │  │ chat/embed/etc │   │                    │
│  │  │ (LLM)    │  │ (Embed)  │  │                │   │                    │
│  │  └──────────┘  └──────────┘  └────────────────┘   │                    │
│  └──────────────────────────────────────────────────┘                    │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                       辅助服务层                                         │
│  ┌────────────┐ ┌──────────┐ ┌────────┐ ┌───────────┐ ┌─────────────┐  │
│  │ TDAI       │ │Langfuse  │ │Redis   │ │ PostgreSQL│ │ ClickHouse  │  │
│  │ Memory     │ │:3001     │ │:6379   │ │ :5432     │ │ :8123       │  │
│  │ :8420      │ │ OTel     │ │ Cache  │ │ Langfuse  │ │ Langfuse    │  │
│  │ L0-L3 记忆 │ │ Tracing  │ │        │ │ 主库      │ │ 事件存储    │  │
│  └────────────┘ └──────────┘ └────────┘ └───────────┘ └─────────────┘  │
│                                                                          │
│  ┌────────┐  ┌──────────────────┐  ┌─────────────┐                      │
│  │ MinIO  │  │ Prometheus       │  │ Grafana     │                      │
│  │ :9000  │  │ + Agent Metrics  │  │ Dashboards  │                      │
│  │ S3存储 │  │ :8000/metrics    │  │             │                      │
│  └────────┘  └──────────────────┘  └─────────────┘                      │
└──────────────────────────────────────────────────────────────────────────┘
```

### 端口一览

| 端口 | 服务 | 说明 |
|------|------|------|
| `:3000` | Open WebUI | AI 聊天前端 |
| `:8000` | LangGraph Agent | 智能体 API 服务 |
| `:8001` | RAG Pipeline | 文档处理流水线 API |
| `:3001` | Langfuse | 可观测性平台 |
| `:8420` | TDAI Memory | 四层长期记忆系统 |
| `:6333` | Qdrant | 向量数据库 |
| `:6334` | Qdrant gRPC | 向量数据库 gRPC |
| `:5432` | PostgreSQL | Langfuse 数据库 |
| `:8123` | ClickHouse | Langfuse 事件存储 |
| `:6379` | Redis | 缓存 |
| `:9000` | MinIO | S3 对象存储 |
| `:11434` | Ollama | 本地 LLM |

---

## 三、核心模块详解

### 3.1 LangGraph Agent（智能体 API 服务）

**端口**: `:8000`
**核心文件**: `langgraph-agent/src/`

#### 3.1.1 整体架构

LangGraph Agent 是平台的核心，它使用 **LangGraph StateGraph** 构建了一个混合智能体编排图：

```
外层 StateGraph（意图路由 + 预处理）
─────────────────────────────────────────────────
  START → intent_router → conditional_router
                               ├─→ rag_fetch ──→ agent_orchestrator
                               ├─→ code_search → agent_orchestrator
                               └─→ tool/general → agent_orchestrator
                                       ↓
                               format_response → END

内层 ReACT 子图（create_react_agent）
─────────────────────────────────────────────────
  SystemMessage + 历史消息
       │
       ▼
   LLM（ChatOllama）
       │
       ├─→ 工具调用 → ToolNode → 结果返回 LLM
       │
       └─→ 直接回答 → 返回结果
```

#### 3.1.2 数据流转 — 单次问答全过程

```
用户提问: "公司年假政策是什么？"
   │
   ├── ① TDAI Memory 召回
   │     │
   │     ├── 调用 tdai_client.sync_recall(query, session_key)
   │     ├── TDAI Gateway POST /recall → 检索 SQLite 记忆
   │     ├── 返回 appendSystemContext（历史记忆摘要）
   │     └── 注入 SystemMessage 到 messages 开头
   │
   ├── ② intent_router（意图分类节点）
   │     │
   │     ├── 启发式关键词匹配（正则）
   │     ├── 匹配关键词模式：
   │     │    - RAG关键词: "知识库""文档""年假""政策"
   │     │    - Code关键词: "代码""函数""搜索文件"
   │     │    - Tool关键词: "运行""执行""terminal"
   │     ├── 计算置信度分数
   │     └── 输出: {"intent_category": "rag", "confidence": 0.85}
   │
   ├── ③ conditional_router（条件路由）
   │     │
   │     ├── intent="rag"    → Send("rag_fetch", state)
   │     ├── intent="code"   → Send("code_search", state)
   │     └── intent="tool|general" → Send("agent_orchestrator", state)
   │
   ├── ④ rag_fetch（RAG 检索节点）
   │     │
   │     ├── 调用 QdrantConnector.search(query, top_k=10)
   │     ├── Qdrant 向量检索流程：
   │     │     ├── embedder.embed_one(query) → Ollama bge-m3 → 1024维向量
   │     │     ├── Qdrant cosine 相似度搜索
   │     │     ├── 可选 ACL/部门过滤（FieldCondition）
   │     │     └── 返回 top_k 结果（含 score, text, metadata）
   │     ├── 构建 SystemMessage 注入检索结果上下文
   │     └── 输出: 更新 retriever_context + retriever_sources
   │
   ├── ⑤ agent_orchestrator（ReACT 子图编排器）
   │     │
   │     ├── 动态构建模型（根据意图选择工具子集）
   │     │    - rag:    [rag_query]
   │     │    - code:   [code_search, code_read, file_tree, get_current_time]
   │     │    - tool:   [terminal_execute, system_info, calculate, get_current_time]
   │     │    - general: [全部工具]
   │     ├── 动态选择系统提示词
   │     ├── 调用 create_react_agent 子图
   │     ├── LangGraph 自动管理 agent↔tool 调用循环
   │     ├── 达到 MAX_ITERATIONS（默认5）或 LLM 直接回答时停止
   │     └── 输出: final_answer + tool_log
   │
   ├── ⑥ format_response（格式化输出）
   │     │
   │     ├── 附加引用来源（retriever_sources）
   │     ├── 附加工具调用日志摘要
   │     └── 输出: 最终答案字符串
   │
   ├── ⑦ TDAI Memory 保存
   │     │
   │     ├── 调用 tdai_client.sync_capture(query, answer, session_key)
   │     ├── TDAI Gateway POST /capture
   │     ├── 自动触发 L0→L1→L2→L3 异步提取管线
   │     └── 返回确认
   │
   └── ⑧ 返回最终结果
         │
         ├── answer: 最终答案
         ├── tool_log: 工具调用记录
         ├── intent: 意图分类
         └── sources: 引用来源
```

#### 3.1.3 工具列表

| 工具名称 | 函数 | 说明 | 安全级别 |
|---------|------|------|---------|
| `rag_query` | 搜索企业知识库 | Qdrant 语义检索 + BM25 混合检索 | 只读 |
| `code_search` | 搜索代码库 | 按关键词递归搜索源文件 | 只读 |
| `code_read` | 读取代码文件 | 带行号显示，最大 50KB/200行 | 只读 |
| `file_tree` | 列出目录树 | 可配置深度（默认2层） | 只读 |
| `terminal_execute` | 执行 Shell 命令 | 三层沙箱保护 | 沙箱隔离 |
| `system_info` | 获取系统信息 | CPU/内存/磁盘 | 只读 |
| `calculate` | 安全数学计算 | AST 解析 + 操作白名单 | 安全沙箱 |
| `get_current_time` | 获取当前时间 | 返回日期时间字符串 | 安全 |

#### 3.1.4 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/health` | 健康检查（Qdrant + Ollama + Langfuse） |
| POST | `/api/v1/agent/ask` | 同步问答 |
| POST | `/api/v1/agent/stream` | SSE 流式输出 |
| POST | `/chat/completions` | OpenAI 兼容接口 |
| POST | `/v1/chat/completions` | OpenAI 兼容接口（带 /v1） |
| GET | `/models` | 模型列表（OpenAI 兼容） |
| GET | `/metrics` | Prometheus 指标 |

#### 3.1.5 安全机制

**终端命令沙箱（三层防御）**：

1. **命令黑名单** — 拦截 30+ 危险模式（`rm -rf`, `sudo`, `chmod 777`, `eval`, shell 逃逸等）
2. **路径白名单** — 仅允许在配置目录（`DOCS_DIR`, `PROJECT_ROOT`, 临时目录）内操作
3. **执行隔离** — 超时控制（默认30秒）+ `shell=False` + 限制 cwd

**API 安全**：
- API Key 认证（Bearer Token）
- 滑动窗口速率限制（`/agent/ask`: 30/min, `/agent/stream`: 10/min）
- 熔断器（连续5次失败即熔断，30秒后半开探测）
- 请求长度限制（问题最多4000字符）

---

### 3.2 RAG Pipeline（文档处理流水线）

**端口**: `:8001`
**核心文件**: `rag-pipeline/`

#### 3.2.1 整体架构

RAG Pipeline 是一个独立服务，提供**文档入库 → 向量化 → 混合检索 → LLM 生成**的完整流水线：

```
文档入库流程
────────────────────────────────────────────────────────────────
  PDF/DOCX/PPTX/XLSX/TXT/MD/HTML
       │
       ▼
  parser.py
  ├── Docling（优先）：统一输出 Markdown，按分页符分割
  └── pypdf（降级）：仅 PDF，按页提取
       │
       ▼
  chunker.py
  ├── tiktoken cl100k_base 精确估算 token 数
  ├── 按中英文句号分句
  ├── 按 chunk_size（默认512 token）聚合
  ├── overlap（默认64 token）保留上下文
  ├── 内容哈希去重（SHA-256）
  └── 过滤空 chunk
       │
       ├──→ embedder.py：Ollama /api/embed 批量嵌入 → bge-m3 1024维
       │         │
       │         ▼
       │    Qdrant 向量库（含 payload 索引）
       │    ├── text: chunk 文本
       │    ├── chunk_id: 唯一标识
       │    ├── source/filename/filetype
       │    ├── page/chunk_index/total_chunks
       │    └── acl_groups/department（权限控制）
       │
       └──→ bm25_index.py：rank-bm25 本地倒排索引
                 │
                 ▼
            F:/data/qdrant/bm25.pkl

用户查询流程
────────────────────────────────────────────────────────────────
  用户提问："公司年假有几天？"
       │
       ├──→ query_rewriter.py（可选）
       │      ├── LLM 改写为多个子查询
       │      └── 或关键词拆分降级
       │
       ├──→ Qdrant 向量检索（cosine，top_k=20）
       │
       ├──→ BM25 关键词检索（top_k=20）
       │
       ├──→ RRF 融合排序（k=60，top_k=10）
       │
       ├──→ reranker.py（可选）
       │      └── BAAI/bge-reranker-v2-m3 交叉编码器重排
       │
       ├──→ 构建 Prompt（带引用编号）
       │
       ├──→ Ollama qwen3:8b 生成回答
       │
       └──→ 返回 {answer, citations, trace}
```

#### 3.2.2 API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web UI 首页 |
| GET | `/health` | 健康检查 |
| POST | `/query` | 同步问答 |
| POST | `/query/stream` | SSE 流式问答 |
| POST | `/ingest` | 文档入库 |
| POST | `/rebuild-bm25` | 重建 BM25 索引 |

#### 3.2.3 文档入库

**方式一：CLI 入库**
```powershell
cd rag-pipeline
.venv\Scripts\python.exe ingest.py F:\data\docs
.venv\Scripts\python.exe ingest.py F:\data\docs --recreate
.venv\Scripts\python.exe ingest.py F:\data\docs --chunk-size 512 --overlap 64
```

**方式二：API 入库**
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8001/ingest `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{ path = "F:/data/docs"; chunk_size = 512 } | ConvertTo-Json)
```

**支持的文件格式**：
PDF（Docling优先 → pypdf降级）、DOCX、PPTX、XLSX、TXT、MD、HTML

#### 3.2.4 查询方式

**方式一：Web UI**
打开 `http://127.0.0.1:8001`，在问答页面输入问题。

**方式二：CLI**
```powershell
.venv\Scripts\python.exe query.py "公司年假有几天？"
.venv\Scripts\python.exe query.py "请假流程" --show-trace
.venv\Scripts\python.exe query.py "差旅报销政策" --stream
```

**方式三：API**
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8001/query `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{ question = "公司年假有几天？" } | ConvertTo-Json)
```

---

### 3.3 TDAI Memory（四层长期记忆系统）

**端口**: `:8420`
**集成位置**: `langgraph-agent/src/tdai_client.py`

#### 3.3.1 记忆层级

| 层级 | 名称 | 内容 | 存储方式 |
|------|------|------|---------|
| **L0** | Conversation | 原始对话记录（完整 Q&A） | SQLite |
| **L1** | Atom | 原子化事实提取（日期、喜好、经历等） | SQLite + sqlite-vec |
| **L2** | Scenario | 场景归类与上下文（按主题/项目/时间聚类） | SQLite |
| **L3** | Persona | 用户画像与偏好（总结性描述） | SQLite |

#### 3.3.2 记忆存取流程

```
用户提问
  │
  ├── ① 召回（sync_recall）
  │     ├── POST /recall → TDAI Gateway
  │     ├── BM25 关键词 + sqlite-vec 向量混合检索
  │     ├── RRF 融合排序 → 返回 top_k 记忆
  │     ├── 渐进式策略：从 L3（画像）→ L2（场景）→ L1（原子）→ L0（对话）
  │     ├── 注入 appendSystemContext 到 SystemMessage
  │     └── 仅保留轻量抽象，不加载全部原始对话
  │
  ├── ② 主流程（LangGraph 图执行，不受记忆影响）
  │
  ├── ③ 保存（sync_capture）
  │     ├── POST /capture → TDAI Gateway
  │     ├── 保存 L0 原始对话
  │     └── 触发 L0→L1→L2→L3 异步提取管线
  │
  └── ④ 返回答案（同时完成记忆持久化）
```

#### 3.3.3 容错设计

- 所有 TDAI 调用包裹在 `try/except` 中，失败不影响主流程
- `TDAI_ENABLED=false` 一键关闭
- `tdai_client.get_client()` 返回单例，可按需 `disable()`/`enable()`

---

### 3.4 Langfuse（可观测性平台）

**端口**: `:3001`
**部署**: `langfuse/docker-compose.yml`

#### 3.4.1 架构组件

| 组件 | 说明 |
|------|------|
| **Langfuse Web** | 可观测性仪表盘 |
| **PostgreSQL** | 主数据存储（追踪、会话等） |
| **ClickHouse** | 事件存储（高效分析查询） |
| **Redis** | 缓存和队列 |
| **MinIO** | S3 兼容的对象存储（媒体/附件） |

#### 3.4.2 集成方式

Langfuse 4.x 使用 **OpenTelemetry 自动插桩**，无需手动埋点：

```python
# 1. 初始化 OTel 导出器
exporter = OTLPSpanExporter(
    endpoint="http://langfuse:3001/api/public/otel/v1/traces",
    headers={"Authorization": f"Basic {base64(public_key:secret_key)}"},
)

# 2. 激活 LangchainInstrumentor 自动捕获所有 LangGraph/LangChain 调用
LangchainInstrumentor().instrument()
```

**可观测内容**：
- 每次智能体调用 → 完整追踪（意图路由、工具调用、LLM 交互）
- 请求延迟、Token 用量、错误率
- 全链路端到端可视化

---

### 3.5 Open WebUI（聊天前端）

**端口**: `:3000`

#### 3.5.1 配置模式

Open WebUI 默认连接到 LangGraph Agent 的 **OpenAI 兼容端点**：

```
用户 → Open WebUI (:3000)
             │
             └── OpenAI API → LangGraph Agent (:8000)
                                        │
                                        ├── RAG 自动检索
                                        ├── 工具调用
                                        ├── TDAI 记忆存取
                                        └── Langfuse 追踪
```

**管理后台配置**：
```
管理员设置 → 外部连接 → OpenAI API
  API URL:  http://host.docker.internal:8000   （Docker 环境）
            或 http://kb-langgraph-agent:8000   （同 Docker 网络）
  API Key:  留空或配置 API_KEY
```

---

## 四、完整数据流转全景图

### 从文档入库到最终答案的端到端流程

```
┌─────────────────────────────────────────────────────────────────────────┐
│                 文档入库阶段（一次性操作）                                 │
│                                                                         │
│  PDF/DOCX/etc.                                                         │
│    │                                                                    │
│    ▼                                                                    │
│  parser.py ───────→ Document{content, metadata}                        │
│    │                                                                    │
│    ▼                                                                    │
│  chunker.py ───────→ Chunk{text, chunk_id, metadata}                   │
│    │                 ├── 按句分 → 按512 token聚合 → overlap 64         │
│    │                 └── 内容哈希去重（SHA-256）                        │
│    ▼                                                                    │
│  embedder.py ───────→ Ollama bge-m3 → 1024维浮点向量                   │
│    │                                                                    │
│    ├──→ Qdrant 向量库：                                                  │
│    │     PointStruct{id, vector[], payload{text, chunk_id, source,     │
│    │                  filename, filetype, page, acl_groups, department}}│
│    │                                                                    │
│    └──→ BM25 索引：                                                     │
│          rank-bm25 本地倒排索引 → bm25.pkl                              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                 用户问答阶段（每次请求）                                   │
│                                                                         │
│  用户输入问题 → Open WebUI / CLI / REST API                             │
│    │                                                                    │
│    ▼                                                                    │
│  ┌─────────────────────────────────────────────────────────────┐        │
│  │               LangGraph Agent（:8000）                       │        │
│  │                                                             │        │
│  │  ① TDAI Memory 召回                                         │        │
│  │     │                                                       │        │
│  │     │  POST /recall → TDAI Gateway(:8420)                  │        │
│  │     │  ← appendSystemContext → SystemMessage 注入           │        │
│  │     │                                                       │        │
│  │  ② intent_router（关键词启发式分类）                         │        │
│  │     │                                                       │        │
│  │     │  "年假政策" → category="rag" confidence=0.85           │        │
│  │     │                                                       │        │
│  │  ③ conditional_router（Send 路由）                           │        │
│  │     │                                                       │        │
│  │     │  rag → Send("rag_fetch", state)                        │        │
│  │     │                                                       │        │
│  │  ④ rag_fetch（RAG 检索）                                     │        │
│  │     │                                                       │        │
│  │     │  embed("年假政策") → 1024d向量                          │        │
│  │     │  Qdrant.search() → top-10 chunks                       │        │
│  │     │  → SystemMessage 注入上下文                            │        │
│  │     │                                                       │        │
│  │  ⑤ agent_orchestrator（ReACT 子图）                          │        │
│  │     │                                                       │        │
│  │     │  模型：ChatOllama(qwen3:8b, temperature=0.1)           │        │
│  │     │  工具：仅 rag_query（按意图过滤）                        │        │
│  │     │  LLM 判断需查知识库 → 调用 rag_query                    │        │
│  │     │    → Qdrant 再次检索                                   │        │
│  │     │    → LLM 综合上下文生成最终答案                          │        │
│  │     │                                                       │        │
│  │  ⑥ format_response（附加引用）                                │        │
│  │     │                                                       │        │
│  │  ⑦ TDAI Memory 保存                                         │        │
│  │     │                                                       │        │
│  │     │  POST /capture → TDAI Gateway                         │        │
│  │     │  → L0→L1→L2→L3 异步提取                                │        │
│  │     │                                                       │        │
│  │  ⑧ Langfuse OTel 自动归档追踪                                │        │
│  │                                                             │        │
│  └─────────────────────────────────────────────────────────────┘        │
│    │                                                                    │
│    ▼                                                                    │
│  最终答案（带引用和工具日志）                                            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                 独立 RAG Pipeline 查询（:8001）                           │
│                                                                         │
│  直接在 RAG Pipeline 查询时的流程：                                    │
│                                                                         │
│  用户输入 → POST /query → RAGPipeline.query()                          │
│    │                                                                    │
│    ├── query_rewriter（可选）                                           │
│    │     LLM 改写 → 多个子查询                                          │
│    │                                                                    │
│    ├── retrieve()                                                       │
│    │     ├── 每个子查询 → Qdrant.search() → 向量结果                    │
│    │     ├── 所有子查询结果去重（按 chunk_id）                           │
│    │     ├── BM25.search() → 关键词结果                                 │
│    │     ├── RRF.fuse(vector, bm25) → 融合排序                         │
│    │     ├── Reranker.rerank(query, docs)（可选）                      │
│    │     └── 返回 top_k 上下文块                                        │
│    │                                                                    │
│    ├── build_prompt(question, contexts, citations)                     │
│    │     └── 带编号引用的结构化 Prompt                                  │
│    │                                                                    │
│    ├── call_llm(prompt) → Ollama qwen3:8b                             │
│    │     └── 生成回答（引用编号自动绑定）                               │
│    │                                                                    │
│    └── → RAGResult{answer, citations[], trace{}}                      │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 五、功能使用说明

### 5.1 文档管理

#### 5.1.1 入库文档

将文档放入 `F:\data\docs`（或配置的文档目录），执行：

```powershell
# 方式一：CLI（在 rag-pipeline 目录）
cd rag-pipeline
.venv\Scripts\python.exe ingest.py F:\data\docs

# 方式二：API
Invoke-RestMethod -Method Post http://127.0.0.1:8001/ingest `
  -ContentType "application/json; charset=utf-8" `
  -Body (@{ path = "F:/data/docs" } | ConvertTo-Json)
```

#### 5.1.2 重建索引

```powershell
# 重建集合（删除所有数据后重新入库）
.venv\Scripts\python.exe ingest.py F:\data\docs --recreate

# 仅重建 BM25 索引（不需要重新向量化）
Invoke-RestMethod -Method Post http://127.0.0.1:8001/rebuild-bm25
```

#### 5.1.3 调优分块参数

```powershell
# 调整 chunk_size 和 overlap
.venv\Scripts\python.exe ingest.py F:\data\docs --chunk-size 1024 --overlap 128
```

### 5.2 智能问答

#### 5.2.1 通过 Open WebUI

1. 访问 `http://localhost:3000`
2. 在管理员设置中添加 OpenAI 连接：
   - API URL: `http://host.docker.internal:8000`（Docker 环境）
   - 留空 API Key 或配置 `API_KEY`
3. 在对话页面选择 LangGraph Agent 模型
4. 开始提问，系统自动路由到知识库/代码/工具/通用模式

#### 5.2.2 通过 RAG Pipeline Web UI

访问 `http://127.0.0.1:8001`，直接输入问题，页面会展示完整的检索→融合→生成流程。

#### 5.2.3 通过 CLI

```powershell
# LangGraph Agent CLI
cd langgraph-agent
python -m src.cli ask "公司年假有几天？"
python -m src.cli stream "公司年假有几天？"  # 流式输出

# RAG Pipeline CLI
cd rag-pipeline
.venv\Scripts\python.exe query.py "公司年假有几天？"
.venv\Scripts\python.exe query.py "请假流程" --show-trace
```

### 5.3 意图路由模式

系统自动根据用户问题分类意图，选择合适的处理路径：

| 意图 | 触发关键词 | 处理路径 | 可用工具 |
|------|-----------|---------|---------|
| **rag** | 知识库、文档、政策、手册、年假 | Qdrant 检索 → LLM 生成 | rag_query |
| **code** | 代码、函数、搜索文件、重构 | 代码库搜索 → 文件读取 | code_search, code_read, file_tree |
| **tool** | 运行、执行、system、内存、ls | 沙箱终端 → 系统信息 | terminal_execute, system_info, calculate |
| **general** | 通用对话（无明确关键词） | 全部工具可用 | 全部8个工具 |

### 5.4 长期记忆管理

#### 5.4.1 启用/禁用

```powershell
# 环境变量控制
TDAI_ENABLED=true    # 启用（默认）
TDAI_ENABLED=false   # 禁用
```

#### 5.4.2 查看记忆

TDAI Memory 的数据存储在 `D:/docker-data/data/tdai-memory/` 目录的 SQLite 文件中。所有记忆以 Markdown 文件 + Mermaid 图表形式存储，可人工阅读调优。

#### 5.4.3 记忆参数

```env
TDAI_GATEWAY_URL=http://tdai-memory:8420    # Gateway 地址
TDAI_ENABLED=true                            # 启用开关
TDAI_RECALL_TOP_K=5                          # 记忆召回数量
```

### 5.5 终端沙箱使用

**注意**：`terminal_execute` 工具受三层沙箱保护：

```python
# 允许的命令示例
terminal_execute("ls -la")
terminal_execute("python --version")
terminal_execute("git status")

# 被拦截的命令示例（返回拦截提示）
terminal_execute("rm -rf /")     # 黑名单拦截
terminal_execute("sudo apt-get") # 提权拦截
terminal_execute("eval(...)")    # 代码执行拦截
```

---

## 六、部署指南

### 6.1 前置条件

- **Docker Desktop**（Windows）/ Docker Engine（Linux）
- **Ollama**（宿主机运行）
- 已拉取模型：
  ```powershell
  ollama pull qwen3:8b
  ollama pull bge-m3
  ```

### 6.2 快速启动

```powershell
# 方式一：使用统一编排文件（推荐）
cd D:\docker-data
docker compose -f docker-compose.all.yml up -d

# 方式二：按模块启动
cd D:\local-agent\langgraph-agent
docker compose -f compose.yaml up -d          # LangGraph Agent + Open WebUI

cd D:\local-agent\rag-pipeline
docker compose -f docker-compose.yml up -d     # Qdrant + Open WebUI (RAG版)

cd D:\local-agent\langfuse
docker compose -f docker-compose.yml up -d    # Langfuse 全套
```

### 6.3 开发环境运行

```powershell
# LangGraph Agent（需要先安装依赖）
cd langgraph-agent
pip install -r requirements.txt
python -m src.cli test     # 连通性测试
python -m src.cli serve    # 启动 API 服务

# RAG Pipeline
cd rag-pipeline
.venv\Scripts\python.exe -m uvicorn api:app --host 0.0.0.0 --port 8001
```

### 6.4 重新部署

```powershell
# 代码修改后重新构建并部署
docker compose -f D:/docker-data/docker-compose.all.yml build langgraph-agent
docker compose -f D:/docker-data/docker-compose.all.yml up -d

# 强制完全重建
docker compose -f D:/docker-data/docker-compose.all.yml build --no-cache langgraph-agent
```

---

## 七、运维与监控

### 7.1 健康检查

```powershell
# LangGraph Agent 健康
curl http://localhost:8000/health

# RAG Pipeline 健康
curl http://localhost:8001/health

# TDAI Memory 健康
curl http://localhost:8420/health

# Qdrant
curl http://localhost:6333/

# Ollama
curl http://localhost:11434/api/tags
```

### 7.2 日志查看

```powershell
docker compose logs -f langgraph-agent
docker compose logs -f rag-pipeline
docker compose logs -f tdai-memory
docker compose logs -f open-webui
```

### 7.3 监控指标

LangGraph Agent 暴露 Prometheus 指标：
- `GET /metrics` — Prometheus 端点
- 包含：请求数、延迟直方图、活跃会话、嵌入缓存命中率、错误计数

Grafana Dashboard：`monitoring/grafana-dashboard-langgraph-agent.json`

### 7.4 Langfuse 可观测性

访问 `http://localhost:3001`：
- **Traces**：查看每次智能体调用的完整追踪
- **Sessions**：按会话聚合查看
- **Metrics**：Token 用量、延迟分布、工具调用统计

### 7.5 常用管理命令

```powershell
# 查看运行状态
docker compose ps

# 停止所有服务
docker compose -f D:/docker-data/docker-compose.all.yml down

# 查看资源占用
docker stats

# 进入容器
docker exec -it kb-langgraph-agent bash
```

---

## 八、安全机制

### 8.1 多层安全防护

| 层 | 机制 | 说明 |
|---|---|---|
| **API 层** | API Key 认证 | Bearer Token，可环境变量配置 |
| **API 层** | 速率限制 | 滑动窗口算法（不同端点不同限制） |
| **API 层** | 熔断器 | 连续5次失败熔断30秒 |
| **API 层** | CORS | 仅允许白名单来源 |
| **工具层** | 终端沙箱 | 三层防御（黑名单+白名单+隔离执行） |
| **工具层** | 计算沙箱 | AST 白名单 + 清空 builtins |
| **工具层** | 文件限制 | 最大50KB，强制编码 |
| **数据层** | PII 过滤 | 正则检测敏感信息（可选） |
| **数据层** | ACL/部门过滤 | Qdrant payload 字段级过滤 |
| **数据层** | 路径安全 | 拒绝符号链接，限制入库目录 |

### 8.2 容错机制

| 组件 | 容错方式 |
|---|---|
| **TDAI Memory** | try/except 包裹，失败不阻塞主流程，可完全关闭 |
| **Qdrant** | 连接超时60s + 重试装饰器（指数退避） |
| **Ollama** | 熔断器 + 重试（连接错误可重试） |
| **Largfuse** | 可选依赖，初始化失败不影响服务启动 |
| **Postgres** | 可选检查点，不可用回退 MemorySaver |

---

> **文档维护**：本文件由 AI 辅助生成，涵盖截至 2026-07-09 的项目状态。
> 如有功能变更，请同步更新此文档及相关 README 文件。
