"""`r20_backend/dashboard_payload/position_view.py`（B3 第二十二刀）回归。

## 这个测试在守什么

`collect_position_rows` 把 OKX 的一行原始持仓，规范化成前端直接消费的持仓行。
它补齐 OKX **不直接给、必须推算**的字段：名义价值、保证金、ROI、价格变动。

四处易错点（本文件逐个钉住）：

| # | 细节 | 错了会怎样 |
|---|---|---|
| 1 | `pos == 0` 跳过 | 幽灵持仓条目（OKX 会返回已平未消解的零仓行） |
| 2 | 名义价值优先 `notionalUsd`，否则 `张数×面值×标记价`，标记价缺失退开仓价 | 名义价值算成 0 或明显偏小 |
| 3 | 保证金优先交易所 `imr`，否则 `名义/杠杆`；并输出 `marginSource` | 前端无法判断这个数字可不可信 |
| 4 | `uplRatio × 100` 直接当 ROI（不重算 `upl/margin`） | 口径偏差 |

## 差分口径

与搬走前内联实现逐字段对比，**用随机输入**驱动，覆盖 `-1`/`0`/缺失/字符串等
边界（OKX 字段常以字符串形式返回，或整段缺失）。

**文档化差异**（第一百二十三刀）：净持仓模式（`posSide` 非 long/short 或缺失）
时，展示用 side 由带符号持仓量归一 —— 见 `_doc_delta`。
"""

from __future__ import annotations

import ast
import random
import unittest
from pathlib import Path

from r20_backend.dashboard_payload.position_view import collect_position_rows

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "r20_backend" / "dashboard_cache.py"
MODULE = ROOT / "r20_backend" / "dashboard_payload" / "position_view.py"
COLLECT = ROOT / "r20_backend" / "dashboard_payload" / "collect.py"   # 第九十四刀：相位 1 现住此

INSTRUMENTS = [
    {"instId": "BTC-USDT-SWAP", "ctVal": 0.01},
    {"instId": "ETH-USDT-SWAP", "ctVal": 0.1},
]


def _legacy(pos_data, positions, trackers, *, load_instruments):
    """搬走前 update_cache_cycle 里的内联持仓遍历（逐字原样）。"""
    long_count = 0
    short_count = 0
    total_pos_upl = 0.0
    if isinstance(pos_data, list):
        for p in pos_data:
            pos_val = float(p.get("pos", 0.0) or 0.0)
            if pos_val == 0.0:
                continue
            pos_side = p.get("posSide", p.get("side", "")).lower()
            if "long" in pos_side:
                long_count += 1
            elif "short" in pos_side:
                short_count += 1
            upl = float(p.get("upl", 0.0) or 0.0)
            total_pos_upl += upl
            pos_key = f"{p.get('instId')}_{p.get('posSide', 'net')}"
            t_info = trackers.get(pos_key, {})
            trailing_sl = t_info.get("trailingStopPx", "--")
            stage_desc = t_info.get("stage_desc", "持有监控中")
            strategy_tag = t_info.get("strategy_tag") or ("🌊 低吸" if "long" in pos_side else "⚡ 高空")
            avg_px = float(p.get("avgPx", 0) or 0)
            mark_px = float(p.get("markPx", 0) or 0)
            pos_sz = float(p.get("pos", 0) or 0)
            ct_val = 1.0
            inst_id_val = p.get("instId", "")
            for target_item in load_instruments():
                if target_item["instId"] == inst_id_val:
                    ct_val = target_item.get("ctVal", 1.0)
                    break
            okx_notional = float(p.get("notionalUsd", 0) or 0)
            okx_imr = float(p.get("imr", 0) or 0)
            notional_usdt = round(okx_notional if okx_notional > 0 else (pos_sz * ct_val * (mark_px if mark_px > 0 else avg_px)), 2)
            raw_upl_ratio = float(p.get("uplRatio", 0.0) or 0.0)
            real_roi_pct = round(raw_upl_ratio * 100, 2)
            price_chg = round(((mark_px - avg_px) / avg_px * 100) if avg_px > 0 else 0, 2)
            # 注意：这里**故意**用修复后的写法，而不是搬走前的原样。
            # 搬走前那行 `float(p.get("lever", "3") or 3.0)` 在 lever 为
            # 字符串 "0" 时得到 0.0（"0" 是 truthy，`or` 不兜底），下一行
            # 除法抛 ZeroDivisionError 打崩整个仪表盘周期。这是本轮重构中发现
            # 并修复的**既有缺陷**，故这段 legacy 参照也同步修复，
            # 否则差分会在**旧实现崩溃**的输入上失去意义（见 ZeroLeverFixTest）。
            try:
                lever_val = float(p.get("lever", "3"))
            except (TypeError, ValueError):
                lever_val = 3.0
            if lever_val <= 0:
                lever_val = 3.0
            margin_usdt_val = round(okx_imr if okx_imr > 0 else (notional_usdt / lever_val), 2)
            # US-003 环境轴贯通（后来补的纯附加字段）：持仓行必须标明 DEMO/LIVE，
            # 否则前端会把模拟仓当实盘展示。参照实现同步补齐 —— 只加键、不改
            # 任何既有取数口径，故差分的意义不受影响。
            try:
                from scripts.okx_runtime import current_environment
                _okx_env = current_environment()
                _acc_mode = "DEMO" if _okx_env.simulated else "LIVE"
                _env_mode = _okx_env.mode.lower()
            except Exception:
                _acc_mode = "DEMO"
                _env_mode = "demo"
            positions.append({
                "venue": "okx", "exchange": "okx",
                "instId": p.get("instId"),
                "name": p.get("instId", "").replace("-USDT-SWAP", ""),
                "posSide": pos_side, "side": pos_side,
                "pos": p.get("pos"), "pos_sz": pos_sz,
                "notional_usdt": notional_usdt, "margin_usdt": margin_usdt_val,
                "marginSource": "exchange_imr" if okx_imr > 0 else "notional_div_leverage",
                "imr": okx_imr or None, "lever": p.get("lever", "3"),
                "account_mode": _acc_mode, "environment": _env_mode,
                "avgPx": avg_px, "markPx": mark_px, "upl": upl,
                "uplRatio": real_roi_pct, "roi_pct": real_roi_pct,
                "price_change_pct": price_chg,
                "liqPx": p.get("liqPx", "--"), "bePx": p.get("bePx", "--"),
                "trailingSl": trailing_sl, "stageDesc": stage_desc,
                "strategyTag": strategy_tag,
                "tp1Hit": t_info.get("tp1_hit", False),
                "tp2Hit": t_info.get("tp2_hit", False)
            })
    return long_count, short_count, total_pos_upl


