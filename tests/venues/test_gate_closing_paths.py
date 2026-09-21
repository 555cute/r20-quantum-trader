"""Gate 收口：大户比通道、规格缺省、凭证、持仓过滤、杠杆、改单尾部（第二百七十九刀）。

补齐 `gate.py` 最后一批分支，让该文件除个别结构性假象外**全部被用例走过**。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from r20_backend.exchanges.gate import GateAdapter, GateAPIError


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter.__new__(GateAdapter)
        self.ad.base_url = "https://api.gateio.ws"
        self.ad.environment = "demo"
        self.ad.native_symbol = lambda s: f"{s}_USDT" if s else ""
        self.ad.canonical = lambda s: str(s).split("_")[0]
        self.calls = []

    def _public(self, payload):
        def _get(path, params=None, **kw):
            self.calls.append((path, dict(params or {})))
            return payload
        self.ad._public_get = _get
        return self.ad

    def _signed(self, payload):
        def _req(method, path, params=None, body=None, **kw):
            self.calls.append((method, path, dict(params or {}), dict(body or {})))
            return payload
        self.ad.signed_request = _req
        return self.ad


class TopTraderRatioTest(_Base):
    def test_primary_channel_with_size_ratio(self):
        self._public([{"top_lsr_size": "1.8", "lsr_account": "0.9"}])
        self.assertEqual(self.ad.fetch_top_trader_ratio("BTC"), 1.8)

    def test_falls_back_to_account_ratio_when_size_ratio_unusable(self):
        """`top_lsr_size` 缺失或 ≤0 ⇒ 退到 `lsr_account`（口径不同但总比没有好）。"""
        self._public([{"lsr_account": "0.75"}])
        self.assertEqual(self.ad.fetch_top_trader_ratio("BTC"), 0.75)
        self._public([{"top_lsr_size": "0", "lsr_account": "0.5"}])
        self.assertEqual(self.ad.fetch_top_trader_ratio("BTC"), 0.5)

    def test_session_channel_is_tried_when_primary_is_empty(self):
        """主通道空 ⇒ **换 session 通道**再试；200 才采信，非 200 忽略。"""
        self._public([])
        resp = SimpleNamespace(status_code=200, json=lambda: [{"top_lsr_size": "2.5"}])
        session = MagicMock()
        session.get.return_value = resp
        self.ad.get_session = lambda: session
        self.assertEqual(self.ad.fetch_top_trader_ratio("BTC"), 2.5)

    def test_all_failures_return_none(self):
        """★ 两个通道都拿不到 / 比例非正 / 类型不对 ⇒ **`None`**（不拿 0 冒充中性）。"""
        self._public([])
        session = MagicMock()
        session.get.side_effect = RuntimeError("出口被封")
        self.ad.get_session = lambda: session
        self.assertIsNone(self.ad.fetch_top_trader_ratio("BTC"), "通道都炸 ⇒ None")
        self._public([{"top_lsr_size": "-1", "lsr_account": "0"}])
        self.assertIsNone(self.ad.fetch_top_trader_ratio("BTC"), "比例非正 ⇒ None")
        self._public([{"top_lsr_size": "abc"}])
        self.assertIsNone(self.ad.fetch_top_trader_ratio("BTC"), "类型不对 ⇒ None")


class SpecKeysPositionsTest(_Base):
    def test_load_spec_unknown_contract_is_none(self):
        self._public([{"name": "ETH_USDT"}])
        self.assertIsNone(self.ad._load_spec("BTC_USDT"), "查不到规格 ⇒ None（不编造）")

    def test_real_keys_returns_the_pair_and_refuses_half_of_it(self):
        """走**真** `_keys`：齐备 ⇒ 返回；缺一半 ⇒ 拒（半份凭证等于没有）。"""
        from r20_backend.exchanges import ExchangeCapabilityError
        with patch("r20_backend.exchanges.registry.venue_credentials",
                   return_value=("K", "S")):
            self.assertEqual(GateAdapter._keys(self.ad), ("K", "S"))
        with patch("r20_backend.exchanges.registry.venue_credentials",
                   return_value=("K", "")):
            with self.assertRaises(ExchangeCapabilityError):
                GateAdapter._keys(self.ad)

    def test_positions_skips_zero_rows(self):
        self._signed([{"contract": "BTC_USDT", "size": 0},
                      {"contract": "BTC_USDT", "size": 2.5, "entry_price": "100"}])
        out = self.ad.positions()
        self.assertEqual(len(out), 1, "零仓跳过")
        self.assertEqual(out[0]["side"], "long")
        self.assertEqual(self.calls[-1][2], {"holding": "true"}, "只问有持仓的行")

    def test_non_dict_row_crashes_because_there_is_no_type_guard(self):
        """⚠️ **实测边界（列待议）**：`positions()` 直接对每行调 `.get()`，**没有**
        `isinstance(p, dict)` 守卫 ⇒ 回包里混进一个非 dict 行就抛 `AttributeError`，
        **整次持仓读取作废**。

        对比：Binance 的 `positions()`/`open_orders()` 都有「非 dict 行跳过」的守卫，
        本仓已把该形态写成纪律（**单行坏掉不该毁掉整次读取**）⇒ 两所**不一致**。
        改它属失败语义变更（吞掉 vs 上抛）⇒ 只钉现状、列为待议。
        """
        self._signed([{"contract": "BTC_USDT", "size": 1}, "not-a-dict"])
        with self.assertRaises(AttributeError):
            self.ad.positions()

    def test_set_leverage_posts_on_the_position_endpoint(self):
        self._signed({"ok": True})
        self.ad.set_leverage("BTC", 3.7, margin_mode="isolated")
        method, path, params = self.calls[-1][0], self.calls[-1][1], self.calls[-1][2]
        self.assertEqual(method, "POST")
        self.assertTrue(path.endswith("/positions/BTC_USDT/leverage"))
        self.assertEqual(params, {"leverage": "3", "margin_mode": "isolated"},
                         "杠杆取整数、档位随参数下发")


class AmendTailTest(_Base):
    def test_cancel_price_order_hits_the_price_orders_resource(self):
        self._signed({"ok": True})
        self.ad.cancel_price_order("999")
        method, path, _ = self.calls[-1][0], self.calls[-1][1], self.calls[-1][2]
        self.assertEqual(method, "DELETE")
        self.assertTrue(path.endswith("/price_orders/999"),
                        "保护单是独立资源族，不能撤普通 orders 端点")

    def test_amend_passes_close_flag_through(self):
        self._signed({"ok": True})
        self.ad.amend_price_order("1", close=True)
        self.assertIs(self.calls[-1][3]["close"], True)
        self._signed({"ok": True})
        self.ad.amend_price_order("1", close=False)
        self.assertIs(self.calls[-1][3]["close"], False, "只传给出的字段，且按布尔规范化")

    def test_fallback_tolerates_a_failed_old_leg_cancel(self):
        """回退序列里撤旧腿失败**只吞掉**：双 SL 短暂共存是安全的（两条都 reduce_only）。"""
        state = {"n": 0}

        def _req(method, path, params=None, body=None, **kw):
            state["n"] += 1
            if state["n"] == 1:                       # 原生 amend 失败 ⇒ 触发回退
                raise GateAPIError("NOT_SUPPORTED", "amend unavailable")
            return {"id": "new-sl"}
        self.ad.signed_request = _req
        self.ad.attach_protective_orders = lambda *a, **k: {"sl": "new-sl"}
        self.ad.cancel_price_order = lambda oid: (_ for _ in ()).throw(RuntimeError("撤不动"))
        oid = self.ad.amend_stop_loss("BTC", "long", "old-sl", 95.0)
        self.assertEqual(oid, "new-sl", "撤旧失败不影响返回值（新腿已就位）")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
