# ==============================================================================
# R20 Quantum Trader - Multi-stage Production Dockerfile
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build Vue 3 Production Frontend
# ------------------------------------------------------------------------------
FROM node:20-slim AS frontend-builder

WORKDIR /build

ENV NODE_ENV=development

# 先安装依赖利用 Docker 缓存层
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --prefer-offline || npm install

# 复制前端源码并构建
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------------------
# Stage 2: Python 3.11 Runtime Container
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

WORKDIR /app

# 安装必要的运行时工具与时区数据
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 设置环境变量：Python 输出直冲控制台、时区默认北京时间、PYTHONPATH包含项目根目录
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TZ=Asia/Shanghai

# 安装 Python 后端核心依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 从 Stage 1 复制已编译好的静态资源（FastAPI 会自动在 frontend/dist 挂载）
COPY --from=frontend-builder /build/dist /app/frontend/dist

# 复制应用代码
COPY r20_backend/ /app/r20_backend/
COPY r20_gateway/ /app/r20_gateway/
COPY scripts/ /app/scripts/
COPY deploy/ /app/deploy/
COPY docs/ /app/docs/
COPY env.example /app/env.example

# 预先创建持久化数据目录
RUN mkdir -p /app/data /app/logs /app/backups

# 入口脚本赋予可执行权限
RUN chmod +x /app/deploy/docker-entrypoint.sh

# 暴露 FastAPI 控制面端口
EXPOSE 8080

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["backend"]