def _both(pos_data, trackers=None):
    trackers = trackers if trackers is not None else {}
    a, b = [], []
    ga = collect_position_rows(pos_data, a, trackers, load_instruments=lambda: INSTRUMENTS)
    gb = _legacy(pos_data, b, trackers, load_instruments=lambda: INSTRUMENTS)
    return a, ga, b, gb


def _doc_delta(data, legacy_rows, legacy_counts):
    """把**旧实现**的输出按文档化差异改写成新实现应有的输出。

    差异规则（与 `position_view.collect_position_rows` 同源，逐字对应）：
    `posSide` 既不含 long 也不含 short 时，展示用 `posSide`/`side` 取
    `"long" if 带符号持仓量 > 0 else "short"`；多空计数随之。
    """
    if not isinstance(data, list):
        return legacy_rows, legacy_counts
    out_rows, lc, sc = [], 0, 0
    i = 0
    for p in data:
        pos_val = float(p.get("pos", 0.0) or 0.0)
        if pos_val == 0.0:
            continue
        row = dict(legacy_rows[i])
        i += 1
        raw = str(p.get("posSide") or p.get("side") or "").lower()
        if "long" not in raw and "short" not in raw:
            side = "long" if pos_val > 0 else "short"
            row["posSide"] = side
            row["side"] = side
            # ⚠️ side 派生的**展示**字段也要一起改：`strategyTag` 的默认值按 side 取
            # （`"🌊 低吸" if "long" in pos_side else "⚡ 高空"`）—— 随机差分正是靠这条
            # 抓到我第一版差异助手漏了它。tracker 里的 `strategy_tag` 优先，保持不动。
            if str(row.get("strategyTag") or "").startswith(("🌊", "⚡")):
                row["strategyTag"] = "🌊 低吸" if side == "long" else "⚡ 高空"
        out_rows.append(row)
        if "long" in str(row["posSide"]):
            lc += 1
        elif "short" in str(row["posSide"]):
            sc += 1
    return out_rows, (lc, sc, legacy_counts[2])


