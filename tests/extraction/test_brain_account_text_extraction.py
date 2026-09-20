"""`scripts/brain/account_text.py`（B3 第三十刀）回归。

## 这个测试在守什么

两段把**在途持仓**与**在途未成交挂单**渲染成给模型的文本。最要紧的不变量是
**"缺失 vs 空"的三态语义**：

| 入参 | 渲染 |
|---|---|
| `None` | `[MISSING_CONTEXT:...]` —— **上下文没给** |
| `[]` | 「当前无任何在途持仓敞口 (100% 现金空仓状态)」—— **确定空仓** |
| 非空 | 逐条列举 |

`None` 是"我不知道"，`[]` 是"确定没有"。**渲染成同一句话会让模型把"上下文缺失"
误读成"空仓"，进而放大仓位。**

## 为什么这两段此前几乎没有覆盖（如实记录）

`tests/llm/test_prompt_rendering_isolated.py` 用 `packages=[]`、且**从不传**
`active_positions_detail` / `pending_orders_detail` —— 所以两段的**主体逻辑
（逐条渲染）此前完全没有被任何测试执行过**。本刀借抽离把它们补上。

## 四处易错点（详见模块文档串）

1. 持仓利润描述**正反两分支**，且回撤分母不同（多头 `hwm-entry`、空头 `entry-lwm`）；
2. 挂单方向串 8 种组合，**`reduce_only` 与普通单的判定不对称**；
3. 价格 `""` 或字面 `"0"` → 市价单显示「市价」，否则 `--`；
4. `cTime` 毫秒转秒且**先 `or 0`**，`<=0` 显示 `--`。
"""

from __future__ import annotations

import ast
import copy
import datetime
import random
import unittest
from pathlib import Path

