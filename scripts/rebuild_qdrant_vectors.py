"""Rebuild a Qdrant collection from payload text using Ollama embeddings."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def json_request(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} returned HTTP {exc.code}: {body}"
        ) from exc


def scroll_points(
    base_url: str,
    collection: str,
    batch_size: int,
    with_vectors: bool = False,
):
    offset: Any = None
    while True:
        payload: dict[str, Any] = {
            "limit": batch_size,
            "with_payload": True,
            "with_vector": with_vectors,
        }
        if offset is not None:
            payload["offset"] = offset
        result = json_request(
            base_url,
            f"/collections/{quote(collection)}/points/scroll",
            method="POST",
            payload=payload,
        )["result"]
        yield from result["points"]
        offset = result.get("next_page_offset")
        if offset is None:
            break


def validate_embeddings(
    vectors: Any,
    expected_count: int,
    expected_dim: int,
) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        received = len(vectors) if isinstance(vectors, list) else "invalid"
        raise RuntimeError(
            f"Embedding count mismatch: expected {expected_count}, "
            f"received {received}"
        )
    validated: list[list[float]] = []
    for index, vector in enumerate(vectors):
        if not isinstance(vector, list) or len(vector) != expected_dim:
            received = len(vector) if isinstance(vector, list) else "invalid"
            raise RuntimeError(
                f"Embedding {index} dimension mismatch: expected {expected_dim}, "
                f"received {received}"
            )
        numeric = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in numeric):
            raise RuntimeError(f"Embedding {index} contains non-finite values")
        norm = math.sqrt(sum(value * value for value in numeric))
        if norm <= 1e-12:
            raise RuntimeError(f"Embedding {index} is a zero vector")
        validated.append(numeric)
    return validated


def embed_texts(
    ollama_url: str,
    model: str,
    texts: list[str],
    expected_dim: int,
) -> list[list[float]]:
    result = json_request(
        ollama_url,
        "/api/embed",
        method="POST",
        payload={"model": model, "input": texts, "truncate": True},
    )
    return validate_embeddings(
        result.get("embeddings"),
        expected_count=len(texts),
        expected_dim=expected_dim,
    )


def create_target_collection(
    source_info: dict[str, Any],
    target_url: str,
    collection: str,
    expected_dim: int,
    recreate: bool,
) -> list[str]:
    collection_path = f"/collections/{quote(collection)}"
    try:
        json_request(target_url, collection_path)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
    else:
        if not recreate:
            raise RuntimeError(
                f"Target collection {collection!r} already exists; "
                "use --recreate-target to replace it"
            )
        json_request(target_url, collection_path, method="DELETE")

    source_vectors = source_info["config"]["params"]["vectors"]
    if source_vectors.get("size") != expected_dim:
        raise RuntimeError(
            f"Source vector dimension is {source_vectors.get('size')}, "
            f"expected {expected_dim}"
        )
    json_request(
        target_url,
        collection_path,
        method="PUT",
        payload={
            "vectors": {
                "size": expected_dim,
                "distance": source_vectors.get("distance", "Cosine"),
            },
            "on_disk_payload": bool(
                source_info["config"]["params"].get("on_disk_payload", True)
            ),
        },
    )

    indexes: list[str] = []
    for field_name, schema in sorted(source_info.get("payload_schema", {}).items()):
        field_schema = schema.get("data_type") if isinstance(schema, dict) else schema
        json_request(
            target_url,
            f"{collection_path}/index?wait=true",
            method="PUT",
            payload={"field_name": field_name, "field_schema": field_schema},
        )
        indexes.append(field_name)
    return indexes


def vector_stats(
    base_url: str,
    collection: str,
    batch_size: int,
    expected_dim: int,
) -> dict[str, int]:
    total = 0
    nonzero = 0
    for point in scroll_points(
        base_url,
        collection,
        batch_size,
        with_vectors=True,
    ):
        total += 1
        validate_embeddings([point.get("vector")], 1, expected_dim)
        nonzero += 1
    return {"total": total, "nonzero": nonzero, "zero": total - nonzero}


def upsert_embedded_batch(
    target_url: str,
    collection: str,
    ollama_url: str,
    model: str,
    expected_dim: int,
    points: list[dict[str, Any]],
) -> int:
    texts = [point["payload"]["text"] for point in points]
    vectors = embed_texts(
        ollama_url,
        model,
        texts,
        expected_dim,
    )
    payload = {
        "points": [
            {
                "id": point["id"],
                "payload": point["payload"],
                "vector": vector,
            }
            for point, vector in zip(points, vectors)
        ]
    }
    json_request(
        target_url,
        f"/collections/{quote(collection)}/points?wait=true",
        method="PUT",
        payload=payload,
    )
    return len(points)


def rebuild(
    source_url: str,
    target_url: str,
    collection: str,
    ollama_url: str,
    model: str,
    expected_dim: int,
    batch_size: int,
    recreate_target: bool,
    query_text: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    source_info = json_request(
        source_url,
        f"/collections/{quote(collection)}",
    )["result"]
    source_count = int(source_info["points_count"])
    indexes = create_target_collection(
        source_info,
        target_url,
        collection,
        expected_dim,
        recreate_target,
    )

    processed = 0
    batch: list[dict[str, Any]] = []
    for point in scroll_points(
        source_url,
        collection,
        batch_size,
        with_vectors=False,
    ):
        text = point.get("payload", {}).get("text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(f"Point {point.get('id')} has no usable payload text")
        batch.append(point)
        if len(batch) < batch_size:
            continue
        processed += upsert_embedded_batch(
            target_url,
            collection,
            ollama_url,
            model,
            expected_dim,
            batch,
        )
        print(f"Rebuilt {processed}/{source_count} points", flush=True)
        batch = []
    if batch:
        processed += upsert_embedded_batch(
            target_url,
            collection,
            ollama_url,
            model,
            expected_dim,
            batch,
        )
        print(f"Rebuilt {processed}/{source_count} points", flush=True)

    if processed != source_count:
        raise RuntimeError(
            f"Source count changed during rebuild: expected {source_count}, "
            f"processed {processed}"
        )

    target_info = json_request(
        target_url,
        f"/collections/{quote(collection)}",
    )["result"]
    target_count = int(target_info["points_count"])
    stats = vector_stats(
        target_url,
        collection,
        batch_size,
        expected_dim,
    )
    if target_count != source_count or stats["nonzero"] != source_count:
        raise RuntimeError(
            f"Target validation failed: source={source_count}, "
            f"target={target_count}, nonzero={stats['nonzero']}"
        )

    query_vector = embed_texts(
        ollama_url,
        model,
        [query_text],
        expected_dim,
    )[0]
    query_result = json_request(
        target_url,
        f"/collections/{quote(collection)}/points/query",
        method="POST",
        payload={
            "query": query_vector,
            "limit": 5,
            "with_payload": True,
            "with_vector": False,
        },
    )["result"]["points"]
    if not query_result:
        raise RuntimeError("Vector query returned no results")

    return {
        "ok": True,
        "source_url": source_url,
        "target_url": target_url,
        "collection": collection,
        "model": model,
        "vector_dimension": expected_dim,
        "source_points": source_count,
        "target_points": target_count,
        "vector_stats": stats,
        "payload_indexes": indexes,
        "query_text": query_text,
        "query_results": [
            {
                "id": point["id"],
                "score": point.get("score"),
                "source": point.get("payload", {}).get("source"),
                "filename": point.get("payload", {}).get("filename"),
            }
            for point in query_result
        ],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default="http://127.0.0.1:6333")
    parser.add_argument("--target-url", default="http://127.0.0.1:16333")
    parser.add_argument("--collection", default="enterprise_kb")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="bge-m3:latest")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--query-text",
        default="生产环境知识库备份恢复和向量检索",
    )
    parser.add_argument("--recreate-target", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    if args.dimension < 1:
        parser.error("--dimension must be at least 1")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = args.output_dir / f"qdrant-rebuild-{suffix}.json"
    try:
        report = rebuild(
            source_url=args.source_url,
            target_url=args.target_url,
            collection=args.collection,
            ollama_url=args.ollama_url,
            model=args.model,
            expected_dim=args.dimension,
            batch_size=args.batch_size,
            recreate_target=args.recreate_target,
            query_text=args.query_text,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "source_url": args.source_url,
            "target_url": args.target_url,
            "collection": args.collection,
            "model": args.model,
            "error": str(exc),
        }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Report: {report_path.resolve()}")
    if not report["ok"]:
        print(f"Rebuild failed: {report['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
