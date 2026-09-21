"""持仓行装配：保证金与止损线的**来源标注**（第二百零四刀）。

这些字段决定面板上「这条腿的保证金/止损是从哪来的」，属于**来源必须诚实**的一类语义：
交易所值、缓存值、还是本地按名义/杠杆推出来的 —— 三者可信度不同，**不得混为一谈**。

| 分支 | 结果 |
|---|---|
| 交易所给了 `imr`（>0）| 用交易所值，来源 `exchange_imr`（**最可信，优先**）|
| 没有 imr，但有缓存 `margin_usdt`（>0）| 用缓存值，来源沿用行上的 `marginSource`，缺省 `cached` |
| 两者都没有 | 本地推算 `名义/杠杆`（两位小数），来源 `notional_div_leverage`；**名义为 0 ⇒ 保证金给 `None`**（不是 0：0 是「不占保证金」，None 是「算不出」，不能混）|

止损线同理三态：交易所云止损 `exchange_cloud` ＞ 本地 tracker `local_tracker` ＞ `unavailable`。
"""

import unittest
from unittest.mock import patch

from r20_backend.dashboard_payload import factors


def _pos(**over):
    p = {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos_sz": 2.0, "markPx": 100.0,
         "notional_usdt": 200.0, "lever": 2.0}
    p.update(over)
    return p


class MarginProvenanceTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(factors, "load_instruments", return_value=[])
        p.start()
        self.addCleanup(p.stop)

    def _run(self, position, tracker=None):
        row = factors.enrich_position_risk_fields("unused.json", [position],
                                                  {"BTC-USDT-SWAP_long": tracker or {}})
        return (row or [position])[0]

    def test_exchange_margin_wins(self):
        row = self._run(_pos(imr=7.5, margin_usdt=99.0, marginSource="cached"))
        self.assertEqual(row["margin_usdt"], 7.5)
        self.assertEqual(row["marginSource"], "exchange_imr", "交易所值最可信 ⇒ 优先且标注来源")

    def test_cached_margin_is_used_and_its_source_label_kept(self):
        row = self._run(_pos(margin_usdt=12.0, marginSource="exchange_imr_cached"))
        self.assertEqual(row["margin_usdt"], 12.0)
        self.assertEqual(row["marginSource"], "exchange_imr_cached", "沿用行上原有来源标注")

    def test_cached_margin_without_label_defaults_to_cached(self):
        row = self._run(_pos(margin_usdt=12.0))
        self.assertEqual(row["marginSource"], "cached")

    def test_fallback_margin_is_notional_over_leverage(self):
        row = self._run(_pos(notional_usdt=200.0, lever=4.0))
        self.assertEqual(row["margin_usdt"], 50.0)
        self.assertEqual(row["marginSource"], "notional_div_leverage")

    def test_uncomputable_margin_is_none_not_zero(self):
        """★ 算不出保证金 ⇒ `None`；**0 是「不占保证金」**，两者不能混。"""
        row = self._run(_pos(notional_usdt=0.0, pos_sz=0.0, markPx=0.0))
        self.assertIsNone(row["margin_usdt"])
        self.assertEqual(row["marginSource"], "notional_div_leverage")

    def test_leverage_floor_avoids_division_by_zero(self):
        row = self._run(_pos(notional_usdt=200.0, lever=0.0))
        self.assertEqual(row["margin_usdt"], 200.0, "杠杆缺省按 1.0（不除零）")


class StopProvenanceTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(factors, "load_instruments", return_value=[])
        p.start()
        self.addCleanup(p.stop)

    def _run(self, position, tracker=None):
        row = factors.enrich_position_risk_fields("unused.json", [position],
                                                  {"BTC-USDT-SWAP_long": tracker or {}})
        return (row or [position])[0]

    def test_exchange_cloud_stop_wins(self):
        row = self._run(_pos(exchangeSl=95.0, trailingSl=93.0),
                        {"trailingStopPx": 90.0})
        self.assertEqual(row["displayStop"], 95.0)
        self.assertEqual(row["stopSource"], "exchange_cloud")

    def test_local_tracker_stop_is_second(self):
        row = self._run(_pos(), {"trailingStopPx": 90.0})
        self.assertEqual(row["displayStop"], 90.0)
        self.assertEqual(row["trailingSl"], 90.0)
        self.assertEqual(row["stopSource"], "local_tracker")

    def test_no_stop_anywhere_is_unavailable_and_none(self):
        row = self._run(_pos())
        self.assertIsNone(row["displayStop"])
        self.assertIsNone(row["trailingSl"])
        self.assertEqual(row["stopSource"], "unavailable", "**缺席即缺席**：不编造止损价")

    def test_take_profit_prefers_exchange_then_tracker(self):
        self.assertEqual(self._run(_pos(exchangeTp=110.0), {"takeProfitPx": 108.0})["displayTakeProfit"], 110.0)
        self.assertEqual(self._run(_pos(), {"takeProfitPx": 108.0})["displayTakeProfit"], 108.0)
        self.assertIsNone(self._run(_pos())["displayTakeProfit"])

    def test_stage_and_strategy_defaults_follow_the_side(self):
        long_row = self._run(_pos(posSide="long"))
        short_row = self._run(_pos(posSide="short"))
        self.assertEqual(long_row["stageDesc"], "持有监控中")
        self.assertEqual(long_row["strategyTag"], "顺势做多")
        self.assertEqual(short_row["strategyTag"], "逢高做空")
        kept = self._run(_pos(stageDesc="已减半", strategyTag="自定义"))
        self.assertEqual((kept["stageDesc"], kept["strategyTag"]), ("已减半", "自定义"),
                         "已有值不得被默认值覆盖")

    def test_stale_verification_is_relabelled_when_a_timestamp_exists(self):
        """★ 旧的「未知/过期」核验状态 + 有核验时间 ⇒ 改标 `verification_stale`（可解释的旧）。"""
        row = self._run(_pos(protectionStatus="unknown_stale"),
                        {"cloudProtection": {"verifiedAt": "2026-09-21 10:00:00", "detail": "ok"}})
        self.assertEqual(row["protectionStatus"], "verification_stale")
        self.assertEqual(row["cloudProtectionLastVerified"], "2026-09-21 10:00:00")
        self.assertEqual(row["cloudProtectionLastDetail"], "ok")
        untouched = self._run(_pos(protectionStatus="healthy"),
                              {"cloudProtection": {"verifiedAt": "2026-09-21 10:00:00"}})
        self.assertEqual(untouched["protectionStatus"], "healthy", "非旧状态不得被改写")


if __name__ == "__main__":
    unittest.main()
