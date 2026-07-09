"""语义分块:按句子切分,合并到目标 token 数,保留 overlap.

使用 tiktoken 进行准确的 token 估算，替代之前的正则启发式方法。
Uses tiktoken for accurate token estimation instead of regex heuristics.
"""

import re
import hashlib
from typing import List
from dataclasses import dataclass, field
from loguru import logger

try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _ENCODER = None


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)
    chunk_id: str = ""  # 唯一 ID,方便 Qdrant 检索/删除


def _split_sentences(text: str) -> List[str]:
    """中英文混合分句 / Split Chinese-English mixed text into sentences."""
    pattern = r'(?<=[。！？.!?\n])\s*'
    sentences = re.split(pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def _estimate_tokens(text: str) -> int:
    """使用 tiktoken 准确估算 token 数 / Accurate token estimation with tiktoken."""
    if _ENCODER is not None:
        return len(_ENCODER.encode(text, disallowed_special=()))
    # Fallback: 正则启发式估算 / Regex heuristic fallback
    cn = len(re.findall(r"[一-鿿]", text))
    en = len(re.findall(r"[a-zA-Z]+", text))
    return cn + int(en * 1.3)


def chunk_document(doc: "Document", chunk_size: int = 512, overlap: int = 64) -> List["Chunk"]:
    """语义分块:按句子聚合,达到 chunk_size 触发切分,保留 overlap.

    新增 / Added:
        - 文档去重: 基于内容哈希 / Deduplication via content hash
        - 空 chunk 过滤 / Empty chunk filtering with logging
        - tiktoken token 估算 / tiktoken-based token estimation
    """
    sentences = _split_sentences(doc.content)
    chunks = []
    buffer = []
    buffer_tokens = 0

    for sent in sentences:
        sent_tokens = _estimate_tokens(sent)
        # 单句超长,强制切 / Force-cut extremely long sentences
        if sent_tokens > chunk_size * 1.5:
            if buffer:
                chunks.append(" ".join(buffer))
                buffer = []
                buffer_tokens = 0
            # 按字符硬切 / Hard cut by character
            for i in range(0, len(sent), chunk_size * 2):
                chunks.append(sent[i : i + chunk_size * 2])
            continue

        if buffer_tokens + sent_tokens > chunk_size and buffer:
            chunks.append(" ".join(buffer))
            # overlap: 保留最后几句 / Keep last few sentences for overlap
            overlap_buf = []
            overlap_tok = 0
            for s in reversed(buffer):
                t = _estimate_tokens(s)
                if overlap_tok + t > overlap:
                    break
                overlap_buf.insert(0, s)
                overlap_tok += t
            buffer = overlap_buf
            buffer_tokens = overlap_tok

        buffer.append(sent)
        buffer_tokens += sent_tokens

    if buffer:
        chunks.append(" ".join(buffer))

    # 构造 Chunk 对象，过滤空 chunk / Construct Chunk objects, filter empty chunks
    result = []
    source = doc.metadata.get("source", "unknown")
    filename = doc.metadata.get("filename", "unknown")
    page = doc.metadata.get("page", 0)

    for i, text in enumerate(chunks):
        text = text.strip()
        if not text:
            logger.debug(f"  跳过空 chunk: {source} p{page} c{i}")
            continue

        # 使用内容哈希作为 chunk_id，支持去重 / Use content hash for dedup-friendly chunk_id
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        cid = f"{filename}::p{page}::c{i}::{content_hash}"
        meta = dict(doc.metadata)
        meta["chunk_index"] = i
        meta["total_chunks"] = len(chunks)
        meta["content_hash"] = content_hash
        result.append(Chunk(text=text, metadata=meta, chunk_id=cid))

    logger.info(f"  分块完成: {len(chunks)} raw → {len(result)} valid chunks (skipped {len(chunks) - len(result)} empty)")
    return result


def chunk_documents(
    docs: List["Document"],
    chunk_size: int = 512,
    overlap: int = 64,
    dedup: bool = True,
) -> List["Chunk"]:
    """分块所有文档，可选去重 / Chunk all documents with optional deduplication.

    Args:
        docs: List of Document objects
        chunk_size: Target chunk size in tokens
        overlap: Overlap between chunks in tokens
        dedup: Whether to deduplicate by content hash
    """
    all_chunks: List[Chunk] = []
    seen_hashes: set[str] = set()

    for doc in docs:
        doc_chunks = chunk_document(doc, chunk_size, overlap)
        for chunk in doc_chunks:
            if dedup and chunk.metadata.get("content_hash") in seen_hashes:
                logger.debug(f"  跳过重复 chunk: {chunk.chunk_id}")
                continue
            seen_hashes.add(chunk.metadata.get("content_hash", ""))
            all_chunks.append(chunk)

    logger.info(f"分块完成: {len(docs)} 文档 → {len(all_chunks)} chunks (去重后 / after dedup)")
    return all_chunks
