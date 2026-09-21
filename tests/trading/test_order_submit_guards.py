"""下单提交（`submit_protected_limit_order`）的三道"拒单"与多所路由（第二百二十七刀）。

这个函数是**钱离开账户前的最后一道程序**。本刀钉住它拒单的每一条理由，以及多所路由的成败：

- **选所路由 / 预算预留失败** ⇒ 本轮不下单（并带上理由）；不带 `venue_ctx` 只 warn（非 AI 通路）；
- **核心安全复验**（几何/R:R）不过 ⇒ 拒单 + **释放预留**（预留不释放会白占额度）；
- **价格锚定**三连拒：BUY 限价**穿价**高于现价超阈值（会即时成交于意外价、SL 锚点失真 ⇒
  开-秒平循环放血）、SELL 反向穿价、与现价距离**荒谬**（>50%）⇒ 判定幻觉报价；取价失败不阻断；
- **多所路由**（gate/binance）：通过统一原生执行路由；路由器说失败 ⇒ 释放预留并如实回报；
  异常 ⇒ 释放预留并回报异常；
- OKX 直下：**发单前尽量对齐杠杆档位**（失败仅 warn —— 保护腿/张数/几何已定，杠杆只影响保证金效率）；
  交易所返回**无可核验订单号** ⇒ 拒单并释放预留。
"""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.trader.order_submit import submit_protected_limit_order

INST = "BTC-USDT-SWAP"


class _Okx:
    def __init__(self, *, rows=None, raises=None, lev_raises=False):
        self._rows = [{"ordId": "okx-1"}] if rows is None else rows
        self._raises = raises
        self.lev_raises = lev_raises
        self.orders = []
        self.leverage_calls = []

    def set_leverage(self, inst_id, lever, *, mgn_mode, pos_side):
        if self.lev_raises:
            raise RuntimeError("档位设置失败")
        self.leverage_calls.append((inst_id, lever))

    def place_order(self, inst_id, side, size, **kw):
        if self._raises is not None:
            raise self._raises
        self.orders.append((inst_id, side, size, kw))
        return self._rows


class _Reg:
    def native_symbol_pure(self, base, venue):
        return f"{base}_{venue.upper()}"


class Rig:
    def __init__(self, *, routing=None, price=100000.0, tp=105000.0, sl=95000.0,
                 ticker="100000", simulated=False, okx=None, venue="okx"):
        self.released = []
        self.recorded = []
        self.confirmed = []
        self.printed = []
        self.okx = okx or _Okx()
        self.routing = routing or {"ok": True, "reservation": "res-1", "venue": venue}
        self.price, self.tp, self.sl = price, tp, sl
        self.ticker = ticker
        self.simulated = simulated
        self.venue = venue
        self.geometry = (True, "", 1.0)      # 核心安全复验：默认放行，可按用例改
        self.listing_ok = True               # 合约对账（US-007）：默认放行
        self.listing_reason = ""
        self.listing_raises = None
        self.geometry_calls = []

    def run(self, *, venue_ctx=None, src=None, pos_side="long", **over):
        params = dict(
            confirm_signal_reservation=lambda r: self.confirmed.append(r),
            record_open_intent=lambda i, s: self.recorded.append((i, s)),
            release_signal_reservation=lambda r, why: self.released.append((r, why)),
            route_and_reserve_signal=lambda *a, **k: self.routing,
            MAX_LEVERAGE=20, MIN_LEVERAGE=1, canonical_base=lambda i: i.split("-")[0],
            current_environment=lambda: SimpleNamespace(
                simulated=self.simulated, mode="demo" if self.simulated else "live"),
            fetch_ticker=lambda i: {"last": self.ticker},
            okx_rest=self.okx, venue_registry=_Reg())
        params.update(over)
        # ⚠️ 隔离**真实**的 listing gate 与几何复验：
        # ① 真目录里没有我造的 `BTC_OKX` ⇒ 它会 fail-closed 拒单（那是它工作正常，
        #    本发现的旁证是它真的会拦）；
        # ② 真 `validate_quote_geometry_and_rr` 的返回元数与本文件的桩不同。
        # 本文件要测的是**其后**的价格锚定 / 多所路由 / 直下闸门，故此处按 ok 隔离。
        _listing = (patch("r20_backend.exchanges.listing.ensure_contract_listed",
                          side_effect=self.listing_raises) if self.listing_raises
                    else patch("r20_backend.exchanges.listing.ensure_contract_listed",
                               return_value=SimpleNamespace(ok=self.listing_ok,
                                                            reason=self.listing_reason)))
        with _listing, \
             patch("scripts.order_risk.validate_quote_geometry_and_rr",
                   side_effect=lambda *a, **k: (self.geometry_calls.append(a),
                                                self.geometry)[1]):
            return submit_protected_limit_order(
                INST, "buy" if pos_side == "long" else "sell", pos_side, 3.0,
                self.price, self.tp, self.sl, venue_ctx=venue_ctx, **params)


