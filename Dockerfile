# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-builder
WORKDIR /build/r20_frontend
COPY r20_frontend/package.json r20_frontend/package-lock.json ./
RUN npm ci
COPY r20_frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ARG R20_BUILD_REVISION=local-build
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    R20_ENV_FILE=/app/config/.env \
    R20_DEPLOYMENT_MODE=docker \
    R20_GATEWAY_MODE=embedded \
    R20_BUILD_REVISION=${R20_BUILD_REVISION} \
    DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git gosu tini tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 r20 \
    && useradd --uid 10001 --gid r20 --create-home --shell /usr/sbin/nologin r20

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY --chown=r20:r20 . .
COPY --from=frontend-builder --chown=r20:r20 /build/r20_frontend/dist ./r20_frontend/dist
RUN chmod +x /app/deploy/docker-entrypoint.sh \
    && mkdir -p /app/config /app/data /app/logs /app/backups \
    && chown -R r20:r20 /app/config /app/data /app/logs /app/backups

EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/app/deploy/docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "r20_backend.app:app", "--host", "0.0.0.0", "--port", "8080"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health', timeout=3)); raise SystemExit(0 if data.get('status') == 'ok' else 1)"
