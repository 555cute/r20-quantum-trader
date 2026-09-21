"""Gate 保护腿挂单与棘轮改单（第二百七十七刀）。

| 语义 | 纪律 |
|---|---|
| 双腿 | `tp`/`sl` 各一条 reduce_only 触发单；**都没给 ⇒ 拒**（不挂空腿）|
| dual 载荷 | 调用方按只读探测结果传 `position_mode=dual` ⇒ **预先**用 `auto_size`（不浪费一次注定被拒的请求）；否则 `size=0 + close=true` |
| 反应式兼容 | 老调用方没传探测结果时，被 `AUTO_INVALID_PARAM_CLOSE`/`dual mode` 拒 ⇒ 换 `auto_size` **再试一次** |
| 回执 | 缺 `id` ⇒ `bad_response`（**受理 ≠ 挂上**）|
| ★ 回滚 | 任一条腿半途失败 ⇒ **撤掉已挂腿再上抛**（回滚失败只吞，但**绝不留下裸单**）|
| 棘轮 | `amend_stop_loss` **优先原生 amend**（同单改价、无裸仓缝隙）；仅在明确不支持时回退「先挂新→再撤旧」；撤旧失败可容忍（双 SL 共存是安全的）|
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from r20_backend.exchanges.gate import (
    GateAdapter, GateAPIError, AUTO_SIZE_CLOSE_LONG, AUTO_SIZE_CLOSE_SHORT,
)
from r20_backend.exchanges.base import ExchangeCapabilityError


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter.__new__(GateAdapter)
        self.ad.base_url = "https://api.gateio.ws"
        self.ad.native_symbol = lambda s: f"{s}_USDT" if s else ""
        self.ad.capabilities = SimpleNamespace(native_amend=True)
        self.sent = []
        self.cancelled = []

    def _send(self, *, results=None, fail=None):
        """`results` 按调用序号给回执；`fail` 是 {序号: 异常}。"""
        state = {"n": 0}

        def _req(method, path, params=None, body=None, **kw):
            state["n"] += 1
            self.sent.append((method, path, dict(params or {}), dict(body or {})))
            if fail and state["n"] in fail:
                raise fail[state["n"]]
            if results and state["n"] <= len(results):
                return results[state["n"] - 1]
            return {"id": f"id{state['n']}"}
        self.ad.signed_request = _req
        self.ad.cancel_price_order = lambda oid: (self.cancelled.append(str(oid)), {"ok": True})[1]
        return self.ad


class AttachProtectiveOrdersTest(_Base):
    def test_requires_at_least_one_leg(self):
        with self.assertRaises(ExchangeCapabilityError):
            self.ad.attach_protective_orders("BTC", "long")
        self.assertEqual(self.sent, [])

    def test_dual_mode_payload_is_chosen_up_front(self):
        """★ 传了探测结果 ⇒ **预先**按 dual 构造（省掉一次注定被拒的请求）。"""
        self._send()
        placed = self.ad.attach_protective_orders("BTC", "long", tp_px=110, sl_px=90,
                                                  position_mode="dual")
        self.assertEqual(placed, {"tp": "id1", "sl": "id2"})
        for _, _, _, body in self.sent:
            init = body["initial"]
            self.assertEqual(init["auto_size"], AUTO_SIZE_CLOSE_LONG, "多头平仓腿 = close_long")
            self.assertNotIn("close", init, "dual 模式不传 close（会被拒）")
            self.assertNotIn("size", init)

    def test_single_mode_uses_close_true(self):
        self._send()
        self.ad.attach_protective_orders("BTC", "short", sl_px=90, position_mode="single")
        init = self.sent[-1][3]["initial"]
        self.assertIs(init["close"], True)
        self.assertEqual(init["size"], 0, "单向模式用 size=0 + close=true")
        self.assertEqual(self.sent[-1][3]["trigger"]["rule"], 1, "空 SL：价≥触发")

    def test_reactive_retry_when_mode_was_not_probed(self):
        """老调用方未传探测结果 ⇒ 被拒后换 `auto_size` **再试一次**（兼容退路）。"""
        self._send(results=[None, {"id": "ok"}],
                   fail={1: GateAPIError("AUTO_INVALID_PARAM_CLOSE", "dual mode requires auto_size")})
        placed = self.ad.attach_protective_orders("BTC", "long", sl_px=90)
        self.assertEqual(placed["sl"], "ok")
        self.assertEqual(len(self.sent), 2, "第一次被拒、第二次换载荷成功")
        second = self.sent[1][3]["initial"]
        self.assertEqual(second["auto_size"], AUTO_SIZE_CLOSE_LONG)
        self.assertNotIn("close", second)

    def test_other_errors_are_not_retried_and_get_rolled_back(self):
        """★ 非「模式不匹配」的错误 ⇒ **不重试**，且**回滚已挂腿**（不留裸单）。"""
        self._send(results=[{"id": "tp-id"}],
                   fail={2: GateAPIError("INVALID_PARAM", "bad trigger price")})
        with self.assertRaises(GateAPIError):
            self.ad.attach_protective_orders("BTC", "long", tp_px=110, sl_px=90)
        self.assertEqual(self.cancelled, ["tp-id"], "已挂的 TP 腿必须被撤掉")
        self.assertEqual(len(self.sent), 2, "只发了 tp 与失败的 sl，共 2 次")

    def test_missing_receipt_id_is_a_bad_response_and_rolls_back(self):
        """★ 缺 `id` ⇒ `bad_response`（**受理 ≠ 挂上**），并回滚之前的腿。"""
        self._send(results=[{"id": "tp-id"}, {"no_id": True}])
        with self.assertRaises(GateAPIError) as ctx:
            self.ad.attach_protective_orders("BTC", "long", tp_px=110, sl_px=90)
        self.assertEqual(ctx.exception.label, "bad_response")
        self.assertIn("缺 id", str(ctx.exception))
        self.assertEqual(self.cancelled, ["tp-id"])

    def test_rollback_failure_is_tolerated_but_original_error_surfaces(self):
        """回滚本身失败只吞掉，**原始错误照旧上抛**（不掩盖真因）。"""
        self._send(results=[{"id": "tp-id"}], fail={2: GateAPIError("X", "boom")})
        self.ad.cancel_price_order = lambda oid: (_ for _ in ()).throw(RuntimeError("撤不动"))
        with self.assertRaises(GateAPIError) as ctx:
            self.ad.attach_protective_orders("BTC", "long", tp_px=110, sl_px=90)
        self.assertEqual(ctx.exception.label, "X", "上抛的是原始错误")


class ListAndCancelProtectiveTest(_Base):
    def test_list_missing_contract_is_empty_but_other_errors_raise(self):
        self._send(results=[[{"id": "1", "contract": "BTC_USDT"}]])
        self.assertEqual(len(self.ad.list_protective_orders("BTC")), 1)
        self.assertEqual(self.sent[-1][2]["contract"], "BTC_USDT")
        self.assertEqual(self.sent[-1][2]["status"], "open")

        self._send(fail={1: GateAPIError("CONTRACT_NOT_FOUND", "unknown contract")})
        self.assertEqual(self.ad.list_protective_orders("NOPE"), [])
        self._send(fail={1: GateAPIError("INVALID_KEY", "bad key")})
        with self.assertRaises(GateAPIError):
            self.ad.list_protective_orders("BTC")

    def test_cancel_protective_orders_swallows_failures_into_a_print(self):
        """⚠️ **实测边界（列待议）**：这只实现了「尽力撤」—— 某条失败**只 `print`**，
        返回列表**只含成功项**，调用方**无法区分**「全部撤掉」与「部分失败」。

        与 Binance 侧审计 D3 的修法（逐笔失败必须收集上报、不许把全败报成成功）**不一致**：
        本仓记录在案的钱路形态是「清场的成功必须由『每一笔都没失败』推出」。此处**只钉现状、未擅自改**。
        """
        self._send(results=[[{"id": "1"}, {"id": "2"}]])
        state = {"n": 0}

        def _cancel(oid):
            state["n"] += 1
            if str(oid) == "2":
                raise RuntimeError("撤 2 被拒")
            return {"ok": str(oid)}
        self.ad.cancel_price_order = _cancel
        with patch("builtins.print") as pr:
            out = self.ad.cancel_protective_orders("BTC")
        self.assertEqual(out, [{"ok": "1"}], "返回值只含成功项 ⇒ 失败被静默")
        self.assertTrue(pr.called, "失败只留在 stdout 打印里")
        self.assertEqual(state["n"], 2)


class AmendPriceOrderTest(_Base):
    def test_requires_order_id(self):
        for bad in (None, ""):
            with self.subTest(order_id=bad):
                with self.assertRaises(ExchangeCapabilityError):
                    self.ad.amend_price_order(bad, trigger_price="1")

    def test_auto_size_is_validated(self):
        with self.assertRaises(ExchangeCapabilityError):
            self.ad.amend_price_order("1", auto_size="close_both")
        self._send()
        self.ad.amend_price_order("1", auto_size=AUTO_SIZE_CLOSE_SHORT)
        self.assertEqual(self.sent[-1][3]["auto_size"], AUTO_SIZE_CLOSE_SHORT)

    def test_only_given_fields_are_sent(self):
        self._send()
        self.ad.amend_price_order("1", trigger_price=100.5, price_type=1)
        body = self.sent[-1][3]
        self.assertEqual(body, {"order_id": "1", "trigger_price": "100.5", "price_type": 1},
                         "只传给出的字段；order_id 一律字符串")
        self.assertEqual(self.sent[-1][0], "PUT")
        self._send()
        self.ad.amend_price_order(7, amount="1.250")     # 十进制张数走字符串
        self.assertEqual(self.sent[-1][3]["amount"], "1.250")
        self._send()
        self.ad.amend_price_order(7, size=3)
        self.assertEqual(self.sent[-1][3]["size"], 3)


class AmendStopLossTest(_Base):
    def test_prefers_native_amend_when_available(self):
        """★ 棘轮**优先原生 amend**（同单改价，天然无裸仓缝隙）。"""
        self._send(results=[{"id": "old-sl"}])
        oid = self.ad.amend_stop_loss("BTC", "long", "old-sl", 95.0)
        self.assertEqual(oid, "old-sl")
        self.assertTrue(self.sent[-1][1].endswith("/price_orders/amend"))
        self.assertEqual(self.cancelled, [], "原生改单成功 ⇒ 不需要撤旧")

    def test_falls_back_to_place_new_then_cancel_old(self):
        """明确不支持 amend ⇒ 回退「先挂新（保护无缝隙）→ 再撤旧」。"""
        self._send(results=[None, {"id": "new-sl"}],
                   fail={1: GateAPIError("NOT_SUPPORTED", "amend unavailable")})
        oid = self.ad.amend_stop_loss("BTC", "long", "old-sl", 95.0)
        self.assertEqual(oid, "new-sl")
        self.assertEqual(self.cancelled, ["old-sl"], "新腿挂上后才撤旧腿")

    def test_capability_error_from_amend_is_re_raised(self):
        """`ExchangeCapabilityError`（如 auto_size 非法）⇒ **上抛**，不当成「不支持」静默回退
        （静默回退会绕过调用方的参数校验错，把问题藏成一次"成功"的挂新单）。"""
        self.ad.amend_price_order = lambda *a, **k: (_ for _ in ()).throw(
            ExchangeCapabilityError("auto_size 非法"))
        with self.assertRaises(ExchangeCapabilityError):
            self.ad.amend_stop_loss("BTC", "long", "old-sl", 95.0)
        self.assertEqual(self.sent, [], "能力错误不得触发任何请求")


if __name__ == "__main__":
    unittest.main()
