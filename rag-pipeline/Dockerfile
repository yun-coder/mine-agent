# 多阶段构建 / Multi-stage build
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# 运行时阶段 / Runtime stage
FROM python:3.11-slim

WORKDIR /app

# 安装必要工具 / Install utilities
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# 从 builder 复制依赖 / Copy dependencies from builder
COPY --from=builder /install /usr/local

# 复制应用代码 / Copy application code
COPY *.py ./
COPY static/ ./static/

# 非 root 用户 / Non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

# 暴露端口 / Expose port
EXPOSE 8000

# 健康检查 / Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 启动命令 / Start command
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
