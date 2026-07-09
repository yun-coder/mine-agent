"""Question-answering CLI: python query.py "your question" [--acl=group1,group2]."""
import argparse
import json

from rag_pipeline import RAGPipeline
from config import settings


def main():
    parser = argparse.ArgumentParser(description="RAG 问答")
    parser.add_argument("question", nargs="+", help="问题")
    parser.add_argument("--acl", help="ACL 组过滤，逗号分隔")
    parser.add_argument("--dept", help="部门过滤，逗号分隔")
    parser.add_argument("--show-trace", action="store_true", help="显示检索 trace")
    parser.add_argument("--stream", action="store_true", help="流式输出")
    args = parser.parse_args()

    question = " ".join(args.question)
    filter_acl = args.acl.split(",") if args.acl else None
    filter_dept = args.dept.split(",") if args.dept else None

    pipeline = RAGPipeline(use_reranker=settings.use_reranker)

    if args.stream:
        # Stream mode: retrieve then yield tokens
        contexts, trace = pipeline.retrieve(
            question, filter_acl=filter_acl, filter_dept=filter_dept
        )
        if not contexts:
            print("知识库中未找到相关信息。")
            return
        prompt = pipeline.build_prompt(question, contexts)
        print("答案: ", end="", flush=True)
        for token in pipeline.call_llm_stream(prompt):
            print(token, end="", flush=True)
        print()
        print("\n" + "=" * 80)
        print(f"引用 ({len(contexts)} 条):")
        for i, c in enumerate(contexts, 1):
            meta = c.get("metadata", {})
            score = c.get("rerank_score", c.get("score", 0))
            print(f"  [{i}] {meta.get('filename', '?')} 第 {meta.get('page', '?')} 页 (score={score:.3f})")
            print(f"      {c['text'][:150]}...")
    else:
        result = pipeline.query(question, filter_acl=filter_acl, filter_dept=filter_dept)
        print("\n" + "=" * 80)
        print(f"答案:\n{result.answer}")
        print("\n" + "=" * 80)
        print(f"引用 ({len(result.citations)} 条):")
        for i, c in enumerate(result.citations, 1):
            meta = c.get("metadata", {})
            score = c.get("rerank_score", c.get("score", 0))
            print(f"  [{i}] {meta.get('filename', '?')} 第 {meta.get('page', '?')} 页 (score={score:.3f})")
            print(f"      {c['text'][:150]}...")

    if args.show_trace:
        print("\n" + "=" * 80)
        print("检索 trace:")
        print(json.dumps(result.trace if not args.stream else trace, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
