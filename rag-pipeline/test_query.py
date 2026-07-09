"""轻量 query 测试:无 rerank,详细计时"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rag_pipeline import RAGPipeline
import httpx

print("[1/4] 初始化 pipeline (use_reranker=False)...")
t0 = time.time()
p = RAGPipeline(use_reranker=False)
print(f"  初始化耗时: {time.time()-t0:.2f}s")

print("\n[2/4] 检索...")
t0 = time.time()
contexts, trace = p.retrieve("年假几天?")
print(f"  检索耗时: {time.time()-t0:.2f}s")
print(f"  找到 {len(contexts)} chunks:")
for c in contexts:
    print(f"    - {c['metadata'].get('filename','?')} | score={c['score']:.3f}")
    print(f"      {c['text'][:100]}")

print("\n[3/4] 构造 prompt...")
prompt = p.build_prompt("年假几天?", contexts)
print(f"  prompt 长度: {len(prompt)} chars")

print("\n[4/4] 调用 LLM (timeout=600s)...")
t0 = time.time()
try:
    with httpx.Client(timeout=600.0) as client:
        r = client.post(
            "http://127.0.0.1:11434/api/generate",
            json={"model": "qwen3:8b", "prompt": prompt, "stream": False, "options": {"temperature": 0.1, "num_ctx": 4096}}
        )
        r.raise_for_status()
        result = r.json()
        print(f"  LLM 耗时: {time.time()-t0:.2f}s")
        print(f"  eval_count: {result.get('eval_count')} tokens")
        print(f"  eval_duration: {result.get('eval_duration')/1e9:.2f}s")
        print(f"\n=== ANSWER ===\n{result['response']}")
except Exception as e:
    print(f"  LLM 失败: {e}")
