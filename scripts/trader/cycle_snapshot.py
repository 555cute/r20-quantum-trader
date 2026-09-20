"""周期快照的两段纯装配（B3 抽取第十七块）。

从 `scripts/ai_factor_trader.py::execute_portfolio` 搬出两段"只做数据装配"的长块。

## 1. `collect_pending_inst_ids` —— 外所挂单枚举与去重计数

原实现是 execute_portfolio 里一段 35 行的**裸循环**：遍历 `gate` / `binance` 两个
外所，把"仍然挂着、且是开仓方向（非 reduce_only）"的订单折成
`pending_inst_ids`，并按方向累计 `pending_long_count` / `pending_short_count`。

它嵌在 652 行的主流程里，阅读时会被误当成控制流；实际是一段自成单元的采集逻辑，
且含一个**易漏的过滤不变量**：只有 `side` 属于 `buy`/`sell` 且
`reduce_only` 不为真时才计入 —— 减仓/保护单不属入场生命周期管辖，
若被计入会让 `reserved_slot_count`（预占槽位）虚高，**该开的仓开不出来**。

## 2. `build_state_payload` —— 面板/巡检状态快照

原实现 25 行的手工字典拼装（含 17 个字段的逐项拷贝与四舍五入）。
搬出后字段清单集中一处，新增面板字段不必在 652 行里翻找。

## 边界说明（有意保守）

本模块**只搬这两块**。同一函数里另有三处"从订单对象推基础币种"的表达式
（约 L470 / L1437 / L2393），看似可合并，但**三者语义并不相同**：

- L470 与 L2393：`str(x.get("base") or "").upper() or inst.split("_")[0].split("-")[0].upper()`
  —— 取冒号前第一段；
- L1437：`str(x.get("base") or str(x.get("inst_id", "")).split("_")[0]).upper()`
  —— **没有第二级 `.split("-")[0]`**。

对 `BTC-USDT` 这种带连字符的 inst_id，L1437 会得到 `"BTC-USDT"`，
另两处会得到 `"BTC"`。三者**不等价**，合并即行为变更 —— 故不动。
本模块保留原样的推导顺序（含 `.split("_")[0].split("-")[0]`）。
"""
from __future__ import annotations

from typing import List


def broken_execution_venues(venues, environment, *, venue_registry,
                            venue_execution_ready) -> List[str]:
    """**执行闸开着却不可就绪**的所 ⇒ 凭证已死（第一百三十一刀）。

    为什么单独成函数：这类所会被 `venue_execution_ready` 否决，于是
    `fetch_other_venue_positions` 也跳过它，且返回 `ok=True` **无任何错误**
    ⇒ 它的持仓/挂单**不进**配额与敞口，而跨所笔数看起来完整。
    口径是"**读不出来**（不是没有仓）"，所以必须能**逐周期明说"未计入"**，
    且这条判据要能单元测试（此前内联在逐字门保护的段体里，改一处就要动差异表）。

    判据精确到"闸开着（本该能交易）却不可就绪"：registry 未登记 / 闸没开属于
    "结构性无该所"，不是本函数的范围。任何异常都**不猜**（跳过该所，不误报）。
    """
    out: List[str] = []
    for v in venues or ():
        try:
            _flag_on = bool(venue_registry.execution_open(v, environment))
            _ready = bool(venue_execution_ready(v, environment))
        except Exception:
            continue
        if _flag_on and not _ready:
            out.append(str(v))
    return out


