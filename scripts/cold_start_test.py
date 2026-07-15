#!/usr/bin/env python3
"""Measure container readiness plus cold and warm model request latency."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from benchmark_agent import execute_request, load_env_value


def wait_for_liveness(url: str, timeout: float) -> tuple[float, int]:
    started = time.perf_counter()
    attempts = 0
    while time.perf_counter() - started < timeout:
        attempts += 1
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return time.perf_counter() - started, attempts
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Liveness did not become ready within {timeout}s")


def unload_ollama_model(base_url: str, model: str, timeout: float) -> float:
    body = json.dumps({"model": model, "keep_alive": 0}).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urlopen(request, timeout=timeout) as response:
        response.read()
        if response.status != 200:
            raise RuntimeError(f"Ollama unload returned HTTP {response.status}")
    return time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", default="kb-langgraph-agent")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=300.0)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    api_key = load_env_value("API_KEY", args.env_file)
    if not api_key:
        raise SystemExit("API_KEY is required via environment or --env-file")

    restart_started = time.perf_counter()
    subprocess.run(
        ["docker", "restart", args.container],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    docker_restart_seconds = time.perf_counter() - restart_started
    live_seconds, attempts = wait_for_liveness(
        f"{args.base_url.rstrip('/')}/health/live",
        args.startup_timeout,
    )
    unload_seconds = unload_ollama_model(
        args.ollama_url, args.model, args.request_timeout
    )

    cold = execute_request(
        args.base_url.rstrip("/"),
        "chat",
        api_key,
        args.model,
        args.request_timeout,
    )
    warm = execute_request(
        args.base_url.rstrip("/"),
        "chat",
        api_key,
        args.model,
        args.request_timeout,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "container": args.container,
        "model": args.model,
        "docker_restart_seconds": round(docker_restart_seconds, 3),
        "liveness_ready_seconds": round(live_seconds, 3),
        "liveness_attempts": attempts,
        "model_unload_seconds": round(unload_seconds, 3),
        "cold_request": cold,
        "warm_request": warm,
        "cold_to_warm_ratio": (
            round(cold["elapsed_seconds"] / warm["elapsed_seconds"], 3)
            if warm["elapsed_seconds"]
            else None
        ),
        "run_id": uuid.uuid4().hex,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output_dir / f"cold-start-{timestamp}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"ready={report['liveness_ready_seconds']}s "
        f"cold={cold['elapsed_seconds']:.3f}s "
        f"warm={warm['elapsed_seconds']:.3f}s"
    )
    print(f"Report: {output}")
    return 0 if cold["ok"] and warm["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
