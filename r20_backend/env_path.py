"""Resolve the writable R20 environment file across local and container deployments."""
from __future__ import annotations
import os
from pathlib import Path


def configured_env_file(root: Path) -> Path:
    configured = os.getenv("R20_ENV_FILE", "").strip()
    return Path(configured).expanduser().resolve() if configured else root / ".env"