class RoutingGateTest(unittest.TestCase):
    def test_routing_refusal_blocks_the_order(self):
        rig = Rig(routing={"ok": False, "error": "预算不足"})
        ok, why = rig.run(venue_ctx={"notional_usdt": 100, "margin_usdt": 50, "intent_id": "i1"})
        self.assertFalse(ok)
        self.assertIn("预算不足", why)
        self.assertEqual(rig.okx.orders, [], "路由拒绝 ⇒ 一单都不许发")
        self.assertEqual(rig.confirmed, [])

    def test_missing_venue_ctx_only_warns(self):
        """非 AI 信号通路不带 ctx：只 warn 不闸门（保留 US-007 契约）。"""
        rig = Rig()
        with patch("sys.stdout") as out:
            rig.run(venue_ctx=None)
        self.assertEqual(len(rig.okx.orders), 1, "通用通路仍可下单")
        self.assertTrue(rig.confirmed, "成功要确认预留（此时应为 None）")

    def test_reservation_is_confirmed_on_success(self):
        rig = Rig()
        ok, order_id = rig.run(venue_ctx={"notional_usdt": 100, "margin_usdt": 50})
        self.assertTrue(ok)
        self.assertEqual(order_id, "okx-1")
        self.assertEqual(rig.confirmed, ["res-1"], "成功后必须确认预留（否则额度白占）")
        self.assertEqual(rig.recorded, [(INST, "buy")], "成功要记录开仓意图")


class SafetyReverificationTest(unittest.TestCase):
    def test_geometry_failure_rejects_and_releases(self):
        rig = Rig()
        rig.geometry = (False, "R:R 不足", 0.5)
        ok, why = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1})
        self.assertFalse(ok)
        self.assertIn("核心安全复验拒绝", why)
        self.assertEqual(rig.released, [("res-1", "核心安全复验拒绝")],
                         "拒单必须释放预留（不释放＝白占额度）")
        self.assertEqual(rig.okx.orders, [])


