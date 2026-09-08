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

If `R20_SETUP_TOKEN` and `R20_ADMIN_TOKEN` are empty (or the example placeholder), first boot generates both and prints them once to logs. The setup token is the `admin` login password and is not written to disk. The admin token is the `X-R20-Admin-Token` header for control-plane API calls after accounts exist; it is stored in the persisted env file. It cannot log into the console, manage users, or pass `require_superadmin`. Existing accounts authenticate with a server-side session. Set a permanent password in the admin console.



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

### Binance strategy feedback loop

The selected adapter supplies candles, tickers, instrument filters, funding rates (`premiumIndex`), open interest, the global account long/short ratio, and taker buy/sell volumes. Funding is expressed in percent. Binance BASE taker volumes are converted to estimated USDT notional at the current mark price before entering the strategy's USDT fields; this is not an exact historical turnover measurement. Missing auxiliary statistics remain unavailable, not neutral zero. OKX-only SmartMoney and optional OKX CLI news are not Binance execution data.

Risk configuration is loaded from `R20_ENV_FILE`, including the persisted Docker `data/config/.env`. The final model prompt always includes the live risk budget, even when an older profile omits its placeholder. Single-asset and daily-loss budgets include both ratio and absolute caps. The BASE execution path caps leverage and per-order margin, then sizes against the actual submitted limit/stop prices, exchange minimums, and the remaining asset budget. It never silently rescales a DEMO quote after sizing. The control plane and trader share `data/.ai_factor_trader.lock`; the trader acquires it before freezing the selected account.

Accepted orders write an account-scoped `signal_journal.json` entry with `status: submitted`, the exchange order ID, instrument, direction, policy identifiers, and the pre-order factor snapshot. This is not a fill or realized PnL. Binance closed-cycle reconstruction retains `entryOrderIds` from actual opening fills. Ledger sync joins those IDs with the same account/instrument/direction to attach entry and scale-in evidence. Snapshots first observed on a later position poll are observations, not reconstructed entry evidence.

Evolution consumes only `status: closed` ledger rows and their net realized PnL after commission. Missing entry IDs or snapshots stay unknown; there is no nearest-time/name fallback, no cross-account journal borrowing, and incomplete history blocks learning. Funding bills remain separate from the per-cycle realized-plus-commission result. Older verified snapshots survive subsequent history refreshes; OKX histories without verified entry-order linkage are not assigned Binance evidence.

### Configurable news sources

Use **Admin → News Sources** (`/admin/news`) to select OKX aggregated news, Binance official announcements, both, or neither. The default remains OKX for existing deployments. Superadmins can save; other administrators can inspect status. Saving does not perform a network request or change the trading venue/credentials. **Collect now** explicitly requests the saved public sources; otherwise the existing scheduled news job performs the next collection.

Deployment setting: `R20_NEWS_SOURCES=okx`, `binance`, `okx,binance`, or an empty value to disable all sources. Admin changes persist in `R20_ENV_FILE` (Docker: `data/config/.env`) and override the startup environment. Disabling a source immediately filters its cached content from the prompt, public dashboard and sentiment cache API, without waiting for account polling.

Binance collection uses the public website CMS endpoint at `https://www.binance.com/bapi/composite/v1/public/cms/article/list/query`. Categories include new listings, latest news/rule changes, delistings, maintenance and API updates; promotional activities and airdrops are excluded. Records include the official title, publication time, category and original link. No article body, translation, bullish/bearish score or coin association is invented. This website interface is not a versioned trading-API availability guarantee: connectivity failures, throttling or challenge pages are reported as source errors.

Sources have independent bounded requests and per-source caches. A failed source can show its previous records as stale without hiding a healthy source; a successful empty result is distinct from failure. Old cached headlines do not retrigger the news circuit breaker, and stale OKX sentiment is not used as a live factor score. Binance announcements enter the strategy's optional news context; adding this source does not introduce an automatic listing/delisting trade or liquidation rule. Turning news off does not disable price/risk controls or prematurely clear an already active circuit breaker.

Mixed-source selection reserves up to three records per enabled source before filling the remaining slots by publication time; the final selected list is still newest-first. The dashboard has ten slots and the model news context has six, each selected independently with the same rule. Thus frequent OKX updates cannot crowd all Binance announcements out of either surface. A source with fewer records releases unused slots; no placeholder news is created.

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

Docker requires a running Linux container engine (Docker Desktop/WSL2 on Windows). Its image contains the Python backend, built Vue assets and Node/OKX CLI runtime. Runtime secrets are excluded from the build context and injected at deployment. A bind mount over `/app/data` hides image seed files; use the checkout's initialized data directory or initialize it explicitly. Compose `env_file` injects the host `.env` as bootstrap only. The image and Compose set `R20_ENV_FILE=/app/data/config/.env` so admin-saved settings persist on the `./data` volume; that file overrides bootstrap values, while process-manager worker switches still take precedence. Do not bind-mount a single `.env` file. Native installs keep the project-root `.env` default and should not set `R20_ENV_FILE`. Default `docker-compose.yml` pulls `ghcr.io/cnlimiter/r20-quantum-trader:latest` with `pull_policy: always` and has no `build` section. Local source builds use `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build`, which tags `r20-quantum-trader:local` and does not pull GHCR. `docker compose stop`/`start` reuse the same container. Recreate (`up --force-recreate` or `down` then `up`) replaces the container filesystem but keeps `./data`. Older images wrote admin settings to `/app/.env` in the container layer, which is lost on recreate; if an existing container already has admin changes there, copy `/app/.env` to `data/config/.env` on the data volume before upgrading. Packaged data backups exclude that config file; store it separately and never put plaintext credentials in an archive.

## Offline verification

Disable both workers before running the test suite. Tests must supply temporary data and mocked external boundaries; the switches alone do not block explicitly invoked network methods.

```sh
R20_GATEWAY_WORKER_ENABLED=0 R20_DASHBOARD_WORKER_ENABLED=0 R20_TESTING=1 \
  python -m unittest discover -s tests
```

The GitHub Actions workflows are `.github/workflows/ci.yml` and `.github/workflows/docker.yml` (GHCR). Test counts are determined by the current run, not by a static README badge.
