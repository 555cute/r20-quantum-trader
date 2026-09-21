"""因子条的**其余字段语义**（第二百零九刀）—— 全部键名取自实现，不猜。

## 两处实测发现（本刀只钉现状，列为待议）

1. ★ **`or` 会把真实的 0 短路**：`fundingRate`/`oiUsd`/`takerNetUsd`/`lsRatio` 用的是
   `实时值 or 库值` ⇒ 实时值恰为 **0** 时**掉到库值或占位符**；而**同一个字典里**的 `chg24h`
   用的是 `is not None` ⇒ **0 被正确保留**。同一行两种判法并存。
2. ★ **同一个键两种类型**：`fundingRate` 取自实时值时是**数值**，取自库值时是**带 % 的字符串**。

另有一批**无库回退的伪中性默认值**（`rsi_7`=50.0、`vwap_bias`/`macd_hist`/`macd_accel`=0.0、
`trend_1h`/`trend_4h`=震荡、`market_regime`=CHOP、`leverage`=3）—— 与「读不到 ≠ 没有」相涉，
并入待议第 11 条一族，本刀只钉现状。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from r20_backend.dashboard_payload import factors as F


class RemainingFieldsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.pool = [{"instId": "BTC-USDT-SWAP", "name": "BTC"}]
        p = patch.object(F, "load_instruments", return_value=self.pool)
        p.start()
        self.addCleanup(p.stop)

    def _cleanup(self):
        for f in self.dir.iterdir():
            f.unlink()
        self.dir.rmdir()

    def _file(self, payload, name):
        p = self.dir / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def _run(self, decisions=None, state=None, lib=None):
        rows, _ = F._build_factors_from_local_files(
            str(self._file(lib or {}, "lib.json")), str(self._file(decisions or {}, "dec.json")),
            str(self._file(state or {}, "st.json")), [], "2026-09-21 12:00:00")
        return rows[0]

    def test_zero_is_short_circuited_for_flow_fields(self):
        """★ 实测：实时值为 **0** 时，`or` 会掉到因子库 —— 与同行 `chg24h` 的判法相反。"""
        row = self._run(decisions={"BTC-USDT-SWAP": {"raw_oi": 0, "raw_funding_rate": 0}},
                        lib={"instruments": [{"instId": "BTC-USDT-SWAP",
                                              "smart_money_derivatives": {"oi_usd": 123.0}}]})
        self.assertEqual(row["oiUsd"], 123.0,
                         "0 被当成「没有」⇒ 用库值（现状；与 chg24h 的 is not None 判法不一致）")
        self.assertEqual(row["fundingRate"], "--",
                         "实时 0 + 库无 funding_rate_pct ⇒ 落到占位符（0 丢失）")

    def test_funding_rate_has_two_types_depending_on_the_source(self):
        """★ 同一键两种类型：实时值是数字，库值是**带 % 的字符串**。"""
        numeric = self._run(decisions={"BTC-USDT-SWAP": {"raw_funding_rate": 0.0001}})
        self.assertEqual(numeric["fundingRate"], 0.0001)
        self.assertIsInstance(numeric["fundingRate"], float)
        texted = self._run(lib={"instruments": [{"instId": "BTC-USDT-SWAP",
                                                 "smart_money_derivatives":
                                                     {"funding_rate_pct": 0.0123}}]})
        self.assertEqual(texted["fundingRate"], "0.0123%")
        self.assertIsInstance(texted["fundingRate"], str, "库来源 ⇒ 带百分号字符串")

    def test_strategy_tag_and_action_follow_the_three_states(self):
        for action, tag in (("BUY_LONG", "🟢 建议做多"), ("SELL_SHORT", "🔴 建议做空"),
                            ("WAIT", "⚪ AI观望")):
            with self.subTest(action=action):
                row = self._run(decisions={"BTC-USDT-SWAP": {"decision": {"action": action}}})
                self.assertEqual(row["strategy_tag"], tag)
                self.assertEqual(row["action"], action, "原始动作原样带出，供下游分类")

    def test_calculus_missing_library_yields_none_not_zero(self):
        """★ **正面对照**：calculus 子字典缺库 ⇒ 各字段 **None**（不伪造成 0）。"""
        row = self._run()
        self.assertEqual(row["calculus"],
                         {"velocity_1h": None, "accel_1h": None, "jerk_1h": None,
                          "impulse_1h": None}, "缺数据一律 None，不编造 0")
        row2 = self._run(lib={"instruments": [{"instId": "BTC-USDT-SWAP",
                                              "calculus_dynamics": {"velocity": 1.5}}]})
        self.assertEqual(row2["calculus"]["velocity_1h"], 1.5)

    def test_fabricated_neutral_defaults_when_nothing_is_available(self):
        """★ 无库回退的伪中性默认值（并入待议 11 一族，只钉现状）。"""
        row = self._run()
        self.assertEqual(row["rsi_7"], 50.0)
        self.assertEqual(row["vwap_bias"], 0.0)
        self.assertEqual(row["macd_hist"], 0.0)
        self.assertEqual(row["trend_1h"], "震荡")
        self.assertEqual(row["market_regime"], "CHOP")
        self.assertEqual(row["leverage"], 3, "没有 AI 决策 ⇒ 杠杆显示 3（**编造的**，列待议）")
        self.assertEqual(row["risk_reward_ratio"], "--")

    def test_time_string_falls_back_three_levels_and_timestamp_is_not_faked(self):
        row = self._run(decisions={"BTC-USDT-SWAP": {"time_str": "AI 时间"}},
                        state={"instruments": [], "timestamp": "状态时间"})
        self.assertEqual(row["time_str"], "AI 时间", "决策时间优先")
        row = self._run(state={"instruments": [], "timestamp": "状态时间"})
        self.assertEqual(row["time_str"], "状态时间", "其次状态文件时间")
        row = self._run()
        self.assertEqual(row["time_str"], "2026-09-21 12:00:00", "最后落到本次调用时间（诚实兜底）")
        self.assertIsNone(row["timestamp"], "**不编造时间戳**：没有就 None")

    def test_library_second_level_defaults(self):
        row = self._run(lib={"instruments": [{"instId": "BTC-USDT-SWAP",
                                             "volume_money_flow": {"obv_flow": "INFLOW",
                                                                   "vol_ratio_15m": 2.5}}]})
        self.assertEqual(row["obv_flow"], "INFLOW")
        self.assertEqual(row["vol_ratio"], 2.5)
        row2 = self._run()
        self.assertEqual(row2["obv_flow"], "NEUTRAL", "都缺 ⇒ NEUTRAL（保守中性）")
        self.assertEqual(row2["vol_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
