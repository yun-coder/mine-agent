"""Pytest configuration for langgraph-agent tests."""
import sys
import os

# 确保项目根目录在 sys.path 中 / Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("API_KEY", "test-secret-key")
os.environ.setdefault("DOCS_DIR", "D:/projects/langgraph-agent/assets")
os.environ.setdefault("PROJECT_ROOT", "D:/projects")
