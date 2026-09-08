"""风控测试环境隔离助手。

背景：scripts/risk_constants.py 在 import 时执行 load_dotenv(.env) 并读取 R20_* 环境变量；
r20_backend.config 的模块级 load_dotenv 会强制覆盖 os.environ。生产 .env 当前挂在
「进取」套件（4 笔同向 / 3% 单笔风险 / 8% 日亏）时，全量 discover 里先被 import 的
测试会把激进值带进进程环境，risk_constants / ai_factor_trader / ai_brain_trader 在
import 期烘焙这些值 → 断言基线语义的风控测试随机翻红（单独跑绿、全量跑红）。

pin_baseline_risk_env() 把 risk_constants 及其消费方模块在「基线 DEFAULTS」下原地重载，
使其与 .env 当前挂哪套风控完全解耦；env 快照在重载后原样恢复，不泄漏给后续测试。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pin_baseline_risk_env() -> None:
    import risk_constants as rc

    # 快照当前 env，重载结束后恢复，避免基线值泄漏影响其他测试
    snapshot = {k: os.environ.get(k) for k in rc.RISK_ENV_KEYS}

    try:
        import r20_backend.config as cfg
        orig_loader = cfg.load_dotenv
        cfg.load_dotenv = lambda _path: None  # 重载期间禁止 .env 覆盖基线
    except Exception:
        orig_loader = None

    try:
        for key, value in rc.DEFAULTS.items():
            os.environ[key] = str(value)

        # 先重载 risk_constants 本体（含 scripts. 双拼写），再重载绑定了常量的消费方
        for name in ("risk_constants", "scripts.risk_constants",
                     "ai_factor_trader", "scripts.ai_factor_trader",
                     "ai_brain_trader", "scripts.ai_brain_trader"):
            module = sys.modules.get(name)
            if module is not None:
                importlib.reload(module)
    finally:
        if orig_loader is not None:
            cfg.load_dotenv = orig_loader
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
