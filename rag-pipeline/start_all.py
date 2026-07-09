"""Start Qdrant, Open WebUI, and the FastAPI RAG service."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
COMPOSE = PROJECT / "docker-compose.yml"
VENV_PYTHON = PROJECT / ".venv" / "Scripts" / "python.exe"
API_LOG = Path(os.environ.get("LOG_DIR", PROJECT / "logs")) / "api.log"


def _is_hermes_python(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    return "/hermes/" in normalized or "hermes-agent" in normalized


def _system_python_candidates() -> list[Path]:
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Python" / "Python311" / "python.exe")
    candidates.append(Path("C:/Python311/python.exe"))
    return candidates


def _reexec_outside_hermes() -> None:
    """Avoid running project services with the Codex/Hermes helper venv."""
    if not _is_hermes_python(sys.executable):
        return

    args = [str(Path(__file__).resolve()), *sys.argv[1:]]

    if VENV_PYTHON.exists():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *args])

    for candidate in _system_python_candidates():
        if candidate.exists() and not _is_hermes_python(str(candidate)):
            os.execv(str(candidate), [str(candidate), *args])

    py_launcher = shutil.which("py")
    if py_launcher:
        os.execv(py_launcher, [py_launcher, "-3.11", *args])

    print(
        "Refusing to start with Hermes Python and no standalone Python 3.11 was found.\n"
        "Run setup_env.ps1 after installing Python 3.11, then start again.",
        file=sys.stderr,
    )
    sys.exit(1)


_reexec_outside_hermes()

from loguru import logger  # noqa: E402


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    logger.info("$ {}", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result


def is_port_listening(port: int) -> bool:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"if (Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def main() -> None:
    logger.info("Using Python: {}", sys.executable)

    logger.info("1) Starting Qdrant + Open WebUI")
    run(["docker", "compose", "-f", str(COMPOSE), "up", "-d"], cwd=PROJECT, check=False)
    time.sleep(3)

    if is_port_listening(8000):
        logger.warning("FastAPI is already listening on http://127.0.0.1:8000")
    else:
        logger.info("2) Starting FastAPI")
        API_LOG.parent.mkdir(parents=True, exist_ok=True)
        with API_LOG.open("a", encoding="utf-8") as log_file:
            api_proc = subprocess.Popen(
                [sys.executable, "api.py"],
                cwd=PROJECT,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        logger.info("FastAPI PID={} http://127.0.0.1:8000", api_proc.pid)

    time.sleep(5)

    logger.info("3) Health checks")
    import httpx

    try:
        response = httpx.get("http://127.0.0.1:8000/health", timeout=10)
        response.raise_for_status()
        logger.info("/health -> {} {}", response.status_code, response.json())
    except httpx.ConnectError:
        logger.error("FastAPI is not reachable at 127.0.0.1:8000. Check {}", API_LOG)
    except httpx.HTTPStatusError as exc:
        logger.error("FastAPI returned HTTP {}", exc.response.status_code)
        logger.error("Response: {}", exc.response.text[:500])
    except Exception as exc:
        logger.error("FastAPI health check failed: {}: {}", type(exc).__name__, exc)

    try:
        response = httpx.get("http://127.0.0.1:6333/", timeout=5)
        logger.info("Qdrant -> {} {}", response.status_code, response.json().get("title"))
    except Exception as exc:
        logger.error("Qdrant is not reachable: {}", exc)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Services started")
    logger.info("Qdrant:     http://127.0.0.1:6333/dashboard")
    logger.info("Open WebUI: http://127.0.0.1:3000")
    logger.info("RAG API:    http://127.0.0.1:8000/docs")
    logger.info("Ingest:     python ingest.py <docs_dir>")
    logger.info('Query:      python query.py "your question"')


if __name__ == "__main__":
    main()
