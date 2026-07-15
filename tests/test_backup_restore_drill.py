import json
import math
import sys

import pytest

from scripts import backup_restore_drill as drill


def test_qdrant_logical_drill_round_trip(monkeypatch, tmp_path):
    source_points = [
        {
            "id": "point-1",
            "payload": {"source": "one.txt"},
            "vector": [0.1, 0.2, 0.3],
        },
        {
            "id": "point-2",
            "payload": {"source": "two.txt"},
            "vector": [0.4, 0.5, 0.6],
        },
    ]
    vector_config = {"size": 3, "distance": "Cosine"}
    collection_config = {
        "params": {
            "vectors": vector_config,
            "shard_number": 1,
            "replication_factor": 1,
            "write_consistency_factor": 1,
            "on_disk_payload": True,
        },
        "hnsw_config": None,
        "optimizer_config": None,
        "wal_config": None,
        "quantization_config": None,
        "strict_mode_config": None,
    }
    restored_collections = {}

    def fake_qdrant_json(base_url, path, method="GET", payload=None):
        if path == "/":
            return {"title": "qdrant", "version": "test"}
        parts = path.split("/")
        collection = parts[2]
        if method == "GET" and len(parts) == 3:
            if collection == "enterprise_kb":
                return {
                    "result": {
                        "points_count": len(source_points),
                        "config": collection_config,
                        "payload_schema": {
                            "source": {"data_type": "keyword", "points": 2}
                        },
                    }
                }
            restored = restored_collections[collection]
            return {
                "result": {
                    "points_count": len(restored["points"]),
                    "config": {"params": {"vectors": vector_config}},
                }
            }
        if method == "PUT" and len(parts) == 3:
            restored_collections[collection] = {
                "points": [],
                "indexes": [],
            }
            return {"status": "ok"}
        if method == "POST" and "/points/scroll" in path:
            points = (
                source_points
                if collection == "enterprise_kb"
                else restored_collections[collection]["points"]
            )
            start = int(payload.get("offset", 0))
            page = points[start : start + 1]
            next_offset = start + 1 if start + 1 < len(points) else None
            return {
                "result": {
                    "points": page,
                    "next_page_offset": next_offset,
                }
            }
        if method == "PUT" and "/points?wait=true" in path:
            restored_collections[collection]["points"].extend(payload["points"])
            return {"status": "ok"}
        if method == "PUT" and "/index?wait=true" in path:
            restored_collections[collection]["indexes"].append(
                payload["field_name"]
            )
            return {"status": "ok"}
        if method == "DELETE" and len(parts) == 3:
            restored_collections.pop(collection)
            return {"status": "ok"}
        raise AssertionError(f"Unexpected Qdrant call: {method} {base_url}{path}")

    monkeypatch.setattr(drill, "qdrant_json", fake_qdrant_json)

    result = drill.qdrant_logical_drill(
        "http://source",
        "http://restore",
        "enterprise_kb",
        tmp_path,
        "20260715_000000",
        batch_size=1,
    )

    assert result["ok"] is True
    assert result["points_count"] == 2
    assert result["payload_indexes"] == ["source"]
    assert result["backup_bytes"] > 0
    assert restored_collections == {}


def test_main_writes_postgres_result_when_qdrant_fails(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        drill,
        "postgres_drill",
        lambda *args: {"ok": True, "checkpoint_counts": {"checkpoints": 1}},
    )

    def fail_qdrant(*args):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(drill, "qdrant_logical_drill", fail_qdrant)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backup_restore_drill.py",
            "--output-dir",
            str(tmp_path / "backups"),
        ],
    )

    assert drill.main() == 1
    reports = list(tmp_path.glob("backup-restore-drill-*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["postgres"]["ok"] is True
    assert report["qdrant"]["ok"] is False
    assert report["qdrant"]["error"] == "restore failed"
    assert report["overall_ok"] is False


def test_validate_restored_vectors_rejects_zero_vector(monkeypatch):
    monkeypatch.setattr(
        drill,
        "scroll_points",
        lambda *args: iter([{"id": "point-1", "vector": [0.0, 0.0]}]),
    )

    with pytest.raises(RuntimeError, match="zero vector"):
        drill.validate_restored_vectors("http://qdrant", "collection", 1)


def test_validate_restored_vectors_accepts_named_vectors(monkeypatch):
    monkeypatch.setattr(
        drill,
        "scroll_points",
        lambda *args: iter(
            [{"id": "point-1", "vector": {"dense": [0.5, math.sqrt(0.75)]}}]
        ),
    )

    assert (
        drill.validate_restored_vectors("http://qdrant", "collection", 1)
        == 1
    )
