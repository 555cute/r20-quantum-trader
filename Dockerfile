# ==========================================
# Stage 1: Build Vue 3 Frontend
# ==========================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# ==========================================
# Stage 2: Python Runtime & Backend Engine
# ==========================================
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    R20_DOCKER=1 \
    PORT=8080

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt httpx

# Copy Backend, Gateway, Scripts, Plugins and Assets
COPY r20_backend/ ./r20_backend/
COPY r20_gateway/ ./r20_gateway/
COPY scripts/ ./scripts/
COPY plugins/ ./plugins/
COPY dashboard/ ./dashboard/
COPY tests/ ./tests/
# docs/images 供 /docs 配图与 og:image(/images/*) 同源读取，缺了会整站图片 404
COPY docs/ ./docs/

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Ensure required runtime state folders exist
RUN mkdir -p /app/data /app/logs /app/backups

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/api/v1/health || exit 1

CMD ["python", "-m", "uvicorn", "r20_backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
