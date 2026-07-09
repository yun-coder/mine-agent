"""BM25 倒排索引(内存版,够用;大数据量换 OpenSearch/Meilisearch)

新增 / Added:
    - 版本号头: 防止库版本升级导致 pickle 不兼容 / Version header to prevent pickle incompatibility
"""
import re
import pickle
import json
from pathlib import Path
from typing import List, Tuple
from rank_bm25 import BM25Okapi
from loguru import logger
from chunker import Chunk

from config import settings

# BM25 序列化版本号 / BM25 serialization version
BM25_VERSION = 1


def _tokenize(text: str) -> List[str]:
    """中英文混合分词:中文按字 + 英文按词,降为小写"""
    text = text.lower()
    # 拆出英文词
    en_words = re.findall(r"[a-z0-9]+", text)
    # 拆出中文字符
    cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return en_words + cn_chars


class BM25Index:
    def __init__(self):
        self.bm25: BM25Okapi = None
        self.chunk_ids: List[str] = []
        self.texts: List[str] = []
        self.metadatas: List[dict] = []

    def build(self, chunks: List[Chunk]):
        self.chunk_ids = [c.chunk_id for c in chunks]
        self.texts = [c.text for c in chunks]
        self.metadatas = [c.metadata for c in chunks]
        tokenized_corpus = [_tokenize(t) for t in self.texts]
        self.bm25 = BM25Okapi(tokenized_corpus)
        logger.info(f"BM25 索引构建: {len(chunks)} docs")

    def search(self, query: str, top_k: int = 20) -> List[Tuple[int, float]]:
        """返回 (chunk_index, score) 列表"""
        if self.bm25 is None:
            return []
        tokenized_query = _tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [(idx, float(score)) for idx, score in ranked if score > 0]

    def save(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 保存元数据和版本号 / Save metadata with version header
        with open(path, "wb") as f:
            pickle.dump({
                "version": BM25_VERSION,
                "chunk_ids": self.chunk_ids,
                "texts": self.texts,
                "metadatas": self.metadatas,
            }, f)
        # bm25 单独存 / Store bm25 separately
        with open(path.with_suffix(".bm25.pkl"), "wb") as f:
            pickle.dump(self.bm25, f)

    def load(self, path: Path) -> bool:
        path = Path(path)
        if not path.exists():
            return False
        with open(path, "rb") as f:
            data = pickle.load(f)
        # 检查版本号 / Check version
        stored_version = data.get("version", 0)
        if stored_version != BM25_VERSION:
            logger.warning(
                f"BM25 版本不匹配: 存储={stored_version}, 期望={BM25_VERSION}，重新构建 / "
                f"BM25 version mismatch: stored={stored_version}, expected={BM25_VERSION}, will rebuild"
            )
            return False
        self.chunk_ids = data["chunk_ids"]
        self.texts = data["texts"]
        self.metadatas = data["metadatas"]
        bm25_path = path.with_suffix(".bm25.pkl")
        if bm25_path.exists():
            with open(bm25_path, "rb") as f:
                self.bm25 = pickle.load(f)
        return True


def rrf_fuse(*ranked_lists, k: int = 60) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion:多路排序融合
    输入:每个 ranked_list 是 [(doc_id, score)] 列表
    输出:[(doc_id, fused_score)]"""
    scores = {}
    for ranked in ranked_lists:
        for rank, (doc_id, _) in enumerate(ranked, 1):
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
