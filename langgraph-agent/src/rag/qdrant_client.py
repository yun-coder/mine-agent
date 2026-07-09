"""Qdrant 连接器 — 带连接池、嵌入缓存、重试 / Qdrant connector with connection pooling, embedding cache, and retries.

Reuses the embedding approach from rag-pipeline (Ollama /api/embed),
but is self-contained — does not depend on rag-pipeline any modules.
"""

from __future__ import annotations

import hashlib
import httpx
import time
from typing import Optional
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from src.config import settings
from src.api.retry import retry


# ======================================================================
# httpx 连接池单例 / httpx connection pool singleton
# ======================================================================

_embedder_client: Optional[httpx.Client] = None


def _get_embedder_client() -> httpx.Client:
    """获取共享的 httpx Client（连接池）/ Get shared httpx Client (connection pool)."""
    global _embedder_client
    if _embedder_client is None:
        _embedder_client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=120.0, pool=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _embedder_client


def close_embedder_client():
    """关闭嵌入客户端（优雅关闭时使用）/ Close embedder client (used on graceful shutdown)."""
    global _embedder_client
    if _embedder_client is not None:
        _embedder_client.close()
        _embedder_client = None


# ======================================================================
# 嵌入缓存 / Embedding cache
# ======================================================================

class _EmbedCache:
    """简单的内存 LRU 嵌入缓存 / Simple in-memory LRU embedding cache."""

    def __init__(self, max_size: int = 10000, ttl_seconds: int = 3600):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, list[float]]] = {}  # hash -> (timestamp, vector)

    def get(self, text: str) -> Optional[list[float]]:
        h = self._hash(text)
        if h in self._cache:
            ts, vec = self._cache[h]
            if time.time() - ts < self._ttl:
                return vec
            else:
                del self._cache[h]  # 过期 / Expired
        return None

    def put(self, text: str, vector: list[float]) -> None:
        if len(self._cache) >= self._max_size:
            # 淘汰最早的条目 / Evict oldest entry
            oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest_key]
        self._cache[self._hash(text)] = (time.time(), vector)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# 全局嵌入缓存 / Global embedding cache
_embed_cache = _EmbedCache()


# ======================================================================
# Ollama 嵌入器 / Ollama Embedder
# ======================================================================

class OllamaEmbedder:
    """轻量级嵌入器，使用 Ollama /api/embed（批量）或 /api/embeddings（单次）。
    带嵌入缓存以减少重复调用。"""

    EMBED_MODEL: str = "bge-m3"
    DIM: int = 1024

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = base_url or settings.ollama_base_url
        self.model = model or self.EMBED_MODEL
        self._use_batch = True
        self._client = _get_embedder_client()
        logger.info(f"[嵌入器 / Embedder] {self.base_url} model={self.model}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # 先尝试从缓存获取 / Try cache first
        cached: dict[int, Optional[list[float]]] = {}
        uncached_indices: list[int] = []
        for i, t in enumerate(texts):
            result = _embed_cache.get(t)
            if result is not None:
                cached[i] = result
            else:
                cached[i] = None
                uncached_indices.append(i)

        if uncached_indices:
            # 只对未命中的文本调用 Ollama / Only call Ollama for cache misses
            uncached_texts = [texts[i] for i in uncached_indices]
            vectors = self._call_ollama(uncached_texts)
            for idx, vec in zip(uncached_indices, vectors):
                cached[idx] = vec
                _embed_cache.put(texts[idx], vec)

        return [cached[i] for i in range(len(texts))]

    def _call_ollama(self, texts: list[str]) -> list[list[float]]:
        """实际调用 Ollama API / Actually call Ollama API."""
        if self._use_batch:
            try:
                return self._embed_batch(texts)
            except Exception as exc:
                logger.warning(f"[嵌入器 / Embedder] 批量失败 ({exc})，回退到逐条请求 / batch failed, falling back to per-request")
                self._use_batch = False
        return self._embed_per_request(texts)

    @retry(max_retries=2, base_delay=1.0, max_delay=5.0, retryable_exceptions=(httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout))
    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        r = self._client.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
        )
        r.raise_for_status()
        return r.json()["embeddings"]

    @retry(max_retries=2, base_delay=1.0, max_delay=5.0, retryable_exceptions=(httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadTimeout))
    def _embed_per_request(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i, t in enumerate(texts):
            if i % 10 == 0 and i > 0:
                logger.debug(f"  已嵌入 {i}/{len(texts)} / embedded {i}/{len(texts)}")
            # 截断过长文本并记录警告 / Truncate long text with warning
            truncated = t[:8000]
            if len(t) > 8000:
                logger.warning(f"[嵌入器 / Embedder] 文本截断至 8000 字符 / Text truncated to 8000 chars (original: {len(t)})")
            r = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": truncated},
            )
            r.raise_for_status()
            vectors.append(r.json()["embedding"])
        return vectors

    def embed_one(self, text: str) -> list[float]:
        """嵌入单条文本（带缓存）。/ Embed a single text (with cache)."""
        cached = _embed_cache.get(text)
        if cached is not None:
            return cached
        vec = self.embed([text])[0]
        _embed_cache.put(text, vec)
        return vec


