"""Central Source of Truth for R20 Quantum Trading System version and branding."""
from __future__ import annotations

from pathlib import Path

__version__ = "8.3.1"
APP_VERSION = f"v{__version__}"
APP_NAME = "AstraQuant 量化交易系统"
APP_NAME_EN = "AstraQuant"


def get_version() -> str:
    """动态读取当前工作区真实声明的版本号。

    优先从磁盘文件读取最新版本；若读取失败则回退到导入期的静态 __version__。
    确保无论常驻进程是否重启，展示层与对外 API 均能即时感知本地 Git 状态与版本变更。
    """
    try:
        v_path = Path(__file__).resolve()
        content = v_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("__version__") and "=" in line:
                return line.split("=")[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return __version__
