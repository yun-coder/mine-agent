"""RAG-Pipeline 集成测试 / Rag-pipeline integration tests."""

import pytest
from pathlib import Path


class TestRAGPipeline:
    """RAG Pipeline 集成测试 / RAG Pipeline integration tests."""

    def test_pipeline_creation(self):
        """Pipeline 应能创建 / Pipeline should be creatable."""
        from rag_pipeline import RAGPipeline
        pipeline = RAGPipeline(use_reranker=False)
        assert pipeline is not None

    def test_qdrant_connection(self):
        """Qdrant 连接应正常工作 / Qdrant connection should work."""
        from qdrant_store import QdrantStore
        store = QdrantStore()
        # 集合可能不存在，但连接应成功
        # Collection may not exist, but connection should succeed
        assert store.count() >= 0

    def test_ollama_connectivity(self):
        """Ollama 连接应正常 / Ollama connection should work."""
        import httpx
        from config import settings
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{settings.ollama_base_url}/api/tags")
            assert r.status_code == 200
            models = r.json().get("models", [])
            assert len(models) > 0

    def test_parser_pdf(self, tmp_path):
        """PDF 解析应工作 / PDF parsing should work."""
        from parser import parse_file
        # 创建一个空的 PDF（最小合法 PDF）/ Create a minimal valid PDF
        pdf_content = b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\nxref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(pdf_content)
        docs = parse_file(pdf_file)
        # 最小 PDF 可能没有文本内容，但不应崩溃
        # Minimal PDF may have no text, but should not crash
        assert docs is not None

    def test_parser_txt(self, tmp_path):
        """TXT 解析应工作 / TXT parsing should work."""
        from parser import parse_file
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("这是一个测试文档。用于验证解析功能。")
        docs = parse_file(txt_file)
        assert len(docs) > 0
        assert docs[0].content.strip() != ""

    def test_parser_md(self, tmp_path):
        """Markdown 解析应工作 / Markdown parsing should work."""
        from parser import parse_file
        md_file = tmp_path / "test.md"
        md_file.write_text("# 标题\n\n这是测试内容。")
        docs = parse_file(md_file)
        assert len(docs) > 0

    def test_parser_html(self, tmp_path):
        """HTML 解析应工作 / HTML parsing should work."""
        from parser import parse_file
        html_file = tmp_path / "test.html"
        html_file.write_text("<html><body><p>测试段落。</p></body></html>")
        docs = parse_file(html_file)
        assert len(docs) > 0

    def test_ingest_path_validation(self):
        """Ingest 路径验证应工作 / Ingest path validation should work."""
        from fastapi.testclient import TestClient
        from api import app
        client = TestClient(app)

        # 不存在的文件
        response = client.post("/ingest", json={"path": "/nonexistent/path"})
        assert response.status_code == 422  # Pydantic validation

        # 超出文档目录的路径
        response = client.post("/ingest", json={"path": "/etc/passwd"})
        assert response.status_code in (400, 404, 422)
