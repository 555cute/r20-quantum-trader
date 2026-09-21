"""Binance 持仓模式判定与 API 错误载荷（第二百五十八刀）。

**两所的模式词汇不同**（实测：Gate `position_mode='dual'`、Binance `dualSidePosition` 布尔），
故各所有各所的判定函数，**绝不共用一个枚举** —— 合并迟早会把 `dual` 与 `long_short`
混为一谈，而那是两套完全不同的下单契约（对冲模式必须显式 `positionSide` 且禁传 `reduceOnly`）。

判定的第一条纪律：**读不到 ⇒ `unknown`，不拿默认值冒充事实**（调用方据此禁新开仓）。
"""

import unittest

from r20_backend.exchanges.binance import (
    interpret_dual_side_position, BinanceAPIError, BinanceAdapter,
)


class InterpretDualSidePositionTest(unittest.TestCase):
    def test_missing_or_wrong_type_is_unknown(self):
        """读不到 / 类型不对 ⇒ `unknown`（**不拿默认值冒充事实**）。"""
        for payload in (None, [], "net", 42, {}, {"other": 1}):
            with self.subTest(payload=payload):
                self.assertEqual(interpret_dual_side_position(payload), "unknown")

    def test_boolean_payload_maps_to_the_two_modes(self):
        self.assertEqual(interpret_dual_side_position({"dualSidePosition": False}), "net")
        self.assertEqual(interpret_dual_side_position({"dualSidePosition": True}), "long_short")

    def test_string_payload_is_accepted_case_and_space_insensitively(self):
        for raw, want in (("true", "long_short"), ("TRUE", "long_short"), (" true ", "long_short"),
                          ("false", "net"), ("False", "net"), (" FALSE ", "net")):
            with self.subTest(raw=raw):
                self.assertEqual(interpret_dual_side_position({"dualSidePosition": raw}), want)

    def test_other_values_are_unknown_not_truthy_coerced(self):
        """⚠️ **绝不用 Python 真值当事实**：`0`/`1`/`"yes"` 都不是交易所的答案。

        若按真值强转，`1` 会被读成「对冲模式」⇒ 下单契约整个错（该显式 `positionSide`
        的没传、该禁的 `reduceOnly` 没禁）。
        """
        for raw in (0, 1, "yes", "dual", [], {}):
            with self.subTest(raw=raw):
                self.assertEqual(interpret_dual_side_position({"dualSidePosition": raw}),
                                 "unknown")


class BinanceAPIErrorTest(unittest.TestCase):
    """错误载荷要把**官方错误码**原样带出来（运维靠它查文档）。"""

    def test_message_and_attributes(self):
        err = BinanceAPIError(-2015, "Invalid API-key", 401)
        self.assertEqual(err.code, -2015)
        self.assertEqual(err.message, "Invalid API-key")
        self.assertEqual(err.status, 401)
        self.assertIn("[‑2015]".replace("‑", "-"), str(err))
        self.assertIsInstance(err, RuntimeError)

    def test_empty_message_falls_back_to_a_readable_default(self):
        err = BinanceAPIError(-1003, "")
        self.assertIn("request failed", str(err))


class IntervalMapTest(unittest.TestCase):
    def test_internal_bars_map_to_exchange_intervals(self):
        ad = BinanceAdapter.__new__(BinanceAdapter)   # 只测纯映射，不触发 __init__ 的 IO
        self.assertEqual(ad._interval("15m"), "15m")
        self.assertEqual(ad._interval("1H"), "1h", "内部 1H ⇒ 交易所 1h（大小写不同）")
        self.assertEqual(ad._interval("4H"), "4h")
        self.assertEqual(ad._interval("1D"), "1d")

    def test_unknown_bar_falls_back_to_lowercased_input(self):
        ad = BinanceAdapter.__new__(BinanceAdapter)
        self.assertEqual(ad._interval("7H"), "7h", "未知周期退回小写原样，不猜成别的周期")
        self.assertEqual(ad._interval(""), "15m", "空 ⇒ 默认 15m")


if __name__ == "__main__":
    unittest.main()
