---
name: enterprise-readiness-audit-2026-07-02
description: Full enterprise-readiness audit results for langgraph-agent + rag-pipeline, with scores and remediation plan
metadata:
  type: project
  created: 2026-07-02
  status: in-progress
---

# Enterprise Readiness Audit — 2026-07-02

## Current State

This project consists of three components:
- **langgraph-agent**: LangGraph-based agent platform with RAG, code search, and tool calling (FastAPI + Ollama + Qdrant)
- **rag-pipeline**: Document ingestion and retrieval pipeline (FastAPI + Qdrant + OpenWebUI)
- **langfuse**: Observability platform (PostgreSQL + ClickHouse + Redis + MinIO)

## Scores

| Dimension | Score | Severity |
|---|---|---|
| Security | 2/10 | Critical |
| Data Processing | 4/10 | Moderate |
| Performance | 3/10 | Significant |
| Reliability | 3/10 | Major |
| Scalability | 2/10 | Fundamental |
| Production-Readiness | 2/10 | Critical |

## Security Findings (2/10)

1. **No authentication on any API endpoint** — All endpoints (`/api/v1/agent/ask`, `/query`, `/ingest`, `/chat/completions`) are wide open
2. **Wildcard CORS with credentials** — `allow_origins=["*"]` + `allow_credentials=True` is the most permissive CORS possible
3. **Terminal sanitizer only uses regex** — Inherently fragile, bypass potential with obfuscated commands
4. **Hardcoded paths in sanitizer** — `ALLOWED_DIRS` references Windows paths that won't work in containers
5. **No rate limiting anywhere** — No request throttling, token bucket, or sliding window
6. **No input length limits** — `QuestionRequest` has no `max_length` on the question field
7. **No TLS/HTTPS** — All services bind to `0.0.0.0` with plain HTTP
8. **No encryption at rest** — Qdrant data, BM25 pickle files, document storage are on local disk
9. **No secrets rotation** — All credentials are static
10. **Langfuse docker-compose has default secrets** — `NEXTAUTH_SECRET: my-super-secret-key-change-in-prod-1234567890abcdef`
11. **Error responses leak internals** — Exception text exposed to users in fallback messages
12. **Ingest endpoint is a security risk** — Any user can POST any file path for parsing and indexing

## Data Processing Findings (4/10)

1. **Naive chunking** — Regex-based token estimation (`cn + int(en * 1.3)`), not a real tokenizer
2. **No document deduplication** — Uploading same file twice creates duplicate embeddings
3. **No chunk validation** — No min/max content validation, empty chunks silently dropped
4. **BM25 uses pickle** — No schema versioning, library upgrades break loading
5. **Text truncation is silent** — 8000 char truncation with no warning logged
6. **No PII/sensitive content detection** — Credentials, IDs can enter the knowledge base
7. **Limited format support** — No images (OCR), scanned docs, audio, video
8. **No error recovery during parsing** — No retry mechanism, no partial-parse recovery

## Performance Findings (3/10)

1. **No connection pooling** — Every embedder call creates new `httpx.Client()`
2. **No embedding cache** — Same query re-embedded every time
3. **Streaming double-executes** — `astream_events` then `graph.invoke()` again, doubling compute
4. **All synchronous** — No async alternatives for embedding or LLM calls
5. **Extreme timeouts** — LLM calls use 600s (10 min) timeouts
6. **Reranker cold start** — First query after startup has 30-60s delay with no progress indicator
7. **No batch query optimization** — Each sub-query embeds independently
8. **BM25 is in-memory only** — O(n) rebuild, no incremental updates

## Reliability Findings (3/10)

1. **Zero retry logic** — Transient network glitch = hard failure
2. **No circuit breaker** — Slow/failing Ollama causes request pileup
3. **Session state lost on restart** — `MemorySaver` stores everything in process memory
4. **Incomplete health checks** — Doesn't check embedding model, reranker, or LLM generation
5. **Error responses leak internals** — Stack traces and system paths exposed
6. **Langfuse init fires at import time** — Connection attempted on every import

## Scalability Findings (2/10)

1. **Single-process architecture** — No horizontal scaling, no K8s manifests, no load balancer config
2. **In-process state as globals** — `_REACT_AGENT_CACHE`, `_pipeline` are global singletons
3. **Qdrant single-node** — No replication or clustering configured
4. **ClickHouse single-node** — `CLICKHOUSE_CLUSTER_ENABLED: "false"`
5. **No message queue** — Document ingestion is synchronous blocking
6. **BM25 rebuild is O(n) full scan** — No incremental updates

## Production-Readiness Findings (2/10)

1. **Near-zero test coverage** — Only `tests/test_sanitizer.py` (151 lines), no RAG tests, no API tests
2. **No configuration validation** — No startup validation that services are reachable
3. **Ad-hoc logging** — `loguru` with no structured format, no log rotation, no correlation IDs
4. **Incomplete Dockerfile** — rag-pipeline Dockerfile is just one line
5. **No Prometheus metrics** — No request latency histograms, no error rate dashboards
6. **Loose dependency pinning** — `pyproject.toml` uses `>=`, `requirements.txt` has no lock file
7. **No backup strategy** — Local disk with no snapshots or DR plan
8. **No graceful shutdown** — No cleanup of in-memory state or flushing pending operations

## Remediation Plan

See REMEDIATION.md for the implementation plan.
