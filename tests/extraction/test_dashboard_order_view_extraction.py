"""`r20_backend/dashboard_payload/order_view.py`（B3 第二十三刀）回归。

## 这个测试在守什么

`collect_pending_order_rows` 把 OKX 挂单的"机器口径"翻成前端要的"人话口径"。
两处转换最容易错：

1. **方向语义反转**：是否 `reduceOnly` 决定「买」到底是**开多**还是**平空**。
   同一个 `side="buy"`，`reduceOnly` 为真时是**平空**。判错会让前端把平仓单
   显示成开仓单，**文案与颜色全反**。
2. **`reduceOnly` 是字符串**：OKX 返回 `"true"`/`"false"`。
   实现用 `str(...).lower() == "true"` 比较。若有人"简化"成
   `if o.get("reduceOnly")`，**字符串 `"false"` 是 truthy** →
   所有单子都被当成平仓单。这是本段最容易写错、且症状最隐蔽的一处。

其余：价格三态（市价 / `--` / `%g` 格式化 / 非数值原样透出）、
`instId` 的两级后缀剥离、附加保护单只取第一条。
"""

from __future__ import annotations

import ast
import datetime
import random
import unittest
from pathlib import Path

from r20_backend.dashboard_payload.order_view import collect_pending_order_rows

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "r20_backend" / "dashboard_cache.py"
MODULE = ROOT / "r20_backend" / "dashboard_payload" / "order_view.py"
COLLECT = ROOT / "r20_backend" / "dashboard_payload" / "collect.py"   # 第九十四刀：相位 1 现住此

TZ = datetime.timezone(datetime.timedelta(hours=8))


