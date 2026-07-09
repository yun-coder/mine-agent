"""FastAPI service for the local RAG pipeline and static Web UI."""
import asyncio
import json
import os
from pathlib import Path
from typing import List, Optional

os.environ["HF_HOME"] = "D:/projects/data/hf_cache"

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from config import settings
from rag_pipeline import RAGPipeline

PROJECT_DIR = Path(__file__).parent
STATIC_DIR = PROJECT_DIR / "static"

app = FastAPI(title="企业 RAG API", version="1.0.0")
allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_pipeline: Optional[RAGPipeline] = None


def get_pipeline() -> RAGPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = RAGPipeline(use_reranker=settings.use_reranker)
        _pipeline.use_query_rewrite = settings.use_query_rewrite
    return _pipeline


class QueryRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    filter_acl: Optional[List[str]] = Field(None, description="ACL 组过滤")
    filter_dept: Optional[List[str]] = Field(None, description="部门过滤")
    top_k_rerank: Optional[int] = Field(5, description="最终返回 chunk 数；当前 API 默认关闭 rerank")

    @field_validator("question")
    @classmethod
    def validate_question_length(cls, v: str) -> str:
        if len(v) > 4000:
            raise ValueError("问题不能超过 4000 个字符 / Question cannot exceed 4000 characters")
        return v.strip()


class Citation(BaseModel):
    source: str
    filename: str
    page: Optional[int]
    score: float
    text: str


class QueryResponse(BaseModel):
    answer: str
    citations: List[Citation]
    trace: dict
    elapsed_ms: int


class HealthResponse(BaseModel):
    status: str
    qdrant_count: int
    models: dict
    use_reranker: bool


class IngestRequest(BaseModel):
    path: str = Field(..., description="文件或目录绝对路径")
    recreate: bool = Field(False, description="是否重建集合")
    chunk_size: int = Field(512, description="分块大小")
    overlap: int = Field(64, description="分块 overlap")

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        p = Path(v)
        if not p.exists():
            raise ValueError(f"路径不存在 / Path does not exist: {v}")
        return v


@app.get("/", response_class=HTMLResponse)
def index():
    """Web UI homepage."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>企业 RAG API</h1><p>访问 <a href='/docs'>/docs</a> 试用 API</p>")


@app.get("/health", response_model=HealthResponse)
def health():
    import httpx

    p = get_pipeline()
    try:
        count = p.qdrant.count()
    except Exception as e:
        raise HTTPException(503, f"Qdrant 不可用: {e}")

    models = {}
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{settings.ollama_base_url}/api/tags")
            for m in r.json().get("models", []):
                models[m["name"]] = m.get("size", 0)
    except Exception as e:
        models = {"error": str(e)}
    return HealthResponse(
        status="ok",
        qdrant_count=count,
        models=models,
        use_reranker=settings.use_reranker,
    )


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    """Ask the knowledge base."""
    import time

    p = get_pipeline()
    t0 = time.time()
    result = p.query(
        req.question,
        filter_acl=req.filter_acl,
        filter_dept=req.filter_dept,
    )
    elapsed = int((time.time() - t0) * 1000)
    return QueryResponse(
        answer=result.answer,
        citations=[
            Citation(
                source=c.get("metadata", {}).get("source", ""),
                filename=c.get("metadata", {}).get("filename", ""),
                page=c.get("metadata", {}).get("page"),
                score=c.get("rerank_score", c.get("score", 0)),
                text=c["text"][:500],
            )
            for c in result.citations
        ],
        trace=result.trace,
        elapsed_ms=elapsed,
    )


@app.post("/query/stream")
def query_stream(req: QueryRequest):
    """Stream answer token by token via SSE."""
    import time

    p = get_pipeline()
    t0 = time.time()
    contexts, trace = p.retrieve(
        req.question,
        filter_acl=req.filter_acl,
        filter_dept=req.filter_dept,
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    if not contexts:
        return StreamingResponse(
            iter([json.dumps({"type": "done", "elapsed_ms": elapsed_ms}) + "\n"]),
            media_type="text/event-stream",
        )

    prompt = p.build_prompt(req.question, contexts)

    def generate():
        # Send trace info first
        yield json.dumps({"type": "trace", "data": trace}) + "\n"
        # Stream tokens
        full_answer = []
        for token in p.call_llm_stream(prompt):
            full_answer.append(token)
            yield json.dumps({"type": "token", "data": token}) + "\n"
        # Send final answer + citations
        answer = "".join(full_answer)
        yield json.dumps({
            "type": "answer",
            "data": answer,
            "citations": [
                {
                    "source": c.get("metadata", {}).get("source", ""),
                    "filename": c.get("metadata", {}).get("filename", ""),
                    "page": c.get("metadata", {}).get("page"),
                    "score": c.get("rerank_score", c.get("score", 0)),
                    "text": c["text"][:500],
                }
                for c in contexts
            ],
        }) + "\n"
        # Done
        elapsed_ms = int((time.time() - t0) * 1000)
        yield json.dumps({"type": "done", "elapsed_ms": elapsed_ms}) + "\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/rebuild-bm25")
def rebuild_bm25():
    """Rebuild the BM25 index from Qdrant payloads."""
    p = get_pipeline()
    p.rebuild_bm25()
    return {"status": "ok"}


@app.post("/ingest")
def ingest(req: IngestRequest):
    """Ingest documents synchronously. Best for small batches."""
    import time

    from chunker import chunk_documents
    from parser import parse_directory, parse_file
    from qdrant_store import QdrantStore

    p = Path(req.path)

    # 安全检查：拒绝符号链接 / Security: reject symlinks
    if p.is_symlink():
        raise HTTPException(400, "不允许符号链接 / Symlinks are not allowed")

    # 安全检查：路径必须在配置的 docs_dir 内 / Security: path must be within docs_dir
    docs_dir = Path(os.environ.get("DOCS_DIR", str(settings.docs_dir))).resolve()
    try:
        resolved = p.resolve()
        resolved.relative_to(docs_dir)
    except ValueError:
        raise HTTPException(
            403,
            f"路径超出允许范围 / Path outside allowed directory: {docs_dir}",
        )

    # 文件大小限制（50MB）/ File size limit (50MB)
    if p.is_file() and p.stat().st_size > 50 * 1024 * 1024:
        raise HTTPException(400, "文件过大，最大 50MB / File too large, max 50MB")

    if not p.exists():
        raise HTTPException(404, f"路径不存在: {p}")

    t0 = time.time()
    docs = parse_file(p) if p.is_file() else parse_directory(p)

    if not docs:
        return {"status": "empty", "elapsed_ms": int((time.time() - t0) * 1000)}

    chunks = chunk_documents(docs, req.chunk_size, req.overlap)
    store = QdrantStore()
    if req.recreate:
        store.ensure_collection(recreate=True)
    store.upsert_chunks(chunks)

    pipeline = get_pipeline()
    pipeline.rebuild_bm25()

    elapsed = int((time.time() - t0) * 1000)
    return {
        "status": "ok",
        "documents": len(docs),
        "chunks": len(chunks),
        "total_in_collection": store.count(),
        "elapsed_ms": elapsed,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