# ======================================================================
# Qdrant 连接器 / Qdrant Connector
# ======================================================================

class QdrantConnector:
    """智能体 RAG 检索的 Qdrant 薄封装 / Thin wrapper around Qdrant for the Agent's RAG retrieval."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        collection: str | None = None,
    ) -> None:
        self.host = host or settings.qdrant_host
        self.port = port or settings.qdrant_port
        self.collection = collection or settings.qdrant_collection
        self.client = QdrantClient(host=self.host, port=self.port, timeout=60)
        self.embedder = OllamaEmbedder(base_url=settings.ollama_base_url)
        logger.info(f"[Qdrant] {self.host}:{self.port} collection={self.collection}")

    # ------------------------------------------------------------------
    # 集合管理 / Collection management
    # ------------------------------------------------------------------

    def ensure_collection(self, recreate: bool = False) -> None:
        """确保集合存在，可选重建。/ Ensure collection exists, optionally recreate."""
        exists = self.client.collection_exists(self.collection)
        if exists and recreate:
            logger.warning(f"[Qdrant] 删除并重建集合 / Deleting and rebuilding collection: {self.collection}")
            self.client.delete_collection(self.collection)
            exists = False
        if not exists:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=self.embedder.DIM,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(f"  ✓ 创建集合 / Created collection {self.collection}")
        # 负载索引（用于过滤）/ Payload indexes for filtering
        for field in ("source", "filename", "filetype", "acl_groups", "department"):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema="keyword",
                )
            except Exception as exc:
                logger.debug(f"  索引 {field}: {exc}")

    # ------------------------------------------------------------------
    # 批量写入 / Upsert
    # ------------------------------------------------------------------

    def upsert(
        self,
        points: list[PointStruct],
        batch_size: int = 64,
    ) -> None:
        """批量写入向量数据。/ Upsert vector points in batches."""
        self.ensure_collection()
        total = len(points)
        for i in range(0, total, batch_size):
            batch = points[i : i + batch_size]
            texts = [p.payload["text"] for p in batch]
            vectors = self.embedder.embed(texts)
            enriched: list[PointStruct] = []
            for p, v in zip(batch, vectors):
                p.vector = v
                enriched.append(p)
            self.client.upsert(self.collection, points=enriched)
            logger.info(f"  ✓ {min(i + batch_size, total)}/{total}")

    # ------------------------------------------------------------------
    # 搜索 / Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int | None = None,
        filter_acl: list[str] | None = None,
        filter_dept: list[str] | None = None,
    ) -> list[dict]:
        """语义搜索，支持 ACL 和部门过滤。/ Semantic search with optional ACL and department filters."""
        top_k = top_k or settings.top_k_rag
        vec = self.embedder.embed_one(query)

        conditions = []
        if filter_acl:
            conditions.append(
                FieldCondition(key="acl_groups", match=MatchAny(any=filter_acl))
            )
        if filter_dept:
            conditions.append(
                FieldCondition(key="department", match=MatchAny(any=filter_dept))
            )
        qfilter = Filter(must=conditions) if conditions else None

        results = self.client.search(
            collection_name=self.collection,
            query_vector=vec,
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        )
        return [
            {
                "id": str(r.id),
                "score": float(r.score),
                "text": r.payload.get("text", ""),
                "metadata": {k: v for k, v in r.payload.items() if k != "text"},
            }
            for r in results
        ]

    # ------------------------------------------------------------------
    # 元数据辅助方法 / Metadata helpers
    # ------------------------------------------------------------------

    def count(self) -> int:
        """获取集合中的向量总数。/ Count total vectors in collection."""
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(self.collection).count

    def delete_by_source(self, source: str) -> None:
        """按来源删除向量。/ Delete vectors by source."""
        self.client.delete(
            self.collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="source",
                        match=MatchValue(value=source),
                    )
                ]
            ),
        )