class PriceAnchorGateTest(unittest.TestCase):
    """LLM 幻觉价锚定：几何/R:R 只验三者关系，从不比对现价 ⇒ 这里补上现价比对。"""

    def _run_with(self, *, price, pos_side="long"):
        rig = Rig(price=price, ticker="100000")
        ctx = {"notional_usdt": 1, "margin_usdt": 1}
        with patch("r20_backend.exchanges.listing.ensure_contract_listed",
                   return_value=SimpleNamespace(ok=True, reason="")), \
             patch("scripts.order_risk.validate_quote_geometry_and_rr",
                   return_value=(True, "", 1.0)):
            return rig, submit_protected_limit_order(
                INST, "buy" if pos_side == "long" else "sell", pos_side, 3.0, price,
                105000.0, 95000.0, venue_ctx=ctx,
                confirm_signal_reservation=rig.confirmed.append,
                record_open_intent=lambda i, s: rig.recorded.append((i, s)),
                release_signal_reservation=lambda r, why: rig.released.append((r, why)),
                route_and_reserve_signal=lambda *a, **k: rig.routing,
                MAX_LEVERAGE=20, MIN_LEVERAGE=1, canonical_base=lambda i: i.split("-")[0],
                current_environment=lambda: SimpleNamespace(simulated=False, mode="live"),
                fetch_ticker=lambda i: {"last": "100000"}, okx_rest=rig.okx,
                venue_registry=_Reg())

    def test_buy_above_market_beyond_cross_threshold_is_rejected(self):
        """BUY 限价高于现价 ⇒ 即时成交于意外价、SL 锚点失真。"""
        rig, (ok, why) = self._run_with(price=101000.0)
        self.assertFalse(ok)
        self.assertIn("价格锚定拒绝", why)
        self.assertIn("穿价幻觉", why)
        self.assertEqual(rig.released, [("res-1", "价格锚定拒绝")])
        self.assertEqual(rig.okx.orders, [])

    def test_sell_below_market_beyond_cross_threshold_is_rejected(self):
        rig, (ok, why) = self._run_with(price=99000.0, pos_side="short")
        self.assertFalse(ok)
        self.assertIn("穿价幻觉", why)

    def test_absurdly_far_price_is_rejected(self):
        """与现价距离超荒谬阈值 ⇒ 同样判为幻觉（回踩远挂单在 50% 内仍放行）。"""
        rig, (ok, why) = self._run_with(price=40000.0)
        self.assertFalse(ok)
        self.assertIn("荒谬阈值", why)

    def test_far_but_plausible_pullback_is_allowed(self):
        rig, (ok, why) = self._run_with(price=97000.0)
        self.assertTrue(ok, f"回踩方向的远挂单是合法策略，不该被闸掉：{why}")


class MultiVenueRouteTest(unittest.TestCase):
    def _run(self, router, venue="binance"):
        rig = Rig(venue=venue, routing={"ok": True, "reservation": "res-9", "venue": venue})
        # ⚠️ 必须 patch **真模块的属性**：代码写的是 `from r20_backend import execution_router`
        # ⇒ 一旦该模块被别处导入过，`from … import …` 走的是**包属性**，
        # `patch.dict(sys.modules, {...})` 塞的假模块**不会被用到**（单跑本文件时恰好没导入过，
        # 于是"单跑绿、全量红"）。这正是隔离类缺陷的典型形态。
        import r20_backend.execution_router as real_router
        with patch.object(real_router, "open_protected_position", side_effect=router), \
             patch("r20_backend.exchanges.listing.ensure_contract_listed",
                   return_value=SimpleNamespace(ok=True, reason="")), \
             patch("scripts.order_risk.validate_quote_geometry_and_rr",
                   return_value=(True, "", 1.0)):
            return rig, rig.run(venue_ctx={"notional_usdt": 100, "margin_usdt": 50,
                                           "leverage": 5, "confidence": 90})

    def test_router_success_records_intent_and_confirms(self):
        calls = []

        def router(payload, **kw):
            calls.append((payload, kw))
            return {"ok": True, "order_id": "bn-1"}

        rig, (ok, order_id) = self._run(router)
        self.assertTrue(ok)
        self.assertEqual(order_id, "bn-1")
        self.assertEqual(calls[0][0]["venue"], "binance")
        self.assertEqual(calls[0][0]["asset"], "BTC")
        self.assertEqual(calls[0][0]["leverage"], 5.0)
        self.assertEqual(calls[0][0]["confidence"], 90.0, "per-venue 置信度门禁要用原始 AI 置信度")
        self.assertEqual(rig.recorded, [(INST, "buy")])
        self.assertEqual(rig.confirmed, ["res-9"])

    def test_router_refusal_releases_the_reservation(self):
        rig, (ok, why) = self._run(lambda payload, **kw: {"ok": False, "detail": "深度不足"})
        self.assertFalse(ok)
        self.assertIn("BINANCE 下单失败", why)
        self.assertEqual(rig.released, [("res-9", "深度不足")])
        self.assertEqual(rig.confirmed, [])

    def test_router_exception_releases_and_reports(self):
        def boom(payload, **kw):
            raise RuntimeError("路由器炸了")
        rig, (ok, why) = self._run(boom)
        self.assertFalse(ok)
        self.assertIn("执行异常", why)
        self.assertEqual(rig.released, [("res-9", "多所执行异常: 路由器炸了")])


