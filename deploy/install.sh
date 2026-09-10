#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-$ROOT/.venv}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: Python 3 is required" >&2; exit 1; }

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
  2. Connect OKX with a V5 API Key in the admin console (/admin -> 账户接入):
     - Create a LIVE and/or DEMO API key pair in OKX (key/secret/passphrase).
     - Enter them in the admin page; they are stored encrypted (Fernet).
     - Blank fields never overwrite existing keys.
     No Node.js / npm / OKX CLI is required or supported anymore.
  3. Without an API Key the system stays NOT READY and refuses to trade
     (fail-closed). Public market data works key-free.

Never commit credentials or share encrypted secret stores.
EOF
