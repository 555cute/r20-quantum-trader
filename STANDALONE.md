# R20 Standalone Deployment

R20 runs without QwenPaw. The current application consists of a FastAPI control plane (`r20_backend.app`), the mounted dashboard, and a Gateway worker that schedules isolated strategy processes and delivers queued notifications.

## Safety boundary

Starting the backend normally starts the Gateway, which can execute trading jobs. The dashboard refresh worker can query private account data. Importing the dashboard alone no longer starts refresh threads.

For a control-plane-only session, explicitly disable both workers before starting Python:

```sh
R20_GATEWAY_WORKER_ENABLED=0 R20_DASHBOARD_WORKER_ENABLED=0 \
  python -m uvicorn r20_backend.app:app --host 127.0.0.1 --port 8080
```

PowerShell:

```powershell
$env:R20_GATEWAY_WORKER_ENABLED = "0"
$env:R20_DASHBOARD_WORKER_ENABLED = "0"
python -m uvicorn r20_backend.app:app --host 127.0.0.1 --port 8080
```

These switches disable automatic workers, not authenticated HTTP actions. Account refresh, model tests, notification tests and manual jobs can still perform external operations when explicitly requested through their endpoints. Do not expose the admin console without access controls.

## Install

Use Python 3.11 or 3.12 and Node.js 22.12+.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
npm install -g @okx_ai/okx-trade-cli@^1.4.4
cp env.example .env
chmod 600 .env
npm --prefix frontend ci
npm --prefix frontend run build
```

Set a random `R20_SETUP_TOKEN` before first startup. When no administrator exists, the setup token initializes the `admin` account password. Existing accounts authenticate with a server-side session; the setup token is not a permanent bypass. Configure a permanent password through the admin console.

OKX supports separate LIVE/DEMO API keys or local CLI OAuth. Never grant withdrawal permission. Both static-key groups must match their selected environment. The `.env` example defaults to demo. The strategy path and optional OKX news enrichment require the official CLI.

CLI OAuth is tied to the service user's HOME. Complete authorization as that user. Never copy another installation's `~/.okx/`. `python scripts/r20_okx_setup.py` performs an external read-only preflight; run it deliberately before enabling trading, not as an automatic installation step.

## Exchange selection: OKX or Binance USD-M

The account page at `/admin/security` selects the trading venue and environment. Binance support is limited to USD-M USDT perpetual futures; spot, coin-margined futures and withdrawals are not exposed.

```sh
R20_EXCHANGE=binance
R20_BINANCE_ENV=demo
BINANCE_DEMO_API_KEY=
BINANCE_DEMO_SECRET_KEY=
BINANCE_LIVE_API_KEY=
BINANCE_LIVE_SECRET_KEY=
```

Configure the relevant keys in the admin page or deployment environment. Demo and live keys are strictly separate; missing demo keys never fall back to live keys. Binance uses `https://demo-fapi.binance.com` for demo and `https://fapi.binance.com` for live. No custom signed-request host is accepted. The Binance trading transport uses REST, not the OKX CLI; optional OKX news enrichment remains a separate dependency.

Saving exchange credentials or switching environments requires a superadmin session and a target-bound phrase such as `SWITCH BINANCE DEMO`. The trading-cycle lock stays held across configuration writes. A manual trader run requires `RUN BINANCE DEMO TRADER` for that selected environment. Local configuration status does not imply verified exchange connectivity.

Both adapters expose base-asset quantities. Decimal quantities are rounded down to the venue's quantity step; only the OKX adapter converts base quantities to contract counts. The execution layer confirms leverage and retains the risk/reward and position checks.

Binance has no equivalent atomic OKX OCO attachment here. Protection consists of two exchange-hosted conditional orders with linked client IDs. Hedge mode does not send `reduceOnly`; close-all TP/SL does not send quantity. Failure or uncertain responses are reconciled by client ID, and partial-entry cleanup must confirm terminal entry state and flat exposure before removing protection. These controls are not a guarantee against market or connectivity losses.

Binance closed-position history is reconstructed from available user fills, not invented from a balance delta. The audit scan is bounded by the API's recent history window (up to 90 days), and previously saved older closed cycles are retained. Incomplete windows, changing inventory or unsupported non-USDT fee conversion are reported as incomplete rather than attributed to a fabricated trade. Historical leverage/margin/ROI that cannot be established stays unknown. The dashboard's bill endpoint is a bounded recent view, not an all-time statement.

