#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-$ROOT/.venv}

command -v "$PYTHON_BIN" >/dev/null 2>&1 || { echo "ERROR: Python 3 is required" >&2; exit 1; }

# OKX connectivity is V5 API Key direct signing only — no Node.js, no npm,
# no CLI binary. Market data is zero-process REST (www.okx.com -> aws.okx.com).
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
  2. Connect OKX with V5 API Keys (the only method):
     - Recommended: open /admin, account page, fill the DEMO (or LIVE) trio
       API Key / Secret Key / Passphrase. Stored Fernet-encrypted; blank fields
       keep existing values.
     - Or set OKX_DEMO_* / OKX_LIVE_* in .env directly.
  3. Until a complete key trio exists for the selected environment the system
     reports NOT READY and refuses all trading. Never copy another user's
     credentials or commit them.
EOF
