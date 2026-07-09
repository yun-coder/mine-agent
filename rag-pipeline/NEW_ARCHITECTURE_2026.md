# 2026 企业级本地 RAG 知识库 — 最新技术方案

## 一、核心选型

| 模块 | 选型 | 版本 |
|------|------|------|
| 推理引擎 | Ollama | v0.5+ (统一 embedding + rerank + LLM) |
| LLM 模型 | Qwen3:8B | GGUF 量化 |
| Embedding | nomic-embed-text | v1.6 (274MB, 768 维) |
| Reranker | bge-reranker-v2-m3 | Ollama 内置 |
| 向量库 | Qdrant | v1.13+ (原生混合检索) |
| 文档解析 | Unstructured.io | 0.16+ (替代 docling) |
| 检索策略 | 查询改写 + 混合检索 + Rerank + 流式输出 | |

## 二、架构图

```
用户 ──→ FastAPI (SSE 流式) ──→ Qdrant (Hybrid Search)
                              │
                         ┌────┴────┐
                         │ Ollama  │ ← 统一推理 (embedding + rerank + LLM)
                         └─────────┘
                              │
                    并行: 稠密向量 + 稀疏向量
                          RRF 融合 → Rerank → 流式生成
```

## 三、关键技术决策

### 3.1 Embedding: nomic-embed-text vs bge-m3

- **nomic-embed-text:v1.6** — 274MB, 速度快, MTEB 排名前十
- **bge-m3** — 2.4GB, 多语言强但大, 批量接口已支持
- 建议: 先用 nomic 提速, 若召回率不够再换回 bge-m3

### 3.2 Qdrant 原生混合检索 (Hybrid Search)

Qdrant v1.10+ 原生支持稀疏向量 (sparse vector)，内置 BM25 风格相似度：
- 稠密向量 (dense) + 稀疏向量 (sparse) 同时检索
- 原生 RRF 融合，无需独立维护 BM25 索引
- 减少组件: 删除独立 pickle BM25 模块

### 3.3 Reranker: Ollama 内置 bge-reranker

- `ollama pull bge-reranker:v2-m3` (约 4.4GB)
- 统一通过 Ollama `/api/rerank` 调用，无需独立 sentence-transformers 进程
- 替代原有的 CrossEncoder 方案

### 3.4 流式输出 (SSE)

LLM 生成改为 `stream=True`，前端通过 Server-Sent Events 接收逐 token 输出：
- 首字延迟从 5-30s 降到 1-3s
- 感知体验显著提升

### 3.5 文档解析: Unstructured.io

- `unstructured[all-docs]` 替代 `docling`
- 更好的表格识别、图片 OCR (配合 easyocr)
- 更轻量的依赖链

## 四、检索流程

```
用户提问
  │
  ├─→ 查询改写 (LLM, 温度 0.3) ──→ [查询1, 查询2, 查询3]
  │
  ├─→ 并行检索 (Qdrant Hybrid) ──→ 稠密 + 稀疏向量 RRF 融合
  │
  ├─→ Rerank (bge-reranker) ──→ Top-3 精排
  │
  └─→ LLM 生成 (SSE 流式) ──→ 带引用的答案
```

## 五、依赖清单

```txt
# === Web Framework ===
fastapi==0.115.0
uvicorn[standard]==0.30.0
pydantic==2.13.3
pydantic-settings==2.14.0

# === HTTP Client ===
httpx==0.28.1

# === Vector DB ===
qdrant-client==1.13.0

# === Document Parsing ===
unstructured[all-docs]==0.16.0
python-docx==1.1.2
python-pptx==1.0.2
openpyxl==3.1.5

# === OCR (可选) ===
easyocr==1.7.1

# === Evaluation ===
ragas==0.2.10
datasets==3.2.0

# === Logging ===
loguru==0.7.2

# === Env ===
python-dotenv==1.0.1
python-multipart==0.0.12
```

## 六、Docker Compose

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.13.4
    container_name: qdrant
    restart: unless-stopped
    ports: ["6333:6333", "6334:6334"]
    volumes:
      - ./qdrant_storage:/qdrant/storage
    deploy:
      resources:
        limits:
          memory: 4G

  open-webui:
    image: openwebui/open-webui:latest
    container_name: open-webui
    restart: unless-stopped
    ports: ["3000:8080"]
    volumes:
      - ./open-webui-data:/app/backend/data
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      - WEBUI_AUTH=false
      - RAG_EMBEDDING_ENGINE=ollama
      - RAG_EMBEDDING_MODEL=nomic-embed-text:v1.6
    extra_hosts:
      - "host.docker.internal:host-gateway"

  rag-api:
    build:
      context: .
      dockerfile: Dockerfile.rag
    container_name: rag-api
    restart: unless-stopped
    ports: ["8000:8000"]
    volumes:
      - ./docs:/app/docs:ro
      - ./storage:/app/storage
    environment:
      - OLLAMA_BASE_URL=http://host.docker.internal:11434
      - QDRANT_HOST=qdrant
    depends_on:
      - qdrant
```

## 七、Ollama 模型

```bash
ollama pull qwen3:8b
ollama pull nomic-embed-text:v1.6
ollama pull bge-reranker:v2-m3
```

## 八、预期性能

| 指标 | 当前 | 新方案 | 提升 |
|------|------|--------|------|
| 首次回答时间 (TTFT) | 8-15s | 1-3s | 5-10x |
| 输出速度 | ~3 tok/s | ~25 tok/s (流式) | 8x |
| 入库速度 | ~1min/页 | ~10s/页 (批量) | 6x |
| 检索精度 | ~0.65 | ~0.85 | +30% |
| 组件数 | 6 个 | 4 个 | -33% |
