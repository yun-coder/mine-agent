FROM python:3.11-slim

WORKDIR /app

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
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

# 启动命令（调用 CLI serve → 内部执行 uvicorn）
CMD ["python", "-m", "src.cli", "serve"]
