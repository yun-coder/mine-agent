# 企业级本地知识库 RAG

本项目是一个在 Windows 本机运行的 RAG 问答系统：文档解析后切成语义 chunk，写入 Qdrant 向量库，同时构建本地 BM25 索引；查询时支持**查询改写**、**向量+BM25 混合检索**、**RRF 融合**、**可选 reranker 精排**，并将命中的 chunk 作为上下文交给 Ollama 本地大模型生成带引用的答案。

项目目录：`F:\projects\rag-pipeline`
默认数据目录：`F:\data`

## 架构

```text
PDF / DOCX / TXT / MD / HTML
        |
        v
parser.py              Docling 优先，PDF 可降级到 pypdf
        |
        v
chunker.py             按句子累积，默认 chunk_size=512，overlap=64
        |
        +--> embedder.py   Ollama /api/embed 批量接口 (降级逐条)
        |        |
        |        v
        |   Qdrant payload + vector
        |
        +--> bm25_index.py     rank-bm25 本地倒排索引 -> F:/data/qdrant/bm25.pkl

query
        |
        +--> query_rewriter.py  LLM 改写为多个子查询
        |
        +--> Qdrant cosine top_k_vector=20
        +--> BM25 keyword top_k_bm25=20
        +--> RRF fusion top_k_rrf=10
        +--> 可配置 reranker 精排 (单例共享)，默认取 top_k_rerank=5
        +--> Ollama qwen3:8b 流式生成答案和引用
```

> 精排由 `.env` 中的 `USE_RERANKER` 控制。设为 `true` 会加载 `BAAI/bge-reranker-v2-m3`，首次查询可能需要下载模型到 `F:/data/hf_cache`。

## 新功能 (v2)

| 功能 | 说明 |
|------|------|
| SSE 流式输出 | `/query/stream` 端点和 `--stream` CLI 参数，首字延迟 1-3s |
| 批量 Embedding | 优先使用 Ollama `/api/embed` 批量接口，入库提速 3-5x |
| 查询改写 | `query_rewriter.py` 自动将问题扩展为多个子查询，提升召回率 |
| Reranker 单例 | 全局共享 CrossEncoder 实例，避免每次请求重新加载 |
| 去重检索 | 多子查询结果按 chunk_id 去重，避免冗余 |

## 依赖

需要本机安装：

- Python 3.11
- Docker Desktop
- Ollama

Python 依赖见 `requirements.txt`。项目提供 `setup_env.ps1` 创建独立 `.venv`，不会依赖 Codex/Hermes 环境。

## 目录

```text
F:\projects\rag-pipeline\
  api.py                  FastAPI 服务和静态 Web UI
  static\index.html       问答页面
  ingest.py               文档入库 CLI
  query.py                命令行提问 (支持 --stream)
  query_rewriter.py       查询改写模块
  rag_pipeline.py         检索、融合、生成主流程
  chunker.py              文档切块
  parser.py               文档解析
  embedder.py             Embedding 客户端 (批量接口)
  qdrant_store.py         Qdrant 写入和向量检索
  bm25_index.py           BM25 和 RRF
  reranker.py             Reranker (单例模式)
  start_all.py            启动 Docker 服务和 FastAPI
  setup_env.ps1           创建项目虚拟环境并安装依赖
  start_services.ps1      使用项目 venv 启动服务
  stop_services.ps1       停止服务
```

默认数据目录：

```text
F:\data\
  docs\                   待入库文档
  qdrant\                 Qdrant 数据与 BM25 缓存
  hf_cache\               HuggingFace 缓存
  logs\rag\               API 日志
```

## 快速开始

在 PowerShell 中运行：

```powershell
cd F:\projects\rag-pipeline
.\setup_env.ps1
```

拉取 Ollama 模型：

```powershell
ollama pull qwen3:8b
ollama pull bge-m3
```

启动 Ollama。如果 Ollama 已经作为 Windows 服务或托盘应用运行，可以跳过这一步：

```powershell
ollama serve
```

另开一个 PowerShell，启动 Qdrant、Open WebUI 和 FastAPI：

```powershell
cd F:\projects\rag-pipeline
.\start_services.ps1
```

访问：

- RAG 问答页面：http://127.0.0.1:8000
- FastAPI 文档：http://127.0.0.1:8000/docs
- Qdrant Dashboard：http://127.0.0.1:6333/dashboard
- Open WebUI：http://127.0.0.1:3000

## 入库文档

把文档放到 `F:\data\docs`：

```powershell
New-Item -ItemType Directory -Force F:\data\docs
```

入库整个目录：

```powershell
cd F:\projects\rag-pipeline
.\.venv\Scripts\python.exe ingest.py F:\data\docs
```

重建 Qdrant collection：

```powershell
.\.venv\Scripts\python.exe ingest.py F:\data\docs --recreate
```

调整切块大小：

```powershell
.\.venv\Scripts\python.exe ingest.py F:\data\docs --chunk-size 512 --overlap 64
```

也可以通过 API 入库：

```powershell
$body = @{
  path = "F:/data/docs"
  recreate = $false
  chunk_size = 512
  overlap = 64
} | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/ingest `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

## 提问

### 浏览器方式

打开 http://127.0.0.1:8000，直接在问答页输入问题。页面会展示动态流程模拟：文档如何切块、向量和 BM25 如何定位、RRF 如何融合、命中的 payload 如何变成引用答案。

### CLI 方式

```powershell
cd F:\projects\rag-pipeline
.\.venv\Scripts\python.exe query.py "公司年假有几天？"
.\.venv\Scripts\python.exe query.py "请假流程" --show-trace
.\.venv\Scripts\python.exe query.py "差旅报销政策" --stream    # 流式输出
```

