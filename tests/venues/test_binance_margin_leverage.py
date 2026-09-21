"""Binance 保证金档读取与杠杆设置（第二百六十三刀）。

这一族是 2026-09-16 的 P1「**Binance 一单都下不出去**」的正面现场：旧实现**无条件**先调
`/fapi/v1/marginType`，而该端点在 **Demo 域不可用** ⇒ 每一笔下单都死在这一步
（实盘日志十次 `-1102 Mandatory parameter margintype`，而账户档位**本来就是 cross**）。

修法：**先读、后写、以读回为准** ——
1. 读回已是目标档 ⇒ **跳过写入**（压根不碰那个坏端点）；
2. 不一致才写；写入失败后**必须再读回核对**：读回已是目标档 ⇒ 端点坏但现状正确，**告警放行**；
   读回仍不一致 ⇒ **fail-closed 上抛**；读不回档位且错误码属「端点不可用」⇒ 告警放行
   （**不假装核对通过**）。

审计 C1 的意图不变：**确定处于错误档位**时绝不继续强设杠杆。
"""

import unittest
from unittest.mock import patch

from r20_backend.exchanges.binance import BinanceAdapter, BinanceAPIError


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.environment = "demo"
        self.ad.native_symbol = lambda s: f"{s}USDT"
        self.sent = []
        self.margin_reads = []
        self._queue = []

    def _setup(self, *, margin_reads, margin_write=None, leverage_result=None):
        self.margin_reads = list(margin_reads)

        def _margin(inst):
            return self.margin_reads.pop(0) if self.margin_reads else None

        self.ad.position_margin_type = _margin

        def _req(method, path, params=None, **kwargs):
            self.sent.append((method, path, dict(params or {})))
            if path == "/fapi/v1/marginType":
                if margin_write is not None:
                    raise margin_write
                return {"code": "200"}
            return leverage_result if leverage_result is not None else {"leverage": 3}

        self.ad.signed_request = _req
        return self.ad


class PositionMarginTypeTest(_Base):
    def test_read_failure_returns_none_instead_of_guessing(self):
        """读不到 ⇒ `None`（**不猜**成 cross —— 猜错就是「确定处于错误档位」的反面教材）。"""
        def _boom(*a, **k):
            raise RuntimeError("读不动")

        self.ad.signed_request = _boom
        self.assertIsNone(self.ad.position_margin_type("BTCUSDT"))

    def test_list_payload_uses_first_row_and_lowercases(self):
        self.ad.signed_request = lambda m, p, params=None, **k: [{"marginType": "CROSS"}]
        self.assertEqual(self.ad.position_margin_type("BTCUSDT"), "cross")

    def test_unusable_payloads_return_none(self):
        for payload in (None, [], "text", [{"marginType": ""}], [{"other": 1}]):
            with self.subTest(payload=payload):
                self.ad.signed_request = (lambda p: (lambda m, path, params=None, **k: p))(payload)
                self.assertIsNone(self.ad.position_margin_type("BTCUSDT"))


class SetLeverageTest(_Base):
    def test_already_in_target_mode_skips_the_broken_endpoint(self):
        """★ P1 正解：读回已是目标档 ⇒ **完全不碰** `/fapi/v1/marginType`。"""
        self._setup(margin_reads=["cross"])
        self.ad.set_leverage("BTC", 3, "cross")
        paths = [p for _, p, _ in self.sent]
        self.assertEqual(paths, ["/fapi/v1/leverage"], "不得调用那个在 Demo 域不可用的端点")
        self.assertEqual(self.sent[-1][2], {"symbol": "BTCUSDT", "leverage": 3},
                         "杠杆取整数（交易所只认整数倍）")

    def test_unreadable_mode_warns_then_writes_and_continues(self):
        """读不回档位 ⇒ 告警（**无法核对现状**）但仍尝试设置，最后照常设杠杆。"""
        self._setup(margin_reads=[None, "cross"])
        with patch("warnings.warn") as w:
            self.ad.set_leverage("BTC", 3, "cross")
        self.assertTrue(any("无法核对现状" in str(c.args[0]) for c in w.call_args_list))
        self.assertEqual([p for _, p, _ in self.sent],
                         ["/fapi/v1/marginType", "/fapi/v1/leverage"])

    def test_no_change_error_is_tolerated(self):
        """`-4046`（无需变更：现状即目标）⇒ 容忍并继续设杠杆。"""
        self._setup(margin_reads=["isolated"], margin_write=BinanceAPIError(-4046, "no need"))
        self.ad.set_leverage("BTC", 3, "cross")
        self.assertEqual([p for _, p, _ in self.sent], ["/fapi/v1/marginType", "/fapi/v1/leverage"])

    def test_write_failed_but_readback_shows_target_mode_continues_with_warning(self):
        """★ 端点坏、但读回**已是目标档** ⇒ 告警放行（现状正确，不因坏端点阻断全链）。"""
        self._setup(margin_reads=["isolated", "cross"],
                    margin_write=BinanceAPIError(-1102, "Mandatory parameter margintype"))
        with patch("warnings.warn") as w:
            self.ad.set_leverage("BTC", 3, "cross")
        self.assertTrue(any("读回档位已=cross" in str(c.args[0]) for c in w.call_args_list))
        self.assertEqual(self.sent[-1][1], "/fapi/v1/leverage")

    def test_unavailable_endpoint_with_unreadable_mode_warns_and_continues(self):
        """端点不可用 **且** 档位读不回 ⇒ 告警放行，但**不假装核对通过**。"""
        code = sorted(BinanceAdapter.MARGIN_TYPE_ENDPOINT_UNAVAILABLE)[0]
        self._setup(margin_reads=["isolated", None],
                    margin_write=BinanceAPIError(code, "endpoint unavailable"))
        with patch("warnings.warn") as w:
            self.ad.set_leverage("BTC", 3, "cross")
        messages = " ".join(str(c.args[0]) for c in w.call_args_list)
        self.assertIn("未核对通过", messages)

    def test_confirmed_wrong_mode_is_fail_closed(self):
        """★★ 审计 C1：写失败、读回**确实是别的档位** ⇒ **上抛**，绝不继续强设杠杆。"""
        self._setup(margin_reads=["isolated", "isolated"],
                    margin_write=BinanceAPIError(-4059, "No need to change position side"))
        with self.assertRaises(BinanceAPIError) as ctx:
            self.ad.set_leverage("BTC", 3, "cross")
        self.assertEqual(ctx.exception.code, -4059)
        self.assertEqual([p for _, p, _ in self.sent], ["/fapi/v1/marginType"],
                         "确定处于错误档位 ⇒ 连杠杆都不许设")


if __name__ == "__main__":
    unittest.main()
