"""Central Source of Truth for AstraQuant version and branding.

对外品牌 = `AstraQuant`；内部代号 = `R20`（包名 `r20_backend` / `r20_gateway`
与 `R20_*` 环境变量键**有意保留**，理由见 `README.md` 的「品牌与内部代号」一节）。
"""
from __future__ import annotations

from pathlib import Path

__version__ = "8.3.1"
APP_VERSION = f"v{__version__}"
APP_NAME = "AstraQuant"
APP_NAME_EN = "AstraQuant"
#: 官方站点（**带 www**，与 DNS 实际解析一致；页面内的 canonical / og:url 同源）。
APP_SITE = "https://www.astraquant.tech"
#: 官方仓库。
APP_REPO = "https://github.com/555cute/astra-quant-agent"


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
