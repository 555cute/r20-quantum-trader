"""台账可观测性与**因果匹配**（第二百四十一刀）。

| 语义 | 口径 |
|---|---|
| ★ **不可观测就写 NONE** | `classify_trade_observability` 拿不到任何证据 ⇒ **`"NONE"`**（明确标注不可观测，**严禁事后倒推编造**）|
| 证据优先序 | 传入的有效快照 ＞ 该笔自身的 `snapshot_observability` 标签（大写后查白名单）＞ `_SNAPSHOT_KEYS` 中任一**非空** dict ＞ `NONE` |
| ★ 因果铁律 | `match_trade_snapshot`：① 方向必须一致；② 窗口 **[-6h, +20m]**；③ **禁远期未来**（>20m 绝非开仓因果）；④ **禁过期**（>6h 弃用）；⑤ **无快照或未匹配绝不倒推编造** |
| 就近 | 多候选时取**时间最近**的一条，返回它携带的 `snapshot` |
| 缺证据 | 没有候选 / 开仓时间解析不出来 ⇒ **None**（不是空 dict、不是假快照）|
"""

import unittest

from r20_backend.dashboard_payload import ledger_view as LV


class ObservabilityTest(unittest.TestCase):
    def test_dynamics_snapshot_is_classified_not_none(self):
        out = LV.classify_trade_observability({}, {"velocity": 1.0, "acceleration": 0.5})
        self.assertIn(out, {"DYNAMICS_OBSERVED", "PARTIAL", "PRICE_ONLY"},
                      "有快照 ⇒ 走动力学链判定")
        self.assertNotEqual(out, "NONE")

    def test_inline_tag_wins_over_the_key_scan_and_is_case_folded(self):
        tag = sorted(LV._OBSERVABILITY_TAGS)[0]
        out = LV.classify_trade_observability({"snapshot_observability": tag.lower()})
        self.assertEqual(out, tag, "标签大小写归一后仍在白名单 ⇒ 直接采信")

    def test_unknown_tag_falls_through_to_the_key_scan(self):
        out = LV.classify_trade_observability(
            {"snapshot_observability": "乱写的标签", LV._SNAPSHOT_KEYS[0]: {"velocity": 2.0}})
        self.assertNotEqual(out, "乱写的标签")
        self.assertNotEqual(out, "NONE", "键位里有非空快照 ⇒ 仍能判定")

    def test_empty_snapshot_dicts_are_not_evidence(self):
        """★ 空 dict **不是**证据（`{}` 与"没有"同义）—— 不能据此宣称已观测。"""
        out = LV.classify_trade_observability({LV._SNAPSHOT_KEYS[0]: {}})
        self.assertEqual(out, "NONE")

    def test_no_evidence_at_all_is_none_not_a_fabricated_tag(self):
        self.assertEqual(LV.classify_trade_observability({}), "NONE")
        self.assertEqual(LV.classify_trade_observability({"其它键": 1}), "NONE")

    def test_an_empty_passed_snapshot_is_ignored(self):
        out = LV.classify_trade_observability({}, {})
        self.assertEqual(out, "NONE", "空快照等于没给")


class MatchTradeSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.inst = "BTC-USDT-SWAP"
        self.open_time = "2026-09-21 12:00:00"

    def _journal(self, *rows):
        return {self.inst: list(rows)}

    def test_side_mismatch_is_rejected(self):
        side = sorted(LV.SIDE_ALIASES)[0]
        other = [s for s in sorted(LV.SIDE_ALIASES) if LV.SIDE_ALIASES[s] != LV.SIDE_ALIASES[side]][0]
        out = LV.match_trade_snapshot(
            self._journal({"side": other, "entryTime": self.open_time, "snapshot": {"v": 1}}),
            self.inst, self.open_time, side)
        self.assertIsNone(out, "方向不一致 ⇒ 不匹配")

    def test_a_far_future_snapshot_is_not_causal(self):
        from r20_backend.time_utils import beijing_text
        import time as _t
        future = beijing_text(_t.time() + 3600 * 3)
        out = LV.match_trade_snapshot(
            self._journal({"entryTime": future, "snapshot": {"v": 1}}),
            self.inst, beijing_text(_t.time()), None)
        self.assertIsNone(out, "远期未来快照绝不作为开仓因果")

    def test_a_stale_snapshot_is_discarded(self):
        import time as _t
        from r20_backend.time_utils import beijing_text
        old = beijing_text(_t.time() - 3600 * 24)
        out = LV.match_trade_snapshot(
            self._journal({"entryTime": old, "snapshot": {"v": 1}}),
            self.inst, beijing_text(_t.time()), None)
        self.assertIsNone(out, "过期快照（>6h）弃用")

    def test_the_closest_candidate_within_the_window_wins(self):
        import time as _t
        from r20_backend.time_utils import beijing_text
        now = _t.time()
        near = beijing_text(now - 60)
        far = beijing_text(now - 3600 * 3)
        out = LV.match_trade_snapshot(
            self._journal({"entryTime": far, "snapshot": {"which": "far"}},
                          {"entryTime": near, "snapshot": {"which": "near"}}),
            self.inst, beijing_text(now), None)
        self.assertEqual(out, {"which": "near"}, "取最近的一条")

    def test_no_candidate_or_unparseable_time_is_none(self):
        self.assertIsNone(LV.match_trade_snapshot({}, self.inst, self.open_time, None))
        self.assertIsNone(LV.match_trade_snapshot(
            self._journal({"entryTime": self.open_time, "snapshot": {"v": 1}}),
            self.inst, "不是时间", None), "开仓时间解析不出来 ⇒ 不猜")

    def test_a_matched_record_without_a_snapshot_yields_none(self):
        """★ 匹配上了但那条记录**没带快照** ⇒ 返回 None（**不编造**空快照）。"""
        out = LV.match_trade_snapshot(
            self._journal({"entryTime": self.open_time}), self.inst, self.open_time, None)
        self.assertIsNone(out)


class LedgerCapTest(unittest.TestCase):
    def test_the_view_cap_is_sixty(self):
        """台账视图上限是**事实源**（瘦身上限必须与它一致，见第二百四十刀）。"""
        self.assertEqual(LV.LEDGER_TRADES_MAX, 60)


if __name__ == "__main__":
    unittest.main()
