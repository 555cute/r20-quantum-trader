"""R20 执行层风控参数 —— 单一事实源 (Single Source of Truth).

所有硬风控阈值集中在此，按环境变量读取；后台「风控管理页」写入 .env 后，
交易引擎子进程在下一个巡检周期 import 本模块时自动生效（无需重启）。

约定：
- 每个参数的环境变量键以 R20_ 前缀命名，默认值与历史硬编码值完全一致；
- DEFAULTS 表同时被 r20_backend/risk_config.py（管理页 schema）引用，
  防止 UI 默认值与执行层默认值漂移；
- 提示词侧（ai_brain_trader 的 {{risk_budget}} 等）必须从这里取值插值，
  保持「提示词口径 == 代码口径」。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 独立运行（cron/手动）时也要拿到 .env 里的最新风控配置；backend 调度路径下重复加载无害。
try:
    from r20_backend.config import load_dotenv as _load_dotenv
    _load_dotenv(_PROJECT_ROOT / ".env")
except Exception:
    pass


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, "") or default))
    except (TypeError, ValueError):
        return default


# ── 组1 · 仓位与敞口 ──────────────────────────────────────────────
# 最大并发持仓数。0 = 自动跟随标的池容量（历史行为 len(TARGET_INSTRUMENTS)）。
MAX_CONCURRENT_POSITIONS_CAP = _env_int("R20_MAX_CONCURRENT_POSITIONS", 0)
# 同向持仓上限（多/空各自封顶），与提示词「同向单上限」共用同一口径。
MAX_SAME_DIRECTION_POSITIONS = _env_int("R20_MAX_SAME_DIRECTION_POSITIONS", 3)
# 单笔下单保证金占可用余额硬顶。
MAX_MARGIN_EQUITY_RATIO = _env_float("R20_MAX_MARGIN_EQUITY_RATIO", 0.20)
# 单标的累计占用保证金（含金字塔加仓）占可用余额比例上限。
SINGLE_ASSET_EQUITY_RATIO = _env_float("R20_SINGLE_ASSET_EQUITY_RATIO", 0.30)
# 单标的累计保证金绝对封顶（USDT，小资金账户按上面的比例自动收紧）。
MAX_SINGLE_ASSET_MARGIN = _env_float("R20_MAX_SINGLE_ASSET_MARGIN_USDT", 600.0)
# 单笔杠杆区间（AI 在 [下限, 上限] 内按信心自主裁决，执行层强制钳制到区间）。
MAX_LEVERAGE = _env_float("R20_MAX_LEVERAGE", 5.0)
MIN_LEVERAGE = _env_float("R20_MIN_LEVERAGE", 2.0)
# 交叉守卫：下限只允许更保守，不允许越过上限（后台分次保存时的中间态兜底）。
if MIN_LEVERAGE > MAX_LEVERAGE:
    MIN_LEVERAGE = MAX_LEVERAGE

# ── 组2 · 单笔风险 ────────────────────────────────────────────────
# 单笔 1R 风险额占可用余额比例（与池内绝对值取小）。
RISK_PER_TRADE_EQUITY_RATIO = _env_float("R20_RISK_PER_TRADE_RATIO", 0.02)
# 最小盈亏比 R:R 硬底线，低于该值的报价被 order_risk 物理拦截。
MIN_RISK_REWARD_RATIO = _env_float("R20_MIN_RISK_REWARD", 2.0)
# 新开仓最低 AI 置信度门禁。
MIN_ENTRY_CONFIDENCE = _env_float("R20_MIN_ENTRY_CONFIDENCE", 80.0)

# ── 组3 · 止损与熔断 ──────────────────────────────────────────────
# 单日亏损熔断绝对封顶（USDT）。
MAX_DAILY_LOSS_USDT = _env_float("R20_MAX_DAILY_LOSS_USDT", 150.0)
# 单日亏损熔断占可用余额比例（与绝对封顶取小）。
DAILY_LOSS_EQUITY_RATIO = _env_float("R20_DAILY_LOSS_EQUITY_RATIO", 0.05)
# 最长持仓时间（小时）：超时且波幅不足带宽的横盘仓位触发时间止损平仓。
TIME_STOP_HOURS = _env_float("R20_TIME_STOP_HOURS", 8.0)
# 时间止损横盘判定带宽（×ATR），浮盈绝对值小于该带宽才视为无突破。
TIME_STOP_ATR_BAND = _env_float("R20_TIME_STOP_ATR_BAND", 0.15)
# 止损出局后同标的同向冷静期（分钟）。
STOP_COOLDOWN_MINUTES = _env_int("R20_STOP_COOLDOWN_MINUTES", 30)

# ── 组4 · 顺势金字塔加仓门禁 ─────────────────────────────────────
# 单标的最大顺势加仓次数（0 = 禁止加仓）。
MAX_SCALE_IN_COUNT = _env_int("R20_MAX_SCALE_IN_COUNT", 1)
# 允许加仓的最小底仓浮盈率（0.008 = +0.8%）。
MIN_SCALE_IN_PROFIT_RATIO = _env_float("R20_MIN_SCALE_IN_PROFIT_RATIO", 0.008)
# 加仓必须达到的最低 AI 置信度（%）。
MIN_SCALE_IN_CONFIDENCE = _env_float("R20_MIN_SCALE_IN_CONFIDENCE", 75.0)

# ── 默认值表（供后台风控管理页 schema 引用，键 = 环境变量名） ────
# 注意：必须是字面量默认值，绝不能引用上面「已按 .env 解析」的常量——
# 否则后台进程在用户应用过套件后重启，DEFAULTS 会被 .env 污染，
# 导致「均衡波段」套件写入用户当前值、UI「默认」提示失真。
DEFAULTS = {
    "R20_MAX_CONCURRENT_POSITIONS": 0,
    "R20_MAX_SAME_DIRECTION_POSITIONS": 3,
    "R20_MAX_MARGIN_EQUITY_RATIO": 0.20,
    "R20_SINGLE_ASSET_EQUITY_RATIO": 0.30,
    "R20_MAX_SINGLE_ASSET_MARGIN_USDT": 600.0,
    "R20_MIN_LEVERAGE": 2.0,
    "R20_MAX_LEVERAGE": 5.0,
    "R20_RISK_PER_TRADE_RATIO": 0.02,
    "R20_MIN_RISK_REWARD": 2.0,
    "R20_MIN_ENTRY_CONFIDENCE": 80.0,
    "R20_MAX_DAILY_LOSS_USDT": 150.0,
    "R20_DAILY_LOSS_EQUITY_RATIO": 0.05,
    "R20_TIME_STOP_HOURS": 8.0,
    "R20_TIME_STOP_ATR_BAND": 0.15,
    "R20_STOP_COOLDOWN_MINUTES": 30,
    "R20_MAX_SCALE_IN_COUNT": 1,
    "R20_MIN_SCALE_IN_PROFIT_RATIO": 0.008,
    "R20_MIN_SCALE_IN_CONFIDENCE": 75.0,
}

RISK_ENV_KEYS = tuple(DEFAULTS.keys())


def effective_max_positions(pool_size: int) -> int:
    """并发持仓上限 = min(配置值或池容量, 池容量)；同向上限再钳制不超过总仓上限。"""
    pool = max(int(pool_size or 0), 1)
    cap = MAX_CONCURRENT_POSITIONS_CAP
    total = pool if cap <= 0 else max(1, min(cap, pool))
    same = max(1, min(MAX_SAME_DIRECTION_POSITIONS, total))
    return total, same