def _pos(**over):
    base = dict(instId="BTC-USDT-SWAP", posSide="long", pos="3", avgPx="79000",
                markPx="80000", upl="120.5", uplRatio="0.05", lever="5",
                notionalUsd="2400", imr="480", liqPx="70000", bePx="79200")
    base.update(over)
    return base


class FieldTest(unittest.TestCase):
    def test_basic_normalisation(self):
        rows, _, _, _ = _both([_pos()])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["venue"], "okx")
        self.assertEqual(r["exchange"], "okx")
        self.assertEqual(r["name"], "BTC")
        self.assertEqual(r["posSide"], "long")
        self.assertEqual(r["side"], "long")
        self.assertEqual(r["pos_sz"], 3.0)
        self.assertEqual(r["notional_usdt"], 2400.0, "优先用交易所的 notionalUsd")
        self.assertEqual(r["margin_usdt"], 480.0, "优先用交易所的 imr")
        self.assertEqual(r["marginSource"], "exchange_imr")
        self.assertEqual(r["upl"], 120.5)

    def test_short_side_counted(self):
        rows, (lc, sc, _), _, _ = _both([_pos(posSide="short")])
        self.assertEqual(lc, 0)
        self.assertEqual(sc, 1)
        self.assertEqual(rows[0]["posSide"], "short")

    def test_net_side_is_normalised_from_signed_size(self):
        """第一百二十三刀改判：`net`（净持仓模式）按**带符号持仓量**归一为多/空。

        旧行为是"既不含 long 也不含 short ⇒ 两个计数都不加"，于是
        ①多空计数偏小；②行里 `posSide`/`side` 留成 `"net"` ⇒ 下游
        （提示词 `方向:` 与极值分支、factors 策略标签）把**净多头当空头**。
        """
        rows, (lc, sc, _), _, _ = _both([_pos(posSide="net", pos="3")])
        self.assertEqual((lc, sc), (1, 0), "净多头必须计入多头")
        self.assertEqual(rows[0]["posSide"], "long")
        self.assertEqual(rows[0]["side"], "long")

        rows, (lc, sc, _), _, _ = _both([_pos(posSide="net", pos="-2.5")])
        self.assertEqual((lc, sc), (0, 1), "净空头必须计入空头")
        self.assertEqual(rows[0]["posSide"], "short")

    def test_missing_side_is_also_normalised(self):
        """字段整段缺失（同样非 long/short）⇒ 也按符号归一。"""
        rows, (lc, sc, _), _, _ = _both([_pos(posSide="", pos="3")])
        self.assertEqual((lc, sc), (1, 0))
        self.assertEqual(rows[0]["side"], "long")

    def test_display_side_is_never_net(self):
        """契约：持仓行的 `posSide`/`side` 只允许 long/short（消费者据此分方向）。"""
        for raw in ("net", "", "NET", "both"):
            rows, _, _, _ = _both([_pos(posSide=raw, pos="3")])
            self.assertIn(rows[0]["side"], ("long", "short"), f"raw={raw!r}")
            self.assertIn(rows[0]["posSide"], ("long", "short"), f"raw={raw!r}")

    def test_total_upl_sums(self):
        _, (_, _, upl), _, _ = _both([_pos(upl="10"), _pos(upl="-3.5")])
        self.assertAlmostEqual(upl, 6.5, places=10)


class SkipZeroPositionTest(unittest.TestCase):
    def test_zero_pos_is_skipped(self):
        """OKX 会返回已平未消解的零仓行；不跳过会在列表里留幽灵条目。"""
        rows, counts, _, _ = _both([_pos(pos="0"), _pos(pos="0.0")])
        self.assertEqual(rows, [])
        self.assertEqual(counts, (0, 0, 0.0))

    def test_string_zero_is_skipped(self):
        rows, _, _, _ = _both([_pos(pos="0")])
        self.assertEqual(rows, [])

    def test_missing_pos_field_is_skipped(self):
        p = _pos()
        del p["pos"]
        rows, _, _, _ = _both([p])
        self.assertEqual(rows, [])

    def test_nonzero_survives(self):
        rows, _, _, _ = _both([_pos(pos="0.001")])
        self.assertEqual(len(rows), 1)


