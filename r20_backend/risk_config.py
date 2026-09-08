"""后台「风控管理页」的参数 schema、校验与读写辅助。

单一事实源在 scripts/risk_constants.py（执行层 import 时读取 .env 生效）；
本模块只负责：给前端渲染用的元数据、写入前的服务端校验、以及当前生效值读取。
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from scripts.risk_constants import DEFAULTS, RISK_ENV_KEYS

# 分组元数据（前端按此顺序渲染卡片）
GROUPS = [
    {"id": "exposure", "label": "仓位与敞口", "label_en": "Position & Exposure",
     "desc": "控制同时持有多少仓、单边敞口多大、单个标的能吃掉多少保证金。"},
    {"id": "per_trade", "label": "单笔风险门禁", "label_en": "Per-Trade Risk Gates",
     "desc": "每一笔开仓在下单前必须通过的最低质量门槛。"},
    {"id": "stop_loss", "label": "止损与熔断", "label_en": "Stops & Circuit Breaker",
     "desc": "亏损兜底与时间兜底：日亏熔断、最长持仓时间、止损后冷静期。"},
    {"id": "pyramiding", "label": "顺势金字塔加仓", "label_en": "Pyramiding Scale-In",
     "desc": "浮盈加仓的三重门禁：次数、底仓浮盈、AI 置信度。设为 0 次即彻底禁止加仓。"},
]

# 参数 schema：key = .env 键（与执行层一致）；value 为渲染与校验元数据。
# 所有 min/max/default 均为「原生值」（比例类为小数），前端按 display_scale 换算显示。
_PARAMS: list[dict[str, Any]] = [
    # ── 组1 仓位与敞口 ──
    {"key": "R20_MAX_CONCURRENT_POSITIONS", "group": "exposure",
     "label": "最高持仓数（总仓位上限）", "label_en": "Max Concurrent Positions",
     "desc": "全系统同时持有的仓位总数上限。0 = 自动跟随标的池容量；超过池容量的配置会被钳制到池容量。",
     "type": "int", "min": 0, "max": 50, "step": 1, "unit": "仓", "display_scale": 1},
    {"key": "R20_MAX_SAME_DIRECTION_POSITIONS", "group": "exposure",
     "label": "同向持仓上限（单边敞口）", "label_en": "Max Same-Direction Positions",
     "desc": "纯多单或纯空单各自的笔数上限，防高相关标的同向堆叠踩踏。不会超过总仓位上限。",
     "type": "int", "min": 1, "max": 50, "step": 1, "unit": "仓", "display_scale": 1},
    {"key": "R20_MAX_MARGIN_EQUITY_RATIO", "group": "exposure",
     "label": "单笔保证金占比（硬顶）", "label_en": "Max Margin per Order (% of equity)",
     "desc": "单笔下单占用保证金不得超过可用余额的这个比例，超出部分执行层直接砍掉。也是提示词中「强信号单笔保证金上限」的同口径值。",
     "type": "float", "min": 0.01, "max": 1.0, "step": 0.01, "unit": "%", "display_scale": 100},
    {"key": "R20_SINGLE_ASSET_EQUITY_RATIO", "group": "exposure",
     "label": "单标的累计保证金占比", "label_en": "Single-Asset Margin Cap (% of equity)",
     "desc": "同一标的（含金字塔加仓后）累计占用保证金占可用余额的上限。",
     "type": "float", "min": 0.01, "max": 1.0, "step": 0.01, "unit": "%", "display_scale": 100},
    {"key": "R20_MAX_SINGLE_ASSET_MARGIN_USDT", "group": "exposure",
     "label": "单标的保证金绝对封顶", "label_en": "Single-Asset Margin Hard Cap",
     "desc": "单标的累计保证金的绝对金额封顶（USDT）。实际生效取 min(本值, 余额×占比上限)，小资金账户自动收紧。",
     "type": "float", "min": 1.0, "max": 100000.0, "step": 10.0, "unit": "USDT", "display_scale": 1},
    {"key": "R20_MAX_LEVERAGE", "group": "exposure",
     "label": "单笔杠杆上限", "label_en": "Max Leverage",
     "desc": "AI 自主裁决杠杆，但执行层强制钳制不超过此倍数。",
     "type": "float", "min": 1.0, "max": 20.0, "step": 1.0, "unit": "x", "display_scale": 1},
    # ── 组2 单笔风险门禁 ──
    {"key": "R20_RISK_PER_TRADE_RATIO", "group": "per_trade",
     "label": "单笔风险额占比（1R）", "label_en": "Risk per Trade (% of equity)",
     "desc": "单笔最大可承受亏损（1R）占可用余额的比例，与标的池内绝对风险额取小。",
     "type": "float", "min": 0.001, "max": 0.2, "step": 0.001, "unit": "%", "display_scale": 100},
    {"key": "R20_MIN_RISK_REWARD", "group": "per_trade",
     "label": "最小盈亏比 R:R 硬底线", "label_en": "Minimum R:R Ratio",
     "desc": "盈亏比低于该值的开仓报价会被核心风控物理拦截（Fail-Closed），无论来自 AI 还是人工。",
     "type": "float", "min": 1.0, "max": 10.0, "step": 0.1, "unit": ": 1", "display_scale": 1},
    {"key": "R20_MIN_ENTRY_CONFIDENCE", "group": "per_trade",
     "label": "新开仓最低 AI 置信度", "label_en": "Min Entry Confidence",
     "desc": "AI 裁决置信度低于该百分比时禁止新开仓（金字塔加仓另有独立门禁）。",
     "type": "float", "min": 0.0, "max": 100.0, "step": 1.0, "unit": "%", "display_scale": 1},
    # ── 组3 止损与熔断 ──
    {"key": "R20_DAILY_LOSS_EQUITY_RATIO", "group": "stop_loss",
     "label": "日亏熔断比例（按余额）", "label_en": "Daily Loss Circuit Breaker (% of equity)",
     "desc": "当日累计已实现亏损达到可用余额的这个比例时，本周期停止新开仓。",
     "type": "float", "min": 0.005, "max": 0.5, "step": 0.005, "unit": "%", "display_scale": 100},
    {"key": "R20_MAX_DAILY_LOSS_USDT", "group": "stop_loss",
     "label": "日亏熔断绝对封顶", "label_en": "Daily Loss Hard Cap",
     "desc": "熔断线的绝对金额封顶（USDT）。实际生效取 min(本值, 余额×比例)，小资金账户自动收紧。",
     "type": "float", "min": 1.0, "max": 1000000.0, "step": 10.0, "unit": "USDT", "display_scale": 1},
    {"key": "R20_TIME_STOP_HOURS", "group": "stop_loss",
     "label": "最长持仓时间（时间止损）", "label_en": "Max Hold Time (Time Stop)",
     "desc": "持仓超过该时长且波幅仍不足横盘带宽时，主动平仓释放保证金与仓位配比。",
     "type": "float", "min": 0.5, "max": 168.0, "step": 0.5, "unit": "小时", "display_scale": 1},
    {"key": "R20_TIME_STOP_ATR_BAND", "group": "stop_loss",
     "label": "时间止损横盘带宽", "label_en": "Time-Stop ATR Band",
     "desc": "浮盈绝对值小于「该系数 × 1H ATR」才判定为无突破横盘；调大更易触发时间止损。",
     "type": "float", "min": 0.0, "max": 2.0, "step": 0.05, "unit": "× ATR", "display_scale": 1},
    {"key": "R20_STOP_COOLDOWN_MINUTES", "group": "stop_loss",
     "label": "止损后冷静期", "label_en": "Post-Stop Cooldown",
     "desc": "某标的止损出局后，同标的同方向在该分钟内禁止再次开仓，防情绪化反手与连续磨损。",
     "type": "int", "min": 0, "max": 1440, "step": 5, "unit": "分钟", "display_scale": 1},
    # ── 组4 顺势金字塔加仓 ──
    {"key": "R20_MAX_SCALE_IN_COUNT", "group": "pyramiding",
     "label": "单标的最大加仓次数", "label_en": "Max Scale-In Count",
     "desc": "每个标的允许的顺势浮盈加仓次数；0 = 彻底禁止加仓（只允许底仓）。",
     "type": "int", "min": 0, "max": 10, "step": 1, "unit": "次", "display_scale": 1},
    {"key": "R20_MIN_SCALE_IN_PROFIT_RATIO", "group": "pyramiding",
     "label": "加仓最小底仓浮盈率", "label_en": "Min Base-Position Unrealized ROI",
     "desc": "底仓浮盈达到该比例（保本之上）才允许顺势追加，绝不浮盈外加仓。",
     "type": "float", "min": 0.0, "max": 0.2, "step": 0.001, "unit": "%", "display_scale": 100},
    {"key": "R20_MIN_SCALE_IN_CONFIDENCE", "group": "pyramiding",
     "label": "加仓最低 AI 置信度", "label_en": "Min Scale-In Confidence",
     "desc": "金字塔加仓需要达到的 AI 置信度门槛，通常应高于新开仓门禁。",
     "type": "float", "min": 0.0, "max": 100.0, "step": 1.0, "unit": "%", "display_scale": 1},
]

_INDEX = {p["key"]: p for p in _PARAMS}

# ── 优质预设套件（一键应用；values 为原生单位，必须通过本 schema 校验） ──
SUITES: list[dict[str, Any]] = [
    {"id": "conservative", "name": "🛡️ 稳健防守", "tagline": "本金安全绝对优先",
     "desc": "适合新账户、小资金或高波动恶劣行情：仓位少而精、置信度与盈亏比门槛拉高、杠杆压至 3x、"
             "彻底禁止金字塔加仓、日亏 3% 即熔断。牺牲部分机会换极低回撤。",
     "values": {
         "R20_MAX_CONCURRENT_POSITIONS": 4, "R20_MAX_SAME_DIRECTION_POSITIONS": 2,
         "R20_MAX_MARGIN_EQUITY_RATIO": 0.10, "R20_SINGLE_ASSET_EQUITY_RATIO": 0.20,
         "R20_MAX_SINGLE_ASSET_MARGIN_USDT": 300.0, "R20_MAX_LEVERAGE": 3.0,
         "R20_RISK_PER_TRADE_RATIO": 0.01, "R20_MIN_RISK_REWARD": 2.5, "R20_MIN_ENTRY_CONFIDENCE": 85.0,
         "R20_DAILY_LOSS_EQUITY_RATIO": 0.03, "R20_MAX_DAILY_LOSS_USDT": 100.0,
         "R20_TIME_STOP_HOURS": 12.0, "R20_TIME_STOP_ATR_BAND": 0.10, "R20_STOP_COOLDOWN_MINUTES": 60,
         "R20_MAX_SCALE_IN_COUNT": 0, "R20_MIN_SCALE_IN_PROFIT_RATIO": 0.012, "R20_MIN_SCALE_IN_CONFIDENCE": 85.0,
     }},
    {"id": "balanced", "name": "⚖️ 均衡波段", "tagline": "推荐默认 · 攻守兼备",
     "desc": "系统出厂基线：同向 3 仓防共振踩踏、单笔保证金 20% 硬顶、2% 单笔风险、R:R 底线 2.0、"
             "8 小时时间止损释放配比、允许 1 次严格浮盈加仓。适合日常 1H~4H 波段运营。",
     "values": {key: DEFAULTS[key] for key in DEFAULTS}},
    {"id": "aggressive", "name": "🚀 进取猎手", "tagline": "单边趋势市 · 经验账户专用",
     "desc": "适合明确单边主升/主跌浪与老手账户：同向放宽至 4 仓吃足趋势、置信度门禁降至 72% 抢先上车、"
             "允许 2 次金字塔加仓放大盈利单、持仓时间放宽至 16 小时。回撤与熔断线同步放大，风险自负。",
     "values": {
         "R20_MAX_CONCURRENT_POSITIONS": 0, "R20_MAX_SAME_DIRECTION_POSITIONS": 4,
         "R20_MAX_MARGIN_EQUITY_RATIO": 0.25, "R20_SINGLE_ASSET_EQUITY_RATIO": 0.40,
         "R20_MAX_SINGLE_ASSET_MARGIN_USDT": 800.0, "R20_MAX_LEVERAGE": 7.0,
         "R20_RISK_PER_TRADE_RATIO": 0.03, "R20_MIN_RISK_REWARD": 2.0, "R20_MIN_ENTRY_CONFIDENCE": 72.0,
         "R20_DAILY_LOSS_EQUITY_RATIO": 0.08, "R20_MAX_DAILY_LOSS_USDT": 300.0,
         "R20_TIME_STOP_HOURS": 16.0, "R20_TIME_STOP_ATR_BAND": 0.20, "R20_STOP_COOLDOWN_MINUTES": 15,
         "R20_MAX_SCALE_IN_COUNT": 2, "R20_MIN_SCALE_IN_PROFIT_RATIO": 0.006, "R20_MIN_SCALE_IN_CONFIDENCE": 70.0,
     }},
]

# 套件自检：键必须全在 schema 内、值必须越界为零、同向≤总仓（显式配置时）
for _s in SUITES:
    for _k, _v in _s["values"].items():
        _p = _INDEX[_k]
        assert _p["min"] <= _v <= _p["max"], f"suite {_s['id']} 越界: {_k}={_v}"
    _total, _same = _s["values"].get("R20_MAX_CONCURRENT_POSITIONS", 0), _s["values"].get("R20_MAX_SAME_DIRECTION_POSITIONS", 3)
    assert _total <= 0 or _same <= _total, f"suite {_s['id']} 同向>总仓"
assert {s["id"] for s in SUITES} == {"conservative", "balanced", "aggressive"}


def suite_values(suite_id: str) -> dict[str, float | int]:
    for s in SUITES:
        if s["id"] == suite_id:
            return dict(s["values"])
    raise ValueError(f"未知风控预设套件: {suite_id}")

# 一致性自检：schema 必须与执行层 DEFAULTS 一一对应，防止悄悄漂移
assert set(_INDEX) == set(DEFAULTS), (
    f"risk schema drift: schema={sorted(set(_INDEX) - set(DEFAULTS))} defaults={sorted(set(DEFAULTS) - set(_INDEX))}")
for _p in _PARAMS:
    _p["default"] = DEFAULTS[_p["key"]]


def schema() -> dict[str, Any]:
    return {"groups": GROUPS, "params": _PARAMS}


def current_values() -> dict[str, float | int]:
    """当前生效值（原生单位）：读进程环境变量（update_env 会同步刷新），缺省回退默认值。"""
    out: dict[str, float | int] = {}
    for p in _PARAMS:
        raw = os.environ.get(p["key"], "")
        try:
            out[p["key"]] = int(float(raw)) if p["type"] == "int" else float(raw)
        except (TypeError, ValueError):
            out[p["key"]] = DEFAULTS[p["key"]]
    return out


def _coerce(param: dict[str, Any], value: Any) -> float | int:
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{param['label']}: 必须是数字，收到 {value!r}")
    if param["type"] == "int":
        num = int(round(num))
    else:
        num = round(num, 6)
    if num < param["min"] or num > param["max"]:
        disp_min, disp_max = param["min"] * param["display_scale"], param["max"] * param["display_scale"]
        raise ValueError(f"{param['label']}: 须在 {disp_min:g}~{disp_max:g} {param['unit']} 之间（收到 {num * param['display_scale']:g}）")
    return num


def normalize(values: Mapping[str, Any]) -> dict[str, str]:
    """校验前端提交的 {env_key: native_value}，返回可直接 update_env 的字符串映射。"""
    unknown = [k for k in values if k not in _INDEX]
    if unknown:
        raise ValueError(f"未知风控参数: {', '.join(sorted(unknown))}")
    parsed: dict[str, float | int] = {}
    errors: list[str] = []
    for key, value in values.items():
        param = _INDEX[key]
        try:
            parsed[key] = _coerce(param, value)
        except ValueError as exc:
            errors.append(str(exc))
    # 跨字段一致性：同向上限不应超过显式配置的总仓上限（0=自动 时由执行层按池容量钳制）
    total = parsed.get("R20_MAX_CONCURRENT_POSITIONS", current_values().get("R20_MAX_CONCURRENT_POSITIONS", 0))
    same = parsed.get("R20_MAX_SAME_DIRECTION_POSITIONS", current_values().get("R20_MAX_SAME_DIRECTION_POSITIONS", 3))
    if isinstance(total, (int, float)) and total > 0 and isinstance(same, (int, float)) and same > total:
        errors.append(f"同向持仓上限 ({same:g}) 不能高于最高持仓数 ({total:g})")
    if errors:
        raise ValueError("；".join(errors))
    return {k: str(v) for k, v in parsed.items()}


def reset_keys() -> list[str]:
    return list(RISK_ENV_KEYS)
