import math

import pytest
from fastapi.testclient import TestClient

from src.agent.toolkit.sanitizer import terminal_sanitizer
from src.agent.tools import _exec_code_read
from src.rag.qdrant_client import _validate_embedding_vectors


def test_secret_files_are_not_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    secret = tmp_path / ".env"
    secret.write_text("API_KEY=do-not-return", encoding="utf-8")

    result = _exec_code_read(str(secret))

    assert "not allowed" in result.lower()
    assert "do-not-return" not in result


def test_terminal_blocks_secret_files_and_inline_code():
    assert terminal_sanitizer("Get-Content .env")["safe"] is False
    assert terminal_sanitizer("type ..\\.env")["safe"] is False
    assert terminal_sanitizer("python -c \"print(1)\"")["safe"] is False


def test_terminal_restricts_docker_and_git_subcommands():
    assert terminal_sanitizer("docker ps")["safe"] is True
    assert terminal_sanitizer("docker exec agent sh")["safe"] is False
    assert terminal_sanitizer("git status")["safe"] is True
    assert terminal_sanitizer("git reset --hard")["safe"] is False


def test_root_liveness_endpoint():
    from src.main import create_app

    client = TestClient(create_app())
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_embedding_validation_rejects_zero_and_non_finite_vectors():
    with pytest.raises(RuntimeError, match="zero vector"):
        _validate_embedding_vectors([[0.0, 0.0]], 1, 2)

    with pytest.raises(RuntimeError, match="non-finite"):
        _validate_embedding_vectors([[math.nan, 1.0]], 1, 2)