class NotionalTest(unittest.TestCase):
    def test_falls_back_to_size_times_ctval_times_mark(self):
        rows, _, _, _ = _both([_pos(notionalUsd="0", pos="3", markPx="80000")])
        # BTC ctVal=0.01 → 3 * 0.01 * 80000 = 2400
        self.assertEqual(rows[0]["notional_usdt"], 2400.0)

    def test_fallback_uses_avg_px_when_mark_missing(self):
        """`mark_px if mark_px > 0 else avg_px` —— 二级兜底不能省。"""
        rows, _, _, _ = _both([_pos(notionalUsd="0", pos="3", markPx="0", avgPx="79000")])
        self.assertEqual(rows[0]["notional_usdt"], round(3 * 0.01 * 79000, 2))

    def test_negative_notional_also_falls_back(self):
        rows, _, _, _ = _both([_pos(notionalUsd="-5", pos="3", markPx="80000")])
        self.assertEqual(rows[0]["notional_usdt"], 2400.0)

    def test_unknown_instrument_uses_ctval_1(self):
        rows, _, _, _ = _both([_pos(instId="DOGE-USDT-SWAP", notionalUsd="0",
                                    pos="100", markPx="2")])
        self.assertEqual(rows[0]["notional_usdt"], round(100 * 1.0 * 2, 2))


class MarginTest(unittest.TestCase):
    def test_falls_back_to_notional_over_lever(self):
        rows, _, _, _ = _both([_pos(imr="0", notionalUsd="2400", lever="5")])
        self.assertEqual(rows[0]["margin_usdt"], 480.0)
        self.assertEqual(rows[0]["marginSource"], "notional_div_leverage")

    def test_imr_field_is_none_when_zero(self):
        rows, _, _, _ = _both([_pos(imr="0")])
        self.assertIsNone(rows[0]["imr"], "imr=0 时输出 None（前端据此判断有无交易所口径）")

    def test_zero_lever_does_not_divide_by_zero(self):
        """`lever="0"` → `or 3.0` 兜底到 3，不得抛 ZeroDivisionError。"""
        rows, _, _, _ = _both([_pos(imr="0", notionalUsd="2400", lever="0")])
        self.assertEqual(rows[0]["margin_usdt"], 800.0)

    def test_missing_lever_defaults_to_3(self):
        p = _pos(imr="0", notionalUsd="2400")
        del p["lever"]
        rows, _, _, _ = _both([p])
        self.assertEqual(rows[0]["margin_usdt"], 800.0)


class ZeroLeverFixTest(unittest.TestCase):
    """重构中发现并修复的既有缺陷：`lever` 为字符串 `"0"` 时打崩整个周期。

    原实现是 `float(p.get("lever", "3") or 3.0)`。`or 3.0` 挡的是 falsy，
    而 **`"0"` 是 truthy 字符串**，于是 `lever_val` 变成 `0.0`，
    紧随其后的 `notional_usdt / lever_val` 抛 `ZeroDivisionError`。

    这个异常从 `update_cache_cycle()` 冒出后，背景工人的 `except Exception: pass`
    会**静默吞掉**它 —— 表现是仪表盘缓存永远不刷新、整页停在旧数据，
    而日志里连一条 traceback 都没有。属于最难查的一类。
    """

    def test_string_zero_lever_does_not_crash(self):
        rows, _, _, _ = _both([_pos(imr="0", notionalUsd="2400", lever="0")])
        self.assertEqual(rows[0]["margin_usdt"], 800.0, "应回退到 3 倍杠杆")

    def test_numeric_zero_lever_does_not_crash(self):
        rows, _, _, _ = _both([_pos(imr="0", notionalUsd="2400", lever=0)])
        self.assertEqual(rows[0]["margin_usdt"], 800.0)

    def test_negative_lever_falls_back(self):
        rows, _, _, _ = _both([_pos(imr="0", notionalUsd="2400", lever="-5")])
        self.assertEqual(rows[0]["margin_usdt"], 800.0)

    def test_non_numeric_lever_falls_back(self):
        rows, _, _, _ = _both([_pos(imr="0", notionalUsd="2400", lever="abc")])
        self.assertEqual(rows[0]["margin_usdt"], 800.0)

    def test_the_old_expression_really_was_broken(self):
        """把**旧表达式**写出来，证明这条修复不是多余的。

        若哪天有人把修复回退成 `float(x or 3.0)`，这条会红。
        """
        old = float("0" or 3.0)
        self.assertEqual(old, 0.0, "字符串 '0' 是 truthy，`or` 不兜底")
        with self.assertRaises(ZeroDivisionError):
            _ = 2400.0 / old

    def test_imr_present_path_was_never_affected(self):
        """有交易所 imr 时不走除法，故原缺陷只在 imr 缺失/为 0 时触发。"""
        rows, _, _, _ = _both([_pos(imr="480", lever="0")])
        self.assertEqual(rows[0]["margin_usdt"], 480.0)
        self.assertEqual(rows[0]["marginSource"], "exchange_imr")


