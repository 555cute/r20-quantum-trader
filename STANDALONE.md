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

Preserve `.env`, the encrypted secret stores and their encryption keys, JSON configuration/state, and SQLite databases. Gateway uses `data/r20_gateway.db`, authentication uses `data/r20_admin.db`, and the trade database is `data/r20_quant.db`. Use the backup subsystem to obtain consistent SQLite copies. See [RECOVERY_GUIDE.md](RECOVERY_GUIDE.md).

Docker requires a running Linux container engine (Docker Desktop/WSL2 on Windows). Its image contains the Python backend, built Vue assets and Node/OKX CLI runtime. Runtime secrets are excluded from the build context and injected at deployment. A bind mount over `/app/data` hides image seed files; use the checkout's initialized data directory or initialize it explicitly.

## Offline verification

Disable both workers before running the test suite. Tests must supply temporary data and mocked external boundaries; the switches alone do not block explicitly invoked network methods.

```sh
R20_GATEWAY_WORKER_ENABLED=0 R20_DASHBOARD_WORKER_ENABLED=0 R20_TESTING=1 \
  python -m unittest discover -s tests
```

The GitHub Actions workflow is `.github/workflows/ci.yml`. Test counts are determined by the current run, not by a static README badge.
