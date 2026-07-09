"""Embedding 客户端: 优先使用 Ollama /api/embed 批量接口, 降级到逐条 /api/embeddings"""
import httpx
from typing import List
from loguru import logger

from config import settings


class OllamaEmbedder:
    def __init__(self, base_url: str = None, model: str = None):
        self.base_url = base_url or settings.ollama_base_url
        self.model = model or settings.embed_model
        self.dim = 1024  # bge-m3 输出维度
        self._use_batch = True  # 尝试批量接口
        logger.info(f"Embedding 客户端: {self.base_url} model={self.model}")

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Batch embedding via /api/embed, fallback to per-request."""
        if not texts:
            return []

        if self._use_batch:
            try:
                return self._embed_batch(texts)
            except Exception as e:
                logger.warning(f"Batch embed failed ({e}), falling back to per-request")
                self._use_batch = False

        return self._embed_per_request(texts)

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Use Ollama /api/embed endpoint for batch embedding."""
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
            )
            r.raise_for_status()
            return r.json()["embeddings"]

    def _embed_per_request(self, texts: List[str]) -> List[List[float]]:
        """Fallback: one request per text."""
        vectors = []
        with httpx.Client(timeout=60.0) as client:
            for i, t in enumerate(texts):
                if i % 10 == 0 and i > 0:
                    logger.debug(f"  embedded {i}/{len(texts)}")
                r = client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": t[:8000]},
                )
                r.raise_for_status()
                vectors.append(r.json()["embedding"])
        return vectors

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]