class RoiTest(unittest.TestCase):
    def test_roi_is_upl_ratio_times_100(self):
        rows, _, _, _ = _both([_pos(uplRatio="0.05")])
        self.assertEqual(rows[0]["roi_pct"], 5.0)
        self.assertEqual(rows[0]["uplRatio"], 5.0, "两个键同值")

    def test_roi_is_not_recomputed_from_upl_and_margin(self):
        """**口径**：ROI 直接取 `uplRatio`，不是 `upl/margin`。

        构造两者明显不同的输入（upl=100、margin=480 → 重算得 20.83），
        真实实现应给 `uplRatio` 换算值 5.0。若有人"顺手统一口径"重算，这条会红。
        """
        rows, _, _, _ = _both([_pos(uplRatio="0.05", upl="100", imr="480")])
        self.assertEqual(rows[0]["roi_pct"], 5.0)
        self.assertNotEqual(rows[0]["roi_pct"], round(100 / 480 * 100, 2))

    def test_negative_roi(self):
        rows, _, _, _ = _both([_pos(uplRatio="-0.1234")])
        self.assertEqual(rows[0]["roi_pct"], -12.34)


class PriceChangeTest(unittest.TestCase):
    def test_price_change_pct(self):
        rows, _, _, _ = _both([_pos(avgPx="100", markPx="110")])
        self.assertEqual(rows[0]["price_change_pct"], 10.0)

    def test_price_change_zero_when_avg_missing(self):
        rows, _, _, _ = _both([_pos(avgPx="0", markPx="110")])
        self.assertEqual(rows[0]["price_change_pct"], 0)

    def test_price_change_negative(self):
        rows, _, _, _ = _both([_pos(avgPx="100", markPx="90")])
        self.assertEqual(rows[0]["price_change_pct"], -10.0)


class TrackerTest(unittest.TestCase):
    def test_tracker_fields_attached(self):
        trackers = {"BTC-USDT-SWAP_long": {
            "trailingStopPx": 78000, "stage_desc": "第2段", "strategy_tag": "自定义",
            "tp1_hit": True, "tp2_hit": False}}
        rows, _, _, _ = _both([_pos()], trackers)
        r = rows[0]
        self.assertEqual(r["trailingSl"], 78000)
        self.assertEqual(r["stageDesc"], "第2段")
        self.assertEqual(r["strategyTag"], "自定义")
        self.assertTrue(r["tp1Hit"])
        self.assertFalse(r["tp2Hit"])

    def test_tracker_defaults_when_missing(self):
        rows, _, _, _ = _both([_pos()], {})
        r = rows[0]
        self.assertEqual(r["trailingSl"], "--")
        self.assertEqual(r["stageDesc"], "持有监控中")
        self.assertEqual(r["strategyTag"], "🌊 低吸", "多头默认标签")
        self.assertFalse(r["tp1Hit"])
        self.assertFalse(r["tp2Hit"])

    def test_short_default_strategy_tag(self):
        rows, _, _, _ = _both([_pos(posSide="short")], {})
        self.assertEqual(rows[0]["strategyTag"], "⚡ 高空")

    def test_tracker_key_uses_inst_and_side(self):
        trackers = {"BTC-USDT-SWAP_short": {"strategy_tag": "空头专属"}}
        rows, _, _, _ = _both([_pos(posSide="long")], trackers)
        self.assertEqual(rows[0]["strategyTag"], "🌊 低吸",
                         "long 不应读到 short 的 tracker")