### API 方式

```powershell
# 普通查询
$body = @{
  question = "公司年假有几天？"
  filter_dept = @("技术部")
} | ConvertTo-Json
Invoke-RestMethod -Method Post http://127.0.0.1:8000/query `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))

# 流式查询 (SSE)
Invoke-RestMethod -Method Post http://127.0.0.1:8000/query/stream `
  -ContentType "application/json; charset=utf-8" `
  -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
```

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:6333/
Invoke-RestMethod http://127.0.0.1:11434/api/tags
docker compose ps
```

端口：

| 服务 | 地址 | 说明 |
|---|---|---|
| RAG Web UI | http://127.0.0.1:8000 | 本项目问答页 |
| RAG API Docs | http://127.0.0.1:8000/docs | Swagger |
| /query/stream | http://127.0.0.1:8000/query/stream | SSE 流式端点 |
| Qdrant | http://127.0.0.1:6333 | 向量数据库 REST |
| Qdrant gRPC | 127.0.0.1:6334 | gRPC |
| Open WebUI | http://127.0.0.1:3000 | 备用聊天前端 |
| Ollama | http://127.0.0.1:11434 | 本地 LLM 和 embedding |

## 数据结构

解析后的统一文档结构：

```python
Document(
    content="文档正文或 Markdown",
    metadata={
        "source": "F:/data/docs/example.md",
        "filename": "example.md",
        "filetype": ".md",
        "page": 1,
        "title": "example"
    }
)
```

切块后的结构：

```python
Chunk(
    text="chunk 文本",
    chunk_id="F:/data/docs/example.md::p1::c0",
    metadata={
        "source": "F:/data/docs/example.md",
        "filename": "example.md",
        "filetype": ".md",
        "page": 1,
        "chunk_index": 0,
        "total_chunks": 3
    }
)
```

写入 Qdrant 的 point：

```python
PointStruct(
    id="<uuid>",
    vector=[...],  # bge-m3 1024 维
    payload={
        "text": "chunk 文本",
        "chunk_id": "...::c0",
        "source": "...",
        "filename": "...",
        "filetype": ".md",
        "page": 1,
        "chunk_index": 0,
        "total_chunks": 3
    }
)
```

查询返回的 citation：

```json
{
  "source": "F:/data/docs/example.md",
  "filename": "example.md",
  "page": 1,
  "score": 0.812,
  "text": "命中的 chunk 文本"
}
```

## 工具说明

| 工具 | 用途 |
|---|---|
| FastAPI | 提供 `/`, `/health`, `/query`, `/query/stream`, `/ingest`, `/rebuild-bm25` |
| Ollama | 运行 `qwen3:8b` 和 `bge-m3` |
| Qdrant | 存储向量和 payload，按 cosine 相似度检索 |
| rank-bm25 | 本地关键词召回 |
| Docling | 解析 PDF、DOCX、PPTX、XLSX 等文档 |
| pypdf | Docling 不可用时解析 PDF 的降级方案 |
| Loguru | 日志 |
| RAGAS | 离线评估脚本 `evaluate.py` 使用 |

## 配置

默认配置在 `config.py`，也可以通过 `.env` 覆盖：

```dotenv
OLLAMA_BASE_URL=http://127.0.0.1:11434
LLM_MODEL=qwen3:8b
EMBED_MODEL=bge-m3
QDRANT_HOST=127.0.0.1
QDRANT_PORT=6333
QDRANT_COLLECTION=enterprise_kb
DOCS_DIR=F:/data/docs
QDRANT_DATA_DIR=F:/data/qdrant
HF_CACHE_DIR=F:/data/hf_cache
LOG_DIR=F:/data/logs/rag
USE_RERANKER=true
```

修改 `.env` 后重启 FastAPI。

## 评估

准备 `F:\data\rag_eval\dataset.jsonl`：

```json
{"question":"公司年假有几天？","ground_truth":"工作满 1 年不满 10 年为 5 天。"}
```

运行：

```powershell
cd F:\projects\rag-pipeline
.\.venv\Scripts\python.exe evaluate.py F:\data\rag_eval\dataset.jsonl
```

## 停止服务

```powershell
cd F:\projects\rag-pipeline
.\stop_services.ps1
```

或者：

```powershell
docker compose down
Get-Process python -ErrorAction SilentlyContinue
```

## 常见问题

### `/query` 返回 500 或一直等待

先检查 Ollama：

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
ollama list
```

确认至少有 `qwen3:8b` 和 `bge-m3`。

### `/health` 显示 Qdrant 不可用

```powershell
cd F:\projects\rag-pipeline
docker compose up -d
docker compose ps
Invoke-RestMethod http://127.0.0.1:6333/
```

### 没有引用或找不到结果

检查 collection 里是否有数据：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

如果 `qdrant_count` 是 0，重新入库：

```powershell
.\.venv\Scripts\python.exe ingest.py F:\data\docs
```

### 中文 curl body 解析失败

Windows 下建议用 PowerShell `Invoke-RestMethod` 或 Python `requests.post(json=...)`，避免 shell 编码差异。

### 不想依赖 Hermes/Codex Python

运行：

```powershell
cd F:\projects\rag-pipeline
.\setup_env.ps1
.\start_services.ps1
```

`start_all.py` 会拒绝使用路径中包含 Hermes 的 Python，并优先切到项目 `.venv`、系统 Python 3.11 或 `py -3.11`。

### 流式输出不工作

- CLI 需加 `--stream` 参数
- API 使用 `/query/stream` 端点而非 `/query`
- 确保 Ollama 正在运行且 `qwen3:8b` 已拉取
