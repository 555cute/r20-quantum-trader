<div align="center">

[简体中文](README.md) · [**English**](README.en.md)

# AstraQuant

### Multi-exchange agentic quant trading terminal — decision by LLMs, risk by hard code

[![Release](https://img.shields.io/badge/Release-v8.3.1-blue.svg?style=flat-square)](https://github.com/555cute/astra-quant-agent/releases/tag/v8.3.1)
[![Website](https://img.shields.io/badge/Site-www.astraquant.tech-6E56CF.svg?style=flat-square)](https://www.astraquant.tech)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg?style=flat-square)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue-3.5%2B-4FC08D.svg?style=flat-square)](https://vuejs.org/)
[![Tests](https://img.shields.io/badge/Tests-10k%2B%20Passing-brightgreen.svg?style=flat-square)](tests/)
[![Community](https://img.shields.io/badge/Community-LINUX%20DO-F97316.svg?style=flat-square&logo=linux&logoColor=white)](https://linux.do/)

**Named AI seats debate → a CIO seat adopts the final call → a physical risk pipeline vetoes → OKX / Binance / Gate get real orders.**

*The LLM proposes. Python physically disposes. Nothing here is simulated by default.*

[Quick start](#-quick-start) · [What it is](#-what-it-is) · [Design principles](#-four-design-principles) · [Screenshots](#-screenshots) · [Strategy console](#-strategy-configuration-centers) · [Capital & risk](#-capital-scaling-and-tiered-risk) · [Deploy](#-deploy) · [Code map](#-code-map-for-developers--handoff-agents) · [Brand & codename](#-brand-and-internal-codename-read-before-renaming)

</div>

> 🌟 **This project is launched with, and officially links to, the [LINUX DO (linux.do)](https://linux.do/) open-source community.**

---

## 🚀 Quick start

```bash
git clone https://github.com/555cute/astra-quant-agent.git && cd astra-quant-agent
./deploy/docker-start.sh          # Docker, recommended; for a host install use ./deploy/install.sh
```

| Surface | URL |
|---|---|
| Trading dashboard (frontend) | `http://localhost:8080/` |
| Admin control plane | `http://localhost:8080/admin/login` (default user `admin`) |

> ⚠️ You need **two** sets of credentials to actually trade: an **LLM provider key** and **exchange API keys**.
> The stack boots in **paper / demo mode**; it only touches real money after you configure both and switch explicitly.

---

## 🧭 What it is

AstraQuant is an **agentic, multi-exchange quant decision-and-execution operating system** built for professional trading teams.

Every **15 minutes** the main brain runs a cycle: named AI seats (trend, momentum, quantitative, macro, …) each form an independent proposal, and a **CIO seat adopts the final call**. Before any order can reach an exchange gateway, it must pass the **execution-layer Python risk pipeline** gate by gate. Only then is it submitted to **OKX / Binance / Gate** as a maker limit order, with take-profit and stop-loss attached atomically as native conditional orders.

It is deliberately **not** a toy that hides trading logic in a hardcoded script:

- **The committee is genuinely multi-model** — each seat binds its own provider, model and reasoning temperature; two rounds of cross-examination feed a CIO arbitration.
- **Risk is a tunable physical pipeline** — 26 execution-layer risk knobs live in a single source of truth and are configurable from the admin UI. If an interceptor errors or times out, opening a position is **fail-closed**: unconditionally blocked.
- **Strategy is data, not code** — prompts, committee seats, risk thresholds and model routing are editable in the UI, saveable as version snapshots with a SHA-256 fingerprint, and atomically rollbackable in 0.5s.
- **Everything is auditable** — the raw chain-of-thought, the seat cross-examination record, the venue-selection evidence, and the `policy_version` / `policy_hash` in force are persisted for every decision.

---

## 🎯 Four design principles

1. **Cognition belongs to the model; physical risk control belongs to the base layer.**
   The LLM and the committee hold only a **right to propose** a trade intent. Before an order reaches an exchange gateway it must pass 100% of the underlying Python gates (order geometry validity, a hard risk/reward floor, the 4H trend veto, same-direction exposure quotas, …). Any interceptor error or timeout blocks position opening unconditionally. This is the layer that stops a model hallucination from becoming a realized loss.

2. **Market-regime auto-detection.**
   No more "run trend-following into a chop and get ground down, run a grid into a trend and get trapped." The engine derives regime from calculus velocity `v`, acceleration `a`, integral energy `E` and the volatility distribution, then recommends a matching strategy preset.

3. **Three-venue parity.**
   OKX, Binance and Gate are equal peers in both risk and execution. One unified multi-venue asset view, with automatic recognition of each venue's hedge-mode positions and native conditional protection orders.

4. **White-box explainability and self-evolving ledger.**
   The raw reasoning chain, seat cross-examination and venue-selection evidence are all recorded. Every 6 hours the system mines the real closed-trade ledger for attribution and distils lessons into long-term memory (with anti-bias guardrails and a 7–14 day sharpness half-life).

---

## 📸 Screenshots

### 1. 🖥️ Trading workstation

Asset overview, the causal-calculus dynamics matrix, and a KLineChart v10 native candlestick engine:

![Trading dashboard](docs/images/v800_live_dashboard.png)

### 2. 🌊 Chain-of-thought (CoT) trajectory drawer

Every decision exposes an expandable **"chain of thought and mathematical basis"**: structure, calculus features, probability expectations and the model's raw draft:

![Decision trajectory and CoT](docs/images/v800_trajectory_cot.png)

### 3. 🧠 AI decision radar

![AI decision radar](docs/images/v800_radar_view.png)

### 4. ⚙️ Admin control plane

Runtime health, three-venue connection state, LLM latency, memory, and every physical risk-interception event:

![Admin control plane](docs/images/v800_admin_overview.png)

---

## 🧩 Strategy configuration centers

> **"The right to define strategy always belongs to the trader, not to hardcoded system logic."**

Legacy quant software buries trading logic in underlying scripts where it is painful to tune and impossible to review. AstraQuant decouples **prompts, the committee, physical risk, cognitive review, version snapshots, risk thresholds, model routing and multi-venue execution** into nine visual modules:

| # | Module | What it governs |
|---|---|---|
| 1 | 🎨 **Prompt Studio** | Visually compose the "System core rules" and the "User market-feature template"; inject 9 classes of live semantic variables (`{{market_regime}}`, `{{market_matrix}}`, `{{risk_budget}}`, `{{news_intelligence}}`, `{{trading_memory}}`, `{{account_positions}}`, …) in one click; duplicate profiles, import/export JSON, anti-poisoning guardrails built in |
| 2 | 👥 **Multi-model investment committee** | Seats are 100% user-defined; each seat **independently binds its own provider and model** (e.g. an aggressive officer on Claude, a risk officer on DeepSeek-R1, a quant officer on GPT-4o) with its own system prompt and temperature; two rounds of cross-examination, then CIO arbitration |
| 3 | 🛡️ **Fail-closed physical interception pipeline** | A non-bypassable Python plugin pipeline. Four factory gates: 4H macro-trend filter / confidence gatekeeper / 1H ADX chop filter / true risk-reward gatekeeper. Each plugin can be single-stepped in an online sandbox to see pass vs. veto |
| 4 | 🧬 **Self-evolution engine** | Every 6 hours, mine the full closed-trade ledger for closed-loop attribution and distil lessons into long-term memory; outlier rejection, anti-bias red lines and a 7–14 day half-life; one-click rollback to the official baseline |
| 5 | 📦 **Unified policy snapshots** | Hash "prompts + interceptors + committee seats + instrument pool" into a SHA-256 policy fingerprint (e.g. `v8.3.1@a7f29b1c`); 0.5s atomic rollback; every order records the `policy_version` / `policy_hash` in force at submission |
| 6 | 🎛️ **Execution risk control center** | All 26 execution risk knobs are tunable, with three one-click presets (🛡️ Conservative / ⚖️ Balanced / 🚀 Aggressive); high-risk actions require typed confirmation phrases |
| 7 | 🤖 **LLM gateway & reasoning config** | Direct multi-provider matrix (OpenAI / Claude / Gemini / DeepSeek / Qwen, …); global thinking budget 10–1800s for long-CoT flagships; automatic failover within seconds on rate limits (429) or provider outages |
| 8 | 🌐 **Three-venue parity topology** | Native connections to all three venues with independent credentials and position modes; a cross-venue causal-calculus matrix comparing spreads, funding rates and momentum acceleration |
| 9 | 🧪 **Backtest & sandbox subsystem** | Multi-asset portfolio backtesting, statistical significance checks and sandbox dry-runs that share the exact same risk and execution code path as live trading |

![Prompt Studio](docs/images/v800_prompt_studio.png)
![Multi-model committee](docs/images/v800_council_board.png)
![Fail-closed interceptor pipeline](docs/images/v800_interceptors_failclosed.png)
![Unified policy snapshots](docs/images/v800_policy_snapshot.png)
![Execution risk control center](docs/images/v800_risk_control.png)
![LLM gateway and reasoning config](docs/images/v800_llm_hub.png)
![Self-evolution engine](docs/images/v800_self_evolution.png)
![Cross-venue causal calculus matrix](docs/images/v800_calculus_factors.png)

---

## 📊 Multi-tier logging and observability

| Logger | Path | Writer | Coverage |
| :--- | :--- | :--- | :--- |
| `trader` | `logs/ai_factor_trader.log` | Main brain cycle process | Instrument pool quotes, multi-seat debate, confidence filtering, venue routing, protection-order attachment and stop-loss ratcheting |
| `backend` | `logs/uvicorn.log` | FastAPI / Uvicorn | HTTP request/response flow, middleware, CORS, exception stacks and read-side data-plane errors |
| `scheduler` | `logs/r20_gateway.log` | Scheduler daemon | Singleton lock acquisition, scheduled job dispatch, heartbeat lease and fragment pruning |
| `audit` | `logs/r20_admin_audit.jsonl` | Security audit subsystem (append-only) | Timestamp, IP, action type and result (login, password change, credential edits, risk tuning, emergency close-all) |

For the Prometheus + Grafana stack see [`deploy/observability/README.md`](deploy/observability/README.md): it turns `/api/v1/admin/metrics` into dashboards and alerts, bound to `127.0.0.1` by default.

---

## 💰 Capital scaling and tiered risk

Every threshold is **derived proportionally from account equity** — no hardcoded absolute floors — so a small account (e.g. a 20 USDT demo) and an institutional one (4000 USDT+) both run safely:

| Metric | Rule (baseline default) | 20 USDT pool | 4000 USDT pool |
| :--- | :--- | ---: | ---: |
| **1R risk per trade** | `min(per-asset cap, available × 2%)` | 0.40 U | 15.0 U |
| **Margin hard cap per trade** | `available × 20%` | 4.0 U | 800.0 U |
| **Cumulative margin per asset** | `min(600, available × 30%)` | 6.0 U | 600.0 U |
| **Daily max-loss circuit breaker** | `min(150, available × 5%)` | 1.0 U | 150.0 U |

---

## 🧪 Tests and gates

This repo treats "it runs green" as part of the deliverable, not an afterthought. Every number in these docs is watched by a test:

```bash
# 1) Backend: audit / LLM / UI / multi-venue contract subset
.venv/bin/pytest tests/audit tests/llm tests/ui tests/venues -q

# 2) Backend: full offline suite
.venv/bin/pytest tests/ -q

# 3) Frontend: type check + build + tests
cd frontend
npx vue-tsc --noEmit -p tsconfig.app.json
npm run build
node --test tests/*.test.mjs
```

> ⚠️ **Always use the virtualenv**: no global interpreter is assumed. Use `.venv/bin/python` / `.venv/bin/pytest`.

---

<a id="code-map"></a>
## 🗂️ Code map (for developers / handoff agents)

> ⚠️ **Note**: this repository has **never contained an `OPENCODE.md`** — some older guidance points at that non-existent file; do not go looking for it. The real entry points are:

| To learn about | Read | Why |
|---|---|---|
| **Backend layering** | `r20_backend/README.md` | Layers (L0 facade / L1 wiring / L2 routers / L3 domain / L4 subpackages) and the module-extraction convention |
| **Runtime scripts & daemons** | `scripts/README.md` | Which file is the entry point vs. the daemon, root-module inventory, schedules and the dual-spelling import rule |
| **Frontend components & state** | `frontend/src/components/admin/README.md` | Component / composable boundaries and the Vue 3 admin + trading-desk state machines |
| **Standalone deployment** | `STANDALONE.md` | Local standalone install, environment configuration and service startup |
| **Emergency recovery** | `RECOVERY_GUIDE.md` | Emergency stop, cold data restore and process reset playbooks |
| **Prompt engineering** | `docs/PROMPT_GUIDE.md` | The live semantic variable dictionary, band-breathing authoring rules and committee playbook |
| **Observability** | `deploy/observability/README.md` | Prometheus scrape config and Grafana dashboard import |

**Architecture gates that watch the docs themselves:**

1. **Every subpackage module is registered** — `tests/audit/test_directory_docs_current.py` requires newly added modules under managed subpackages to be listed in their `__init__.py`;
2. **Every root module is registered** — root-level `r20_backend/*.py` and `scripts/*.py` modules must appear in the corresponding `README.md` table;
3. **Documented numbers cannot rot** — `tests/core/test_readme_baseline_numbers.py` requires documented baseline test counts to stay within the same order of magnitude as the real suite, so a 2× drift gets caught.

> 📌 **Commit discipline**: run the gates on **the tree you are about to commit** (`git status --short`, plus `git ls-files --error-unmatch <path>` for each new file).

---

## 🚀 Deploy

### Option A: 🐳 Docker (recommended, zero environment setup)

Ships Python 3.11, builds the frontend bundle, and orchestrates the web engine plus the gateway worker:

```bash
git clone https://github.com/555cute/astra-quant-agent.git
cd astra-quant-agent
cp env.example .env && vim .env        # fill in your LLM and exchange keys
./deploy/docker-start.sh               # equivalent to: docker compose up -d --build

docker compose ps                      # status
docker compose logs -f                 # follow logs
```

Both services declare `restart: unless-stopped` **and** carry in-container supervision plus a heartbeat: after a container restart, a host reboot, or an OOM kill, they bring themselves back without an external `autoheal`.

### Option B: host / bare-metal

```bash
git clone https://github.com/555cute/astra-quant-agent.git
cd astra-quant-agent
sh deploy/install.sh                   # creates .venv and installs dependencies
vim .env

source .venv/bin/activate
cd frontend && npm install && npm run build && cd ..

python -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
# or simply: ./start.sh
```

Windows / PowerShell users can use `start.ps1`. systemd unit templates live in `deploy/` (see `deploy/r20-quantum.service` and the sibling units).

---

## ⚠️ Disclaimer

1. This project is **open-source quant trading software and a quantitative research framework**, intended for technical research, study and paper/demo environments only;
2. Crypto markets carry extreme risk and uncertainty; no historical backtest implies future returns;
3. Users are expected to have professional quant trading knowledge. Before you fully understand the strategy logic, validate it in a **DEMO** environment;
4. The developers and the open-source community accept no liability for any direct or indirect losses arising from use of this software.

---

## 🤝 Community & acknowledgements

This project officially links to and endorses the **[LINUX DO (linux.do)](https://linux.do/)** open-source community:

- 🐧 **Community support** — thanks to LINUX DO for the open technical soil and strategy inspiration, and to every member for continuous feedback and live-trading suggestions;
- 💬 **Discussion** — join the [LINUX DO community](https://linux.do/) to talk multi-model committee tuning, prompt authoring and live risk control;
- Open, transparent, evolving together.

---

## 🏷️ Brand and internal codename (read before renaming)

**Public brand: AstraQuant** (website <https://www.astraquant.tech>; docs in [README.md](README.md) / [README.en.md](README.en.md)). **Internal codename: R20.**

A rebrand happened in 2026-09 that changed **only the externally visible layer**; internal identifiers are **intentionally retained**:

| Layer | Content | State |
|---|---|---|
| **Public** | Repo name · description · topics · README · UI brand strings · notification titles · container image name · robots/sitemap/canonical | **AstraQuant** |
| **Internal** | Python packages `r20_backend` / `r20_gateway` · **`R20_*` environment variable keys** · filenames containing `r20` · database filenames | **R20 retained** |
| **Internal (wire contracts)** | Session header `X-R20-Session` · high-risk confirmation phrases `UPDATE R20` / `BACKUP R20` / `RESTORE R20` · backup archive magic `R20GCM2` · Grafana dashboard UID `r20-quantum-trader` · `/opt/r20-quantum-trader` in the systemd units | **R20 retained** |

**Why the internals were not renamed too**: `R20_*` is the **configuration contract users have already written into their `.env`** — changing the prefix would silently strip configuration from every deployed instance (leaving it "not ready") while buying exactly zero public traffic. Package names are wired into 2000+ imports, and the confirmation phrases and session header are **byte-for-byte** contracts between frontend and backend — changing one side alone would break high-risk operations. So if you are here to "finish the rename": read this section first. **It is not unfinished work; it is deliberate.**

`R20_*` appearing in user-facing copy is correct — that is an **interface name**, not a brand name.

---

## 📄 License

Released under the [MIT License](LICENSE). Free and open source.
