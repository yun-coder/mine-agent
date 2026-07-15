"""优雅关闭处理器 / Graceful shutdown handler.

在收到 SIGTERM/SIGINT 信号时：
1. 关闭嵌入客户端连接池
2. 刷新 pending 操作
3. 通知依赖服务
"""

from __future__ import annotations

import signal
import sys
from loguru import logger


def setup_graceful_shutdown():
    """注册优雅关闭信号处理器 / Register graceful shutdown signal handlers."""

    def shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"[Shutdown] 收到信号 {sig_name}，开始优雅关闭... / Received signal {sig_name}, shutting down gracefully...")

        # 关闭嵌入客户端 / Close embedder client
        try:
            from src.rag.qdrant_client import close_embedder_client
            close_embedder_client()
            logger.info("[Shutdown] 嵌入客户端已关闭 / Embedder client closed")
        except Exception as exc:
            logger.warning(f"[Shutdown] 关闭嵌入客户端失败: {exc}")

        # 关闭 LLM 辅助客户端 / Close LLM helper client
        try:
            from src.agent.tools import close_llm_client
            close_llm_client()
            logger.info("[Shutdown] LLM 客户端已关闭 / LLM client closed")
        except Exception as exc:
            logger.warning(f"[Shutdown] 关闭 LLM 客户端失败: {exc}")

        try:
            from src.agent.graph import close_checkpointer
            close_checkpointer()
            logger.info("[Shutdown] Checkpointer closed")
        except Exception as exc:
            logger.warning(f"[Shutdown] Checkpointer close failed: {exc}")

        logger.info("[Shutdown] 关闭完成 / Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
