"""Process-level path to the persisted R20 environment file."""
from pathlib import Path
import os


def _startup_override() -> str | None:
    raw = os.environ.get("R20_ENV_FILE")
    if raw is None:
        return None
    stripped = str(raw).strip()
    return stripped or None


_ENV_FILE_OVERRIDE = _startup_override()


def env_file_path(root: Path) -> Path:
    """Return the persisted env file for ``root``.

    ``R20_ENV_FILE`` is captured from the process environment at import.
    Empty values are treated as unset. The helper does not read files and
    does not follow a later ``R20_ENV_FILE`` assignment from a loaded ``.env``.
    """
    override = _ENV_FILE_OVERRIDE
    if override is None:
        return Path(root) / ".env"
    path = Path(override)
    if path.is_absolute():
        return path
    return Path(root) / path
