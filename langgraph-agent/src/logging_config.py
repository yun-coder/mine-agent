"""结构化日志配置 / Structured logging configuration.

使用 JSON 格式的 loguru 日志，便于 ELK/Loki 等日志系统采集。
Uses JSON-formatted loguru logs for ELK/Loki log aggregation.
"""

from __future__ import annotations

import json
import os
import sys

from datetime import datetime, timezone
from loguru import logger

# 移除默认处理器 / Remove default handler
logger.remove()


def _serialize_record(record: dict) -> str:
    """将 loguru 记录序列化为 JSON 行 / Serialize loguru record as JSON line."""
    timestamp = datetime.fromtimestamp(record["time"].timestamp(), tz=timezone.utc).isoformat()
    entry = {
        "timestamp": timestamp,
        "level": record["level"].name,
        "message": record["message"].strip(),
        "logger": record["name"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
    }
    # 添加额外字段（如果有）/ Add extra fields if present
    if "extra" in record and record["extra"]:
        entry["extra"] = record["extra"]
    return json.dumps(entry, ensure_ascii=False)


def _get_correlation_id(record: dict) -> str:
    """从记录中获取关联 ID / Get correlation ID from record."""
    return record.get("extra", {}).get("correlation_id", "N/A")


# 控制台处理器（彩色，人类可读）/ Console handler (colored, human-readable)
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    colorize=True,
)

# JSON 文件处理器（路径从环境变量读取 / Log path from env var）
_log_dir = os.environ.get("LOG_DIR", "/app/logs")
logger.add(
    f"{_log_dir}/{{time:YYYY-MM-DD}}_agent.log",
    rotation="10 MB",
    retention="30 days",
    format=_serialize_record,
    level="DEBUG",
    enqueue=True,
)
