"""无主活动持仓不许静默（第一百一十刀下半）。

## 起因：`_holding_row` 会**静默丢弃**准入清单外的活动持仓

`scripts/sync_full_ledger.py::_holding_row` 里 `if inst_id not in allowed: return None`
—— 不在准入清单的活仓**没有任何留痕**：不进台账、不告警、日志里也看不出来，
于是风险界面看不到它，而开仓预检还会把它当"外部仓"（第 110 刀前半已修诊断）。

## ⚠️ 归因诚实（别把两件事混成一件）

- **已证实的线上事件**（2026-09-20 08:00–10:45 日志：UNI/ARB 被报"外部仓"）根因是
  **`own_position` 从未被生产调用方传入** —— 与"准入清单丢弃"**无关**：
  实测两者当前都在准入清单内（14 个币），UNI 的 holding 行也确实写进了台账。
- 本条（准入清单丢弃）是**已复现的代码路径 + 当前尚未触发的潜在风险**：
  今日实跑 `binance 活仓 1 → 未进台账 0`、`gate 活仓 0 → 未进台账 0`。
  它会在"交易所有仓而该币不在清单"时发生（清单外新币、人工下单、池配置变更等）。

本门钉三件事：① 丢弃要**留痕**（收集器）；② 载荷**有界**但计数完整；
③ 旁车 → 面板 `source_errors` 必须**显式上报**，且旧旁车（无该字段）不得误报。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))          # 根层脚本的双拼写之一

import sync_full_ledger as sfl  # noqa: E402

REAL_ARB = {"instId": "ARB-USDT-SWAP", "pos": -2416.7, "posSide": "short"}
_ENV = type("E", (), {"mode": "demo", "simulated": True})()


class HoldingRowTraceTest(unittest.TestCase):
    def test_allowlisted_row_is_built_and_not_traced(self):
        bag = []
        row = sfl._holding_row(dict(REAL_ARB, instId="BTC-USDT-SWAP", pos=1.0),
                               "binance", env=_ENV, trackers={}, tz_bj=None,
                               allowed={"BTC-USDT-SWAP"}, council_by_inst={}, unmanaged=bag)
        self.assertIsNotNone(row)
        self.assertEqual(bag, [], "进了台账的仓不该出现在无主列表")

    def test_non_allowlisted_live_position_is_traced(self):
        bag = []
        row = sfl._holding_row(REAL_ARB, "binance", env=_ENV, trackers={}, tz_bj=None,
                               allowed={"BTC-USDT-SWAP"}, council_by_inst={}, unmanaged=bag)
        self.assertIsNone(row, "本刀**不**改变是否进台账（那是风险界面语义，须单独拍板）")
        self.assertEqual(bag, [{"venue": "binance", "instId": "ARB-USDT-SWAP",
                                "size": -2416.7, "side_raw": "short"}])

    def test_zero_size_position_is_not_traced(self):
        bag = []
        row = sfl._holding_row(dict(REAL_ARB, pos=0.0), "binance", env=_ENV, trackers={},
                               tz_bj=None, allowed=set(), council_by_inst={}, unmanaged=bag)
        self.assertIsNone(row)
        self.assertEqual(bag, [], "零仓不是持仓，别制造噪声")

    def test_collector_is_optional_backward_compatible(self):
        row = sfl._holding_row(REAL_ARB, "binance", env=_ENV, trackers={}, tz_bj=None,
                               allowed=set(), council_by_inst={})
        self.assertIsNone(row, "不传收集器时行为与改动前一致（既有调用方零影响）")


class PayloadBoundedTest(unittest.TestCase):
    def test_count_is_complete_while_items_are_bounded(self):
        big = [{"venue": "binance", "instId": f"X{i}", "size": i} for i in range(25)]
        r = sfl.unmanaged_positions_payload(big)
        self.assertEqual(r["count"], 25, "计数必须完整——报少一条等于没报")
        self.assertEqual(len(r["items"]), sfl.UNMANAGED_LIST_MAX)
        self.assertEqual(r["omitted"], 25 - sfl.UNMANAGED_LIST_MAX)

    def test_small_list_has_no_omitted(self):
        r = sfl.unmanaged_positions_payload([{"venue": "binance", "instId": "ARB-USDT-SWAP",
                                              "size": -2416.7, "side_raw": "short"}])
        self.assertEqual((r["count"], r["omitted"]), (1, 0))
        self.assertEqual(r["items"][0]["instId"], "ARB-USDT-SWAP")

    def test_empty_or_none(self):
        self.assertEqual(sfl.unmanaged_positions_payload(None)["count"], 0)
        self.assertEqual(sfl.unmanaged_positions_payload([])["items"], [])


class SidecarToSourceErrorsTest(unittest.TestCase):
    def setUp(self):
        from r20_backend.dashboard_payload import integrity_sidecars as I
        self.I = I
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "ledger_sync_status.json")

    def _write(self, payload):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _fresh(self, **over):
        p = {"generated_at": datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
            "environment": "demo", "venues": {"binance": {"status": "ok"}}}
        p.update(over)
        return p

    def test_unmanaged_positions_are_surfaced(self):
        self._write(self._fresh(unmanaged_positions={
            "count": 1, "omitted": 0,
            "items": [{"venue": "binance", "instId": "ARB-USDT-SWAP", "size": -2416.7}]}))
        errs = []
        self.I.merge_ledger_sync_status(errs, self.tmp.name, datetime=datetime)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("不在准入清单", errs[0])
        self.assertIn("ARB-USDT-SWAP", errs[0])
        self.assertIn("无人管理", errs[0])

    def test_legacy_sidecar_without_field_is_silent(self):
        self._write(self._fresh())
        errs = []
        self.I.merge_ledger_sync_status(errs, self.tmp.name, datetime=datetime)
        self.assertEqual(errs, [], "旧版旁车无该字段 ⇒ 不得误报")

    def test_zero_count_is_silent(self):
        self._write(self._fresh(unmanaged_positions={"count": 0, "items": []}))
        errs = []
        self.I.merge_ledger_sync_status(errs, self.tmp.name, datetime=datetime)
        self.assertEqual(errs, [])

    def test_stale_sidecar_is_ignored(self):
        old = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
               - datetime.timedelta(seconds=99999)).isoformat()
        self._write(self._fresh(generated_at=old, unmanaged_positions={
            "count": 3, "items": [{"instId": "ARB-USDT-SWAP", "size": -1}]}))
        errs = []
        self.I.merge_ledger_sync_status(errs, self.tmp.name, datetime=datetime)
        self.assertEqual(errs, [], "过期旁车一律不报（与既有 failed/truncated 同规则）")


if __name__ == "__main__":
    unittest.main()