class OkxDirectTest(unittest.TestCase):
    def test_leverage_alignment_is_attempted_before_order(self):
        rig = Rig()
        with patch("scripts.order_risk.validate_quote_geometry_and_rr",
                   return_value=(True, "", 1.0)):
            ok, _ = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1, "leverage": 5})
        self.assertTrue(ok)
        self.assertEqual(rig.okx.leverage_calls, [(INST, 5)],
                         "发单前应尽量把档位对齐到 AI 裁决的杠杆")

    def test_leverage_alignment_failure_does_not_block_the_order(self):
        okx = _Okx(lev_raises=True)
        rig = Rig(okx=okx)
        with patch("scripts.order_risk.validate_quote_geometry_and_rr",
                   return_value=(True, "", 1.0)):
            ok, _ = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1, "leverage": 5})
        self.assertTrue(ok, "设档失败不该阻断（保护腿/张数/几何已定）")
        self.assertEqual(len(okx.orders), 1)

    def test_response_without_order_id_is_rejected(self):
        okx = _Okx(rows=[{}])
        rig = Rig(okx=okx)
        with patch("scripts.order_risk.validate_quote_geometry_and_rr",
                   return_value=(True, "", 1.0)):
            ok, why = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1})
        self.assertFalse(ok)
        self.assertIn("verifiable order id", why)
        self.assertEqual(rig.confirmed, [], "没有可核验订单号 ⇒ 不许确认预留")

    def test_order_exception_releases_the_reservation(self):
        okx = _Okx(raises=RuntimeError("交易所 500"))
        rig = Rig(okx=okx)
        with patch("scripts.order_risk.validate_quote_geometry_and_rr",
                   return_value=(True, "", 1.0)):
            ok, why = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1})
        self.assertFalse(ok)
        self.assertIn("交易所 500", why)
        self.assertEqual(rig.released, [("res-1", "下单异常")])


if __name__ == "__main__":
    unittest.main()

class DemoRescaleTest(unittest.TestCase):
    """demo 沙盒与真实盘价差 >5% 时按沙盒价**重算**报价与保护价（否则必然废单）。

    重算后还要**再验一遍几何边界**：long 的 SL 必须低于入场、TP 必须高于入场（short 反之），
    否则就地按比例推回来。
    """

    def _run(self, *, price, tp, sl, ticker, pos_side="long"):
        rig = Rig(price=price, tp=tp, sl=sl, ticker=ticker, simulated=True)
        rig.geometry = (True, "", 1.0)
        return rig, rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1},
                            pos_side=pos_side)

    def test_long_prices_are_rescaled_to_the_demo_market(self):
        rig, (ok, _) = self._run(price=100000.0, tp=105000.0, sl=95000.0, ticker="95000")
        self.assertTrue(ok)
        _, _, _, kw = rig.okx.orders[0]
        self.assertAlmostEqual(kw["px"], 95000.0, places=4,
                               msg="入场价要按沙盒价重算（否则单子挂在真实盘根本不会成交）")
        self.assertLess(kw["attach_sl"], kw["px"], "重算后 SL 仍必须在入场下方")
        self.assertGreater(kw["attach_tp"], kw["px"], "重算后 TP 仍必须在入场上方")

    def test_long_inverted_brackets_after_rescale_are_pushed_back(self):
        """重算把 TP 压到入场下方 ⇒ 必须按比例推回（否则保护方向反了）。"""
        rig, (ok, _) = self._run(price=100000.0, tp=96000.0, sl=95000.0, ticker="95000")
        self.assertTrue(ok)
        _, _, _, kw = rig.okx.orders[0]
        self.assertGreater(kw["attach_tp"], kw["px"], f"TP 被推回入场上方：{kw}")
        self.assertLess(kw["attach_sl"], kw["px"])

    def test_short_brackets_are_mirrored(self):
        rig, (ok, _) = self._run(price=100000.0, tp=100000.0, sl=100000.0,
                                 ticker="95000", pos_side="short")
        self.assertTrue(ok)
        _, _, _, kw = rig.okx.orders[0]
        self.assertGreater(kw["attach_sl"], kw["px"], "空头 SL 必须在入场上方")
        self.assertLess(kw["attach_tp"], kw["px"], "空头 TP 必须在入场下方")

    def test_small_divergence_does_not_rescale(self):
        """价差在阈值内不改价。（现价取在**上方**：BUY 限价低于现价才不算穿价。）"""
        rig, (ok, _) = self._run(price=100000.0, tp=105000.0, sl=95000.0, ticker="101000")
        self.assertTrue(ok)
        _, _, _, kw = rig.okx.orders[0]
        self.assertAlmostEqual(kw["px"], 100000.0, places=4, msg="价差在阈值内不该改价")


