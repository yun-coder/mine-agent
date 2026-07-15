#!/usr/bin/env python3
"""Concurrent HTTP benchmark for the local LangGraph Agent service."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def load_env_value(name: str, env_file: Path) -> str:
    value = os.environ.get(name, "").strip()
    if value or not env_file.exists():
        return value
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        if key.strip() == name:
            return raw_value.strip().strip("\"'")
    return ""


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 3)


def execute_request(
    base_url: str,
    mode: str,
    api_key: str,
    model: str,
    timeout: float,
) -> dict:
    if mode == "health":
        request = Request(f"{base_url}/health/live", method="GET")
    else:
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": "Reply with exactly: OK",
                    }
                ],
                "stream": mode == "stream",
            }
        ).encode("utf-8")
        request = Request(
            f"{base_url}/v1/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Session-ID": f"benchmark-{uuid.uuid4().hex}",
            },
        )

    started = time.perf_counter()
    status = 0
    response_body = b""
    error = ""
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            response_body = response.read()
    except HTTPError as exc:
        status = exc.code
        response_body = exc.read()
        error = f"HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        error = type(exc).__name__

    elapsed = time.perf_counter() - started
    body_text = response_body.decode("utf-8", errors="replace")
    valid = status == 200
    failure_markers = (
        "An error occurred during tool execution",
        "工具调用过程中发生错误",
    )
    if any(marker in body_text for marker in failure_markers):
        valid = False
        error = "agent fallback response"
    if valid and mode == "stream":
        valid = "data: [DONE]" in body_text
    elif valid and mode == "chat":
        try:
            valid = bool(json.loads(body_text).get("choices"))
        except json.JSONDecodeError:
            valid = False

    return {
        "ok": valid,
        "status": status,
        "elapsed_seconds": elapsed,
        "bytes": len(response_body),
        "error": error if not valid else "",
    }


def run_stage(
    base_url: str,
    mode: str,
    concurrency: int,
    requests: int,
    api_key: str,
    model: str,
    timeout: float,
) -> dict:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(execute_request, base_url, mode, api_key, model, timeout)
            for _ in range(requests)
        ]
        results = [future.result() for future in futures]
    wall_time = time.perf_counter() - started
    successful = [item["elapsed_seconds"] for item in results if item["ok"]]
    statuses = Counter(str(item["status"]) for item in results)
    errors = Counter(item["error"] for item in results if item["error"])

    return {
        "mode": mode,
        "concurrency": concurrency,
        "requests": requests,
        "successes": len(successful),
        "success_rate": round(len(successful) / requests, 4),
        "status_codes": dict(statuses),
        "errors": dict(errors),
        "wall_seconds": round(wall_time, 3),
        "throughput_rps": round(requests / wall_time, 3),
        "latency_seconds": {
            "p50": percentile(successful, 0.50),
            "p95": percentile(successful, 0.95),
            "max": round(max(successful), 3) if successful else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="langgraph-agent")
    parser.add_argument("--concurrency", default="1,2,4")
    parser.add_argument("--modes", default="health,chat,stream")
    parser.add_argument("--health-requests", type=int, default=20)
    parser.add_argument("--chat-requests", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pause-between-chat-modes", type=float, default=61.0)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    levels = [int(value) for value in args.concurrency.split(",") if value.strip()]
    modes = [value.strip() for value in args.modes.split(",") if value.strip()]
    api_key = load_env_value("API_KEY", args.env_file)
    if any(mode != "health" for mode in modes) and not api_key:
        raise SystemExit("API_KEY is required via environment or --env-file")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "model": args.model,
        "stages": [],
    }
    previous_chat_mode = False
    for mode in modes:
        is_chat_mode = mode in {"chat", "stream"}
        if previous_chat_mode and is_chat_mode and args.pause_between_chat_modes > 0:
            time.sleep(args.pause_between_chat_modes)
        request_count = (
            args.health_requests if mode == "health" else args.chat_requests
        )
        for concurrency in levels:
            stage = run_stage(
                base_url,
                mode,
                concurrency,
                request_count,
                api_key,
                args.model,
                args.timeout,
            )
            report["stages"].append(stage)
            latency = stage["latency_seconds"]
            print(
                f"{mode:6} c={concurrency} success={stage['successes']}/"
                f"{request_count} p50={latency['p50']}s "
                f"p95={latency['p95']}s rps={stage['throughput_rps']}"
            )
        previous_chat_mode = is_chat_mode

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output_dir / f"benchmark-{timestamp}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {output}")
    return 0 if all(stage["success_rate"] == 1.0 for stage in report["stages"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