from scripts.brain.account_text import (
    build_pending_order_lines,
    build_position_lines,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "brain" / "account_text.py"
PROMPT = ROOT / "scripts" / "brain" / "prompt.py"
FACADE = ROOT / "scripts" / "ai_brain_trader.py"

TZ_BJ = datetime.timezone(datetime.timedelta(hours=8))


def _sf(x):
    """测试用 safe_float：与门面语义一致（坏值 → 0.0）。"""
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- legacy 实现


def _legacy_positions(active_positions_detail, safe_float):
    pos_lines = []
    if active_positions_detail and len(active_positions_detail) > 0:
        for p in active_positions_detail:
            inst_name = p.get('name') or p.get('instId')
            side = p.get('side') or p.get('posSide', 'long')
            is_long = "long" in str(side).lower()
            entry_px = safe_float(p.get('avgPx', 0))
            cur_px = safe_float(p.get('markPx') or p.get('lastPx') or entry_px)
            hwm = safe_float(p.get('highWaterMark', 0))
            lwm = safe_float(p.get('lowWaterMark', 0))
            tp_px = p.get('takeProfitPx', '--')
            stage_desc = p.get('stage_desc', '持有监控中')
            profit_desc = ""
            if is_long and hwm > entry_px and entry_px > 0:
                peak_gain_pct = round((hwm - entry_px) / entry_px * 100, 2)
                dd_from_peak = round((hwm - cur_px) / (hwm - entry_px) * 100, 1) if hwm > entry_px else 0.0
                profit_desc = f" | 曾最高到: {hwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"
            elif not is_long and lwm > 0 and lwm < entry_px and entry_px > 0:
                peak_gain_pct = round((entry_px - lwm) / entry_px * 100, 2)
                dd_from_peak = round((cur_px - lwm) / (entry_px - lwm) * 100, 1) if lwm < entry_px else 0.0
                profit_desc = f" | 曾最低到: {lwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"
            v_badge = f"[{str(p.get('venue', 'OKX')).upper()}] "
            pos_lines.append(
                f"- {v_badge}标的: {inst_name} | 方向: {side} {p.get('lever', p.get('leverage', '3'))}x | 开仓均价: {p.get('avgPx')} | 当前价: {cur_px} | 浮盈: {p.get('upl')} U (ROI: {round(safe_float(p.get('uplRatio')) * 100, 2)}%){profit_desc} | 动态止损线: {p.get('trailingStopPx', p.get('trailingSl', '--'))} | 目标止盈: {tp_px} | 状态: {stage_desc}"
            )
    else:
        pos_lines.append("[MISSING_CONTEXT:account_positions]" if active_positions_detail is None else "当前无任何在途持仓敞口 (100% 现金空仓状态)")
    return "\n".join(pos_lines)


def _legacy_pending(pending_orders_detail, tz_bj):
    pending_lines = []
    if pending_orders_detail and len(pending_orders_detail) > 0:
        for o in pending_orders_detail:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            side_raw = str(o.get("side", "")).lower()
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()
            if reduce_only:
                side_str = "市价平多" if (side_raw == "sell" and ord_type == "market") else ("限价平多" if side_raw == "sell" else ("市价平空" if ord_type == "market" else "限价平空"))
            else:
                side_str = "限价买多" if (side_raw == "buy" and ord_type != "market") else ("市价买多" if side_raw == "buy" else ("限价卖空" if ord_type != "market" else "市价卖空"))
            raw_px = str(o.get("px") or "").strip()
            px_val = raw_px if raw_px and raw_px != "0" else ("市价" if ord_type == "market" else "--")
            # aa6d4e0 归一（张数取绝对值 + 缺值给 `--`）：参照实现同步补齐，
            # 否则差分在 `sz=None` / 负张数上失去意义。既有取数口径未改。
            raw_sz = o.get("sz")
            try:
                sz_float = float(raw_sz or 0)
                sz_val = f"{abs(sz_float):g}" if sz_float != 0 else str(raw_sz if raw_sz is not None else "--")
            except (TypeError, ValueError):
                sz_val = str(raw_sz if raw_sz is not None else "--")
            ord_id = str(o.get("ordId", ""))
            attach_list = o.get("attachAlgoOrds", [])
            tp_sl_info = ""
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_p = att.get("tpTriggerPx", "--")
                sl_p = att.get("slTriggerPx", "--")
                tp_sl_info = f" | 附带云端止盈: {tp_p} / 止损: {sl_p}"
            pending_lines.append(
                f"- [挂单ID: {ord_id}] {inst_id} | {side_str} {sz_val}张 @ {px_val} | 挂单时间: {c_time_str}{tp_sl_info}"
            )
    else:
        pending_lines.append("[MISSING_CONTEXT:pending_orders]" if pending_orders_detail is None else "当前无任何在途未成交限价挂单 (挂单池为空)")
    return "\n".join(pending_lines)


# ------------------------------------------------------------------- 三态语义


class ThreeStateSemanticsTest(unittest.TestCase):
    """**核心**：`None`（我不知道）与 `[]`（确定没有）绝不能渲染成同一句话。"""

    def test_positions_none_is_missing_context(self):
        out = build_position_lines(None, safe_float=_sf)
        self.assertEqual(out, "[MISSING_CONTEXT:account_positions]")

    def test_positions_empty_is_flat_market(self):
        out = build_position_lines([], safe_float=_sf)
        self.assertIn("当前无任何在途持仓敞口", out)
        self.assertNotIn("MISSING_CONTEXT", out)

    def test_positions_two_states_differ(self):
        self.assertNotEqual(build_position_lines(None, safe_float=_sf),
                            build_position_lines([], safe_float=_sf),
                            "None 与 [] 必须是两种不同文本")

    def test_pending_none_is_missing_context(self):
        out = build_pending_order_lines(None, tz_bj=TZ_BJ, datetime=datetime)
        self.assertEqual(out, "[MISSING_CONTEXT:pending_orders]")

    def test_pending_empty_is_empty_pool(self):
        out = build_pending_order_lines([], tz_bj=TZ_BJ, datetime=datetime)
        self.assertIn("当前无任何在途未成交限价挂单", out)
        self.assertNotIn("MISSING_CONTEXT", out)

    def test_pending_two_states_differ(self):
        self.assertNotEqual(build_pending_order_lines(None, tz_bj=TZ_BJ, datetime=datetime),
                            build_pending_order_lines([], tz_bj=TZ_BJ, datetime=datetime))


# --------------------------------------------------------------------- 持仓


class LongProfitDescTest(unittest.TestCase):
    def _long(self, **over):
        p = {"name": "BTC", "instId": "BTC-USDT-SWAP", "side": "long",
             "avgPx": "100", "markPx": "120", "highWaterMark": "150",
             "lowWaterMark": "90", "upl": "20", "uplRatio": "0.2", "venue": "okx"}
        p.update(over)
        return p

    def test_peak_and_drawdown_rendered(self):
        """多头：极值浮盈与"从极值回撤"按 **hwm 基准** 算。"""
        out = build_position_lines([self._long()], safe_float=_sf)
        # peak = (150-100)/100*100 = 50.0
        # dd   = (150-120)/(150-100)*100 = 60.0
        self.assertIn("曾最高到: 150.0", out)
        self.assertIn("极值浮盈 +50.0%", out)
        self.assertIn("现已从极值回撤 60.0%", out)

    def test_no_peak_when_current_at_high(self):
        out = build_position_lines([self._long(markPx="150")], safe_float=_sf)
        self.assertIn("回撤 0.0%", out)

    def test_hwm_not_above_entry_omits_desc(self):
        """`hwm <= entry` → 不输出利润描述（三重合条件之一）。"""
        out = build_position_lines([self._long(highWaterMark="100")], safe_float=_sf)
        self.assertNotIn("曾最高到", out)

    def test_entry_zero_omits_desc(self):
        """`entry_px <= 0` → 不输出（避免除零）。"""
        out = build_position_lines([self._long(avgPx="0")], safe_float=_sf)
        self.assertNotIn("曾最高到", out)

    def test_short_does_not_use_long_branch(self):
        out = build_position_lines([self._long(side="short")], safe_float=_sf)
        self.assertNotIn("曾最高到", out)


class ShortProfitDescTest(unittest.TestCase):
    def _short(self, **over):
        p = {"name": "ETH", "instId": "ETH-USDT-SWAP", "side": "short",
             "avgPx": "100", "markPx": "80", "highWaterMark": "110",
             "lowWaterMark": "70", "upl": "20", "uplRatio": "0.2", "venue": "gate"}
        p.update(over)
        return p

    def test_trough_and_drawdown_use_lwm_basis(self):
        """空头：**最低价**与回撤按 **`entry - lwm` 基准**（与多头分母不同）。"""
        out = build_position_lines([self._short()], safe_float=_sf)
        # peak = (100-70)/100*100 = 30.0
        # dd   = (80-70)/(100-70)*100 = 33.3   ← 分母是 entry-lwm 而不是 lwm
        self.assertIn("曾最低到: 70.0", out)
        self.assertIn("极值浮盈 +30.0%", out)
        self.assertIn("现已从极值回撤 33.3%", out)

    def test_lwm_zero_omits(self):
        out = build_position_lines([self._short(lowWaterMark="0")], safe_float=_sf)
        self.assertNotIn("曾最低到", out)

    def test_lwm_not_below_entry_omits(self):
        out = build_position_lines([self._short(lowWaterMark="120")], safe_float=_sf)
        self.assertNotIn("曾最低到", out)

    def test_long_and_short_drawdown_denominators_differ(self):
        """同参数下两个方向给的"极值回撤"不是同一个数（分母不同）。"""
        lo = build_position_lines([self._long_like()], safe_float=_sf)
        sh = build_position_lines([self._short()], safe_float=_sf)
        self.assertNotIn("回撤 33.3%", lo)

    def _long_like(self):
        return {"name": "ETH", "instId": "ETH-USDT-SWAP", "side": "long",
                "avgPx": "100", "markPx": "80", "highWaterMark": "110",
                "lowWaterMark": "70", "upl": "0", "uplRatio": "0", "venue": "gate"}


class PositionFieldFallbacksTest(unittest.TestCase):
    def test_name_falls_back_to_inst_id(self):
        out = build_position_lines([{"instId": "SOL-USDT-SWAP", "side": "long"}],
                                   safe_float=_sf)
        self.assertIn("SOL-USDT-SWAP", out)

    def test_side_falls_back_to_pos_side(self):
        out = build_position_lines([{"instId": "X", "posSide": "long"}], safe_float=_sf)
        self.assertIn("方向: long", out)

    def test_side_default_is_long(self):
        out = build_position_lines([{"instId": "X"}], safe_float=_sf)
        self.assertIn("方向: long", out)

    def test_lever_falls_back_to_leverage(self):
        out = build_position_lines([{"instId": "X", "leverage": "7"}], safe_float=_sf)
        self.assertIn("7x", out)

    def test_lever_default_is_3(self):
        out = build_position_lines([{"instId": "X"}], safe_float=_sf)
        self.assertIn("3x", out)

    def test_venue_upper(self):
        out = build_position_lines([{"instId": "X", "venue": "okx"}], safe_float=_sf)
        self.assertIn("[OKX]", out)

    def test_venue_default_okx(self):
        out = build_position_lines([{"instId": "X"}], safe_float=_sf)
        self.assertIn("[OKX]", out)

    def test_trailing_stop_falls_back(self):
        out = build_position_lines([{"instId": "X", "trailingSl": "55"}], safe_float=_sf)
        self.assertIn("动态止损线: 55", out)

    def test_tp_default(self):
        out = build_position_lines([{"instId": "X"}], safe_float=_sf)
        self.assertIn("目标止盈: --", out)

    def test_stage_desc_default(self):
        out = build_position_lines([{"instId": "X"}], safe_float=_sf)
        self.assertIn("状态: 持有监控中", out)

    def test_cur_px_falls_back_to_last_px_then_entry(self):
        out = build_position_lines([{"instId": "X", "avgPx": "10", "lastPx": "11"}],
                                   safe_float=_sf)
        self.assertIn("当前价: 11.0", out)

    def test_bad_upl_ratio_becomes_zero(self):
        out = build_position_lines([{"instId": "X", "uplRatio": "abc"}], safe_float=_sf)
        self.assertIn("ROI: 0.0%", out)

    def test_multiple_positions_one_line_each(self):
        out = build_position_lines([{"instId": "A"}, {"instId": "B"}, {"instId": "C"}],
                                   safe_float=_sf)
        self.assertEqual(len(out.split("\n")), 3)


# --------------------------------------------------------------------- 挂单


class PendingSideStringTest(unittest.TestCase):
    """8 种 `reduce_only` × 买卖 × 限价/市价 组合 —— **判定不对称**。"""

    def _o(self, side, reduce_only, ord_type="limit", **over):
        o = {"instId": "BTC-USDT-SWAP", "side": side, "reduceOnly": reduce_only,
             "ordType": ord_type, "px": "100", "sz": "5", "ordId": "O1"}
        o.update(over)
        return o

    def _line(self, o):
        return build_pending_order_lines([o], tz_bj=TZ_BJ, datetime=datetime)

    def test_reduce_only_sell_market(self):
        self.assertIn("市价平多", self._line(self._o("sell", True, "market")))

    def test_reduce_only_sell_limit(self):
        self.assertIn("限价平多", self._line(self._o("sell", True, "limit")))

    def test_reduce_only_buy_market(self):
        self.assertIn("市价平空", self._line(self._o("buy", True, "market")))

    def test_reduce_only_buy_limit(self):
        self.assertIn("限价平空", self._line(self._o("buy", True, "limit")))

    def test_normal_buy_limit(self):
        self.assertIn("限价买多", self._line(self._o("buy", False, "limit")))

    def test_normal_buy_market(self):
        self.assertIn("市价买多", self._line(self._o("buy", False, "market")))

    def test_normal_sell_limit(self):
        self.assertIn("限价卖空", self._line(self._o("sell", False, "limit")))

    def test_normal_sell_market(self):
        self.assertIn("市价卖空", self._line(self._o("sell", False, "market")))

    def test_reduce_only_string_true_is_recognized(self):
        self.assertIn("市价平多", self._line(self._o("sell", "true", "market")))

    def test_reduce_only_uppercase_true(self):
        self.assertIn("市价平多", self._line(self._o("sell", "TRUE", "market")))

    def test_reduce_only_absent_is_false(self):
        o = {"instId": "X", "side": "buy", "ordType": "limit", "px": "1"}
        self.assertIn("限价买多", self._line(o))

    def test_side_case_insensitive(self):
        self.assertIn("限价买多", self._line(self._o("BUY", False, "limit")))

    def test_ord_type_default_is_limit(self):
        o = {"instId": "X", "side": "buy", "px": "1"}
        self.assertIn("限价买多", self._line(o))


class PendingPriceDisplayTest(unittest.TestCase):
    def _line(self, **over):
        o = {"instId": "X", "side": "buy", "ordType": "limit", "sz": "1"}
        o.update(over)
        return build_pending_order_lines([o], tz_bj=TZ_BJ, datetime=datetime)

    def test_normal_price_shown(self):
        self.assertIn("@ 100.5", self._line(px="100.5"))

    def test_empty_px_limit_shows_dash(self):
        self.assertIn("@ --", self._line(px=""))

    def test_missing_px_limit_shows_dash(self):
        self.assertIn("@ --", self._line())

    def test_none_px_limit_shows_dash(self):
        self.assertIn("@ --", self._line(px=None))

    def test_zero_px_limit_shows_dash(self):
        """字面 `"0"` 视同未填 —— 限价单给 `--`。"""
        self.assertIn("@ --", self._line(px="0"))

    def test_zero_px_market_shows_market(self):
        self.assertIn("@ 市价", self._line(px="0", ordType="market"))

    def test_empty_px_market_shows_market(self):
        self.assertIn("@ 市价", self._line(px="", ordType="market"))

    def test_whitespace_px_stripped(self):
        self.assertIn("@ --", self._line(px="   "))

    def test_sz_default(self):
        """`sz` 缺失/空 → `--`。

        ⚠️ 这里**不能**传 `None`：`str(o.get("sz", "--"))` 对 `None` 得到字面
        `"None"`（`dict.get` 只在**键不存在**时用默认值）。我第一版传了 None
        于是期望落空 —— 是我对 `get` 的语义想当然了。
        真正触发默认的是**键不存在**（用 `o.pop` 去掉）或空串。
        """
        o = {"instId": "X", "side": "buy", "px": "1"}   # 无 sz 键
        out = build_pending_order_lines([o], tz_bj=TZ_BJ, datetime=datetime)
        self.assertIn("--张", out)

    def test_sz_none_renders_dash(self):
        """`sz=None` 渲染成 `--`（**旧行为是字面 `None`**，见下）。

        aa6d4e0（修复负数张数泄漏）把 sz 归一为：能转数 → `abs()` 后 `:g`；
        否则 `--`。因此 `sz=None`（键存在但值为空）与「键缺失」现在**同解**，
        而旧实现走 `str(o.get("sz", "--"))` 得到字面 `"None"` —— 那是渲染 bug，
        会让主脑看到 "None张" 这种噪音。本用例的方向是**钉住修复后行为**。
        """
        self.assertIn("--张", self._line(sz=None))
        self.assertNotIn("None张", self._line(sz=None))

    def test_sz_negative_renders_absolute(self):
        """带符号张数必须取绝对值：Gate 用「正多负空」，负号泄漏到提示词会让
        主脑把「3 张空」读成「-3 张多」。这是 aa6d4e0 修的真雷，补钉。"""
        self.assertIn("3张", self._line(sz="-3"))
        self.assertIn("2.5张", self._line(sz=-2.5))
        self.assertIn("3张", self._line(sz="3"))
        # 非数字保持原样（不臆造 `--`，也不必抛）
        self.assertIn("abc张", self._line(sz="abc"))
        # 零是"确实 0 张"，不是缺值
        self.assertIn("0张", self._line(sz="0"))

    def test_ord_id_default_empty(self):
        self.assertIn("[挂单ID: ]", self._line())


class PendingTimeTest(unittest.TestCase):
    def _line(self, cTime):
        o = {"instId": "X", "side": "buy", "px": "1", "cTime": cTime}
        return build_pending_order_lines([o], tz_bj=TZ_BJ, datetime=datetime)

    def test_millis_converted_to_seconds_bj(self):
        # 2026-09-14 12:00:00 UTC == 20:00:00 北京
        ms = 1789387200000
        out = self._line(ms)
        expected = datetime.datetime.fromtimestamp(ms / 1000.0, tz=TZ_BJ).strftime("%Y-%m-%d %H:%M:%S")
        self.assertIn(expected, out)

    def test_zero_shows_dash(self):
        self.assertIn("挂单时间: --", self._line(0))

    def test_none_shows_dash(self):
        self.assertIn("挂单时间: --", self._line(None))

    def test_empty_string_shows_dash(self):
        """`"" or 0` → 0 → `--`（`or` 兜住空串）。"""
        self.assertIn("挂单时间: --", self._line(""))

    def test_negative_shows_dash(self):
        self.assertIn("挂单时间: --", self._line(-5))


class PendingAttachAlgoTest(unittest.TestCase):
    def _line(self, attach):
        o = {"instId": "X", "side": "buy", "px": "1", "attachAlgoOrds": attach}
        return build_pending_order_lines([o], tz_bj=TZ_BJ, datetime=datetime)

    def test_first_attach_rendered(self):
        out = self._line([{"tpTriggerPx": "9", "slTriggerPx": "5"}])
        self.assertIn("附带云端止盈: 9 / 止损: 5", out)

    def test_only_first_attach_used(self):
        out = self._line([{"tpTriggerPx": "9", "slTriggerPx": "5"},
                          {"tpTriggerPx": "999", "slTriggerPx": "888"}])
        self.assertIn("止盈: 9", out)
        self.assertNotIn("999", out)

    def test_empty_attach_list_no_suffix(self):
        self.assertNotIn("附带云端止盈", self._line([]))

    def test_missing_attach_key_no_suffix(self):
        self.assertNotIn("附带云端止盈", self._line(None))

    def test_attach_defaults(self):
        out = self._line([{}])
        self.assertIn("附带云端止盈: -- / 止损: --", out)

    def test_multiple_orders_one_line_each(self):
        o1 = {"instId": "A", "side": "buy", "px": "1"}
        o2 = {"instId": "B", "side": "sell", "px": "2"}
        out = build_pending_order_lines([o1, o2], tz_bj=TZ_BJ, datetime=datetime)
        self.assertEqual(len(out.split("\n")), 2)


# ------------------------------------------------------------------- 差分


class RandomParityTest(unittest.TestCase):
    """与搬走前内联实现的**大差分**。"""

    SIDES = ["buy", "sell", "BUY", "SELL", "", "x"]
    TYPES = ["limit", "market", "LIMIT", "", None]
    ROF = ["true", "false", "TRUE", "", None, True, False]
    PX = ["", "0", "0.0", "100", "  100  ", None, "abc"]
    POS_SIDES = ["long", "short", "LONG", "buy", "sell", "", None]

    #: ⚠️ **文档化差异**（第一百一十九刀，2026-09-20）：对拍前把"键存在但值为 None"
    #: 按**缺失**处理。
    #:
    #: 搬到前的实现用 `p.get(k, 默认)` —— 键存在但值为 None 时**回退不生效**，
    #: 于是提示词把字面量 `None` 喂给模型（真机实测 `data/dashboard_last_good.json`
    #: 的 binance UNI 行：`trailingStopPx: None` 而 `trailingSl: 9.025`、
    #: `takeProfitPx: None` 而 `exchangeTp: 8.365`、`stage_desc: None` 而
    #: `stageDesc: '云端双腿防护中'` ⇒ 模型被告知"无止损/无止盈/状态 None"，
    #: 而交易所那笔空仓**确实挂着**云端双腿）。
    #:
    #: 新实现一律走 `or` 链回退到真实来源，并追加 `保护:` 判据段。本差异**只**落在
    #: "值为 None"这一点上（且新行为更正确），故对拍时把 None 值键删掉、两边吃同一份
    #: 输入；新行为另由 `NoneFallbackTest` / `ProtectionVerdictPromptTest` 正向钉住。
    @staticmethod
    def _doc_delta_normalize(position):
        return {k: v for k, v in position.items() if v is not None}

    def test_positions_random_parity(self):
        rng = random.Random(30301)
        for i in range(12000):
            n = rng.randint(0, 3)
            positions = []
            for _ in range(n):
                positions.append(self._doc_delta_normalize({
                    "name": rng.choice(["BTC", None]), "instId": rng.choice(["B", "E"]),
                    "side": rng.choice(self.POS_SIDES),
                    "avgPx": rng.choice(["100", "0", None, "abc"]),
                    "markPx": rng.choice(["120", None, ""]),
                    "lastPx": rng.choice(["118", None]),
                    "highWaterMark": rng.choice(["150", "100", "0", None]),
                    "lowWaterMark": rng.choice(["70", "100", "0", None]),
                    "upl": rng.choice(["20", None]), "uplRatio": rng.choice(["0.2", "abc", None]),
                    "venue": rng.choice(["okx", "gate", ""]),
                    "lever": rng.choice(["3", None]), "leverage": rng.choice(["7", None]),
                    "trailingStopPx": rng.choice(["95", None]),
                    "trailingSl": rng.choice(["94", None]),
                    "takeProfitPx": rng.choice(["130", None]),
                }))
            arg = None if (n == 0 and rng.random() < 0.5) else positions
            got = build_position_lines(arg, safe_float=_sf)
            want = _legacy_positions(arg, _sf)
            self.assertEqual(got, want, f"第{i}组分叉")

    def test_pending_random_parity(self):
        rng = random.Random(30302)
        for i in range(12000):
            n = rng.randint(0, 3)
            orders = []
            for _ in range(n):
                orders.append({
                    "instId": rng.choice(["B", ""]), "side": rng.choice(self.SIDES),
                    "reduceOnly": rng.choice(self.ROF), "ordType": rng.choice(self.TYPES),
                    "px": rng.choice(self.PX), "sz": rng.choice(["5", None]),
                    "ordId": rng.choice(["O1", None]),
                    # ⚠️ 不喂 "abc"：`int(o.get("cTime",0) or 0)` 对非数字会抛
                    # ValueError，且**没有** try 包裹 —— 这是既有的**先声明**契约
                    # （cTime 来自交易所，必为数字或缺失）。我第一版喂了 "abc"，
                    # 结果是我自己的测试崩了，不是实现有问题。
                    "cTime": rng.choice([1789387200000, 0, -5, None, ""]),
                    "attachAlgoOrds": rng.choice([
                        [], None, [{}], [{"tpTriggerPx": "9", "slTriggerPx": "5"}],
                        [{"tpTriggerPx": "9"}, {"tpTriggerPx": "8"}],
                    ]),
                })
            arg = None if (n == 0 and rng.random() < 0.5) else orders
            got = build_pending_order_lines(arg, tz_bj=TZ_BJ, datetime=datetime)
            want = _legacy_pending(arg, TZ_BJ)
            self.assertEqual(got, want, f"第{i}组分叉")


# ------------------------------------------------------------------- 接线


class WiringTest(unittest.TestCase):
    def test_impl_in_submodule_not_in_prompt_body(self):
        prompt_src = PROMPT.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        for fn in ("build_position_lines", "build_pending_order_lines"):
            self.assertIn(f"def {fn}(", mod_src)
            self.assertNotIn(f"def {fn}(", prompt_src)

    def test_prompt_calls_the_builders(self):
        prompt_src = PROMPT.read_text(encoding="utf-8")
        self.assertIn("_build_position_lines(", prompt_src)
        self.assertIn("_build_pending_order_lines(", prompt_src)

    def test_facade_injects_both_builders(self):
        facade_src = FACADE.read_text(encoding="utf-8")
        self.assertIn("_build_position_lines=_build_position_lines", facade_src)
        self.assertIn("_build_pending_order_lines=_build_pending_order_lines", facade_src)

    def test_prompt_no_longer_contains_inline_loop_bodies(self):
        """门面函数体里不该再有那两段逐条渲染。"""
        tree = ast.parse(PROMPT.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "construct_full_market_prompt")
        src = ast.get_source_segment(PROMPT.read_text(encoding="utf-8"), fn)
        for gone in ("曾最高到", "极值浮盈", "市价平多", "限价买多",
                     "附带云端止盈", "挂单ID", "pos_lines.append", "pending_lines.append"):
            self.assertNotIn(gone, src, f"函数体仍残留 {gone!r}")

    def test_resolution_does_not_use_bare_g_subscript(self):
        """`_g[...]` 裸下标会让隔离 exec 的测试新增一项就 KeyError。

        本刀把它统一改成 `_resolve(...)`（带回退）。这条防止后人改回去。
        """
        src = PROMPT.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "construct_full_market_prompt")
        # ⚠️ 必须用 **AST** 判，不能用文本查：`_resolve` 的 docstring 里**故意**
        # 举了 `_g["NAME"]` 当"不要这么写"的反例。我第一版写了个手搓的注释剥离，
        # 结果**没处理 docstring**（docstring 行不以 # 开头）→ 仍然误报。
        # 正确做法：找 `_g[...]` 形式的 **Subscript 节点**，且下标是**字符串常量**。
        const_subscripts = []
        for node in ast.walk(fn):
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name) and node.value.id == "_g"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                const_subscripts.append((node.lineno, node.slice.value))
        self.assertEqual(const_subscripts, [],
                         f"仍有 _g[\"...\"] 裸下标: {const_subscripts}")
        # `_resolve` 必须存在且被使用
        self.assertIn("_resolve(", ast.get_source_segment(src, fn))

    def test_module_has_no_import_time_binding_of_injected_names(self):
        """模块级不得出现被注入的名字（否则会 shadow 掉测试缝）。"""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.asname or a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                top |= {a.asname or a.name for a in node.names}
        for injected in ("safe_float", "sl_atr_mult_for", "build_risk_budget_text"):
            self.assertNotIn(injected, top)

    def test_module_does_not_import_trader(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").startswith("scripts.ai_brain_trader"))
                self.assertFalse((node.module or "").startswith("scripts.ai_factor_trader"))


class NoneFallbackTest(unittest.TestCase):
    """第一百一十九刀：**键存在但值为 None** 时必须继续回退到真实来源。

    真机形状（`data/dashboard_last_good.json` 的 binance UNI 行）：
    `trailingStopPx=None` 而 `trailingSl=9.025`、`exchangeTp=8.365`、
    `stage_desc=None` 而 `stageDesc='云端双腿防护中'`。
    旧实现把 `None` / `--` 喂给模型 ⇒ 模型以为这笔空仓**没有止损止盈**。
    """

    LIVE_ROW = {"venue": "binance", "instId": "UNI-USDT-SWAP", "posSide": "short",
                "avgPx": "8.825", "markPx": "8.774", "upl": "4.17", "uplRatio": "0.2",
                "lever": "6", "trailingStopPx": None, "trailingSl": 9.025,
                "takeProfitPx": None, "exchangeTp": 8.365, "stage_desc": None,
                "stageDesc": "云端双腿防护中"}

    def _line(self, **over):
        row = dict(self.LIVE_ROW, **over)
        return build_position_lines([row], safe_float=_sf)

    def test_none_valued_stop_falls_back_to_real_stop(self):
        out = self._line()
        self.assertIn("动态止损线: 9.025", out)
        self.assertIn("目标止盈: 8.365", out)

    def test_none_valued_stage_falls_back_to_camel_key(self):
        self.assertIn("状态: 云端双腿防护中", self._line())

    def test_never_emits_the_literal_none(self):
        """提示词里出现字面量 `None` 就是 bug（模型会当字符串读）。"""
        self.assertNotIn("None", self._line())
        self.assertNotIn("None", self._line(trailingSl=None, exchangeSl=None,
                                           exchangeTp=None, stageDesc=None))

    def test_absent_everything_still_shows_dashes(self):
        out = self._line(trailingStopPx=None, trailingSl=None, takeProfitPx=None,
                         exchangeTp=None, stageDesc=None)
        self.assertIn("动态止损线: --", out)
        self.assertIn("目标止盈: --", out)
        self.assertIn("状态: 持有监控中", out)

    def test_exchange_sl_is_the_last_fallback(self):
        out = self._line(trailingStopPx=None, trailingSl=None, exchangeSl=9.5)
        self.assertIn("动态止损线: 9.5", out)


class ProtectionVerdictPromptTest(unittest.TestCase):
    """第一百一十八/十九刀：把**保护判据**如实告诉模型（此前完全没有这个信息）。"""

    def _line(self, **over):
        row = {"venue": "binance", "instId": "UNI-USDT-SWAP", "posSide": "short",
               "avgPx": "9", "markPx": "8.9", "upl": "1", "uplRatio": "0.1"}
        row.update(over)
        return build_position_lines([row], safe_float=_sf)

    def test_fully_protected_is_stated(self):
        out = self._line(protectionStatus="fully_protected",
                         protectionCoveragePct=100.0, protectionExpiry="never")
        self.assertIn("保护: 完全保护 100%", out)

    def test_trigger_price_type_is_disclosed(self):
        """第一百六十七刀：按什么价触发要如实说（mark 抗插针，last 易被插针打掉）。"""
        self.assertIn("止损按标记价触发", self._line(
            protectionStatus="fully_protected", protectionCoveragePct=100.0,
            protectionSlTriggerPxType="mark"))
        self.assertIn("止损按最新成交价触发", self._line(
            protectionStatus="fully_protected", protectionCoveragePct=100.0,
            protectionSlTriggerPxType="last"))

    def test_unreported_trigger_type_says_so_never_guesses(self):
        """腿在但类型未上报 ⇒ 明说"未上报"，**不得**默认成标记价。"""
        out = self._line(protectionStatus="fully_protected", protectionCoveragePct=100.0,
                         protectionSlTriggerPxType="unknown")
        self.assertIn("止损触发价类型未上报", out)
        self.assertNotIn("止损按标记价触发", out)

    def test_missing_trigger_type_field_adds_nothing(self):
        """字段缺（旧数据/无该类腿）⇒ 不提这一段，不编。"""
        out = self._line(protectionStatus="fully_protected", protectionCoveragePct=100.0)
        self.assertNotIn("触发", out)

    def test_unprotected_is_shouted_not_softened(self):
        out = self._line(protectionStatus="unprotected", protectionCoveragePct=0.0)
        self.assertIn("保护: ⚠️ 无活止损腿", out)

    def test_unknown_is_not_dressed_up_as_safe(self):
        out = self._line(protectionStatus="unknown")
        self.assertIn("保护状态不可判定", out)
        self.assertNotIn("完全保护", out)

    def test_expired_leg_is_marked(self):
        out = self._line(protectionStatus="partially_protected",
                         protectionCoveragePct=50.0, protectionExpiry="expired")
        self.assertIn("腿已过期", out)

    def test_full_size_stop_without_tp_is_not_called_insufficient(self):
        """⭐ 第一百二十一刀：修我自己上一刀造出的**自相矛盾文案**。

        OKX 判据里 `partially_protected` 含"只有满量止损、没有止盈"这一档
        （下行已全覆盖）⇒ 旧文案渲染成「部分保护（覆盖不足） 100%」，自相矛盾。
        """
        out = self._line(protectionStatus="partially_protected",
                         protectionCoveragePct=100.0)
        self.assertIn("止损满量但缺止盈腿", out)
        self.assertNotIn("覆盖不足", out)
        self.assertNotIn("100%", out, "覆盖率已由文字表达，不重复自相矛盾的百分比")

    def test_partial_without_coverage_number_claims_nothing_extra(self):
        """没有覆盖率数字时不得宣称"覆盖不足"（无证据不下结论）。"""
        out = self._line(protectionStatus="partially_protected")
        self.assertIn("部分保护（覆盖量未知）", out)
        self.assertNotIn("覆盖不足", out)

    def test_partial_coverage_states_the_real_percentage(self):
        out = self._line(protectionStatus="partially_protected",
                         protectionCoveragePct=40.0)
        self.assertIn("止损仅覆盖 40%", out)
        self.assertNotIn("覆盖不足", out)

    def test_no_verdict_no_segment(self):
        """没有判据就不写这一段（不假装）。"""
        self.assertNotIn("保护:", self._line())


if __name__ == "__main__":
    unittest.main()

# WRITE-PROBE