class GarbageLeverageInCtxTest(unittest.TestCase):
    def test_garbage_leverage_is_treated_as_absent(self):
        """`venue_ctx` 里的杠杆是垃圾值 ⇒ 视同没给（不设档、不抛）。"""
        rig = Rig()
        ok, _ = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1, "leverage": "abc"})
        self.assertTrue(ok)
        self.assertEqual(rig.okx.leverage_calls, [], "垃圾杠杆值不许拿去设档")

class ListingGateTest(unittest.TestCase):
    """环境维合约对账（US-007）：已下架/未上市 ⇒ fail-closed 拒单并释放预留。"""

    def test_delisted_contract_is_rejected(self):
        rig = Rig()
        rig.listing_ok, rig.listing_reason = False, "合约已下架"
        ok, why = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1})
        self.assertFalse(ok)
        self.assertIn("合约对账拒绝", why)
        self.assertEqual(rig.released, [("res-1", "合约对账拒绝")])
        self.assertEqual(rig.okx.orders, [], "下架合约绝不许下单")

    def test_listing_gate_unavailable_does_not_block(self):
        """对账**不可用**（目录拉不到）⇒ 只 warn 放行（对账是增强不是闸门）。"""
        rig = Rig()
        rig.listing_raises = RuntimeError("目录服务挂了")
        ok, _ = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1})
        self.assertTrue(ok, "对账不可用不该阻断下单")


class RescaleFailureTraceTest(unittest.TestCase):
    def test_rescale_failure_is_traced_and_does_not_block(self):
        """重算过程出异常 ⇒ **不阻断**，但必须**出声**（原来这里是静默 `pass`）。

        实测行为（如实钉住）：异常发生在**重算中途** ⇒ 入场价已改成沙盒价、而 tp/sl 仍是原值；
        所幸**不可绕过的几何复验在其后**仍会跑，不一致的报价在那里被拒（下面第二条断言钉这一点）。
        """
        import io
        from contextlib import redirect_stdout
        rig = Rig(price=100000.0, tp="不是数字", sl=95000.0, ticker="95000", simulated=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok, _ = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1})
        self.assertTrue(ok, "重算失败不该阻断下单")
        self.assertIn("沙盒报价重算失败", buf.getvalue(),
                      "失败必须留痕：原来静默 pass 会让「重算出 bug」毫无痕迹地过去")
        _, _, _, kw = rig.okx.orders[0]
        self.assertEqual(kw["px"], 95000.0, "中途失败：入场价已按沙盒价改过")
        self.assertTrue(rig.geometry_calls,
                        "即便重算中途失败，**核心安全复验也必须仍然跑到**（不可绕过）")


class LongStopPushBackTest(unittest.TestCase):
    def test_long_stop_above_entry_after_rescale_is_pushed_below(self):
        """重算把 SL 推到入场上方 ⇒ 必须按比例压回下方（否则多头的止损方向反了）。"""
        rig = Rig(price=100000.0, tp=105000.0, sl=101000.0, ticker="95000", simulated=True)
        ok, _ = rig.run(venue_ctx={"notional_usdt": 1, "margin_usdt": 1})
        self.assertTrue(ok)
        _, _, _, kw = rig.okx.orders[0]
        self.assertLess(kw["attach_sl"], kw["px"], f"SL 要被压回入场下方：{kw}")

