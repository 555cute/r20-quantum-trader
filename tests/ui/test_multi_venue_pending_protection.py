# -*- coding: utf-8 -*-
"""跨所挂单行的云端保护腿展示（2026-09-16）。

## 这个测试在守什么

Binance/Gate 的 TP/SL **不在挂单对象里**（只有 OKX 有 `attachAlgoOrds`），
旧实现给跨所挂单一律写死 `tp_px="--" / sl_px="--"`。后果是实盘上：

    用户看到：[ETH] 限价空单 2408.5 —— 保护列全 "--"（像裸单）
    交易所真相：STOP_MARKET 2456.5 + TAKE_PROFIT_MARKET 2298.5 早已挂出（reduceOnly）

这正是本仓红线「UI 不说谎」的反面：不是编造，而是**该展示的真实数据没接上**。
修法是复用 `collect_cross_venue_positions` **上方已经取回**的 `v_algos`
（零新增交易所调用），并**只认反向（reduceOnly）腿**——避免同标的其它方向持仓的
保护腿串味；匹配不到仍然诚实留在 `"--"`。
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from r20_backend.dashboard_payload.multi_venue import collect_cross_venue_positions


class _FakeAdapter:
    def __init__(self, *, open_orders, algos, positions=None):
        self._open = open_orders
        self._algos = algos
        self._pos = positions or []
        self.algo_calls = 0

    def positions(self):
        return self._pos

    def open_orders(self):
        return self._open

    def list_protective_orders(self, *a, **k):
        self.algo_calls += 1
        return self._algos


def _leg(symbol, side, order_type, trigger, raw_extra=None):
    raw = {"orderType": order_type, "triggerPrice": trigger}
    raw.update(raw_extra or {})
    return {"symbol": symbol, "side": side, "trigger_price": trigger,
            "type": order_type, "raw": raw}


class PendingOrderProtectionDisplayTest(unittest.TestCase):
    def _run(self, adapter):
        pending: list = []
        empty = _FakeAdapter(open_orders=[], algos=[])
        # 只让 binance 返回夹具：`collect_cross_venue_positions` 会遍历 binance/gate
        # 两个场所，否则同一份夹具会被记两次。
        def _pick(venue, *a, **k):
            return adapter if venue == "binance" else empty
        with patch("r20_backend.exchanges.get_adapter", _pick), \
             patch("r20_backend.dashboard_payload.multi_venue._global_env_axis",
                   lambda: "demo"):
            collect_cross_venue_positions([], pending, 0, 0, 0.0)
        return pending

    def test_reduce_only_legs_are_shown_on_the_pending_row(self):
        """ETH 空单挂单：反向（buy）的 STOP=SL、TAKE_PROFIT=TP 必须出现在行上。"""
        ad = _FakeAdapter(
            open_orders=[{"symbol": "ETHUSDT", "side": "sell", "price": 2408.5, "size": "0.58"}],
            algos=[_leg("ETHUSDT", "buy", "STOP_MARKET", 2456.5),
                   _leg("ETHUSDT", "buy", "TAKE_PROFIT_MARKET", 2298.5)],
        )
        rows = self._run(ad)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sl_px"], "2456.5")
        self.assertEqual(rows[0]["tp_px"], "2298.5")
        self.assertEqual(ad.algo_calls, 1, "不得为本展示新增交易所调用（复用 v_algos）")

    def test_same_side_legs_are_not_misattributed(self):
        """同向（sell）腿属于别的方向持仓，绝不能当成这张挂单的保护腿。"""
        ad = _FakeAdapter(
            open_orders=[{"symbol": "ETHUSDT", "side": "sell", "price": 2408.5, "size": "0.58"}],
            algos=[_leg("ETHUSDT", "sell", "TAKE_PROFIT_MARKET", 2718.0)],
        )
        rows = self._run(ad)
        self.assertEqual(rows[0]["sl_px"], "--")
        self.assertEqual(rows[0]["tp_px"], "--", "串味的保护腿比 '--' 更危险")

    def test_other_symbol_legs_are_ignored(self):
        ad = _FakeAdapter(
            open_orders=[{"symbol": "ETHUSDT", "side": "sell", "price": 2408.5, "size": "0.58"}],
            algos=[_leg("BTCUSDT", "buy", "STOP_MARKET", 70000.0)],
        )
        rows = self._run(ad)
        self.assertEqual(rows[0]["sl_px"], "--")

    def test_no_legs_stays_honest_dash(self):
        ad = _FakeAdapter(
            open_orders=[{"symbol": "SOLUSDT", "side": "buy", "price": 100.0, "size": "5"}],
            algos=[],
        )
        rows = self._run(ad)
        self.assertEqual((rows[0]["sl_px"], rows[0]["tp_px"]), ("--", "--"),
                         "没腿就是没腿，不得留空也不得编造")

    def test_long_entry_matches_sell_legs(self):
        """买多挂单的保护腿是 sell 侧——镜像方向同样要能匹配上。"""
        ad = _FakeAdapter(
            open_orders=[{"symbol": "SOLUSDT", "side": "buy", "price": 97.0, "size": "5"}],
            algos=[_leg("SOLUSDT", "sell", "STOP_MARKET", 94.0),
                   _leg("SOLUSDT", "sell", "TAKE_PROFIT_MARKET", 103.0)],
        )
        rows = self._run(ad)
        self.assertEqual(rows[0]["sl_px"], "94")
        self.assertEqual(rows[0]["tp_px"], "103")

    def test_gate_native_protective_orders_matching(self):
        """Gate 原生结构：contract 为 BTC_USDT，价格在 trigger.price，text 在 initial.text。"""
        gate_algos = [
            {
                "id": "2100868994629107712",
                "trigger": {"price": "77060.0", "rule": 2},
                "initial": {"contract": "BTC_USDT", "text": "t-r20sl21170244", "auto_size": "close_long"}
            },
            {
                "id": "2100952253513859072",
                "trigger": {"price": "81000.0", "rule": 1},
                "initial": {"contract": "BTC_USDT", "text": "t-r20tp41020503", "auto_size": "close_long"}
            }
        ]
        ad = _FakeAdapter(
            open_orders=[{"contract": "BTC_USDT", "side": "buy", "price": 77620.0, "size": "177"}],
            algos=gate_algos,
        )
        rows = self._run(ad)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "BTC", "标的名称必须是干净的 BTC，绝不能带下划线 BTC_")
        self.assertEqual(rows[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(rows[0]["sl_px"], "77060")
        self.assertEqual(rows[0]["tp_px"], "81000")

    def test_signed_size_normalized_to_unsigned_sz(self):
        """Gate 等交易所原生 size 为负数时，看板挂单 sz 必须归一为正数（绝对值），绝不泄露负号。"""
        ad = _FakeAdapter(
            open_orders=[{"contract": "BTC_USDT", "side": "sell", "price": 80620.0, "size": -173}],
            algos=[],
        )
        rows = self._run(ad)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sz"], "173", "挂单 sz 必须取绝对值正数，绝不能带负号 -173")
        self.assertEqual(rows[0]["side"], "sell")


if __name__ == "__main__":
    unittest.main()


class CrossVenueProtectionVerdictTest(unittest.TestCase):
    """第一百一十八刀：外所持仓也要有**保护度判据**（此前只有 OKX 有）。

    实测的缺口：`collect_algo_protection` 给 OKX 持仓写 `protectionStatus`，
    而外所持仓只有 `exchangeSl`/`exchangeTp` —— 面板上**看不出**"这笔 binance 空仓
    到底有没有活止损"。而在 `R20_VENUE_PROTECTION_WATCHDOG` 默认关闭的当下
    （唯一会复核外所腿的周期巡检），这个字段就是运营唯一的可见面。

    判据直接复用已有专测的 `venue_protection.scan_protective_orders`
    （腿**已经取回**，零新增交易所调用）。
    """

    def _run(self, algos, positions, *, readable=True, errors=None):
        # ⚠️ 第一个入参是 **positions**（外所持仓会被 append 进去），
        # 第二个是 pending_orders_list —— 别把两者写反（本门第一版就写反了，
        # 于是持仓行全落进了被丢弃的列表里）。
        got_positions: list = []
        pending: list = []
        ad = _FakeAdapter(open_orders=[], algos=algos, positions=positions)
        if not readable:
            ad.list_protective_orders = None       # 适配器不支持 ⇒ 读不到 ≠ 没保护
        empty = _FakeAdapter(open_orders=[], algos=[])

        def _pick(venue, *a, **k):
            return ad if venue == "binance" else empty
        errs = errors if errors is not None else []
        with patch("r20_backend.exchanges.get_adapter", _pick), \
             patch("r20_backend.dashboard_payload.multi_venue._global_env_axis",
                   lambda: "demo"):
            collect_cross_venue_positions(got_positions, pending, 0, 0, 0.0,
                                          source_errors=errs)
        return got_positions, errs

    @staticmethod
    def _pos(base="UNI", size=-82.0, entry=9.0, mark=8.9):
        return [{"base": base, "symbol": f"{base}USDT", "size_signed": size,
                 "side": "short" if size < 0 else "long",
                 "entry_price": entry, "mark_price": mark, "leverage": 5}]

    def test_unprotected_position_is_labelled_and_warned(self):
        """有仓、零腿 ⇒ `unprotected` + `source_errors` 告警（这是最该吼的情形）。"""
        pending, errs = self._run([], self._pos())
        self.assertEqual(len(pending), 1)
        row = pending[0]
        self.assertEqual(row["protectionStatus"], "unprotected")
        self.assertEqual(row["protectionCoveragePct"], 0.0)
        self.assertEqual(row["protectionLegs"], 0)
        self.assertTrue(any("没有活止损腿" in e for e in errs),
                        f"无活止损必须有告警，实际 {errs}")

    def test_fully_covered_position_is_labelled_without_warning(self):
        algos = [_leg("UNIUSDT", "buy", "STOP_MARKET", 9.5, {"quantity": "82"}),
                 _leg("UNIUSDT", "buy", "TAKE_PROFIT_MARKET", 8.4, {"quantity": "82"})]
        pending, errs = self._run(algos, self._pos())
        row = pending[0]
        self.assertEqual(row["protectionStatus"], "fully_protected")
        self.assertEqual(row["protectionCoveragePct"], 100.0)
        self.assertEqual(row["protectionLegs"], 2)
        self.assertEqual(errs, [], "已保护不得告警（永远在响的告警等于没有告警）")

    def test_stale_wrong_size_leg_is_not_full_coverage(self):
        """**旧量腿**（上一仓遗留，量不符）不得被算成满覆盖 —— 这正是实测隐患。"""
        algos = [_leg("UNIUSDT", "buy", "STOP_MARKET", 9.5, {"quantity": "51"})]
        pending, _ = self._run(algos, self._pos(size=-82.0))
        row = pending[0]
        self.assertNotEqual(row["protectionStatus"], "fully_protected")
        self.assertLess(row["protectionCoveragePct"], 100.0)
        self.assertGreater(row["protectionCoveragePct"], 0.0)

    def test_unreadable_legs_are_unknown_not_unprotected(self):
        """适配器不支持读腿 ⇒ `unknown` 且**不告警**（没有证据就不下结论）。"""
        pending, errs = self._run([], self._pos(), readable=False)
        row = pending[0]
        self.assertEqual(row["protectionStatus"], "unknown")
        self.assertEqual(errs, [], "读不到 ≠ 没保护，不得谎报缺口")

    def test_expired_leg_does_not_count_as_live_stop(self):
        """过期腿不算"有活止损"（Gate 腿带 7 天 `expiration`）。"""
        past = int(time.time()) - 3600
        algos = [_leg("UNIUSDT", "buy", "STOP_MARKET", 9.5,
                      {"quantity": "82", "expiration": str(past)})]
        pending, errs = self._run(algos, self._pos())
        row = pending[0]
        self.assertEqual(row["protectionExpiry"], "expired")
        self.assertNotEqual(row["protectionStatus"], "fully_protected",
                            "腿已过期 ⇒ 不得报'完全保护'")
        self.assertTrue(any("没有活止损腿" in e for e in errs), "过期即缺口，必须告警")

    def test_no_position_no_verdict_no_noise(self):
        """空仓：不得凭空产生保护告警（否则每轮都在响）。"""
        pending, errs = self._run([], [])
        self.assertEqual(pending, [])
        self.assertEqual(errs, [])

    def test_leg_read_failure_keeps_position_visible_and_marks_unknown(self):
        """⭐ 连带伤害修复：读腿抛错**不得**让该所的持仓从面板消失。

        旧实现把 positions / open_orders / legs 挤在同一个 try 里 ⇒ 读腿失败会让
        该所**整段跳过**（持仓也一起没了）——"读不到"被渲染成"没有仓位"。
        """
        class _Raising(_FakeAdapter):
            def list_protective_orders(self, *a, **k):
                raise RuntimeError("legs endpoint 500")

        ad = _Raising(open_orders=[], algos=[], positions=self._pos())
        empty = _FakeAdapter(open_orders=[], algos=[])
        got_positions: list = []
        errs: list = []
        with patch("r20_backend.exchanges.get_adapter",
                   lambda v, *a, **k: ad if v == "binance" else empty), \
             patch("r20_backend.dashboard_payload.multi_venue._global_env_axis",
                   lambda: "demo"):
            collect_cross_venue_positions(got_positions, [], 0, 0, 0.0, source_errors=errs)
        self.assertEqual(len(got_positions), 1, "读腿失败不得吞掉持仓行")
        self.assertEqual(got_positions[0]["protectionStatus"], "unknown",
                         "读不到腿 ⇒ 不可判定（不得宣称已保护，也不得宣称缺口）")
        self.assertTrue(any("保护腿 binance" in e and "读取失败" in e for e in errs),
                        f"读腿失败必须留痕，实际 {errs}")

    def test_venue_read_failure_is_recorded_not_silent(self):
        """某所持仓读取失败 ⇒ 面板要说明"这所本轮没并入"，而不是看起来什么都没发生。"""
        class _Broken:
            def positions(self):
                raise RuntimeError("positions timeout")

        empty = _FakeAdapter(open_orders=[], algos=[])
        got_positions: list = []
        errs: list = []
        with patch("r20_backend.exchanges.get_adapter",
                   lambda v, *a, **k: _Broken() if v == "binance" else empty), \
             patch("r20_backend.dashboard_payload.multi_venue._global_env_axis",
                   lambda: "demo"):
            collect_cross_venue_positions(got_positions, [], 0, 0, 0.0, source_errors=errs)
        self.assertEqual(got_positions, [])
        self.assertTrue(any("跨所 binance" in e and "读取失败" in e for e in errs),
                        f"该所读取失败必须留痕，实际 {errs}")

    def test_other_symbol_legs_do_not_satisfy_coverage(self):
        """⭐ 别的币的腿不得被算成本仓的保护（本刀真机实测踩到：UNI 一度算到 11 张腿）。

        `_protection_verdict` 拿的是**该所全量腿**，所以必须开
        `scan_protective_orders(require_symbol_match=True)`。
        """
        algos = [_leg("ETHUSDT", "buy", "STOP_MARKET", 3000.0, {"quantity": "82"}),
                 _leg("SOLUSDT", "buy", "STOP_MARKET", 100.0, {"quantity": "82"})]
        pending, errs = self._run(algos, self._pos(base="UNI", size=-82.0))
        row = pending[0]
        self.assertEqual(row["protectionStatus"], "unprotected",
                         "别的币的腿把覆盖满足了 ⇒ 面板会说谎")
        self.assertEqual(row["protectionLegs"], 0)
        self.assertTrue(any("没有活止损腿" in e for e in errs))


class PendingOrderTimeHonestyTest(unittest.TestCase):
    """第一百二十四刀：挂单时间**不得谎报"刚刚"**，且与 OKX 侧同格式。

    真机实测（改前）：两行 binance 挂单 `time='刚刚'` 而 `cTime=''` ——
    根因是 Binance 适配器把时间只放在 `raw` 里（Gate 是 `dict(o)` 拷贝原始行，
    所以 `create_time` 本来能取到），`multi_venue` 取不到就 `cTime=""` + 写死"刚刚"。
    面板于是把任意年龄的委托显示成"刚刚"。
    """

    def _run(self, orders):
        got: list = []
        ad = _FakeAdapter(open_orders=orders, algos=[])
        empty = _FakeAdapter(open_orders=[], algos=[])

        def _pick(venue, *a, **k):
            return ad if venue == "binance" else empty
        with patch("r20_backend.exchanges.get_adapter", _pick), \
             patch("r20_backend.dashboard_payload.multi_venue._global_env_axis", lambda: "demo"):
            collect_cross_venue_positions([], got, 0, 0, 0.0, source_errors=[])
        return got

    _MS = 1789901235000          # 2026-09-20 18:47:15 (北京)

    @staticmethod
    def _order(**over):
        row = {"base": "XRP", "symbol": "XRPUSDT", "order_id": "O1", "side": "sell",
               "price": 1.383, "size": 826.5, "status": "NEW"}
        row.update(over)
        return row

    def test_binance_raw_carries_the_timestamp(self):
        """Binance 形状：时间只在 `raw` 里（真机就是这种）。"""
        got = self._run([self._order(raw={"time": self._MS, "updateTime": self._MS})])
        self.assertEqual(got[0]["cTime"], str(self._MS))
        self.assertEqual(got[0]["time"], "09-20 18:47:15")

    def test_gate_top_level_create_time_in_seconds(self):
        """Gate 形状：顶层 `create_time` 是**秒** ⇒ 归一成毫秒。"""
        got = self._run([self._order(create_time=self._MS // 1000)])
        self.assertEqual(got[0]["cTime"], str(self._MS))
        self.assertEqual(got[0]["time"], "09-20 18:47:15")

    def test_missing_time_never_claims_just_now(self):
        """取不到时间 ⇒ `--`（与 OKX 一致），**绝不**写"刚刚"。"""
        got = self._run([self._order()])
        self.assertEqual(got[0]["cTime"], "")
        self.assertEqual(got[0]["time"], "--")
        self.assertNotEqual(got[0]["time"], "刚刚")

    def test_display_time_matches_okx_formatter(self):
        """同格式契约：与 OKX 侧 `order_view` 的 `c_time_str` 对同一时间戳一致。"""
        import datetime as _dt
        from r20_backend.dashboard_payload.multi_venue import _bj_time_str
        from r20_backend.dashboard_payload.order_view import collect_pending_order_rows
        tz_bj = _dt.timezone(_dt.timedelta(hours=8))
        okx_rows: list = []
        collect_pending_order_rows(
            [{"instId": "XRP-USDT-SWAP", "cTime": self._MS, "side": "sell",
              "reduceOnly": "false", "ordType": "limit", "px": "1.383", "sz": "826.5",
              "ordId": "O1"}],
            okx_rows, tz_beijing=tz_bj, datetime=_dt)
        self.assertEqual(okx_rows[0]["time"], _bj_time_str(self._MS),
                         "两个生产者的展示时间格式必须一致")
        self.assertEqual(okx_rows[0]["time"], "09-20 18:47:15")