def collect_pending_inst_ids(*, venues, venue_mode, broken_venues, venue_registry,
                             load_instruments, auth_markers, warn=None):
    """枚举外所挂单，返回 `(pending_inst_ids, pending_long_count, pending_short_count)`。

    逐字保留原实现的四条过滤：跳过已判死的所、跳过未开放执行的所、
    跳过非 dict 元素、跳过非开仓方向（`reduce_only` 为真或 `side` 不在 buy/sell）。

    `warn` 收到每所一条失败信息（原实现是直接 `print`）；调用点传
    `print` 以保持输出完全一致。传 `None` 表示静默（测试用）。

    参数全部注入，不在 import 期绑定任何门面对象 —— 见
    `r20_backend/README.md` §5：门面会被 `pin_baseline_risk_env()` 原地重载。
    """
    pending_inst_ids: set = set()
    pending_long_count = 0
    pending_short_count = 0

    for _gv in venues:
        try:
            if _gv in broken_venues:
                continue   # 回收侧已实证凭证死，不再逐标的空转（每轮进程级重探）
            if not (venue_mode and venue_registry.execution_open(_gv, venue_mode)):
                continue
            _gad = venue_registry.get_adapter(_gv, environment=venue_mode)
            if _gv == "binance":
                _grows = _gad.open_orders() or []
            else:
                _grows = []
                # 性能：标的池**读一次**即可。原实现在内层循环里每次迭代都
                # `load_instruments()`（一次磁盘读 + JSON 解析 + 逐项校验 + 重建列表），
                # 8 个标的就白读 8 次。提到循环外，语义不变（见测试）。
                _gpool = load_instruments()
                for _ins in _gpool:
                    _gb = str(_ins.get("instId") or "").split("-")[0].upper()
                    if _gb:
                        _grows.extend(_gad.list_open_orders(_gb) or [])
            for o in _grows:
                if not isinstance(o, dict):
                    continue
                _graw = o.get("raw") if isinstance(o.get("raw"), dict) else {}
                _gs = str(o.get("side") or _graw.get("side") or "").lower()
                if not _gs:
                    _sz_raw = o.get("size") if o.get("size") is not None else _graw.get("size")
                    if _sz_raw is None:
                        _sz_raw = o.get("amount") if o.get("amount") is not None else _graw.get("amount")
                    try:
                        if _sz_raw is not None and float(_sz_raw) != 0:
                            _gs = "buy" if float(_sz_raw) > 0 else "sell"
                    except (TypeError, ValueError):
                        pass
                _gro = (o.get("reduce_only") if o.get("reduce_only") is not None
                        else _graw.get("reduce_only"))
                if _gro is None:
                    _gro = (o.get("is_reduce_only") if o.get("is_reduce_only") is not None
                            else _graw.get("is_reduce_only"))
                if _gs not in ("buy", "sell") or _gro in (True, "true", "1"):
                    continue
                _ginst = str(o.get("inst_id") or o.get("contract")
                             or _graw.get("contract") or _graw.get("symbol") or "")
                _gbase = (str(o.get("base") or "").upper()
                          or _ginst.replace("_USDT", "").replace("USDT", "").split("-")[0].upper())
                if not _gbase:
                    continue
                pending_inst_ids.add(f"{_gbase}-USDT-SWAP")
                if _gs == "buy":
                    pending_long_count += 1
                else:
                    pending_short_count += 1
        except Exception as _gexc:                                    # noqa: BLE001
            if not any(m in str(_gexc) for m in auth_markers):
                if warn is not None:
                    warn(f"[周期快照] warn 外所 {_gv} 挂单枚举失败"
                         f"（去重计数从缺；对账器本期将据'实况未核验'**不释放任何预留**"
                         f"——第一百二十七刀起挂单侧也进该标志）: {str(_gexc)[:80]}")

    return pending_inst_ids, pending_long_count, pending_short_count


def build_state_payload(*, timestamp_full, active_pos_count, max_positions, long_count,
                        short_count, cb_active, cb_reason, executed_actions,
                        all_factors, evaluate_asset_signal):
    """装配写入 `trading_state.json` 的字典（面板与巡检的读取源）。

    逐字保留原实现的字段顺序与四舍五入（`round(..., 1)` / `round(..., 2)`）——
    面板按这些键取值，缺一个或改一次精度都会显示错。

    `evaluate_asset_signal` 注入：每个标的的 `(score, action, strat_tag, strat_desc)`
    由它现算，保证快照与执行用的是**同一次**信号求值口径。
    """
    payload = {
        "timestamp": timestamp_full,
        "active_positions_count": active_pos_count,
        "max_positions": max_positions,
        "long_count": long_count,
        "short_count": short_count,
        "circuit_breaker": {"active": cb_active, "reason": cb_reason},
        "executed_actions": executed_actions,
        "instruments": []
    }

    for f in all_factors:
        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        payload["instruments"].append({
            "name": f["name"],
            "instId": f["instId"],
            "type": f["type"],
            "price": f["price"],
            "rsi": round(f["rsi"], 1),
            "rsi_7": round(f.get("rsi_7", 50.0), 1),
            "vwap_bias": round(f.get("vwap_bias", 0.0), 2),
            "macd_hist": f.get("macd_hist", 0.0),
            "macd_accel": f.get("macd_accel", 0.0),
            "obv_flow": f.get("obv_flow", "NEUTRAL"),
            "bb_bandwidth": f.get("bb_bandwidth", 0.0),
            "vol_ratio": f.get("vol_ratio", 1.0),
            "market_regime": f.get("market_regime", "CHOP"),
            "structure_1h": f.get("structure_1h", "CHOP"),
            "trend_1h": "多头" if f.get("trend_1h_bullish") else "空头",
            "trend_4h": "多头" if f.get("trend_4h_bullish") else "空头",
            "score": score,
            "action": action,
            "strategy": strat_tag,
            "desc": strat_desc,
            "position": f["position"]
        })

    return payload
