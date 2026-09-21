"""保护腿解析件与台账取证的三态（第二百四十四刀）。

这一族是**判定件**：它们的返回值决定「这腿是谁的、能不能撤、按什么价触发」。
判定件读错一个字，后果是**撤掉用户手单**或**把本来能证明的腿降级成不可判定**。
"""

import unittest
import tempfile
import os

from scripts.trader.venue_protection import (
    _leg_size, _expiry, _leg_matches_base, protection_trigger_type_fields,
    read_ledger_rows, cancel_protective_leg,
)


class LegSizeTest(unittest.TestCase):
    def test_remaining_quantity_family_is_preferred_over_ordered_size(self):
        """**剩余量优先**：部分成交后的余量才是真实风险敞口。

        真机依据：Gate 用 `left` 表示剩余，`size` 是原始下单量 —— 只看 `size`
        会把"已部分成交"的腿当成满量，覆盖判定随之说谎。
        """
        self.assertEqual(_leg_size({"left": 3, "size": 10}), 3.0)
        self.assertEqual(_leg_size({"size_remaining": 2, "size": 10}), 2.0)
        self.assertEqual(_leg_size({"raw": {"remaining": 1}, "size": 10}), 1.0)
        # 剩余量为 0（已全成交/已撤）⇒ **不得**当成"有量"，回落到下单量族
        self.assertEqual(_leg_size({"left": 0, "size": 10}), 10.0)
        self.assertIsNone(_leg_size({}))


class ExpiryTest(unittest.TestCase):
    def test_millisecond_absolute_timestamp(self):
        """毫秒绝对时间戳 ⇒ 秒 + `absolute`（分界取自唯一实现 EPOCH_MS_THRESHOLD）。"""
        sec, kind = _expiry({"expiration": 1_700_000_000_000})
        self.assertEqual(sec, 1_700_000_000.0)
        self.assertEqual(kind, "absolute")

    def test_relative_expiry_is_added_to_creation_time(self):
        sec, kind = _expiry({"expiration": 3600, "create_time": 1_700_000_000})
        self.assertEqual(kind, "relative")
        self.assertEqual(sec, 1_700_003_600.0)

    def test_no_expiry_says_never_not_unknown(self):
        """**确实没有**到期时间 ⇒ `never`（与"读不到"不是一回事）。"""
        self.assertEqual(_expiry({"expiration": 0}), (None, "never"))

    def test_relative_without_creation_time_is_unknown(self):
        self.assertEqual(_expiry({"expiration": 3600}), (None, "unknown"))


class TriggerTypeFieldsTest(unittest.TestCase):
    def test_unreadable_source_reports_unknown_for_both(self):
        """取数失败 ⇒ **两个字段都 unknown**（读不到，可能本来有）。"""
        out = protection_trigger_type_fields(None, readable=False)
        self.assertEqual(out, {"protectionSlTriggerPxType": "unknown",
                               "protectionTpTriggerPxType": "unknown"})

    def test_junk_and_unknown_kinds_are_skipped(self):
        out = protection_trigger_type_fields(
            ["垃圾", {"kind": "hedge"}, {"kind": "sl", "trigger_px_type": "mark"}],
            readable=True)
        self.assertEqual(out["protectionSlTriggerPxType"], "mark")
        self.assertIsNone(out["protectionTpTriggerPxType"],
                          "没有 tp 腿 ⇒ None（没有这东西），不是 unknown")


class LegMatchesBaseTest(unittest.TestCase):
    def test_prefix_match_covers_both_contract_spellings(self):
        self.assertTrue(_leg_matches_base({"symbol": "BTC_USDT"}, "BTC"))
        self.assertTrue(_leg_matches_base({"symbol": "BTCUSDT"}, "BTCUSDT"))

    def test_no_base_means_no_filtering(self):
        """没指定币种 ⇒ **不过滤**（返回 True），不是"没有一条匹配"。"""
        self.assertTrue(_leg_matches_base({"symbol": "WBTC_USDT"}, ""))
        self.assertTrue(_leg_matches_base({}, None))

    def test_wrapped_coin_is_not_matched(self):
        """`WBTCUSDT`（包装币）**不是** `BTC` 的腿 —— 子串包含会说谎，前缀相等不会。"""
        self.assertFalse(_leg_matches_base({"symbol": "WBTC_USDT"}, "BTC"))


