# R20 Quantum Trader Standalone Deployment

R20 runs independently of QwenPaw. The product is composed of:

- `r20_backend.app`: standalone FastAPI control plane and read-only monitoring API.
- `r20_gateway.worker`: the R20-native, single-owner scheduler and durable notification-delivery worker for the 15-minute trader, 60-second factor refresh, 10-minute news refresh, daily reports, evolution review, and nightly backup.
- `scripts/`: strategy and execution modules, run as isolated Python processes.
- `.env` + encrypted R20 Secret Store: LLM, OKX API Key, and notification credentials. A complete OKX key trio is required for private requests and trading; public market data needs no credentials.
- Native OKX V5 REST signing: the single private connectivity path for the strategy, control plane, and ledger.

## Install

```sh
sh deploy/install.sh
. .venv/bin/activate
```

The installer creates the Python environment, installs requirements, copies `env.example` only when `.env` is absent, and restricts `.env` to mode `0600`. Node.js/npm is needed only if you build the Vue frontend from source (`cd frontend && npm install && npm run build`), not for backend installation or OKX connectivity.

Set `LLM_*` and a random `R20_SETUP_TOKEN` before the first launch. Open `/admin` to complete administrator setup, then configure **API Key / Secret Key / Passphrase** in Account Access. Create separate **DEMO and LIVE** trios, grant only necessary read/trade permissions (never withdrawal), and bind server IPs where possible. Keep `R20_OKX_ENV=demo` for initial validation.

The admin form stores credentials locally with **Fernet encryption**; **blank fields keep existing values**. Direct `OKX_DEMO_*` / `OKX_LIVE_*` entries in `.env` are a plaintext bootstrapping alternative. Never commit either credentials or encryption material. Both services need access to the same project configuration and secret store under appropriate file permissions.

An incomplete selected profile is **NOT READY**: private operations and trading fail closed. Public REST market data remains available without a key. Server restarts no longer depend on a short-lived login grant: persist `data/r20_secrets.enc` and its matching `data/.r20_secret_key`. Revoked keys, changed IP restrictions, and network faults can still prevent exchange access.

The authenticated `GET /api/v1/admin/okx/runtime` (management session in `X-R20-Session`) reports `environment`, `mode_configured`, `live_configured`, `demo_configured`, `fingerprint`, `base_url`, `connection: "static-v5-key"`, and `status: "READY" | "NOT_READY"`; `not_ready_reason` is present when unconfigured. This is a **local configuration check**, not an exchange connectivity or permission test. Use a read-only account snapshot to confirm connectivity before enabling trading.

## Run Locally

Terminal 1:

```sh
. .venv/bin/activate
python -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
```

Terminal 2:

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

When `www.r20.cn` is already reverse-proxied into a QwenPaw container, keep QwenPaw on its existing port and let the R20 standalone gateway own port `8080`. `r20_backend.app` mounts the existing dashboard at `/`, while `/admin` and `/api/v1/*` remain R20-native routes. This preserves the hostname, reverse-proxy rules, dashboard paths, QwenPaw process, and QwenPaw backup layout.

Add the `[program:r20-backend]` block from the container supervisor configuration and restart the container during a maintenance window so supervisord adopts it. Do not run the legacy `dashboard.app` Uvicorn process at the same time as `r20_backend.app`.

## systemd

Copy `deploy/r20-quantum.service` and `deploy/r20-gateway.service` to `/etc/systemd/system/`, update `WorkingDirectory` and `EnvironmentFile`, then:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now r20-quantum r20-gateway
```

Before enabling `r20-gateway`, disable the old QwenPaw cron jobs to prevent duplicate execution. Do not run both schedulers simultaneously. The current Gateway worker owns the scheduler; the legacy `r20_backend.scheduler` and `deploy/r20-scheduler.service` are retained only for compatibility and must not run alongside it.

Before starting the Gateway, open `/admin` and verify the selected DEMO key profile and a read-only account snapshot. A `NOT_READY` runtime response means trading must remain disabled; `READY` alone does not prove exchange acceptance or override risk controls. Ensure the dedicated service user can read the project configuration and matching encrypted store/key; do not print their contents for diagnostics.
