#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-$ROOT/.venv}
OKX_CLI_SPEC=${OKX_CLI_SPEC:-@okx_ai/okx-trade-cli@^1.4.4}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: Python 3 is required" >&2; exit 1; }

# OKX CLI (Node.js) 仅供私有交易接口使用；公共行情（K线/盘口/指标）为
# 零进程直连 (www.okx.com → aws.okx.com → CLI)，缺 Node 不影响行情采集。
HAS_NODE=1
command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || HAS_NODE=0
if [ "$HAS_NODE" = "0" ]; then
  echo "WARN: Node.js 18+/npm not found - OKX CLI install will be skipped." >&2
  echo "WARN: Public market data still works (zero-process direct REST)." >&2
  echo "WARN: Configure OKX via DEMO/Live API Key in /admin, or install Node later for the CLI OAuth path." >&2
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -r "$ROOT/requirements.txt"

if command -v okx >/dev/null 2>&1; then
  echo "OKX CLI already installed: $(okx --version 2>/dev/null | sed -n '1p')"
elif [ "$HAS_NODE" = "1" ]; then
  echo "Installing official OKX CLI: $OKX_CLI_SPEC"
  npm install -g "$OKX_CLI_SPEC" || echo "WARN: OKX CLI install failed; private order path unavailable until fixed." >&2
else
  echo "SKIP: OKX CLI not installed (node/npm missing)." >&2
fi

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/env.example" "$ROOT/.env"
fi
chmod 600 "$ROOT/.env"
chmod +x "$ROOT/scripts/r20_okx_setup.py"

cat <<EOF

R20 dependencies installed.
Next:
  1. Edit $ROOT/.env and keep R20_OKX_ENV=demo initially.
  2. Configure OKX using ONE method:
     - Recommended standalone path: enter a DEMO API Key in /admin.
     - CLI OAuth path: run OAuth login as the SAME Linux user that runs both services.
  3. Verify without placing an order:
     $VENV_DIR/bin/python $ROOT/scripts/r20_okx_setup.py

OAuth site must be explicitly selected: global / eea / us / tr.
Do not copy another user's ~/.okx directory or commit credentials.
EOF
