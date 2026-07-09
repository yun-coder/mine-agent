# 本地架构优化计划

> 基于当前项目代码，不改架构前提下的渐进式优化，按收益排序。

---

## 优化前当前瓶颈诊断

| 环节 | 耗时 | 原因 |
|------|------|------|
| Embedding 入库 | ~60s/页 | `embedder.py` 一次一条 HTTP 请求，串行 |
| LLM 首字 | 5-30s | 非流式生成，等全部输出完才返回 |
| Reranker 加载 | ~3s/次 | CrossEncoder 首次加载模型到内存 |
| BM25 重建 | ~10s/千chunk | pickle 序列化 + 独立维护 |
| 全链路 | 串行 | embedding → 检索 → rerank → LLM 无并行 |

---

## Phase 1: 流式输出 (收益最大, 1小时)

**目标**: 首字延迟从 5-30s → 1-3s

### 修改文件: `rag_pipeline.py`

**改动 1**: `call_llm` 改为流式调用

```python
def call_llm_stream(self, prompt: str):
    """流式调用 Ollama, 生成器逐 token 产出"""
    with httpx.Client(timeout=600.0) as client:
        with client.stream(
            "POST",
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": settings.llm_model,
                "prompt": prompt,
                "stream": True,
                "options": {"temperature": 0.1, "num_ctx": 8192},
            },
        ) as response:
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if "response" in data:
                    yield data["response"]
```

### 修改文件: `api.py`

**改动 2**: `/query` 端点增加流式接口

```python
from fastapi.responses import StreamingResponse

@app.post("/query/stream", response_class=StreamingResponse)
def query_stream(req: QueryRequest):
    """流式问答: SSE 逐 token 输出"""
    import json

    pipeline = get_pipeline()

    def generate():
        contexts, trace = pipeline.retrieve(
            req.question,
            filter_acl=req.filter_acl,
            filter_dept=req.filter_dept,
        )
        # 先发检索结果
        yield json.dumps({"type": "trace", "data": trace}) + "\n"
        # 再流式生成答案
        prompt = pipeline.build_prompt(req.question, contexts)
        for token in pipeline.call_llm_stream(prompt):
            yield json.dumps({"type": "token", "data": token}) + "\n"
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

### 修改文件: `query.py`

**改动 3**: CLI 也支持流式输出

```python
# 在 main() 中, 使用 call_llm_stream 替代 call_llm
# 逐 token print 到终端
for token in pipeline.call_llm_stream(prompt):
    print(token, end="", flush=True)
print()
```

---

## Phase 2: Embedding 批量接口 (入库提速 3-5x, 2小时)

**目标**: 批量嵌入替代逐条请求

### 修改文件: `embedder.py`

```python
class OllamaEmbedder:
    def embed(self, texts: List[str]) -> List[List[float]]:
        """使用 /api/embed 批量接口 (替代逐条 /api/embeddings)"""
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": texts,
                }
            )
            r.raise_for_status()
            return r.json()["embeddings"]

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]
```

### 修改文件: `config.py`

可选: 更换 embedding 模型为更快的 nomic-embed-text

```python
embed_model: str = "nomic-embed-text"  # 替代 bge-m3, 274MB vs 2.4GB
```

执行:
```bash
ollama pull nomic-embed-text
```

---

## Phase 3: Reranker 懒加载 + 缓存 (首查询提速 2-3s, 30分钟)

**目标**: 避免每次请求都重新加载 reranker 模型

### 修改文件: `reranker.py`

```python
class LocalReranker:
    _instance = None
    _lock = None

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", cache_dir: str = None):
        # ... 现有逻辑不变
        pass

    @classmethod
    def get_instance(cls, cache_dir: str = None) -> "LocalReranker":
        """单例模式, 全局共享 reranker 实例"""
        if cls._instance is None:
            cls._instance = cls(cache_dir=cache_dir)
        return cls._instance
```

### 修改文件: `rag_pipeline.py`

```python
def _ensure_reranker(self):
    """使用单例共享 reranker 实例"""
    if not self.use_reranker or self._reranker_load_attempted:
        return
    self._reranker_load_attempted = True
    try:
        logger.info("Loading reranker (first run downloads model)...")
        self.reranker = LocalReranker.get_instance(cache_dir=str(settings.hf_cache_dir))
        logger.info("Reranker ready")
    except Exception as e:
        logger.warning(f"Reranker failed; falling back to retrieval order: {e}")
        self.use_reranker = False
