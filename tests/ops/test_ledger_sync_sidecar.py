"""审计 A2 封闭单测：台账逐所同步状态旁车 + 熔断器 fail-closed。

覆盖：旁车写入形状、缺失/过旧/损坏容错、failed 所触发日亏不可判暂停开仓。
律①：零网络零真实数据目录（DATA_DIR 全部 patch 到 tmp）。
"""
from __future__ import annotations
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.sync_full_ledger as sfl
import r20_backend.execution.circuit_breaker as cb


class SyncStatusSidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._p_sfl = patch.object(sfl, "DATA_DIR", self.tmp.name)
        self._p_sfl.start()
        self.addCleanup(self._p_sfl.stop)
        sfl._FETCH_STATUS.clear()
        self.addCleanup(sfl._FETCH_STATUS.clear)

    def test_write_and_shape(self):
        sfl._mark("okx", "ok", truncated_at=100)
        sfl._mark("binance", "failed", reason="timeout")
        sfl._mark("gate", "ok", rows=3, truncated=False)

        class _Env:
            simulated = True

        sfl._write_sync_status(_Env())
        path = os.path.join(self.tmp.name, "ledger_sync_status.json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            payload = json.loads(fh.read())
        self.assertEqual(payload["environment"], "demo")
        self.assertEqual(payload["venues"]["binance"]["status"], "failed")
        self.assertEqual(payload["venues"]["binance"]["reason"], "timeout")
        self.assertEqual(payload["venues"]["okx"]["truncated_at"], 100)
        # 时间戳必须带时区（读侧按 tz-aware 计算新鲜度）
        datetime.datetime.fromisoformat(payload["generated_at"])


class BreakerSidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "trading_ledger.json").write_text("[]", encoding="utf-8")
        patches = [
            patch.object(cb, "DATA_DIR", root),
            patch.object(cb, "LEDGER_JSON_FILE", root / "trading_ledger.json"),
            patch.object(cb, "CIRCUIT_BREAKER_FILE", root / "circuit_breaker.json"),
            patch.object(cb, "check_black_swan_sentinel", lambda **kw: (False, "")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_sidecar(self, venues, minutes_ago=0.0):
        gen = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
               - datetime.timedelta(minutes=minutes_ago))
        (self.tmp and (Path(self.tmp.name) / "ledger_sync_status.json")).write_text(
            json.dumps({"generated_at": gen.isoformat(), "environment": "demo", "venues": venues}),
            encoding="utf-8")

    def test_missing_sidecar_is_not_a_trip(self):
        self.assertEqual(cb._ledger_sync_failed_venues(), [])
        active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
        self.assertFalse(active, reason)

    def test_failed_venue_trips_fail_closed(self):
        self._write_sidecar({"binance": {"status": "failed", "reason": "HTTP 500"}})
        self.assertEqual(cb._ledger_sync_failed_venues(), ["binance"])
        active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
        self.assertTrue(active)
        self.assertIn("台账跨所同步不完整", reason)
        self.assertIn("binance", reason)

    def test_all_ok_and_only_truncated_do_not_trip(self):
        self._write_sidecar({"okx": {"status": "ok"}, "gate": {"status": "ok", "truncated": True},
                             "binance": {"status": "ok", "rows": 0}})
        self.assertEqual(cb._ledger_sync_failed_venues(), [])
        active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
        self.assertFalse(active, reason)

    def test_stale_sidecar_ignored(self):
        self._write_sidecar({"binance": {"status": "failed"}}, minutes_ago=60)
        self.assertEqual(cb._ledger_sync_failed_venues(), [])

    def test_corrupt_sidecar_ignored(self):
        (Path(self.tmp.name) / "ledger_sync_status.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(cb._ledger_sync_failed_venues(), [])

    def test_unconfigured_venue_does_not_trip_circuit_breaker(self):
        """未配置凭证的场所（免密只读行情模式）报错绝不能误熔断其他正常场所。"""
        with patch("r20_backend.exchanges.venue_credentials", return_value=("", "")):
            self._write_sidecar({
                "okx": {"status": "ok"},
                "binance": {"status": "failed", "reason": "Binance [-2015]: Invalid API-key, IP, or permissions for action"},
                "gate": {"status": "failed", "reason": "Gate INVALID_KEY: Invalid key provided"},
            })
            self.assertEqual(cb._ledger_sync_failed_venues(), [])
            active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
            self.assertFalse(active, f"未配置凭证的免密行情所导致了误熔断: {reason}")


class SidecarUnknownIsFailClosedTest(unittest.TestCase):
    """旁车"不可判定" ⇒ **禁开仓**（第一百四十四刀，用户拍板 fail-closed）。

    背景：旧 docstring 声称"过旧由 ledger 的 file_health STALE 通道兜底"，但两个调用方
    都没有该检查 ⇒ 旁车损坏/过旧会被读成"各所同步正常"，当日亏损求和可能不完整却**不熔断**。

    用户拍板方向：**不可判定 ≠ 安全** ⇒ 旁车损坏/过旧一律安全暂停开仓
    （仓位管理与既有保护单不受影响；旁车恢复后自动解除）。

    兼容契约保留：`_ledger_sync_failed_venues()`（壳）与既有"缺失/过旧 ⇒ 不列出"的用例不变；
    方向体现在**调用方**而不是这个壳上。
    """

    def setUp(self):
        import tempfile
        from pathlib import Path as _P
        self.tmp = tempfile.TemporaryDirectory(prefix="sidecar-unknown-")
        self.addCleanup(self.tmp.cleanup)
        root = _P(self.tmp.name)
        (root / "trading_ledger.json").write_text("[]", encoding="utf-8")
        patches = [
            patch.object(cb, "DATA_DIR", root),
            patch.object(cb, "LEDGER_JSON_FILE", root / "trading_ledger.json"),
            patch.object(cb, "CIRCUIT_BREAKER_FILE", root / "circuit_breaker.json"),
            patch.object(cb, "check_black_swan_sentinel", lambda **kw: (False, "")),
        ]
        for pt in patches:
            pt.start()
            self.addCleanup(pt.stop)
        self.root = root

    def _write_sidecar(self, venues, minutes_ago=0.0):
        gen = (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
               - datetime.timedelta(minutes=minutes_ago))
        (self.root / "ledger_sync_status.json").write_text(
            json.dumps({"generated_at": gen.isoformat(), "environment": "demo",
                        "venues": venues}), encoding="utf-8")

    def test_missing_sidecar_is_known_empty_not_unknown(self):
        """全新环境尚未同步过 ⇒ 不算不可判定（否则会把开仓全停）。"""
        failed, unknown = cb._ledger_sync_sidecar_state()
        self.assertEqual((failed, unknown), ([], ""))
        active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
        self.assertFalse(active, reason)

    def test_stale_sidecar_blocks_new_entries(self):
        self._write_sidecar({"binance": {"status": "failed"}}, minutes_ago=60)
        failed, unknown = cb._ledger_sync_sidecar_state()
        self.assertEqual(failed, [], "兼容：壳仍不列出（过旧场景）")
        self.assertIn("过旧", unknown)
        active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
        self.assertTrue(active, "不可判定 ⇒ fail-closed（用户拍板）")
        self.assertIn("不可判定", reason)
        self.assertIn("暂停开仓", reason)

    def test_corrupt_sidecar_blocks_new_entries(self):
        (self.root / "ledger_sync_status.json").write_text("{ 半截", encoding="utf-8")
        failed, unknown = cb._ledger_sync_sidecar_state()
        self.assertEqual(failed, [])
        self.assertIn("损坏", unknown)
        active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
        self.assertTrue(active)
        self.assertIn("不可判定", reason)

    def test_fresh_healthy_sidecar_still_does_not_trip(self):
        """回归护栏：健康旁车不得被新逻辑误伤。"""
        self._write_sidecar({"okx": {"status": "ok"}, "binance": {"status": "ok"}})
        active, reason = cb.is_circuit_breaker_active(usdt_available=1000.0)
        self.assertFalse(active, reason)

    def test_twin_caller_shares_the_same_direction(self):
        """trader 孪生版必须同源（防孪生漂移：一处禁、一处不禁）。"""
        twin = (Path(__file__).resolve().parents[2] / "scripts" / "trader"
                / "circuit_guard.py").read_text(encoding="utf-8")
        self.assertIn("_ledger_sync_sidecar_state", twin)
        self.assertIn("不可判定", twin)
        self.assertIn('return True, (f"台账同步状态不可判定', twin,
                      "孪生版必须同样 fail-closed（返回 True），而不是只打印")