## Choose exactly one Gateway owner

### Backend-owned: local process or Docker

With `R20_GATEWAY_WORKER_ENABLED=1` (default), launch only:

```sh
python -m uvicorn r20_backend.app:app --host 127.0.0.1 --port 8080
```

Do not additionally launch `python -m r20_gateway.worker`, a legacy scheduler, QwenPaw trading cron, or `dashboard/start.sh`. Avoid `--reload` and multiple Uvicorn workers for trading deployments.

### systemd-owned: two explicitly coordinated services

The supplied `deploy/r20-quantum.service` sets `R20_GATEWAY_WORKER_ENABLED=0` in its ExecStart environment. This process-manager setting takes precedence over `.env`. `deploy/r20-gateway.service` is then the only Gateway owner.

Adjust both units together: User/Group, HOME, PATH, WorkingDirectory, EnvironmentFile and Python executable. They must refer to the same installation and credential owner. Install and enable the pair only after disabling legacy trading cron and `r20-scheduler.service`.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now r20-quantum r20-gateway
```

`r20_backend.scheduler` remains a legacy entry point. Its lock does not coordinate its schedule with Gateway scheduling. Do not run them together.

## HTTP surface

- `/`, `/trading`, `/factors`, `/news`, `/lab`, `/history`: monitoring SPA.
- `/admin/`: authenticated management SPA.
- `/api/docs`: API documentation.
- `/api/all` and `/api/overview`: public monitoring snapshots from the mounted dashboard.
- `/api/v1/health` and `/api/v1/status`: process health and status.
- `/api/v1/admin/*`: management, including configuration mutations and manual Gateway jobs.

The control plane is **not read-only**. `POST /api/v1/admin/gateway/jobs/{job_id}/run` can invoke the trading script. Manual close has a separate enable switch, administrator authorization, password verification, a position-bound token, confirmation and a trading-cycle lock.

## Persistence and recovery

Preserve `.env`, the encrypted secret stores and their encryption keys, JSON configuration/state, and SQLite databases. Gateway uses `data/r20_gateway.db` and authentication uses `data/r20_admin.db`. Account-sensitive state (ledger, trackers, baseline, snapshots and `r20_quant.db`) is isolated under `data/exchanges/<exchange>/<mode>/<credential-fingerprint>/`. Shared prompt, council and memory configuration remains under `data/`. Old root-level account files are left untouched and are not automatically assigned to a new account. Verify ownership and quantity units before any manual migration. SQLite hot backups retain these nested account directories. See [RECOVERY_GUIDE.md](RECOVERY_GUIDE.md).

Docker requires a running Linux container engine (Docker Desktop/WSL2 on Windows). Its image contains the Python backend, built Vue assets and Node/OKX CLI runtime. Runtime secrets are excluded from the build context and injected at deployment. A bind mount over `/app/data` hides image seed files; use the checkout's initialized data directory or initialize it explicitly. Compose `env_file` injects the host `.env` as bootstrap only. The image and Compose set `R20_ENV_FILE=/app/data/config/.env` so admin-saved settings persist on the `./data` volume; that file overrides bootstrap values, while process-manager worker switches still take precedence. Do not bind-mount a single `.env` file. Native installs keep the project-root `.env` default and should not set `R20_ENV_FILE`. `docker compose stop`/`start` reuse the same container. Recreate (`up --force-recreate` or `down` then `up`) replaces the container filesystem but keeps `./data`. Older images wrote admin settings to `/app/.env` in the container layer, which is lost on recreate; if an existing container already has admin changes there, copy `/app/.env` to `data/config/.env` on the data volume before upgrading. Packaged data backups exclude that config file; store it separately and never put plaintext credentials in an archive.

## Offline verification

Disable both workers before running the test suite. Tests must supply temporary data and mocked external boundaries; the switches alone do not block explicitly invoked network methods.

```sh
R20_GATEWAY_WORKER_ENABLED=0 R20_DASHBOARD_WORKER_ENABLED=0 R20_TESTING=1 \
  python -m unittest discover -s tests
```

The GitHub Actions workflows are `.github/workflows/ci.yml` and `.github/workflows/docker.yml` (GHCR). Test counts are determined by the current run, not by a static README badge.
