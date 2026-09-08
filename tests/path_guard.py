"""Path containment that treats Windows 8.3 short names as the same location."""
from __future__ import annotations

import os
from pathlib import Path


def contained(path, root) -> bool:
    if isinstance(path, int):
        return True
    left = Path(os.path.normcase(os.path.realpath(path)))
    right = Path(os.path.normcase(os.path.realpath(root)))
    try:
        left.relative_to(right)
        return True
    except ValueError:
        return False
