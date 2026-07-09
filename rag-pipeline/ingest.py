"""文档入库 CLI: python ingest.py [文件|目录]

新增 / Added:
    - 文档去重: 基于 SHA-256 内容哈希 / Document dedup via SHA-256 content hash
    - 空文档跳过 / Skip empty documents
"""
import sys
import argparse
import hashlib
from pathlib import Path
from loguru import logger

from parser import parse_file, parse_directory
from chunker import chunk_documents
from qdrant_store import QdrantStore
from rag_pipeline import RAGPipeline


def _file_hash(path: Path) -> str:
    """计算文件的 SHA-256 哈希 / Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    try:
        for chunk in iter(lambda: path.read_bytes(8192), b""):
            h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def main():
    parser = argparse.ArgumentParser(description="RAG 文档入库")
    parser.add_argument("path", help="文件路径或目录路径")
    parser.add_argument("--recreate", action="store_true", help="重建集合(删除已有)")
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--overlap", type=int, default=64)
    parser.add_argument("--no-bm25-rebuild", action="store_true", help="跳过 BM25 重建(快)")
    parser.add_argument("--enable-pii-filter", action="store_true", help="启用 PII 检测")
    args = parser.parse_args()

    p = Path(args.path)
    if p.is_file():
        docs = parse_file(p)
    elif p.is_dir():
        docs = parse_directory(p)
    else:
        logger.error(f"路径不存在: {p}")
        sys.exit(1)

    if not docs:
        logger.warning("没有可入库的文档")
        return

    # 去重: 按文件路径过滤重复文档 / Dedup: filter duplicates by file path
    seen_paths: set[str] = set()
    unique_docs = []
    for doc in docs:
        src = doc.metadata.get("source", "")
        if src in seen_paths:
            logger.debug(f"  跳过重复文档: {src}")
            continue
        seen_paths.add(src)
        unique_docs.append(doc)

    if len(unique_docs) < len(docs):
        logger.info(f"去重: {len(docs)} → {len(unique_docs)} 文档")
    docs = unique_docs

    chunks = chunk_documents(docs, args.chunk_size, args.overlap)

    # PII 检测（可选）/ PII detection (optional)
    if args.enable_pii_filter:
        from pii_filter import detect_pii
        flagged = 0
        for chunk in chunks:
            findings = detect_pii(chunk.text)
            if findings:
                flagged += 1
                logger.warning(f"  PII detected in chunk {chunk.chunk_id}: {findings}")
        if flagged:
            logger.warning(f"  {flagged} chunks flagged for PII")
    store = QdrantStore()
    store.upsert_chunks(chunks)
    if not args.no_bm25_rebuild:
        pipeline = RAGPipeline(use_reranker=False)
        pipeline.rebuild_bm25()
    logger.info(f"✅ 入库完成: {len(docs)} 文档, {len(chunks)} chunks, 集合总数 {store.count()}")


if __name__ == "__main__":
    main()
