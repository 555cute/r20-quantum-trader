# ==========================================
# Stage 1: Build Vue 3 Frontend (Vite 8 needs Node ^20.19 || >=22.12)
# ==========================================
FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN rm -rf public/images
COPY docs/images/ ./public/images/
RUN npm run build

# ==========================================
# Stage 2: Python runtime, Node for OKX CLI, backend
# ==========================================
FROM python:3.11-slim AS runner

WORKDIR /app
LABEL org.opencontainers.image.title="R20 Quantum Trader" \
      org.opencontainers.image.description="R20 Quantum Trading System" \
      org.opencontainers.image.licenses="MIT"


ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080 \
    R20_OKX_ENV=demo \
    R20_MANUAL_CLOSE_ENABLED=0 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    procps \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g @okx_ai/okx-trade-cli@^1.4.4

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY r20_backend/ ./r20_backend/
COPY r20_gateway/ ./r20_gateway/
COPY r20_exchange/ ./r20_exchange/
COPY scripts/ ./scripts/
COPY plugins/ ./plugins/
COPY dashboard/ ./dashboard/
COPY docs/images/ ./docs/images/
COPY data/.gitkeep ./data/.gitkeep
COPY data/prompt_library.json ./data/prompt_library.json

COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN mkdir -p /app/data /app/logs /app/backups

ENV R20_ENV_FILE=/app/data/config/.env

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/api/v1/health || exit 1

CMD ["python", "-m", "uvicorn", "r20_backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
