"""Pytest configuration for rag-pipeline tests."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DOCS_DIR", "D:/projects/rag-pipeline/output")
os.environ.setdefault("HF_HOME", "D:/projects/data/hf_cache")
