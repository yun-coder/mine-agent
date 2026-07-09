"""日志统一入口"""
import sys
from loguru import logger
from config import settings

# 移除默认 handler
logger.remove()

# 控制台:INFO
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>")

# 文件:DEBUG,按天切分
log_file = settings.log_dir / "rag_{time:YYYY-MM-DD}.log"
logger.add(str(log_file), level="DEBUG", rotation="00:00", retention="30 days", encoding="utf-8", enqueue=True)
