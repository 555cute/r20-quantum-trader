# R20 Quantum Trader v6.0.0 Preview Standalone Deployment

v6.0.0-preview removes the runtime dependency on QwenPaw. The product is now composed of:

- `r20_backend.app`: standalone FastAPI control plane and read-only monitoring API.
- `r20_gateway.worker`: the R20-native, single-owner scheduler and durable notification-delivery worker for the 15-minute trader, 60-second factor refresh, 10-minute news refresh, daily reports, evolution review, and nightly backup.
- `scripts/`: strategy and execution modules, run as isolated Python processes.
- `.env`: only source for LLM, OKX, and optional notification credentials.

## Install

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
chmod 600 .env
```

Set `LLM_*` and `OKX_*` credentials in `.env`. Never commit this file.

Before the first launch, set a random `R20_SETUP_TOKEN` in `.env`. Open `/admin`, enter it to unlock the setup page, then set a permanent administrator token. The page never displays configured secret values. `.env` is written atomically and set to permission mode `0600`.

The standalone backend uses `OKX_*` for native read-only REST calls. Existing strategy execution remains on the local OKX CLI bridge during this migration phase; move that bridge's credentials to the target host before enabling the scheduler.

## Run Locally

Terminal 1:

```sh
. .venv/bin/activate
python -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
```

默认 `.env` 使用 `R20_GATEWAY_MODE=embedded`，后端会自动监督并启动唯一 Gateway Worker，因此本地运行不需要第二个终端。

只有使用 systemd 独立管理 Gateway 时才设置：

```dotenv
R20_GATEWAY_MODE=external
```

并在第二个终端运行：

```sh
. .venv/bin/activate
python -m r20_gateway.worker
```

The backend exposes only read-only control-plane endpoints:

- `GET /api/v1/health`
- `GET /api/v1/status`
- `GET /api/v1/cache/{decisions|factors|ledger|sentiment|self-improvement}`
- `GET /api/v1/market/{instId}`
- `GET /api/v1/account/positions`

No HTTP trade-trigger endpoint is exposed except the separately enabled, confirmation-protected manual close action. The admin console also supports a protected update check and `git pull --ff-only`; it refuses to update a dirty worktree and never restarts services automatically.

## QwenPaw Container Coexistence

When `www.r20.cn` is already reverse-proxied into a QwenPaw container, keep QwenPaw on its existing port and let the R20 standalone backend own port `8080`. `r20_backend.app` serves the compiled Vue application from `r20_frontend/dist`, while `/api/all` and `/api/v1/*` remain R20-native API routes. This preserves the hostname and reverse-proxy rules while providing real `/terminal/*` and `/admin` routes.

Add the `[program:r20-backend]` block from the container supervisor configuration and restart the container during a maintenance window so supervisord adopts it. Do not run the legacy `dashboard.app` Uvicorn process at the same time as `r20_backend.app`.

## Docker Compose

Docker 部署使用项目根目录的 `Dockerfile` 与 `compose.yaml`。配置文件持久化在 `docker/config/.env`，数据库、日志和备份分别使用命名卷。Docker 模式使用 embedded Gateway，不要额外启动 `r20_gateway.worker` 或旧 scheduler。

```sh
mkdir -p docker/config
cp env.example docker/config/.env
docker compose up -d --build
```

详细端口、卷、更新和备份命令见 [README.md](README.md#docker-compose-部署)。

## systemd

Copy `deploy/r20-quantum.service` and `deploy/r20-gateway.service` to `/etc/systemd/system/`, update `WorkingDirectory` and `EnvironmentFile`, then. `r20-quantum.service` 已设置 `R20_GATEWAY_MODE=external`，避免后端 embedded supervisor 与独立 Gateway 服务重复启动：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now r20-quantum r20-gateway
```

Before enabling `r20-gateway`, disable the old QwenPaw cron jobs to prevent duplicate execution. Do not run both schedulers simultaneously. The current Gateway worker owns the scheduler; the legacy `r20_backend.scheduler` and `deploy/r20-scheduler.service` are retained only for compatibility and must not run alongside it.
