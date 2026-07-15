#!/usr/bin/env python3
"""Isolated PostgreSQL and Qdrant backup/restore drill."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import Request, urlopen


POSTGRES_PREFIX = "langgraph_restore_drill_"
QDRANT_PREFIX = "enterprise_kb_restore_drill_"
CHECKPOINT_TABLES = (
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
)


def run(command: list[str], capture: bool = False) -> str:
    result = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def docker_psql(container: str, database: str, sql: str) -> str:
    return run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "langfuse",
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            sql,
        ],
        capture=True,
    )


def table_counts(container: str, database: str) -> dict[str, int]:
    available = set(
        filter(
            None,
            docker_psql(
                container,
                database,
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename LIKE 'checkpoint%';",
            ).splitlines(),
        )
    )
    missing = set(CHECKPOINT_TABLES) - available
    if missing:
        raise RuntimeError(f"Missing checkpoint tables in {database}: {sorted(missing)}")
    counts = {}
    for table in CHECKPOINT_TABLES:
        counts[table] = int(
            docker_psql(container, database, f'SELECT count(*) FROM "{table}";')
        )
    return counts


def postgres_drill(container: str, output_dir: Path, suffix: str) -> dict:
    database = f"{POSTGRES_PREFIX}{suffix}"
    if not re.fullmatch(r"langgraph_restore_drill_[a-z0-9_]+", database):
        raise RuntimeError("Unsafe PostgreSQL drill database name")
    container_dump = f"/tmp/{database}.dump"
    host_dump = output_dir / f"postgres-{suffix}.dump"
    source_counts = table_counts(container, "langfuse")
    started = time.perf_counter()
    created = False
    try:
        run(
            [
                "docker",
                "exec",
                container,
                "pg_dump",
                "-U",
                "langfuse",
                "-d",
                "langfuse",
                "-Fc",
                "-f",
                container_dump,
            ]
        )
        run(["docker", "cp", f"{container}:{container_dump}", str(host_dump)])
        run(["docker", "exec", container, "createdb", "-U", "langfuse", database])
        created = True
        run(
            [
                "docker",
                "exec",
                container,
                "pg_restore",
                "-U",
                "langfuse",
                "-d",
                database,
                "--no-owner",
                "--no-privileges",
                container_dump,
            ]
        )
        restored_counts = table_counts(container, database)
        if source_counts != restored_counts:
            raise RuntimeError(
                f"PostgreSQL count mismatch: {source_counts} != {restored_counts}"
            )
        return {
            "ok": True,
            "source_database": "langfuse",
            "temporary_database": database,
            "checkpoint_counts": source_counts,
            "backup_file": str(host_dump),
            "backup_bytes": host_dump.stat().st_size,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        if created and database.startswith(POSTGRES_PREFIX):
            run(
                [
                    "docker",
                    "exec",
                    container,
                    "dropdb",
                    "-U",
                    "langfuse",
                    "--force",
                    database,
                ]
            )
        run(
            ["docker", "exec", container, "rm", "-f", container_dump],
        )


def qdrant_json(
    base_url: str,
    path: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Qdrant {method} {path} returned HTTP {exc.code}: {body}"
        ) from exc


def upload_snapshot(base_url: str, collection: str, snapshot: Path) -> dict:
    boundary = f"----langgraph-drill-{uuid.uuid4().hex}"
    file_bytes = snapshot.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="snapshot"; '
        f'filename="{snapshot.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii") + file_bytes + f"\r\n--{boundary}--\r\n".encode("ascii")
    path = (
        f"/collections/{quote(collection)}/snapshots/upload"
        "?priority=snapshot&wait=true"
    )
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urlopen(request, timeout=300) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Qdrant snapshot upload returned HTTP {exc.code}: {body}"
        ) from exc


def validate_restored_vectors(
    base_url: str,
    collection: str,
    expected_points: int,
    batch_size: int = 64,
) -> int:
    nonzero = 0
    for point in scroll_points(base_url, collection, batch_size):
        vector = point.get("vector")
        if isinstance(vector, dict):
            values = [
                float(value)
                for named_vector in vector.values()
                for value in named_vector
            ]
        elif isinstance(vector, list):
            values = [float(value) for value in vector]
        else:
            raise RuntimeError(
                f"Qdrant restored point {point.get('id')} has no vector"
            )
        if not values or not all(math.isfinite(value) for value in values):
            raise RuntimeError(
                f"Qdrant restored point {point.get('id')} has an invalid vector"
            )
        if math.sqrt(sum(value * value for value in values)) <= 1e-12:
            raise RuntimeError(
                f"Qdrant restored point {point.get('id')} has a zero vector"
            )
        nonzero += 1
    if nonzero != expected_points:
        raise RuntimeError(
            f"Qdrant restored vector count mismatch: expected {expected_points}, "
            f"received {nonzero}"
        )
    return nonzero


def qdrant_snapshot_drill(
    base_url: str,
    collection: str,
    output_dir: Path,
    suffix: str,
) -> dict:
    temporary_collection = f"{QDRANT_PREFIX}{suffix}"
    if not temporary_collection.startswith(QDRANT_PREFIX):
        raise RuntimeError("Unsafe Qdrant drill collection name")
    source = qdrant_json(base_url, f"/collections/{quote(collection)}")["result"]
    started = time.perf_counter()
    snapshot_result = qdrant_json(
        base_url,
        f"/collections/{quote(collection)}/snapshots",
        method="POST",
    )["result"]
    snapshot_name = snapshot_result["name"]
    snapshot_file = output_dir / f"qdrant-{suffix}-{snapshot_name}"
    download_url = (
        f"{base_url.rstrip('/')}/collections/{quote(collection)}"
        f"/snapshots/{quote(snapshot_name)}"
    )
    with urlopen(download_url, timeout=300) as response:
        snapshot_file.write_bytes(response.read())

    created = False
    try:
        upload_result = upload_snapshot(
            base_url, temporary_collection, snapshot_file
        )
        if upload_result.get("status") != "ok":
            raise RuntimeError(f"Qdrant upload failed: {upload_result}")
        created = True
        restored = qdrant_json(
            base_url, f"/collections/{quote(temporary_collection)}"
        )["result"]
        source_points = int(source["points_count"])
        restored_points = int(restored["points_count"])
        source_vectors = source["config"]["params"]["vectors"]
        restored_vectors = restored["config"]["params"]["vectors"]
        if source_points != restored_points or source_vectors != restored_vectors:
            raise RuntimeError("Qdrant restored collection validation failed")
        sample = qdrant_json(
            base_url,
            f"/collections/{quote(temporary_collection)}/points/scroll",
            method="POST",
            payload={"limit": 1, "with_payload": True, "with_vector": False},
        )["result"]["points"]
        restored_nonzero_vectors = validate_restored_vectors(
            base_url,
            temporary_collection,
            restored_points,
        )
        return {
            "ok": True,
            "source_collection": collection,
            "temporary_collection": temporary_collection,
            "points_count": source_points,
            "vector_config": source_vectors,
            "restored_nonzero_vectors": restored_nonzero_vectors,
            "sample_payload_readable": bool(sample and sample[0].get("payload")),
            "snapshot_name": snapshot_name,
            "backup_file": str(snapshot_file),
            "backup_bytes": snapshot_file.stat().st_size,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        if created and temporary_collection.startswith(QDRANT_PREFIX):
            qdrant_json(
                base_url,
                f"/collections/{quote(temporary_collection)}",
                method="DELETE",
            )


def canonical_point(point: dict) -> bytes:
    return json.dumps(
        point,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def scroll_points(
    base_url: str,
    collection: str,
    batch_size: int,
):
    offset = None
    while True:
        payload = {
            "limit": batch_size,
            "with_payload": True,
            "with_vector": True,
        }
        if offset is not None:
            payload["offset"] = offset
        result = qdrant_json(
            base_url,
            f"/collections/{quote(collection)}/points/scroll",
            method="POST",
            payload=payload,
        )["result"]
        yield from result["points"]
        offset = result.get("next_page_offset")
        if offset is None:
            break


def qdrant_logical_backup(
    base_url: str,
    collection: str,
    output_dir: Path,
    suffix: str,
    batch_size: int,
) -> tuple[Path, dict]:
    source = qdrant_json(
        base_url, f"/collections/{quote(collection)}"
    )["result"]
    server = qdrant_json(base_url, "/")
    backup_file = output_dir / (
        f"qdrant-logical-{suffix}-{collection}.jsonl.gz"
    )
    temporary_file = backup_file.with_suffix(f"{backup_file.suffix}.tmp")
    digest = hashlib.sha256()
    point_count = 0
    metadata = {
        "type": "metadata",
        "format": "qdrant-logical-backup-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_collection": collection,
        "source_server": server,
        "collection_config": source["config"],
        "payload_schema": source["payload_schema"],
    }
    try:
        with gzip.open(temporary_file, "wt", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(metadata, ensure_ascii=False) + "\n")
            for point in scroll_points(
                base_url, collection, batch_size=batch_size
            ):
                point_bytes = canonical_point(point)
                digest.update(point_bytes)
                file.write(
                    json.dumps(
                        {"type": "point", "point": point},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                point_count += 1
            summary = {
                "type": "summary",
                "point_count": point_count,
                "points_sha256": digest.hexdigest(),
            }
            file.write(json.dumps(summary, separators=(",", ":")) + "\n")
        temporary_file.replace(backup_file)
    finally:
        temporary_file.unlink(missing_ok=True)

    expected_points = int(source["points_count"])
    if point_count != expected_points:
        raise RuntimeError(
            f"Qdrant export count mismatch: {point_count} != {expected_points}"
        )
    return backup_file, {
        "source": source,
        "point_count": point_count,
        "points_sha256": digest.hexdigest(),
        "source_server": server,
    }


def collection_create_payload(config: dict) -> dict:
    params = config["params"]
    payload = {
        "vectors": params["vectors"],
        "on_disk_payload": params.get("on_disk_payload", True),
    }
    for key in (
        "shard_number",
        "sharding_method",
        "replication_factor",
        "write_consistency_factor",
        "sparse_vectors",
    ):
        if params.get(key) is not None:
            payload[key] = params[key]
    optional_config = {
        "hnsw_config": "hnsw_config",
        "wal_config": "wal_config",
        "optimizer_config": "optimizers_config",
        "quantization_config": "quantization_config",
        "strict_mode_config": "strict_mode_config",
    }
    for source_key, target_key in optional_config.items():
        if config.get(source_key) is not None:
            payload[target_key] = config[source_key]
    return payload


def qdrant_logical_restore(
    base_url: str,
    temporary_collection: str,
    backup_file: Path,
    batch_size: int,
) -> dict:
    metadata = None
    expected_summary = None
    digest = hashlib.sha256()
    point_count = 0
    batch = []
    created = False
    try:
        with gzip.open(backup_file, "rt", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                record = json.loads(line)
                record_type = record.get("type")
                if line_number == 1:
                    if (
                        record_type != "metadata"
                        or record.get("format") != "qdrant-logical-backup-v1"
                    ):
                        raise RuntimeError("Unsupported Qdrant logical backup format")
                    metadata = record
                    qdrant_json(
                        base_url,
                        f"/collections/{quote(temporary_collection)}",
                        method="PUT",
                        payload=collection_create_payload(
                            metadata["collection_config"]
                        ),
                    )
                    created = True
                    continue
                if record_type == "point":
                    point = record["point"]
                    digest.update(canonical_point(point))
                    batch.append(point)
                    point_count += 1
                    if len(batch) >= batch_size:
                        qdrant_json(
                            base_url,
                            f"/collections/{quote(temporary_collection)}"
                            "/points?wait=true",
                            method="PUT",
                            payload={"points": batch},
                        )
                        batch = []
                elif record_type == "summary":
                    expected_summary = record
                else:
                    raise RuntimeError(
                        f"Unexpected Qdrant backup record at line {line_number}"
                    )
        if batch:
            qdrant_json(
                base_url,
                f"/collections/{quote(temporary_collection)}/points?wait=true",
                method="PUT",
                payload={"points": batch},
            )
        if metadata is None or expected_summary is None:
            raise RuntimeError("Incomplete Qdrant logical backup")
        if (
            point_count != int(expected_summary["point_count"])
            or digest.hexdigest() != expected_summary["points_sha256"]
        ):
            raise RuntimeError("Qdrant logical backup checksum validation failed")

        restored_indexes = []
        for field_name, schema in metadata["payload_schema"].items():
            qdrant_json(
                base_url,
                f"/collections/{quote(temporary_collection)}/index?wait=true",
                method="PUT",
                payload={
                    "field_name": field_name,
                    "field_schema": schema["data_type"],
                },
            )
            restored_indexes.append(field_name)

        restored = qdrant_json(
            base_url, f"/collections/{quote(temporary_collection)}"
        )["result"]
        restored_digest = hashlib.sha256()
        restored_count = 0
        sample_payload_readable = False
        for point in scroll_points(
            base_url, temporary_collection, batch_size=batch_size
        ):
            restored_digest.update(canonical_point(point))
            restored_count += 1
            sample_payload_readable |= bool(point.get("payload"))
        if (
            restored_count != point_count
            or restored_digest.hexdigest() != digest.hexdigest()
        ):
            raise RuntimeError("Qdrant restored point validation failed")
        expected_vectors = metadata["collection_config"]["params"]["vectors"]
        restored_vectors = restored["config"]["params"]["vectors"]
        if restored_vectors != expected_vectors:
            raise RuntimeError("Qdrant restored vector config validation failed")
        return {
            "temporary_collection": temporary_collection,
            "points_count": restored_count,
            "points_sha256": restored_digest.hexdigest(),
            "vector_config": restored_vectors,
            "payload_indexes": restored_indexes,
            "sample_payload_readable": sample_payload_readable,
        }
    finally:
        if created and temporary_collection.startswith(QDRANT_PREFIX):
            qdrant_json(
                base_url,
                f"/collections/{quote(temporary_collection)}",
                method="DELETE",
            )


def qdrant_logical_drill(
    source_url: str,
    restore_url: str,
    collection: str,
    output_dir: Path,
    suffix: str,
    batch_size: int,
) -> dict:
    temporary_collection = f"{QDRANT_PREFIX}{suffix}"
    if not temporary_collection.startswith(QDRANT_PREFIX):
        raise RuntimeError("Unsafe Qdrant drill collection name")
    started = time.perf_counter()
    backup_file, exported = qdrant_logical_backup(
        source_url,
        collection,
        output_dir,
        suffix,
        batch_size,
    )
    restored = qdrant_logical_restore(
        restore_url,
        temporary_collection,
        backup_file,
        batch_size,
    )
    return {
        "ok": True,
        "strategy": "logical",
        "source_url": source_url,
        "restore_url": restore_url,
        "source_collection": collection,
        **restored,
        "source_server": exported["source_server"],
        "backup_file": str(backup_file),
        "backup_bytes": backup_file.stat().st_size,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def failed_result(exc: Exception, started: float) -> dict:
    return {
        "ok": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def write_report(path: Path, report: dict) -> None:
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-container", default="kb-postgres")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument(
        "--qdrant-restore-url",
        help="Restore target; defaults to --qdrant-url",
    )
    parser.add_argument("--qdrant-collection", default="enterprise_kb")
    parser.add_argument(
        "--qdrant-strategy",
        choices=("logical", "snapshot"),
        default="logical",
    )
    parser.add_argument("--qdrant-batch-size", type=int, default=64)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/backups"))
    parser.add_argument("--skip-postgres", action="store_true")
    parser.add_argument("--skip-qdrant", action="store_true")
    args = parser.parse_args()

    if args.qdrant_batch_size < 1:
        parser.error("--qdrant-batch-size must be at least 1")
    if args.skip_postgres and args.skip_qdrant:
        parser.error("Cannot skip both PostgreSQL and Qdrant")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S").lower()
    output = args.output_dir.parent / f"backup-restore-drill-{suffix}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "postgres": {"ok": None, "skipped": True},
        "qdrant": {"ok": None, "skipped": True},
    }
    write_report(output, report)

    if not args.skip_postgres:
        started = time.perf_counter()
        try:
            report["postgres"] = postgres_drill(
                args.postgres_container, args.output_dir, suffix
            )
        except Exception as exc:
            report["postgres"] = failed_result(exc, started)
        write_report(output, report)

    if not args.skip_qdrant:
        started = time.perf_counter()
        try:
            if args.qdrant_strategy == "snapshot":
                report["qdrant"] = qdrant_snapshot_drill(
                    args.qdrant_url,
                    args.qdrant_collection,
                    args.output_dir,
                    suffix,
                )
                report["qdrant"]["strategy"] = "snapshot"
            else:
                report["qdrant"] = qdrant_logical_drill(
                    args.qdrant_url,
                    args.qdrant_restore_url or args.qdrant_url,
                    args.qdrant_collection,
                    args.output_dir,
                    suffix,
                    args.qdrant_batch_size,
                )
        except Exception as exc:
            report["qdrant"] = failed_result(exc, started)
            report["qdrant"]["strategy"] = args.qdrant_strategy
        write_report(output, report)

    executed = [
        result
        for result in (report["postgres"], report["qdrant"])
        if not result.get("skipped")
    ]
    report["overall_ok"] = bool(executed) and all(
        result.get("ok") is True for result in executed
    )
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_report(output, report)
    if report["overall_ok"]:
        print("Backup and restore validation passed; temporary targets were removed.")
    else:
        failed = [
            name
            for name in ("postgres", "qdrant")
            if report[name].get("ok") is False
        ]
        print(f"Backup and restore validation failed: {', '.join(failed)}")
    print(f"Report: {output}")
    return 0 if report["overall_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