class RandomParityTest(unittest.TestCase):
    def test_random_parity(self):
        rng = random.Random(20260929)
        insts = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "DOGE-USDT-SWAP"]
        for _ in range(3000):
            n = rng.randint(0, 5)
            data = []
            for _k in range(n):
                p = {}
                if rng.random() < 0.9:
                    p["instId"] = rng.choice(insts)
                if rng.random() < 0.9:
                    p["posSide"] = rng.choice(["long", "short", "net", ""])
                if rng.random() < 0.9:
                    p["pos"] = rng.choice(["0", "0.0", "3", "-2.5", 7])
                if rng.random() < 0.8:
                    p["avgPx"] = rng.choice(["0", "79000", "1.5", 100])
                if rng.random() < 0.8:
                    p["markPx"] = rng.choice(["0", "80000", "2.5", 110])
                if rng.random() < 0.8:
                    p["upl"] = rng.choice(["-10.5", "0", "120.5"])
                if rng.random() < 0.8:
                    p["uplRatio"] = rng.choice(["-0.05", "0", "0.05", "1.2"])
                if rng.random() < 0.8:
                    p["lever"] = rng.choice(["0", "1", "5", "20"])
                if rng.random() < 0.7:
                    p["notionalUsd"] = rng.choice(["0", "-1", "2400", "999.99"])
                if rng.random() < 0.7:
                    p["imr"] = rng.choice(["0", "-1", "480"])
                if rng.random() < 0.5:
                    p["liqPx"] = rng.choice(["70000", "--"])
                if rng.random() < 0.5:
                    p["bePx"] = rng.choice(["79200", "--"])
                data.append(p)
            trackers = {}
            if data and rng.random() < 0.5:
                trackers["BTC-USDT-SWAP_long"] = {"trailingStopPx": 1, "tp1_hit": True}
            a, ga, b, gb = _both(data, trackers)
            # ⚠️ **文档化差异**（第一百二十三刀）：净持仓模式（`posSide` 既非 long
            # 也非 short，含缺失）时，展示用 side 由**带符号持仓量**归一，旧实现原样
            # 透传（"net"/""）。差异**只**落在这些行与多空计数上，其余逐字段一致。
            exp_rows, exp_counts = _doc_delta(data, b, gb)
            self.assertEqual(a, exp_rows, f"行分叉（超出文档化差异）: {data}")
            self.assertEqual(ga, exp_counts, f"计数分叉（超出文档化差异）: {data}")

    def test_non_list_input(self):
        for bad in (None, 42, "x", {}):
            a, ga, b, gb = _both(bad)
            self.assertEqual(a, b)
            self.assertEqual(ga, gb)
            self.assertEqual(ga, (0, 0, 0.0))


class WiringTest(unittest.TestCase):
    def test_impl_in_submodule_not_facade(self):
        app_src = APP.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        self.assertIn("def collect_position_rows(", mod_src)
        self.assertNotIn("def collect_position_rows(", app_src)
        # 第九十四刀：调用点随相位 1 迁入 collect.py（门面只留注入）
        collect_src = COLLECT.read_text(encoding="utf-8")
        self.assertIn("_core_collect_position_rows(", collect_src)
        self.assertIn("_core_collect_position_rows=_core_collect_position_rows", app_src)

    def test_facade_no_longer_contains_the_inline_loop(self):
        app_src = APP.read_text(encoding="utf-8")
        for gone in ("marginSource", "price_change_pct", '"tp2Hit"'):
            self.assertNotIn(gone, app_src, f"门面仍残留内联片段 {gone!r}")

    def test_counters_accumulated_in_facade(self):
        """三个计数器是**跨行累加**状态，留在门面。"""
        # 第九十四刀：三个计数器随相位 1 迁入 collect.py（跨行累加语义不变）
        collect_src = COLLECT.read_text(encoding="utf-8")
        for acc in ("long_count += _pos_delta[0]",
                    "short_count += _pos_delta[1]",
                    "total_pos_upl += _pos_delta[2]"):
            self.assertIn(acc, collect_src)

    def test_load_instruments_is_injected(self):
        # 第九十四刀：调用点迁入 collect.py ⇒ 判定对象随实现迁移
        tree = ast.parse(COLLECT.read_text(encoding="utf-8"))
        call = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_core_collect_position_rows")
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
        self.assertEqual(kw.get("load_instruments"), "load_instruments",
                         "load_instruments 必须门面注入（测试会 patch 它）")
        self.assertFalse([a for a in call.args if isinstance(a, ast.Lambda)],
                         "不得内联 lambda，否则 patch 失效")

    def test_submodule_does_not_import_instruments(self):
        mod_src = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(mod_src)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {(a.asname or a.name) for a in node.names}
                self.assertNotIn("load_instruments", names)

    def test_module_does_not_import_dashboard(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertFalse(a.name.startswith("r20_backend.dashboard_cache"),
                                     f"反向 import {a.name}")
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").startswith("r20_backend.dashboard_cache"),
                                 f"反向 import {node.module}")


if __name__ == "__main__":
    unittest.main()
