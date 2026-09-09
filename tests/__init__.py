"""测试环境统一隔离。

生产 .env 可能带有用户经后台「风控管理页」应用的套件/自定义覆盖值（R20_* 风控键），
而全部引擎测试的断言基线是代码默认值。本模块在 discover 导入任何测试模块之前：
1) 先正常 import r20_backend.config，完成真实 .env 加载（OKX/LLM/QQ 等配置是测试需要的）；
2) 禁用后续 load_dotenv 回灌（refresh_settings/update_env 每次都会调用它）；
3) 用静态键表从进程环境剥离全部风控覆盖键——必须在 import risk_constants 之前完成，
   因为其常量在 import 时一次性绑定；
4) 再首次导入 risk_constants（此时得到代码默认基线），并断言静态键表与单一事实源一致。
"""
import os

import r20_backend.config as _config

_config.load_dotenv = lambda path: None

_RISK_KEYS_STATIC = (
    "R20_MAX_CONCURRENT_POSITIONS", "R20_MAX_SAME_DIRECTION_POSITIONS",
    "R20_MAX_MARGIN_EQUITY_RATIO", "R20_SINGLE_ASSET_EQUITY_RATIO",
    "R20_MAX_SINGLE_ASSET_MARGIN_USDT", "R20_MAX_LEVERAGE", "R20_MIN_LEVERAGE",
    "R20_RISK_PER_TRADE_RATIO", "R20_MIN_RISK_REWARD", "R20_MIN_ENTRY_CONFIDENCE",
    "R20_MAX_DAILY_LOSS_USDT", "R20_DAILY_LOSS_EQUITY_RATIO",
    "R20_TIME_STOP_HOURS", "R20_TIME_STOP_ATR_BAND", "R20_STOP_COOLDOWN_MINUTES",
    "R20_MAX_SCALE_IN_COUNT", "R20_MIN_SCALE_IN_PROFIT_RATIO", "R20_MIN_SCALE_IN_CONFIDENCE",
)
for _key in _RISK_KEYS_STATIC:
    os.environ.pop(_key, None)

from scripts.risk_constants import RISK_ENV_KEYS as _RISK_KEYS  # noqa: E402

assert set(_RISK_KEYS) == set(_RISK_KEYS_STATIC), (
    "tests/__init__.py 的静态风控键表与 scripts/risk_constants.RISK_ENV_KEYS 漂移，请同步")
