"""熔断判定 `is_circuit_breaker_active` 的 fail-closed 分支（第二百一十一刀）。

活路径（trader 主循环）走的就是这个函数。覆盖率探针在**全量套件**下点出 6 条从未执行的行，
全部是「**不可判定 = 不放松**」的落点：

| 行 | 分支 |
|---|---|
| 105 | 黑天鹅哨兵已熔断 ⇒ 直接把哨兵理由作为熔断理由返回 |
| 115 | 熔断状态文件 `active`/`triggered` 且未过期 ⇒ 熔断 |
| 117 | 熔断状态文件**损坏** ⇒ 熔断（不是"当作没熔断"） |
| 138 | 台账同步旁车检查**不可用** ⇒ 熔断 |
| 149 | 环境不可判定 ⇒ 当日亏损**保守全计**（宁停不漏） |
| 155 | 日亏损风控数据**读取失败** ⇒ 熔断 |

⚠️ 本刀同时纠正了一个**自造的错觉**：我先前凭 `tests/audit` 与 `tests/extraction` 里的
**用例名**推断"哨兵从没被真的跑起来"，但全量探针显示哨兵本体（45-91 行）**早已 100% 覆盖**
（真正的驱动在 `tests/core/test_black_swan_sentinel_revival.py` 等处）。
⇒ 看用例名 ≠ 看覆盖事实；判缺口必须以**全量探针**为准。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.trader.circuit_guard import is_circuit_breaker_active

CB_MODULE = "r20_backend.execution.circuit_breaker"


class CircuitBreakerActiveFailClosedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-cb-")
        self.addCleanup(self.tmp.cleanup)
        self.cb_file = Path(self.tmp.name) / "circuit_breaker.json"
        self.ledger_file = Path(self.tmp.name) / "trading_ledger.json"

    def _call(self, *, sentinel=(False, ""), usdt=1000.0, ledger=None, cap=100.0, env=None):
        if ledger is not None:
            self.ledger_file.write_text(json.dumps(ledger), encoding="utf-8")
        kwargs = dict(circuit_breaker_file=str(self.cb_file),
                      ledger_json_file=str(self.ledger_file),
                      current_environment=env or (lambda: type("E", (), {"mode": "demo"})()),
                      effective_daily_loss_limit=lambda _u: cap,
                      sentinel_check=lambda: sentinel)
        return is_circuit_breaker_active(usdt, **kwargs)

    # ── 105：哨兵已熔断 ────────────────────────────────────────────────
    def test_tripped_sentinel_short_circuits_with_its_reason(self):
        tripped, why = self._call(sentinel=(True, "🚨 哨兵：断崖式暴跌"))
        self.assertTrue(tripped)
        self.assertIn("断崖式暴跌", why, "哨兵的理由必须原样上抛（不许被覆盖成笼统措辞）")

    # ── 115 / 117：熔断状态文件 ───────────────────────────────────────
    def test_active_breaker_file_trips(self):
        self.cb_file.write_text(json.dumps({"active": True, "reason": "极端行情熔断中"}),
                                encoding="utf-8")
        tripped, why = self._call()
        self.assertTrue(tripped, "状态文件写着 active ⇒ 必须熔断")
        self.assertIn("极端行情熔断中", why)

    def test_corrupt_breaker_file_trips_instead_of_passing(self):
        self.cb_file.write_text("{ 这不是合法 JSON", encoding="utf-8")
        tripped, why = self._call()
        self.assertTrue(tripped, "状态文件损坏 ⇒ 不可判定 ⇒ 必须熔断（不当作没熔断）")
        self.assertIn("损坏", why)

    def test_expired_breaker_file_does_not_trip(self):
        self.cb_file.write_text(json.dumps({"active": True, "expires_at_ts": 1}),
                                encoding="utf-8")
        tripped, why = self._call()
        self.assertFalse(tripped, f"已过期的熔断记录不该继续拦着：{why!r}")

    # ── 138 / 149 / 155：台账与日亏损 ─────────────────────────────────
    def test_sidecar_check_unavailable_trips(self):
        self.ledger_file.write_text(json.dumps({"trades": []}), encoding="utf-8")
        with patch(f"{CB_MODULE}._ledger_sync_sidecar_state",
                   side_effect=RuntimeError("sidecar boom")):
            tripped, why = self._call(ledger={"trades": []})
        self.assertTrue(tripped, "台账同步旁车检查不可用 ⇒ 不可判定 ⇒ 必须熔断")
        self.assertIn("旁车检查不可用", why)

    def test_unknown_sidecar_state_trips(self):
        with patch(f"{CB_MODULE}._ledger_sync_sidecar_state", lambda: ([], "SIDECAR_UNKNOWN")):
            tripped, why = self._call(ledger={"trades": []})
        self.assertTrue(tripped, "同步状态不可判定 ⇒ 熔断")
        self.assertIn("不可判定", why)

    def test_failed_venues_trip(self):
        with patch(f"{CB_MODULE}._ledger_sync_sidecar_state", lambda: (["gate"], None)):
            tripped, why = self._call(ledger={"trades": []})
        self.assertTrue(tripped, "有场所同步失败 ⇒ 当日亏损求和不完整 ⇒ 熔断")
        self.assertIn("gate", why)

    def test_undecidable_environment_counts_all_pnl(self):
        """环境不可判定 ⇒ `_mode=""` ⇒ **保守全计**（demo/live 不互抵）⇒ 亏损超限要熔断。"""
        def boom():
            raise RuntimeError("env down")
        with patch(f"{CB_MODULE}._ledger_sync_sidecar_state", lambda: ([], None)), \
             patch(f"{CB_MODULE}.ledger_daily_closed_pnl", lambda ledger, mode, today: -300.0):
            tripped, why = self._call(ledger={"trades": []}, env=boom, cap=100.0)
        self.assertTrue(tripped, "环境读不到时不许因「不知道盈亏算谁的」就放行")
        self.assertIn("触及单日最大风控熔断限额", why)

    def test_unreadable_ledger_trips(self):
        self.ledger_file.write_text("{ 坏台账", encoding="utf-8")
        with patch(f"{CB_MODULE}._ledger_sync_sidecar_state", lambda: ([], None)):
            tripped, why = self._call()
        self.assertTrue(tripped, "日亏损数据读不到 ⇒ 不可判定 ⇒ 必须熔断")
        self.assertIn("日亏损风控数据读取失败", why)

    def test_clean_state_does_not_trip(self):
        with patch(f"{CB_MODULE}._ledger_sync_sidecar_state", lambda: ([], None)), \
             patch(f"{CB_MODULE}.ledger_daily_closed_pnl", lambda ledger, mode, today: 5.0):
            tripped, why = self._call(ledger={"trades": []})
        self.assertFalse(tripped, f"一切正常不得误熔断：{why!r}")
        self.assertEqual(why, "")


if __name__ == "__main__":
    unittest.main()
