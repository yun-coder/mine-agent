import httpx, json, time
r = httpx.post("http://127.0.0.1:8000/query", json={"question": "年假几天?"}, timeout=300)
print(f"HTTP {r.status_code}  耗时: {r.elapsed.total_seconds():.1f}s")
data = r.json()
print(f"\n=== ANSWER ===\n{data['answer']}")
print(f"\n=== 引用 ({len(data['citations'])} 条) ===")
for i, c in enumerate(data['citations'], 1):
    print(f"  [{i}] {c['filename']} score={c['score']:.3f}")
    print(f"      {c['text'][:120]}...")
print(f"\n=== 性能 ===")
print(f"  服务端 elapsed_ms: {data['elapsed_ms']}")
