#!/bin/sh
set -eu

ENV_FILE="${R20_ENV_FILE:-/app/config/.env}"
CONFIG_DIR="$(dirname "$ENV_FILE")"
mkdir -p "$CONFIG_DIR" /app/data /app/logs /app/backups

if [ ! -f "$ENV_FILE" ]; then
  cp /app/env.example "$ENV_FILE"
  echo "[r20] initialized Docker configuration at $ENV_FILE"
fi

python - "$ENV_FILE" <<'PY'
from pathlib import Path
import os
import re
import secrets
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8-sig")
values = {
    "R20_DEPLOYMENT_MODE": "docker",
    "R20_GATEWAY_MODE": os.getenv("R20_GATEWAY_MODE", "embedded"),
    "R20_BUILD_REVISION": os.getenv("R20_BUILD_REVISION", "local-build"),
    "DASHBOARD_HOST": "0.0.0.0",
    "DASHBOARD_PORT": "8080",
}
match = re.search(r"^R20_SETUP_TOKEN=(.*)$", text, re.MULTILINE)
if not match or match.group(1).strip() in {"", "replace_with_a_long_random_setup_token"}:
    values["R20_SETUP_TOKEN"] = secrets.token_urlsafe(32)
for key, value in values.items():
    pattern = rf"^{re.escape(key)}=.*$"
    replacement = f"{key}={value}"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, replacement, text, count=1, flags=re.MULTILINE)
    else:
        text += f"\n{replacement}\n"
path.write_text(text, encoding="utf-8")
PY

chown -R r20:r20 "$CONFIG_DIR" /app/data /app/logs /app/backups
exec gosu r20 "$@"