```

---

## Phase 4: 查询改写 (召回率提升, 2小时)

**目标**: 用户提问 → 自动扩展为多个子查询 → 分别检索 → 合并去重

### 新增文件: `query_rewriter.py`

```python
"""查询改写: 将用户问题扩展为多个检索友好的子查询"""
import json
import httpx
from loguru import logger
from config import settings


def rewrite_query(question: str, num_queries: int = 3) -> list[str]:
    """LLM 改写问题为多个子查询"""
    prompt = f"""将以下问题改写为{num_queries}个更利于知识库检索的查询。
要求:
1. 每个查询简洁明确, 不超过 30 字
2. 只返回 JSON 数组格式, 不要其他内容
3. 覆盖原问题的不同角度

原问题: {question}

输出格式示例: ["查询1", "查询2", "查询3"]
"""
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            f"{settings.ollama_base_url}/api/generate",
            json={
                "model": settings.llm_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 300},
            },
        )
        r.raise_for_status()
        response_text = r.json()["response"].strip()

    # 尝试从响应中提取 JSON 数组
    try:
        # 直接解析
        queries = json.loads(response_text)
    except json.JSONDecodeError:
        # 尝试找 [...] 部分
        start = response_text.find("[")
        end = response_text.rfind("]") + 1
        if start >= 0 and end > start:
            queries = json.loads(response_text[start:end])
        else:
            logger.warning(f"查询改写失败, 返回原问题: {response_text[:200]}")
            return [question]

    if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
        return queries
    return [question]
```

### 修改文件: `rag_pipeline.py`

```python
from query_rewriter import rewrite_query

def retrieve(self, query: str, ...):
    """支持查询改写"""
    # 改写为多个子查询
    sub_queries = rewrite_query(query)
    logger.debug(f"Query rewritten: {query} → {sub_queries}")

    all_results = []
    for sq in sub_queries:
        results = self.qdrant.search(sq, top_k=settings.top_k_vector)
        all_results.extend(results)

    # 去重 (按 chunk_id)
    seen = set()
    unique_results = []
    for r in all_results:
        cid = r.get("metadata", {}).get("chunk_id", r.get("id"))
        if cid and cid not in seen:
            seen.add(cid)
            unique_results.append(r)

    return unique_results[:settings.top_k_vector]
```

---

## Phase 5: 异步化改造 (综合提速, 半天)

**目标**: 检索阶段完全异步并行

### 修改文件: `rag_pipeline.py`

```python
import asyncio
import httpx

async def retrieve_async(self, query: str, ...):
    """异步并行检索: vector + BM25 + rerank 并发"""

    # 并行执行向量和 BM25 检索
    vector_task = asyncio.create_task(self._vector_search(query))
    bm25_task = asyncio.create_task(self._bm25_search(query))

    vector_results, bm25_ranked = await asyncio.gather(vector_task, bm25_task)

    # RRF 融合
    fused = rrf_fuse(
        [(r["id"], r["score"]) for r in vector_results],
        bm25_ranked,
        k=settings.rrf_k,
    )[:settings.top_k_rrf]

    # Rerank
    if self.use_reranker and fused:
        reranked = await self._rerank(query, fused)
    else:
        reranked = fused

    return self._build_final_results(reranked, vector_results)
```

---

## 优化效果预估

| 阶段 | 改动 | 预计提升 |
|------|------|----------|
| Phase 1 流式输出 | SSE + call_llm_stream | 首字 30s → 2s |
| Phase 2 批量 embedding | /api/embed | 入库速度 3-5x |
| Phase 3 Reranker 单例 | 共享实例 | 首查询 -3s |
| Phase 4 查询改写 | rewrite_query | 召回率 +20-30% |
| Phase 5 异步化 | asyncio.gather | 检索阶段 -40% |

---

## 实施顺序

```
Phase 1 (1h)  →  立竿见影, 先改流式输出
Phase 3 (30m) →  顺手做, reranker 单例缓存
Phase 2 (2h)  →  批量 embedding, 提升入库
Phase 4 (2h)  →  查询改写, 提升质量
Phase 5 (半天)→  全面异步化, 收尾
```

总计: **1.5-2 天**完成全部优化，无需改动现有架构。
