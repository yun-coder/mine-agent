"""RAGAS 评估:用准备好的 QA 对评估检索/生成质量
数据集格式 (jsonl):
{"question": "...", "ground_truth": "...", "contexts": ["...", "..."] (可选)}
"""
import json
import argparse
from pathlib import Path
from loguru import logger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", help="评估集 jsonl 路径")
    parser.add_argument("--output", default="./eval_result.json")
    args = parser.parse_args()

    # 加载评估集
    samples = []
    with open(args.dataset, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    logger.info(f"加载 {len(samples)} 条评估样本")

    from rag_pipeline import RAGPipeline
    pipeline = RAGPipeline()

    # 运行 RAG
    results = []
    for s in samples:
        result = pipeline.query(s["question"])
        retrieved_contexts = [c["text"] for c in result.citations]
        results.append({
            "question": s["question"],
            "ground_truth": s["ground_truth"],
            "answer": result.answer,
            "contexts": retrieved_contexts,
        })
        logger.info(f"  Q: {s['question'][:50]}... A: {result.answer[:80]}...")

    # RAGAS 评估
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            context_precision, context_recall,
            faithfulness, answer_relevancy,
        )
        # 准备数据
        ds = Dataset.from_list(results)
        score = evaluate(
            ds,
            metrics=[context_precision, context_recall, faithfulness, answer_relevancy],
        )
        logger.info(f"\n📊 评估结果:\n{score}")
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        score.to_pandas().to_json(args.output, orient="records", force_ascii=False, indent=2)
        logger.info(f"已保存到 {args.output}")
    except ImportError:
        logger.warning("ragas 未安装,跳过自动评估。结果已保存到中间文件。")
        intermediate = Path(args.output).with_suffix(".raw.json")
        intermediate.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        logger.info(f"原始结果: {intermediate}")


if __name__ == "__main__":
    main()