def _legacy(orders_data, pending_orders_list, *, tz_beijing, datetime):
    """搬走前 update_cache_cycle 里的内联挂单遍历（逐字原样）。"""
    if isinstance(orders_data, list):
        for o in orders_data:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_beijing).strftime("%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            inst_clean = inst_id.replace("-USDT-SWAP", "").replace("-SWAP", "")
            side_raw = str(o.get("side", "")).lower()
            pos_side = str(o.get("posSide", "net")).lower()
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()
            raw_px = str(o.get("px") or "").strip()
            if reduce_only:
                if side_raw == "sell":
                    side_label = "市价平多" if ord_type == "market" else "限价平多"
                    is_long = False
                    side_color = "rose"
                else:
                    side_label = "市价平空" if ord_type == "market" else "限价平空"
                    is_long = True
                    side_color = "emerald"
            else:
                if side_raw == "buy":
                    side_label = "市价买多" if ord_type == "market" else "限价买多"
                    is_long = True
                    side_color = "emerald"
                else:
                    side_label = "市价卖空" if ord_type == "market" else "限价卖空"
                    is_long = False
                    side_color = "rose"
            if not raw_px or raw_px == "0":
                px_display = "市价" if ord_type == "market" else "--"
            else:
                try:
                    px_float = float(raw_px)
                    px_display = f"{px_float:g}"
                except ValueError:
                    px_display = raw_px
            attach_list = o.get("attachAlgoOrds", [])
            tp_px = "--"
            sl_px = "--"
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_px = str(att.get("tpTriggerPx") or "--")
                sl_px = str(att.get("slTriggerPx") or "--")
            try:
                from scripts.okx_runtime import current_environment
                _okx_env = current_environment()
                _acc_mode = "DEMO" if _okx_env.simulated else "LIVE"
                _env_mode = _okx_env.mode.lower()
            except Exception:
                _acc_mode = "DEMO"
                _env_mode = "demo"

            _margin_usdt = None
            try:
                from scripts.instrument_pool import load_instruments
                # 面值只认池子（单一事实源）；查不到就不给数字（前端回落原生张数）。
                ct_val = 0.0
                for target_item in load_instruments():
                    if target_item.get("instId") == inst_id or target_item.get("name") == inst_clean:
                        ct_val = float(target_item.get("ctVal", 0.0) or 0.0)
                        break
                _px_float = float(raw_px) if (raw_px and raw_px != "0") else 0.0
                _sz_float = abs(float(o.get("sz", 0) or 0))
                _lev_num = float(str(o.get("lever", "3")).replace("x", "") or 3.0)
                if _lev_num <= 0:
                    _lev_num = 3.0
                if _px_float > 0 and _sz_float > 0 and ct_val > 0:
                    _margin_usdt = round((_sz_float * ct_val * _px_float) / _lev_num, 2)
            except Exception:
                _margin_usdt = None

            pending_orders_list.append({
                "venue": "okx", "exchange": "okx",
                "ordId": str(o.get("ordId", "")),
                "name": inst_clean, "inst": inst_clean, "instId": inst_id,
                "side": "buy" if side_raw == "buy" else "sell",
                "side_label": side_label, "side_raw": side_raw,
                "posSide": pos_side, "is_long": is_long, "side_color": side_color,
                "ord_type": ord_type, "lever": f"{o.get('lever', '3')}x",
                "px": px_display, "sz": str(o.get("sz", "--")),
                "margin_usdt": _margin_usdt,
                "cTime": str(o.get("cTime", "")), "time": c_time_str,
                "state": str(o.get("state", "live")),
                "tp_px": tp_px, "sl_px": sl_px,
                "account_mode": _acc_mode,
                "environment": _env_mode,
            })
    return None


def _both(orders_data):
    a, b = [], []
    ga = collect_pending_order_rows(orders_data, a, tz_beijing=TZ, datetime=datetime)
    _legacy(orders_data, b, tz_beijing=TZ, datetime=datetime)
    return a, ga, b


def _ms(text="2026-09-14 10:00:00"):
    dt = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def _order(**over):
    base = dict(ordId="12345", instId="BTC-USDT-SWAP", side="buy", posSide="long",
                reduceOnly="false", ordType="limit", px="79000.5", sz="3",
                cTime=_ms(), lever="5", state="live")
    base.update(over)
    return base


class DirectionSemanticsTest(unittest.TestCase):
    """`reduceOnly` 决定「买/卖」是开仓还是平仓 —— 判错则文案与颜色全反。"""

    def test_open_long_buy_emerald(self):
        row = _both([_order(side="buy", reduceOnly="false", ordType="limit")])[0][0]
        self.assertEqual(row["side_label"], "限价买多")
        self.assertTrue(row["is_long"])
        self.assertEqual(row["side_color"], "emerald")
        self.assertEqual(row["side"], "buy")

    def test_open_short_sell_rose(self):
        row = _both([_order(side="sell", reduceOnly="false", ordType="limit")])[0][0]
        self.assertEqual(row["side_label"], "限价卖空")
        self.assertFalse(row["is_long"])
        self.assertEqual(row["side_color"], "rose")
        self.assertEqual(row["side"], "sell")

    def test_reduce_short_buy_is_closing_not_opening(self):
        """**核心语义**：reduceOnly + buy = 平空（不是开多）。"""
        row = _both([_order(side="buy", reduceOnly="true")])[0][0]
        self.assertEqual(row["side_label"], "限价平空")
        self.assertTrue(row["is_long"], "平空后释放的是空头，is_long=True（原文如此）")

    def test_reduce_long_sell(self):
        row = _both([_order(side="sell", reduceOnly="true")])[0][0]
        self.assertEqual(row["side_label"], "限价平多")
        self.assertFalse(row["is_long"])

    def test_market_variants(self):
        cases = [
            ("buy", "false", "市价买多"),
            ("sell", "false", "市价卖空"),
            ("buy", "true", "市价平空"),
            ("sell", "true", "市价平多"),
        ]
        for side, ro, expected in cases:
            row = _both([_order(side=side, reduceOnly=ro, ordType="market")])[0][0]
            self.assertEqual(row["side_label"], expected,
                             f"side={side} reduceOnly={ro}")

    def test_all_eight_label_combinations(self):
        seen = set()
        for side in ("buy", "sell"):
            for ro in ("true", "false"):
                for ot in ("market", "limit"):
                    row = _both([_order(side=side, reduceOnly=ro, ordType=ot)])[0][0]
                    seen.add(row["side_label"])
        self.assertEqual(len(seen), 8, f"应有 8 种文案，实际 {sorted(seen)}")


class ReduceOnlyStringTrapTest(unittest.TestCase):
    """`reduceOnly` 是**字符串**；真值判断会让 `"false"` 变成 truthy。"""

    def test_string_false_is_not_reduce_only(self):
        row = _both([_order(reduceOnly="false", side="buy")])[0][0]
        self.assertEqual(row["side_label"], "限价买多",
                         '字符串 "false" 是 truthy；若写成 if o.get(...) 这里会变「平空」')

    def test_the_naive_truthiness_check_would_be_wrong(self):
        """把**错误写法**的效果写出来，证明这条测试不是多余的。"""
        self.assertTrue(bool("false"), '字符串 "false" 为真')
        naive_reduce = bool("false")
        row = _both([_order(reduceOnly="false", side="buy")])[0][0]
        self.assertNotEqual(
            row["side_label"],
            "限价平空" if naive_reduce else "限价买多",
            "真实实现必须与「真值判断」的结果不同，否则说明它退化了")

    def test_missing_reduce_only_defaults_to_false(self):
        o = _order(side="buy")
        del o["reduceOnly"]
        row = _both([o])[0][0]
        self.assertEqual(row["side_label"], "限价买多")

    def test_uppercase_true_is_recognised(self):
        row = _both([_order(side="buy", reduceOnly="TRUE")])[0][0]
        self.assertEqual(row["side_label"], "限价平空", "须先 lower() 再比较")

    def test_boolean_true_from_api_also_handled(self):
        """若交易所返回真布尔（非字符串），`str(True).lower()` == "true" → 仍识别。"""
        row = _both([_order(side="buy", reduceOnly=True)])[0][0]
        self.assertEqual(row["side_label"], "限价平空")


class SideFieldTest(unittest.TestCase):
    def test_side_normalised_to_lowercase(self):
        row = _both([_order(side="BUY")])[0][0]
        self.assertEqual(row["side"], "buy")
        self.assertEqual(row["side_raw"], "buy")

    def test_non_buy_becomes_sell_in_side_field(self):
        """`side` 字段只有 buy/sell 两种取值：不是 buy 一律 sell。"""
        row = _both([_order(side="weird")])[0][0]
        self.assertEqual(row["side"], "sell")
        self.assertEqual(row["side_raw"], "weird", "原始值保留在 side_raw")

    def test_pos_side_defaults_to_net(self):
        o = _order()
        del o["posSide"]
        row = _both([o])[0][0]
        self.assertEqual(row["posSide"], "net")

    def test_ord_type_defaults_to_limit(self):
        o = _order()
        del o["ordType"]
        row = _both([o])[0][0]
        self.assertEqual(row["ord_type"], "limit")
        self.assertEqual(row["side_label"], "限价买多")


class PriceDisplayTest(unittest.TestCase):
    def test_numeric_price_uses_g_format(self):
        row = _both([_order(px="79000.50")])[0][0]
        self.assertEqual(row["px"], "79000.5")

    def test_integer_like_price_has_no_decimals(self):
        row = _both([_order(px="79000.00")])[0][0]
        self.assertEqual(row["px"], "79000")

    def test_string_zero_price_market_is_market_label(self):
        row = _both([_order(px="0", ordType="market")])[0][0]
        self.assertEqual(row["px"], "市价")

    def test_string_zero_price_limit_shows_dashes(self):
        row = _both([_order(px="0", ordType="limit")])[0][0]
        self.assertEqual(row["px"], "--")

    def test_empty_price_market_is_market_label(self):
        row = _both([_order(px="", ordType="market")])[0][0]
        self.assertEqual(row["px"], "市价")

    def test_missing_price_limit_shows_dashes(self):
        o = _order()
        del o["px"]
        row = _both([o])[0][0]
        self.assertEqual(row["px"], "--")

    def test_non_numeric_price_passes_through(self):
        row = _both([_order(px="abc")])[0][0]
        self.assertEqual(row["px"], "abc", "转不动时原样透出，不丢弃")

    def test_whitespace_price_is_treated_as_missing(self):
        row = _both([_order(px="   ", ordType="market")])[0][0]
        self.assertEqual(row["px"], "市价", "raw_px 会 strip()")


class InstrumentNameTest(unittest.TestCase):
    def test_swap_suffix_stripped(self):
        row = _both([_order(instId="BTC-USDT-SWAP")])[0][0]
        self.assertEqual(row["name"], "BTC")
        self.assertEqual(row["inst"], "BTC")
        self.assertEqual(row["instId"], "BTC-USDT-SWAP", "instId 保留完整值")

    def test_non_swap_inst_id_kept_verbatim(self):
        row = _both([_order(instId="ETH-USDT")])[0][0]
        self.assertEqual(row["name"], "ETH-USDT")

    def test_second_swap_strip_is_unreachable_for_okx_ids(self):
        """⚠️ 如实记录：`replace("-SWAP", "")` 这一步**对 OKX 的 instId 永远不生效**。

        因为第一步 `replace("-USDT-SWAP", "")` 已经把 `-SWAP` 一并吃掉了：
        `"BTC-USDT-SWAP"` → `"BTC"`。剩下的形态要么不含 `-SWAP`
        （`"ETH-USDT"`），要么已经被处理完。

        我原本以为它"处理通用 -SWAP 后缀"并写了断言，结果负向验证时
        **把它删掉测试依然全绿** —— 说明那条断言根本没覆盖到它。
        与其假装覆盖，不如把事实写下来：**它的存在是冗余的**，
        但保留无害（处理非 OKX 形态的挂单行时可能有用），故不删、也不假装它被覆盖。
        """
        for inst in ("BTC-USDT-SWAP", "ETH-USDT", "SOL-USDT-SWAP", "X"):
            first = inst.replace("-USDT-SWAP", "")
            double = first.replace("-SWAP", "")
            self.assertEqual(first, double,
                             f"{inst} 上第二个 replace 其实没有作用（冗余但无害）")
            row = _both([_order(instId=inst)])[0][0]
            self.assertEqual(row["name"], first)


class TimeTest(unittest.TestCase):
    def test_c_time_formatted_mm_dd(self):
        row = _both([_order(cTime=_ms("2026-09-14 10:00:00"))])[0][0]
        self.assertRegex(row["time"], r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertTrue(row["time"].startswith("09-14"))

    def test_zero_c_time_shows_dashes(self):
        row = _both([_order(cTime=0)])[0][0]
        self.assertEqual(row["time"], "--")
        self.assertEqual(row["cTime"], "0", "cTime 原样转字符串")

    def test_missing_c_time(self):
        """缺键与显式 0 **不同**：缺键时 `get("cTime", "")` 给空串，不是 "0"。

        两者都让 `time` 退化成 `"--"`（`int("" or 0)` → 0），但 `cTime` 字段本身
        一个是 `""`、一个是 `"0"` —— 我第一版把两者写成一样，测试立刻翻红。
        如此细微的差别值得留一条，免得后来人"统一"成同一个默认值。
        """
        o = _order()
        del o["cTime"]
        row = _both([o])[0][0]
        self.assertEqual(row["time"], "--")
        self.assertEqual(row["cTime"], "", "缺键默认值是空串")

    def test_explicit_zero_c_time_keeps_zero_string(self):
        row = _both([_order(cTime=0)])[0][0]
        self.assertEqual(row["time"], "--")
        self.assertEqual(row["cTime"], "0", "显式 0 转字符串是 \"0\"")


class AlgoAttachmentTest(unittest.TestCase):
    def test_first_attachment_tp_sl(self):
        row = _both([_order(attachAlgoOrds=[
            {"tpTriggerPx": "80000", "slTriggerPx": "77000"},
            {"tpTriggerPx": "99999", "slTriggerPx": "11111"}])])[0][0]
        self.assertEqual(row["tp_px"], "80000")
        self.assertEqual(row["sl_px"], "77000", "只取第一条")

    def test_no_attachments_gives_dashes(self):
        row = _both([_order()])[0][0]
        self.assertEqual(row["tp_px"], "--")
        self.assertEqual(row["sl_px"], "--")

    def test_empty_attachment_list(self):
        row = _both([_order(attachAlgoOrds=[])])[0][0]
        self.assertEqual(row["tp_px"], "--")

    def test_partial_attachment(self):
        row = _both([_order(attachAlgoOrds=[{"tpTriggerPx": "80000"}] )])[0][0]
        self.assertEqual(row["tp_px"], "80000")
        self.assertEqual(row["sl_px"], "--")


class MiscFieldTest(unittest.TestCase):
    def test_venue_and_exchange_are_okx(self):
        row = _both([_order()])[0][0]
        self.assertEqual(row["venue"], "okx")
        self.assertEqual(row["exchange"], "okx")

    def test_ord_id_stringified(self):
        row = _both([_order(ordId=999)])[0][0]
        self.assertEqual(row["ordId"], "999")
        self.assertIsInstance(row["ordId"], str)

    def test_lever_has_x_suffix(self):
        row = _both([_order(lever="5")])[0][0]
        self.assertEqual(row["lever"], "5x")

    def test_lever_zero_is_shown_as_0x_not_replaced(self):
        """与 position_view 不同：这里只做字符串拼接，`"0"` 应如实显示。

        `position_view` 的杠杆参与除法，字符串 `"0"` 会导致除零，故那边回退 3.0；
        本段不参与算术，**回退反而会把真实的 0x 谎报成 3x**。这条钉住"不顺手对齐"。
        """
        row = _both([_order(lever="0")])[0][0]
        self.assertEqual(row["lever"], "0x")

    def test_missing_lever_defaults_to_3x(self):
        o = _order()
        del o["lever"]
        row = _both([o])[0][0]
        self.assertEqual(row["lever"], "3x")

    def test_state_defaults_to_live(self):
        o = _order()
        del o["state"]
        row = _both([o])[0][0]
        self.assertEqual(row["state"], "live")

    def test_sz_stringified(self):
        row = _both([_order(sz="3.5")])[0][0]
        self.assertEqual(row["sz"], "3.5")

    def test_missing_sz_shows_dashes(self):
        o = _order()
        del o["sz"]
        row = _both([o])[0][0]
        self.assertEqual(row["sz"], "--")

    def test_returns_count_of_appended_rows(self):
        _, got, _ = _both([_order(), _order()])
        self.assertEqual(got, 2)

    def test_non_list_returns_zero_and_appends_nothing(self):
        for bad in (None, 42, "x", {}):
            rows, got, legacy_rows = _both(bad)
            self.assertEqual(rows, [])
            self.assertEqual(got, 0)
            self.assertEqual(legacy_rows, [])


class RandomParityTest(unittest.TestCase):
    def test_random_parity(self):
        rng = random.Random(20260930)
        for _ in range(4000):
            n = rng.randint(0, 5)
            data = []
            for _k in range(n):
                o = {}
                if rng.random() < 0.95:
                    o["instId"] = rng.choice(["BTC-USDT-SWAP", "ETH-USDT", "SOL-USDT-SWAP", "X"])
                if rng.random() < 0.95:
                    o["side"] = rng.choice(["buy", "sell", "BUY", "", "weird"])
                if rng.random() < 0.8:
                    o["posSide"] = rng.choice(["long", "short", "net", ""])
                if rng.random() < 0.9:
                    o["reduceOnly"] = rng.choice(["true", "false", "TRUE", "False", True, False])
                if rng.random() < 0.9:
                    o["ordType"] = rng.choice(["market", "limit", "MARKET", ""])
                if rng.random() < 0.9:
                    o["px"] = rng.choice(["0", "", "79000.50", "abc", "  12 ", None, "1e3"])
                if rng.random() < 0.8:
                    o["sz"] = rng.choice(["3", "0.5", 7])
                if rng.random() < 0.8:
                    o["cTime"] = rng.choice([0, _ms(), _ms("2026-09-01 08:30:00")])
                if rng.random() < 0.7:
                    o["lever"] = rng.choice(["0", "3", "20", 5])
                if rng.random() < 0.5:
                    o["state"] = rng.choice(["live", "filled", ""])
                if rng.random() < 0.5:
                    o["attachAlgoOrds"] = rng.choice([
                        [], [{"tpTriggerPx": "1", "slTriggerPx": "2"}],
                        [{"tpTriggerPx": None}], [{"slTriggerPx": "9"}, {"tpTriggerPx": "z"}]])
                data.append(o)
            rows, got, legacy_rows = _both(data)
            self.assertEqual(rows, legacy_rows, f"分叉: {data}")
            self.assertEqual(got, len(rows))


class WiringTest(unittest.TestCase):
    def test_impl_in_submodule_not_facade(self):
        app_src = APP.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        self.assertIn("def collect_pending_order_rows(", mod_src)
        self.assertNotIn("def collect_pending_order_rows(", app_src)
        # 第九十四刀：挂单行的**调用点**随 dashboard 相位 1 迁入 collect.py
        # （门面只留注入：`_core_collect_pending_order_rows=_core_collect_pending_order_rows`）
        collect_src = COLLECT.read_text(encoding="utf-8")
        self.assertIn("_core_collect_pending_order_rows(", collect_src)
        self.assertIn("_core_collect_pending_order_rows=_core_collect_pending_order_rows", app_src)

    def test_facade_no_longer_contains_the_inline_loop(self):
        app_src = APP.read_text(encoding="utf-8")
        for gone in ("市价平多", "限价卖空", "attachAlgoOrds"):
            self.assertNotIn(gone, app_src, f"门面仍残留内联片段 {gone!r}")

    def test_tz_and_datetime_are_injected(self):
        # 第九十四刀：调用点迁入 collect.py ⇒ 判定对象随实现迁移
        tree = ast.parse(COLLECT.read_text(encoding="utf-8"))
        call = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_core_collect_pending_order_rows")
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
        self.assertEqual(kw.get("tz_beijing"), "tz_beijing")
        self.assertEqual(kw.get("datetime"), "datetime")

    def test_submodule_does_not_import_datetime(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {(a.asname or a.name) for a in node.names}
                self.assertNotIn("datetime", names,
                                 "datetime 必须调用期注入（门面同名名字会被重定向）")

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
