#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-$ROOT/.venv}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: Python 3 is required" >&2; exit 1; }

# OKX connectivity is V5 API Key only — a pure-Python HTTPS path with zero
# Node.js/npm/CLI prerequisites. Keys are entered in the /admin web console
# and stored encrypted (Fernet) on the server; see step 2 below.

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -r "$ROOT/requirements.txt"

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/env.example" "$ROOT/.env"
fi
chmod 600 "$ROOT/.env"

cat <<EOF

R20 dependencies installed.
Next:
  1. Edit $ROOT/.env and keep R20_OKX_ENV=demo initially.
  2. Connect OKX with V5 API Keys in the /admin console (账户接入):
     - DEMO档: create keys on OKX 模拟盘, paste API Key / Secret / Passphrase.
     - LIVE档: create keys on OKX 实盘 (trade permission), same place.
     Keys are encrypted at rest (data/r20_secrets.enc); empty fields never
     overwrite stored keys. Without keys the system stays NOT READY and
     refuses to trade — that is by design.
  3. Start the backend, open /admin, confirm the account tab shows
     "DEMO/LIVE 已配置" and a green runtime status.
EOF
