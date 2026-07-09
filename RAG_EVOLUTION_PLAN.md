# RAG 架构演进分析与优化记录

> 记录于 2026-07-09，基于对现有 RAG 系统的架构审查和未来演进方向的分析。

---

## 一、背景

当前项目（`D:\local-agent`）构建了一套企业级 RAG 系统，核心组件包括：

- **LangGraph Agent** (`langgraph-agent/`) — 基于 LangGraph 的智能体 API 服务
- **RAG Pipeline** (`rag-pipeline/`) — 文档处理流水线（解析、分块、嵌入、检索、生成）

系统使用 Qdrant 向量数据库 + BM25 稀疏索引 + RRF 融合的混合检索方案，嵌入模型为 bge-m3（Ollama），生成模型为 qwen3:8b。

---

## 二、RAG 架构演变全景

### 2.1 Standard RAG (当前系统)

```
文档 → 分块 → 向量化 → 存入 Qdrant
    
用户提问 → 一次向量检索 → 结果注入 Prompt → LLM 生成
```

**特征**：固定流水线，检索→生成单向流程，一次搜索后即生成答案。

### 2.2 Agentic RAG (目标架构)

```
用户提问 → Agent(LLM) 自主判断
              │
              ├─ 需要检索 → 调用 rag_query 工具
              │     ├─ 结果不够 → 改写查询再搜
              │     ├─ 结果仍不够 → 再次改写/扩大范围
              │     └─ 结果充足 → 综合生成
              │
              ├─ 需要代码搜索 → 调用 code_search 工具
              │
              └─ 需要系统操作 → 调用 terminal_execute 等工具
```

**特征**：LLM 自主决策何时检索、检索什么、是否继续检索，RAG 只是 Agent 工具箱中的一项能力。

### 2.3 GraphRAG (远期可选)

```
文档 → 提取实体 + 关系 → 构建知识图谱 → 社区检测
    → 生成社区摘要 → 用户提问 → 图遍历 + 语义搜索 → 回答
```

**特征**：适合多跳关系推理（"A 和 B 有什么关系？"），不适合精确事实查询。实现复杂度高。

---

## 三、三种架构对比

| 维度 | Standard RAG (当前) | Agentic RAG (目标) | GraphRAG (远期) |
|------|-------------------|-------------------|----------------|
| **检索方式** | 一次向量搜索 | 多步迭代检索，Agent 自主决策 | 图谱遍历 + 社区摘要 |
| **查询改写** | 线性（可选、固定流程） | 迭代式，根据结果动态调整 | 不需要 |
| **多步推理** | ❌ 不支持 | ✅ LLM 自主判断 | ✅ 图谱原生支持 |
| **关系推理** | ❌ 不支持 | 靠 LLM 推理能力 | ✅ 原生支持 |
| **工具编排** | ❌ 硬编码路由 | ✅ Agent 自主决定 | ❌ 不涉及 |
| **精确事实查找** | 🟢 好 | 🟢 好 | 🟡 一般 |
| **跨文档关系** | 🔴 弱 | 🟡 中 | 🟢 强 |
| **实现复杂度** | 🟢 低 | 🟡 中 | 🔴 高 |
| **查询延迟** | 🟢 低（一次搜索） | 🟡 中（可能多轮） | 🟡 中 |
| **灵活性** | 🔴 固定流水线 | 🟢 Agent 自主决策 | 🔴 固定图谱结构 |

---

## 四、当前系统评估

### ✅ 已有的成熟部分

| 模块 | 评价 |
|------|------|
| **多路检索** (向量 + BM25 + RRF) | 已实现，比单向量搜索强 |
| **重排序** (BGE Reranker) | 已实现（默认关闭） |
| **查询改写** | 代码已写（默认关闭） |
| **文档解析** (Docling + pypdf) | 覆盖主流格式 |
| **API 设计** | RESTful，结构清晰 |
| **Qdrant 向量库** | 稳定运行 |

### ❌ 核心问题

| 问题 | 影响 |
|------|------|
| **意图路由是硬编码关键词匹配** | 只认"知识库""文档"等关键词，用户用自然语言提问时路由错误 |
| **RAG 和 Agent 是拼凑而非融合** | `rag_fetch` 在 Agent 之前强制注入上下文，不是 Agent 自主调用 |
| **没有迭代检索** | 搜一次就结束，搜不好用户只能重问 |
| **没有结果质量验证** | 搜到的内容没人检查是否足够 |
| **不支持多跳(Multi-hop)查询** | 复杂问题需要搜多个地方时做不到 |
| **RAG Pipeline 和 Agent 的 RAG 功能重复** | 两套代码维护两份相同逻辑 |

### 核心架构缺陷

当前图流程：

