FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 系统依赖 / System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖 / Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码（不复制 .venv / logs 等）
COPY src/ ./src/

# 暴露端口 / Expose port
EXPOSE 8000

# 非 root 用户（生产安全）/ Non-root user (production security)
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /app/data/qdrant /app/logs \
    && chown -R appuser:appuser /app/data /app/logs
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

# 启动命令（调用 CLI serve → 内部执行 uvicorn）
CMD ["python", "-m", "src.cli", "serve"]
