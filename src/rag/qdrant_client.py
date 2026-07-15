"""Qdrant 连接器 — 带连接池、嵌入缓存、重试 / Qdrant connector with connection pooling, embedding cache, and retries.

Reuses the embedding approach from rag-pipeline (Ollama /api/embed),
but is self-contained — does not depend on rag-pipeline any modules.

Added Phase 2:
  - BM25 index (rank_bm25) for hybrid vector + keyword retrieval
  - RRF fusion of vector & BM25 results
  - search_hybrid() method as the new default
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
import time
from pathlib import Path
from typing import Any

import httpx
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
from rank_bm25 import BM25Okapi

from src.config import settings
from src.api.retry import retry
from src.api.circuit_breaker import get_ollama_circuit

try:
    from src.metrics import EMBED_CACHE_HITS, EMBED_CACHE_MISSES
    _HAS_METRICS = True
except ImportError:
    _HAS_METRICS = False


# ======================================================================
# httpx 连接池单例 / httpx connection pool singleton
# ======================================================================

_embedder_client: httpx.Client | None = None


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

    def get(self, text: str) -> list[float] | None:
        h = self._hash(text)
        if h in self._cache:
            ts, vec = self._cache[h]
            if time.time() - ts < self._ttl:
                if _HAS_METRICS:
                    EMBED_CACHE_HITS.inc()
                return vec
            else:
                del self._cache[h]  # 过期 / Expired
        if _HAS_METRICS:
            EMBED_CACHE_MISSES.inc()
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
# BM25 工具函数 / BM25 helpers
# ======================================================================

BM25_VERSION = 1


def _tokenize(text: str) -> list[str]:
    """中英文混合分词：中文按字 + 英文按词，降为小写。"""
    text = text.lower()
    en_words = re.findall(r"[a-z0-9]+", text)
    cn_chars = re.findall(r"[一-鿿]", text)
    return en_words + cn_chars


def rrf_fuse(*ranked_lists: list[tuple[str, float]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: 多路排序融合。
    输入：每个 ranked_list 是 [(doc_id, score)] 列表
    输出：[(doc_id, fused_score)] 按融合分数降序。
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (doc_id, _) in enumerate(ranked, 1):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ======================================================================
# Ollama 嵌入器 / Ollama Embedder
# ======================================================================

class OllamaEmbedder:
    """轻量级嵌入器，使用 Ollama /api/embed（批量）或 /api/embeddings（单次）。
    带嵌入缓存以减少重复调用。"""

    EMBED_MODEL: str = settings.embed_model
    DIM: int = settings.embed_dim

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
        cached: dict[int, list[float] | None] = {}
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
        # 熔断器检查 / Circuit breaker check
        cb = get_ollama_circuit()
        if not cb.can_execute():
            logger.warning("[Embedder] Ollama 熔断器已开启，返回空向量 / Circuit open, returning empty vectors")
            return [0.0] * self.DIM

        if self._use_batch:
            try:
                result = self._embed_batch(texts)
                cb.record_success()
                return result
            except Exception as exc:
                logger.warning(f"[嵌入器 / Embedder] 批量失败 ({exc})，回退到逐条请求 / batch failed, falling back to per-request")
                self._use_batch = False

        try:
            result = self._embed_per_request(texts)
            cb.record_success()
            return result
        except Exception as exc:
            cb.record_failure()
            raise

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
    """智能体 RAG 检索的 Qdrant 薄封装 / Thin wrapper around Qdrant for the Agent's RAG retrieval.

    Phase 2 enhancement: supports hybrid search (vector + BM25 + RRF fusion)
    and optional reranking.
    """

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

        # BM25 索引（惰性加载）
        self._bm25: BM25Okapi | None = None
        self._bm25_chunk_ids: list[str] = []
        self._bm25_texts: list[str] = []
        self._bm25_metadatas: list[dict] = []
        self._bm25_path = self._resolve_bm25_path()

        # Reranker（惰性加载）
        self._reranker: Any | None = None

        logger.info(f"[Qdrant] {self.host}:{self.port} collection={self.collection}")

    def _resolve_bm25_path(self) -> Path:
        if settings.rag_bm25_path:
            return Path(settings.rag_bm25_path)
        # 自动选择：优先用项目根下的 data/bm25，然后是 rag-pipeline 的缓存
        candidates = [
            settings.project_root / "data" / "qdrant" / f"{self.collection}.bm25.pkl",
            Path(f"/data/qdrant/{self.collection}.bm25.pkl"),
        ]
        for c in candidates:
            if c.parent.exists():
                return c
        return candidates[0]

    # ------------------------------------------------------------------
    # BM25 索引构建与持久化 / BM25 index lifecycle
    # ------------------------------------------------------------------

    def _ensure_bm25(self) -> None:
        """确保 BM25 索引已加载/构建。"""
        if self._bm25 is not None:
            return
        if self._load_bm25():
            return
        self._build_bm25()

    def _load_bm25(self) -> bool:
        """从 pickle 缓存加载 BM25 索引。"""
        path = self._bm25_path.with_suffix(".meta.pkl") if self._bm25_path.suffix == ".pkl" else self._bm25_path
        bm25_path = self._bm25_path
        if not bm25_path.exists():
            return False
        try:
            with open(bm25_path, "rb") as f:
                data = pickle.load(f)
            if data.get("version") != BM25_VERSION:
                logger.warning("[BM25] 版本不匹配，忽略缓存 / version mismatch, ignoring cache")
                return False
            self._bm25_chunk_ids = data["chunk_ids"]
            self._bm25_texts = data["texts"]
            self._bm25_metadatas = data["metadatas"]
            # BM25Okapi 对象需单独反序列化
            tokenized_corpus = [_tokenize(t) for t in self._bm25_texts]
            self._bm25 = BM25Okapi(tokenized_corpus)
            logger.info(f"[BM25] 从缓存加载 / loaded: {len(self._bm25_chunk_ids)} docs")
            return True
        except Exception as exc:
            logger.warning(f"[BM25] 加载缓存失败 ({exc})，重新构建 / load failed, rebuilding")
            return False

    def _build_bm25(self) -> None:
        """从 Qdrant 集合中 scroll 所有数据构建 BM25 索引。"""
        logger.info("[BM25] 从 Qdrant 构建索引 / building from Qdrant...")
        self.ensure_collection()
        offset = None
        all_chunks: list[dict] = []
        while True:
            results, offset = self.client.scroll(
                collection_name=self.collection,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for r in results:
                payload = r.payload
                all_chunks.append({
                    "chunk_id": payload.get("chunk_id", str(r.id)),
                    "text": payload.get("text", ""),
                    "metadata": {k: v for k, v in payload.items() if k != "text"},
                })
            if offset is None:
                break
        if not all_chunks:
            logger.warning("[BM25] 集合为空，跳过索引构建 / collection empty, skipping")
            return

        self._bm25_chunk_ids = [c["chunk_id"] for c in all_chunks]
        self._bm25_texts = [c["text"] for c in all_chunks]
        self._bm25_metadatas = [c["metadata"] for c in all_chunks]
        tokenized_corpus = [_tokenize(t) for t in self._bm25_texts]
        self._bm25 = BM25Okapi(tokenized_corpus)

        # 持久化到磁盘
        try:
            self._bm25_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._bm25_path, "wb") as f:
                pickle.dump({
                    "version": BM25_VERSION,
                    "chunk_ids": self._bm25_chunk_ids,
                    "texts": self._bm25_texts,
                    "metadatas": self._bm25_metadatas,
                }, f)
            logger.info(f"[BM25] 已持久化 / saved: {len(all_chunks)} docs → {self._bm25_path}")
        except Exception as exc:
            logger.warning(f"[BM25] 持久化失败 / save failed: {exc}")

    def _bm25_search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """BM25 检索，返回 [(chunk_id, score)]。"""
        self._ensure_bm25()
        if self._bm25 is None:
            return []
        tokenized_query = _tokenize(query)
        scores = self._bm25.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [(self._bm25_chunk_ids[idx], float(score)) for idx, score in ranked if score > 0]

    def rebuild_bm25(self) -> None:
        """强制重新构建 BM25 索引（清除缓存）。"""
        self._bm25 = None
        self._bm25_chunk_ids = []
        self._bm25_texts = []
        self._bm25_metadatas = []
        # 删除缓存文件
        if self._bm25_path.exists():
            self._bm25_path.unlink(missing_ok=True)
        self._build_bm25()

    # ------------------------------------------------------------------
    # Reranker 惰性加载 / Reranker lazy loading
    # ------------------------------------------------------------------

    def _ensure_reranker(self) -> None:
        """惰性加载 Reranker。"""
        if self._reranker is not None:
            return
        try:
            from sentence_transformers import CrossEncoder
            logger.info("[Reranker] 加载 / loading BGE-Reranker-v2-m3...")
            self._reranker = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
            logger.info("[Reranker] 就绪 / ready")
        except ImportError:
            logger.warning("[Reranker] sentence-transformers 未安装，跳过 / not installed, skipping")
        except Exception as exc:
            logger.warning(f"[Reranker] 加载失败 / load failed: {exc}")

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
        """语义搜索，支持 ACL 和部门过滤。
        如果 settings.rag_use_hybrid_search 开启，自动使用向量+BM25+RRF 混合检索。
        """
        if settings.rag_use_hybrid_search:
            return self.search_hybrid(
                query=query,
                top_k=top_k or settings.top_k_rag,
                filter_acl=filter_acl,
                filter_dept=filter_dept,
            )
        return self._search_vector_only(query, top_k, filter_acl, filter_dept)

    def _search_vector_only(
        self,
        query: str,
        top_k: int | None = None,
        filter_acl: list[str] | None = None,
        filter_dept: list[str] | None = None,
    ) -> list[dict]:
        """纯向量语义搜索（旧逻辑）。"""
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

    def search_hybrid(
        self,
        query: str,
        top_k: int | None = None,
        top_k_vector: int = 20,
        top_k_bm25: int = 20,
        top_k_rrf: int = 10,
        top_k_rerank: int = 5,
        filter_acl: list[str] | None = None,
        filter_dept: list[str] | None = None,
        use_reranker: bool = False,
    ) -> list[dict]:
        """混合检索：向量 + BM25 + RRF 融合 + 可选重排序。"""
        top_k = top_k or settings.top_k_rag
        top_k_vector = min(top_k_vector, top_k * 2)
        top_k_bm25 = min(top_k_bm25, top_k * 2)
        top_k_rrf = top_k
        top_k_rerank = min(top_k_rerank, top_k)

        # 1) 向量检索
        vec_results = self._search_vector_only(query, top_k=top_k_vector, filter_acl=filter_acl, filter_dept=filter_dept)
        vec_ranked = [(r["id"], r["score"]) for r in vec_results]

        # 2) BM25 检索
        bm25_ranked = self._bm25_search(query, top_k=top_k_bm25)

        # 3) RRF 融合
        if bm25_ranked:
            fused = rrf_fuse(vec_ranked, bm25_ranked, k=settings.rag_rrf_k)[:top_k_rrf]
        else:
            fused = vec_ranked[:top_k_rrf]

        # 构建 id → result 映射（优先用向量结果的 payload，因为含完整 metadata）
        id_to_result: dict[str, dict] = {}
        for r in vec_results:
            id_to_result[r["id"]] = r
        # 补充 BM25 独有的结果（metadata 为空时从 BM25 索引补）
        if self._bm25_chunk_ids:
            for cid, _ in fused:
                if cid not in id_to_result and cid in self._bm25_chunk_ids:
                    idx = self._bm25_chunk_ids.index(cid)
                    id_to_result[cid] = {
                        "id": cid,
                        "score": 0.0,
                        "text": self._bm25_texts[idx],
                        "metadata": self._bm25_metadatas[idx],
                    }

        # 4) 可选重排序
        if use_reranker or settings.rag_use_reranker:
            self._ensure_reranker()
            if self._reranker is not None:
                rerank_input = []
                rerank_ids = []
                for doc_id, _ in fused:
                    r = id_to_result.get(doc_id)
                    if r and r.get("text"):
                        rerank_input.append(r["text"])
                        rerank_ids.append(doc_id)
                if rerank_input:
                    scores = self._reranker.predict(
                        [[query, d] for d in rerank_input],
                        show_progress_bar=False,
                    )
                    ranked = sorted(
                        enumerate(scores), key=lambda x: x[1], reverse=True
                    )[:top_k_rerank]
                    fused = [
                        (rerank_ids[idx], float(s))
                        for idx, s in ranked
                    ]

        # 5) 组装最终结果
        final: list[dict] = []
        for doc_id, score in fused:
            r = id_to_result.get(doc_id)
            if r:
                final.append({**r, "score": score})
            if len(final) >= top_k:
                break
        return final

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
