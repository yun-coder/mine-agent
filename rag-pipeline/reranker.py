"""BGE-Reranker-v2-M3 重排(本地 sentence-transformers)"""
import os
from typing import List, Tuple
from loguru import logger


class LocalReranker:
    _instance = None

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", cache_dir: str = None):
        from sentence_transformers import CrossEncoder
        if cache_dir:
            os.environ["HF_HOME"] = cache_dir
        logger.info(f"加载 Reranker: {model_name}")
        self.model = CrossEncoder(model_name, max_length=512)

    @classmethod
    def get_instance(cls, cache_dir: str = None) -> "LocalReranker":
        """Singleton: share reranker across requests to avoid reload cost."""
        if cls._instance is None:
            cls._instance = cls(cache_dir=cache_dir)
        return cls._instance

    def rerank(self, query: str, documents: List[str], top_k: int = 5) -> List[Tuple[int, float]]:
        """返回 (原始索引, 重排分数) 列表,按分数降序"""
        if not documents:
            return []
        pairs = [[query, d] for d in documents]
        scores = self.model.predict(pairs, show_progress_bar=False)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [(idx, float(s)) for idx, s in ranked]
