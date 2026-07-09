"""Qdrant 向量库操作:建集合、写 chunk、检索、删除"""
from typing import List, Optional
from uuid import uuid4
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue, MatchAny,
)

from config import settings
from chunker import Chunk
from embedder import OllamaEmbedder


class QdrantStore:
    def __init__(self, host: str = None, port: int = None, collection: str = None):
        self.host = host or settings.qdrant_host
        self.port = port or settings.qdrant_port
        self.collection = collection or settings.qdrant_collection
        self.client = QdrantClient(host=self.host, port=self.port, timeout=60)
        self.embedder = OllamaEmbedder()
        logger.info(f"Qdrant: {self.host}:{self.port} collection={self.collection}")

    def ensure_collection(self, recreate: bool = False):
        """确保集合存在,带 payload 索引"""
        exists = self.client.collection_exists(self.collection)
        if exists and recreate:
            logger.warning(f"删除并重建集合: {self.collection}")
            self.client.delete_collection(self.collection)
            exists = False
        if not exists:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.embedder.dim, distance=Distance.COSINE),
            )
            logger.info(f"  ✓ 创建集合 {self.collection}")
        # payload 字段索引(用于过滤)
        for field in ["source", "filename", "filetype", "acl_groups", "department"]:
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema="keyword",
                )
            except Exception as e:
                logger.debug(f"  索引 {field}: {e}")

    def upsert_chunks(self, chunks: List[Chunk], batch_size: int = 32):
        """写入 chunks"""
        self.ensure_collection()
        total = len(chunks)
        for i in range(0, total, batch_size):
            batch = chunks[i:i + batch_size]
            texts = [c.text for c in batch]
            vectors = self.embedder.embed(texts)
            points = [
                PointStruct(
                    id=str(uuid4()),
                    vector=v,
                    payload={
                        "text": c.text,
                        "chunk_id": c.chunk_id,
                        **c.metadata,
                    }
                )
                for c, v in zip(batch, vectors)
            ]
            self.client.upsert(self.collection, points=points)
            logger.info(f"  ✓ {min(i+batch_size, total)}/{total}")

    def search(
        self,
        query: str,
        top_k: int = 10,
        filter_acl: Optional[List[str]] = None,
        filter_dept: Optional[List[str]] = None,
    ) -> List[dict]:
        """向量检索,支持 ACL/部门过滤"""
        vec = self.embedder.embed_one(query)

        conditions = []
        if filter_acl:
            conditions.append(FieldCondition(key="acl_groups", match=MatchAny(any=filter_acl)))
        if filter_dept:
            conditions.append(FieldCondition(key="department", match=MatchAny(any=filter_dept)))
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
                "score": r.score,
                "text": r.payload.get("text", ""),
                "metadata": {k: v for k, v in r.payload.items() if k != "text"},
            }
            for r in results
        ]

    def count(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(self.collection).count

    def delete_by_source(self, source: str):
        """按原始文件删除(用于更新文档)"""
        self.client.delete(
            self.collection,
            points_selector=Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))]),
        )