```
intent_router(关键词匹配)
  ├─ 命中"知识库" → rag_fetch(强制搜一次) → agent_orchestrator
  ├─ 命中"代码"   → code_search → agent_orchestrator
  └─ 没命中       → agent_orchestrator(直接回答)
```

**问题本质**：RAG 不是 Agent 的**工具**，而是 Agent 之前的**预处理步骤**。Agent 没有选择权。

---

## 五、Agentic RAG 设计方案

### 5.1 核心变化

```
旧架构：
  关键词分类 → 强制 RAG → Agent(LLM) 生成

新架构：
  Agent(LLM) 自主判断 → 可选调用 rag_query(支持多轮) → 综合生成
```

### 5.2 关键改动点

1. **删除 intent_router 和 conditional_router** — 不再用关键词硬编码分类
2. **删除 rag_fetch 预处理节点** — 不再强制注入上下文
3. **增强 rag_query 工具** — 支持迭代搜索、查询改写、结果质量自检
4. **Agent 拥有全部工具** — 由 LLM 自主决定何时调用 rag_query、code_search 等
5. **保留 code_search 等专用工具** — LLM 根据问题类型自行选择

### 5.3 新架构流程

```
用户提问
    │
    ▼
Agent(LLM) + 全部工具(rag_query, code_search, terminal_execute, ...)
    │
    ├── LLM 判断: "这个问题需要查知识库"
    │     └──→ 调用 rag_query("公司年假政策")
    │           └──→ 返回结果 → LLM 判断是否足够
    │                 ├── 不够 → 再调 rag_query("年假天数 2025")
    │                 └── 够 → 综合生成答案
    │
    ├── LLM 判断: "需要查代码"
    │     └──→ 调用 code_search → code_read → 回答
    │
    └── LLM 判断: "通用问题，不需要工具"
          └──→ 直接回答
```

### 5.4 增强的 rag_query 工具设计

```
rag_query(query: str, top_k: int = 10) -> str
  → 支持 LLM 多轮调用
  → 内部实现：
    1. 可选的查询改写（LLM 扩展 query）
    2. Qdrant 向量检索
    3. BM25 关键词检索
    4. RRF 融合排序
    5. 可选重排序
    6. 返回带引用的结果
```

---

## 六、实施计划

### Phase 1 — Agentic RAG 整合 (当前)

| 步骤 | 改动文件 | 内容 |
|------|---------|------|
| 1 | `graph.py` | 删除 `intent_router`、`conditional_router`、`rag_fetch`、`code_search_node` 节点 |
| 2 | `graph.py` | 简化图流程：START → `agent_orchestrator` → `format_response` → END |
| 3 | `graph.py` | 给 Agent 注册全部工具，不再按意图过滤 |
| 4 | `graph.py` | 移除意图相关的系统提示词，使用统一的通用提示词 |
| 5 | `tools.py` | 增强 `rag_query` 工具，支持更灵活的调用 |
| 6 | `tools.py` | 清理 `_TOOLS_BY_INTENT`、`_SYSTEM_PROMPTS` 等意图相关代码 |
| 7 | `rag/qdrant_client.py` | 保留，作为 rag_query 的内部实现 |

### Phase 2 — 检索质量提升 (后续)

| 步骤 | 内容 |
|------|------|
| 1 | rag_query 添加内部查询改写（扩写/拆分为子查询） |
| 2 | rag_query 添加结果质量自检（判断是否需要再次检索） |
| 3 | 改进分块策略（按 Markdown 标题分块，而非固定 512 token） |
| 4 | 开启重排序（use_reranker=true） |

### Phase 3 — 多步推理增强 (远期)

| 步骤 | 内容 |
|------|------|
| 1 | 支持 Agent 多次调用 rag_query 进行多跳推理 |
| 2 | 添加"搜索结果摘要"能力，帮助 Agent 判断是否需要继续搜索 |
| 3 | 评估 GraphRAG 是否需要 |

---

## 七、决策记录

### 2026-07-09: 从 Standard RAG 升级到 Agentic RAG

**原因**：
- 关键词意图路由过于脆弱，无法处理自然语言提问
- RAG 作为预处理步骤，Agent 没有自主选择权
- 不支持迭代检索，一次搜索不够时无法自动补充

**方案选择**：选择 Agentic RAG 而非 GraphRAG
- Agentic RAG 改造成本低，收益明确
- GraphRAG 实现复杂，且当前场景（个人知识库问答）以精确事实查询为主
- 后期可根据需求再引入 GraphRAG 作为补充

**影响范围**：
- `langgraph-agent/src/agent/graph.py` — 大幅简化，移除路由逻辑
- `langgraph-agent/src/agent/tools.py` — 增强 rag_query，清理意图相关代码
- `rag-pipeline/` — 保留作为文档入库工具，不再承担查询职责
- `ARCHITECTURE.md` — 需要更新架构描述
