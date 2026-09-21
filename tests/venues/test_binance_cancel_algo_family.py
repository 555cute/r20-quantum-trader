"""Binance Algo 条件单清场族（第二百六十七刀）。

**审计 D3 的核心**：逐笔撤单失败**必须收集上报** —— 旧实现 `pass` 吞掉后仍返回
`{"code":"200","msg":"success"}`，于是**全败也报成功**；而「保护单清场链路」正是据此放行
⇒ **裸旧单残留**（该清的保护腿还留在场上）。

即清场的返回值必须能区分三态：**全部撤掉 / 部分撤掉 / 一笔都没撤掉**。
"""

import unittest

from r20_backend.exchanges.binance import BinanceAdapter


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.native_symbol = lambda s: f"{s}USDT" if s else ""
        self.canceled = []

    def _algos(self, rows):
        self.ad.list_protective_orders = lambda symbol=None: rows

    def _cancel(self, *, fail_on=()):
        fail = {str(x) for x in fail_on}

        def _c(*, algo_id=None, client_algo_id=None):
            if str(algo_id) in fail:
                raise RuntimeError(f"撤 {algo_id} 被交易所拒绝")
            self.canceled.append(str(algo_id))
            return {"code": "200", "algoId": algo_id}
        self.ad.cancel_algo_order = _c


class CancelAllAlgoOpenOrdersTest(_Base):
    def test_all_canceled_reports_success(self):
        self._algos([{"algo_id": "1"}, {"algo_id": "2"}])
        self._cancel()
        res = self.ad.cancel_all_algo_open_orders(symbol="BTC")
        self.assertTrue(res["success"])
        self.assertEqual(res["attempted"], 2)
        self.assertEqual(res["failed"], [])
        self.assertEqual(self.canceled, ["1", "2"])

    def test_partial_failure_is_reported_not_faked(self):
        """★ 一笔失败 ⇒ `success:False` + `failed` 带 algo_id 与原因（不许伪造成功）。"""
        self._algos([{"algo_id": "1"}, {"algo_id": "2"}])
        self._cancel(fail_on=["2"])
        res = self.ad.cancel_all_algo_open_orders(symbol="BTC")
        self.assertFalse(res["success"], "部分失败就不是成功")
        self.assertEqual(res["attempted"], 2)
        self.assertEqual([f["algo_id"] for f in res["failed"]], ["2"])
        self.assertIn("被交易所拒绝", res["failed"][0]["error"])
        self.assertEqual(self.canceled, ["1"])

    def test_all_failed_is_not_success_either(self):
        """全败 ⇒ 同样 `success:False`（旧实现正是在这里伪报成功 ⇒ 裸旧单残留）。"""
        self._algos([{"algo_id": "1"}, {"algo_id": "2"}])
        self._cancel(fail_on=["1", "2"])
        res = self.ad.cancel_all_algo_open_orders(symbol="BTC")
        self.assertFalse(res["success"])
        self.assertEqual(res["canceled"], [])
        self.assertEqual(len(res["failed"]), 2)

    def test_rows_without_any_id_are_skipped_entirely(self):
        """没有 `algo_id` 也没有 `id` 的行 ⇒ **完全跳过**（不计入 `attempted`，也不假装撤过）。"""
        self._algos([{"id": ""}, {"algo_id": "9"}])
        self._cancel()
        res = self.ad.cancel_all_algo_open_orders(symbol="BTC")
        self.assertEqual(res["attempted"], 1)
        self.assertEqual(self.canceled, ["9"])

    def test_cancel_protective_orders_delegates(self):
        """`cancel_protective_orders` 是别名：如实委托，不另搞一套语义。"""
        calls = []
        self.ad.cancel_all_algo_open_orders = lambda **kw: calls.append(kw) or {"success": True}
        out = self.ad.cancel_protective_orders("BTC")
        self.assertEqual(out, {"success": True})
        self.assertEqual(calls, [{"symbol": "BTC"}])
