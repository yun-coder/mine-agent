"""Complete RAG pipeline: hybrid retrieval, optional reranking, generation, citations."""
import json
from dataclasses import dataclass
from typing import List, Optional

import httpx
from loguru import logger

from bm25_index import BM25Index, rrf_fuse
from config import settings
from qdrant_store import QdrantStore
from query_rewriter import rewrite_query
from reranker import LocalReranker


@dataclass
class RAGResult:
    answer: str
    citations: List[dict]
    trace: dict


class RAGPipeline:
    def __init__(self, use_reranker: bool = True):
        self.qdrant = QdrantStore()
        self.bm25 = BM25Index()
        self.bm25_path = settings.qdrant_data_dir / "bm25.pkl"
        self.use_reranker = use_reranker
        self.use_query_rewrite = settings.use_query_rewrite
        self._reranker_load_attempted = False
        if self.bm25_path.exists():
            self.bm25.load(self.bm25_path)
            logger.info(f"Loaded BM25 cache: {self.bm25_path}")

    def _ensure_reranker(self):
        """Lazy-load reranker once; use singleton to avoid reload cost."""
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

    def rebuild_bm25(self):
        """Rebuild BM25 from all Qdrant payloads."""
        logger.info("Rebuilding BM25 index...")
        self.qdrant.ensure_collection()
        offset = None
        all_chunks = []
        while True:
            results, offset = self.qdrant.client.scroll(
                collection_name=self.qdrant.collection,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for r in results:
                from chunker import Chunk

                payload = r.payload
                all_chunks.append(
                    Chunk(
                        text=payload.get("text", ""),
                        metadata={k: v for k, v in payload.items() if k != "text"},
                        chunk_id=payload.get("chunk_id", str(r.id)),
                    )
                )
            if offset is None:
                break
        if all_chunks:
            self.bm25.build(all_chunks)
            self.bm25.save(self.bm25_path)
            logger.info(f"BM25 persisted: {len(all_chunks)} chunks")

    def retrieve(
        self,
        query: str,
        top_k_vector: int = None,
        top_k_bm25: int = None,
        top_k_rrf: int = None,
        top_k_rerank: int = None,
        filter_acl: Optional[List[str]] = None,
        filter_dept: Optional[List[str]] = None,
    ) -> tuple[List[dict], dict]:
        """Hybrid retrieval with query rewriting -> RRF fusion -> optional rerank."""
        top_k_vector = top_k_vector or settings.top_k_vector
        top_k_bm25 = top_k_bm25 or settings.top_k_bm25
        top_k_rrf = top_k_rrf or settings.top_k_rrf
        top_k_rerank = top_k_rerank or settings.top_k_rerank

        trace = {"query": query, "stages": {}}

        # Query rewriting: expand into sub-queries for better recall (optional)
        if self.use_query_rewrite:
            sub_queries = rewrite_query(query)
            trace["stages"]["rewrite"] = {"queries": sub_queries}
            logger.debug(f"Query rewritten: {query} → {sub_queries}")
        else:
            sub_queries = [query]
            trace["stages"]["rewrite"] = {"queries": [query], "disabled": True}

        # Collect results from all sub-queries
        all_vector_results = []
        for sq in sub_queries:
            results = self.qdrant.search(
                sq,
                top_k=top_k_vector,
                filter_acl=filter_acl,
                filter_dept=filter_dept,
            )
            all_vector_results.extend(results)

        # Deduplicate by chunk_id
        seen_ids = set()
        unique_results = []
        for r in all_vector_results:
            cid = r.get("metadata", {}).get("chunk_id") or r.get("id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                unique_results.append(r)

        # Limit to top_k_vector
        unique_results = unique_results[:top_k_vector]

        vector_results = unique_results
        vector_ranked = [(r["id"], r["score"]) for r in vector_results]
        trace["stages"]["vector"] = {
            "count": len(vector_results),
            "top_score": vector_results[0]["score"] if vector_results else 0,
            "sub_queries": len(sub_queries),
        }

        bm25_ranked_raw = self.bm25.search(query, top_k=top_k_bm25)
        text_to_qid = {r["text"][:100]: r["id"] for r in vector_results}
        bm25_ranked = []
        for idx, score in bm25_ranked_raw:
            text_preview = self.bm25.texts[idx][:100]
            qid = text_to_qid.get(text_preview)
            if qid:
                bm25_ranked.append((qid, score))
        trace["stages"]["bm25"] = {"count": len(bm25_ranked)}

        fused = rrf_fuse(vector_ranked, bm25_ranked, k=settings.rrf_k)[:top_k_rrf]
        trace["stages"]["rrf"] = {"count": len(fused)}

        id_to_result = {r["id"]: r for r in vector_results}
        rerank_input = []
        rerank_ids = []
        for doc_id, _ in fused:
            if doc_id in id_to_result:
                rerank_input.append(id_to_result[doc_id]["text"])
                rerank_ids.append(doc_id)

        self._ensure_reranker()
        if self.use_reranker and rerank_input:
            ranked = self.reranker.rerank(query, rerank_input, top_k=top_k_rerank)
            final_ids = [rerank_ids[idx] for idx, _ in ranked]
            final_scores = [s for _, s in ranked]
        else:
            final_ids = rerank_ids[:top_k_rerank]
            final_scores = [1.0] * len(final_ids)

        final_results = []
        for qid, score in zip(final_ids, final_scores):
            r = id_to_result[qid]
            final_results.append({**r, "rerank_score": score})
        trace["stages"]["final"] = {"count": len(final_results)}
        return final_results, trace

    def build_prompt(self, query: str, contexts: List[dict]) -> str:
        """Build a citation-aware prompt for the local LLM."""
        context_text = ""
        for i, c in enumerate(contexts, 1):
            meta = c.get("metadata", {})
            source = meta.get("source", "unknown")
            page = meta.get("page", "?")
            filename = meta.get("filename", "unknown")
            text = c["text"][:300]
            context_text += f"\n[引用 {i}] 来源: {filename} (第 {page} 页)\n路径: {source}\n{text}\n"

        prompt = f"""你是一个专业的企业知识库助手。请基于以下参考资料回答用户问题。
严格要求：
1. 只使用参考资料中的信息回答，不要编造。
2. 每个关键事实后必须标注引用编号，例如 [1][2]。
3. 如果参考资料不足以回答，明确说明“参考资料中未找到相关信息”。
4. 引用编号必须与参考资料对应，不要张冠李戴。

参考资料：
{context_text}

用户问题：{query}

回答："""
        return prompt

    def call_llm(self, prompt: str) -> str:
        """Call Ollama generate API (non-streaming)."""
        with httpx.Client(timeout=600.0) as client:
            r = client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_ctx": 8192},
                },
            )
            r.raise_for_status()
            return r.json()["response"]

    def call_llm_stream(self, prompt: str):
        """Generator: yields tokens one by one from Ollama streaming response."""
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
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if "response" in data:
                            yield data["response"]
                    except json.JSONDecodeError:
                        continue

    def query(
        self,
        question: str,
        filter_acl: Optional[List[str]] = None,
        filter_dept: Optional[List[str]] = None,
    ) -> RAGResult:
        """Run retrieve -> prompt -> generation."""
        contexts, trace = self.retrieve(
            question,
            filter_acl=filter_acl,
            filter_dept=filter_dept,
        )
        if not contexts:
            return RAGResult(
                answer="知识库中未找到相关信息。请确认文档已经入库，或换一种问法。",
                citations=[],
                trace=trace,
            )
        prompt = self.build_prompt(question, contexts)
        answer = self.call_llm(prompt)
        return RAGResult(answer=answer, citations=contexts, trace=trace)