class LedgerEvidenceTest(unittest.TestCase):
    """台账取证三态，方向必须**偏保守**（两害相权取「什么也不做」）。

    把读失败当「没有台账记录」⇒ 本可证明归属的腿被降级为不可判定；
    把读失败当「都是我们的」⇒ **撤掉用户手单**。故读不到一律**不产生证据**。
    """

    def test_missing_file_is_none(self):
        self.assertIsNone(read_ledger_rows(os.path.join(tempfile.gettempdir(),
                                                       "r20_no_such_ledger_xyz.json")))

    def test_unusable_path_is_none_not_crash(self):
        self.assertIsNone(read_ledger_rows(None), "路径都构造不出来 ⇒ 不产生证据（不抛）")

    def test_dict_without_trades_or_rows_is_none_not_empty_ledger(self):
        """结构认不出来的 dict **不得**被当成空台账（那会把可证明的腿降级）。"""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"weird": 1}')
            path = fh.name
        try:
            self.assertIsNone(read_ledger_rows(path, log=lambda *a: None))
        finally:
            os.unlink(path)

    def test_dict_with_rows_is_accepted_and_non_dicts_filtered(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"rows": [{"id": 1}, "垃圾", 3]}')
            path = fh.name
        try:
            self.assertEqual(read_ledger_rows(path), [{"id": 1}])
        finally:
            os.unlink(path)

    def test_non_list_structure_is_none(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('"just a string"')
            path = fh.name
        try:
            self.assertIsNone(read_ledger_rows(path, log=lambda *a: None))
        finally:
            os.unlink(path)


class CancelProtectiveLegProbeTest(unittest.TestCase):
    """撤腿必须走**能力探针**：三所能力各不相同（Gate 只有 `cancel_price_order`、
    Binance 只有 `cancel_algo_order`、OKX 走 `cancel_order`）。旧写法直接调
    `cancel_price_order` ⇒ 对 Binance **恒失败**，而 Binance 恰是孤儿腿最多的所。"""

    class _Gate:
        def __init__(self):
            self.cancelled = []

        def cancel_price_order(self, oid):
            self.cancelled.append(("price", oid))

    class _Binance:
        def __init__(self):
            self.cancelled = []

        def cancel_algo_order(self, algo_id=None):
            self.cancelled.append(("algo", algo_id))

    class _Okx:
        def __init__(self):
            self.cancelled = []

        def cancel_order(self, symbol, oid):
            self.cancelled.append((symbol, oid))

    class _Nothing:
        pass

    def test_empty_id_is_false_without_calling_anything(self):
        ad = self._Gate()
        self.assertFalse(cancel_protective_leg(ad, None))
        self.assertFalse(cancel_protective_leg(ad, ""))
        self.assertEqual(ad.cancelled, [], "无 id ⇒ 不猜着撤")

    def test_gate_uses_price_order(self):
        ad = self._Gate()
        self.assertTrue(cancel_protective_leg(ad, "g1"))
        self.assertEqual(ad.cancelled, [("price", "g1")])

    def test_binance_uses_algo_order(self):
        ad = self._Binance()
        self.assertTrue(cancel_protective_leg(ad, "b1"))
        self.assertEqual(ad.cancelled, [("algo", "b1")])

    def test_okx_falls_back_to_symbol_scoped_cancel(self):
        ad = self._Okx()
        self.assertTrue(cancel_protective_leg(ad, "o1", symbol="BTC-USDT-SWAP"))
        self.assertEqual(ad.cancelled, [("BTC-USDT-SWAP", "o1")])

    def test_okx_without_symbol_cannot_cancel(self):
        """OKX 的撤单**需要 symbol** ⇒ 没给 symbol 就诚实地报「撤不动」（False）。"""
        self.assertFalse(cancel_protective_leg(self._Okx(), "o1"))

    def test_adapter_without_any_cancel_capability_reports_false(self):
        self.assertFalse(cancel_protective_leg(self._Nothing(), "x"))


if __name__ == "__main__":
    unittest.main()
