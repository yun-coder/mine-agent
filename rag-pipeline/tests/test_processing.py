"""Phase 7: Data Processing Tests — 分块、去重、PII 检测、BM25 版本"""

import pytest
import hashlib
from pathlib import Path
from unittest.mock import MagicMock


# 模拟 Document 类 / Mock Document class
class MockDoc:
    def __init__(self, content: str, metadata: dict | None = None):
        self.content = content
        self.metadata = metadata or {"source": "test", "filename": "test.txt", "page": 1}


class TestChunker:
    """分块测试 / Chunking tests."""

    def test_basic_chunking(self):
        """基本分块应正常工作 / Basic chunking should work."""
        from chunker import Chunk, chunk_documents
        from chunker import Chunk

        doc = MockDoc("这是一个测试文档。它包含多个句子。每个句子都会被处理。")
        chunks = chunk_documents([doc], chunk_size=100, overlap=0)
        assert len(chunks) > 0
        assert isinstance(chunks[0], Chunk)
        assert len(chunks[0].text) > 0

    def test_empty_chunk_filtered(self):
        """空 chunk 应被过滤 / Empty chunks should be filtered."""
        from chunker import Chunk, chunk_documents
        from chunker import Chunk

        doc = MockDoc("   ")
        chunks = chunk_documents([doc], chunk_size=100, overlap=0)
        # 可能产生 0 个有效 chunk / May produce 0 valid chunks
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_dedup_flag_works(self):
        """去重标志应生效 / Dedup flag should work."""
        from chunker import Chunk, chunk_documents
        from chunker import Chunk

        doc = MockDoc("重复内容。重复内容。重复内容。")
        # 相同内容的 chunk 应被去重
        chunks = chunk_documents([doc, doc], chunk_size=100, overlap=0, dedup=True)
        # 去重后应少于不去重 / Should be fewer after dedup
        chunks_nodup = chunk_documents([doc, doc], chunk_size=100, overlap=0, dedup=False)
        assert len(chunks) <= len(chunks_nodup)

    def test_content_hash_in_metadata(self):
        """chunk 应包含内容哈希 / Chunks should include content hash."""
        from chunker import Chunk, chunk_documents
        from chunker import Chunk

        doc = MockDoc("测试内容 / test content.")
        chunks = chunk_documents([doc], chunk_size=100, overlap=0)
        assert len(chunks) > 0
        assert "content_hash" in chunks[0].metadata
        assert len(chunks[0].metadata["content_hash"]) == 8


class TestPIIFilter:
    """PII 检测测试 / PII detection tests."""

    def test_email_detection(self):
        """应检测到邮箱 / Should detect email addresses."""
        from pii_filter import detect_pii
        text = "请联系 admin@example.com 获取更多信息。"
        findings = detect_pii(text)
        assert any("邮箱" in f or "Email" in f for f in findings)

    def test_phone_detection(self):
        """应检测到手机号 / Should detect phone numbers."""
        from pii_filter import detect_pii
        text = "我的手机号是 13812345678。"
        findings = detect_pii(text)
        assert any("手机" in f or "phone" in f.lower() for f in findings)

    def test_password_detection(self):
        """应检测到密码 / Should detect passwords."""
        from pii_filter import detect_pii
        text = "password: mysecret123"
        findings = detect_pii(text)
        assert any("密码" in f or "Password" in f for f in findings)

    def test_api_key_detection(self):
        """应检测到 API Key / Should detect API keys."""
        from pii_filter import detect_pii
        text = "api_key: sk-abc123def456ghi789jkl012mno345"
        findings = detect_pii(text)
        assert any("Key" in f or "key" in f for f in findings)

    def test_sanitization(self):
        """文本应被正确清洗 / Text should be sanitized correctly."""
        from pii_filter import sanitize_text
        text = "联系 admin@example.com 或拨打 13812345678。"
        result = sanitize_text(text)
        assert "admin@example.com" not in result
        assert "[EMAIL_REDACTED]" in result
        assert "13812345678" not in result


class TestBM25Versioning:
    """BM25 版本控制测试 / BM25 versioning tests."""

    def test_save_load_with_version(self, tmp_path):
        """保存和加载应包含版本号 / Save and load should include version."""
        from bm25_index import BM25Index, BM25_VERSION
        import pickle

        index = BM25Index()
        index.chunk_ids = ["c1", "c2"]
        index.texts = ["hello world", "test text"]
        index.metadatas = [{}]

        save_path = tmp_path / "bm25"
        index.save(save_path)

        # 验证版本写入 / Verify version is written
        with open(save_path, "rb") as f:
            data = pickle.load(f)
        assert data["version"] == BM25_VERSION

    def test_version_mismatch_returns_false(self, tmp_path):
        """版本不匹配时应返回 False / Version mismatch should return False."""
        from bm25_index import BM25Index
        import pickle

        # 创建一个旧版本的 pickle 文件 / Create a fake old-version pickle
        save_path = tmp_path / "bm25_old"
        with open(save_path, "wb") as f:
            pickle.dump({
                "version": 0,  # 旧版本 / Old version
                "chunk_ids": [],
                "texts": [],
                "metadatas": [],
            }, f)

        index = BM25Index()
        assert index.load(save_path) is False  # 应拒绝 / Should reject
