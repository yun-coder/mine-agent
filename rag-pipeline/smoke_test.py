"""端到端冒烟测试:验证 pipeline 跑通"""
import sys
import os
import tempfile
from pathlib import Path

from loguru import logger
from parser import parse_file
from chunker import chunk_document
from qdrant_store import QdrantStore
from embedder import OllamaEmbedder


def main():
    # 1. 准备测试文档 — 使用临时目录 / Use a temp directory
    test_doc_path = Path(tempfile.gettempdir()) / "_rag_test_doc.md"
    os.makedirs(os.path.dirname(test_doc_path), exist_ok=True)
    with open(test_doc_path, "w", encoding="utf-8") as f:
        f.write("""# 公司年假规定

## 法定年假
工作满 1 年不满 10 年的员工,年假 5 个工作日。
工作满 10 年不满 20 年的员工,年假 10 个工作日。
工作满 20 年以上的员工,年假 15 个工作日。

## 病假
员工因病无法工作,需提供医院证明。3 天以内部门经理批准,3 天以上 HR 审批。

## 加班
工作日加班按 1.5 倍工资,周末 2 倍,法定节假日 3 倍。
""")
    logger.info(f"测试文档: {test_doc_path}")

    # 2. 解析
    docs = parse_file(test_doc_path)
    logger.info(f"解析: {len(docs)} 文档")

    # 3. 分块
    chunks = []
    for d in docs:
        chunks.extend(chunk_document(d, chunk_size=128, overlap=32))
    logger.info(f"分块: {len(chunks)} chunks")
    for c in chunks:
        logger.info(f"  - [{c.metadata.get('page', '?')}] {c.text[:60]}...")

    # 4. 入库
    store = QdrantStore()
    store.upsert_chunks(chunks)
    logger.info(f"✅ 入库完成,总数: {store.count()}")

    # 5. 检索测试
    query = "年假几天?"
    results = store.search(query, top_k=3)
    logger.info(f"\n查询: {query}")
    for r in results:
        logger.info(f"  score={r['score']:.3f} {r['text'][:100]}...")

    # 6. 清理
    os.remove(test_doc_path)
    logger.info("\n✅ 冒烟测试通过")


if __name__ == "__main__":
    main()
