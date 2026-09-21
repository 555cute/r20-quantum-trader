"""跨所保护单**只读预演**与上所列状态（第二百二十九刀）。

扫描接口的取向（据其文档串，本刀逐条落成断言）：

| 语义 | 口径 |
|---|---|
| ★ **绝不下单、绝不撤单** | 走 `audit_cross_venue_protection(dry_run=True)` —— 只是"这一轮**本该**做什么"（`would`: renew/repair/verify/noop）|
| ★ **逐所隔离** | 某所读持仓失败 ⇒ 写进 `snapshot_errors[venue]` **且** `snapshot[venue] = []` —— 「**读不到就如实登记，绝不假装该所干净**」|
| ★ **归属不可判定就不动** | 台账行只作**取证**；读不到 ⇒ `None` ⇒ 不产生证据 ⇒ 腿留在"归属不可判定"，**绝不自动撤** |
| 只数非零仓 | `abs(size_signed) > 0` 才算一仓 |
| ★ watchdog **三态** | `watchdog_enabled` 回传开关状态，导入失败 ⇒ **`None`** —— 用来区分「**巡检没开**」与「**巡检开了但没发现问题**」|
| 上所列 | `/listing_status`：鉴权**先于**参数校验；`environment` 归一后只允许 demo/live ⇒ 否则 400；每所按 **`_LISTING_ENV_MAP`**（gate 的演示档叫 **sandbox**）取上所列；每所回 `ok`+`reason`+`listed_count`+`sample_symbols`+`source`+`checked_at` |
"""

import types
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from r20_backend.routers import exchanges as R

VP = "scripts.trader.venue_protection"


def _env(mode="demo"):
    return types.SimpleNamespace(mode=mode, configured=True, simulated=False)


class ScanTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)
        p2 = patch("scripts.okx_runtime.current_environment", return_value=_env())
        p2.start()
        self.addCleanup(p2.stop)
        p3 = patch("r20_backend.close_intent.adapter_environment",
                   side_effect=lambda v, e: e)
        p3.start()
        self.addCleanup(p3.stop)
        self.report = {"would": ["renew BTC"], "critical": [], "errors": {}}
        p4 = patch(f"{VP}.audit_cross_venue_protection", return_value=dict(self.report))
        self.audit = p4.start()
        self.addCleanup(p4.stop)
        p5 = patch(f"{VP}.read_ledger_rows", return_value=[{"row": 1}])
        self.ledger = p5.start()
        self.addCleanup(p5.stop)

    def _run(self, adapters):
        p = patch("r20_backend.exchanges.get_adapter",
                  side_effect=lambda v, environment=None: adapters[v])
        p.start()
        self.addCleanup(p.stop)
        return R.venue_protection_scan(None, None)

    def test_the_scan_is_a_dry_run_preview_with_ledger_as_evidence(self):
        """★ **绝不下单、绝不撤单**：必须是 dry_run，且台账行只作**取证**输入。"""
        out = self._run({v: MagicMock() for v in ("okx", "gate", "binance")})
        kw = self.audit.call_args.kwargs
        self.assertIs(kw["dry_run"], True, "扫描必须是只读预演")
        self.assertEqual(kw["ledger_rows"], [{"row": 1}], "台账行作为取证输入")
        self.assertEqual(kw["environment"], "demo")
        self.assertIs(kw["venue_registry"].__class__, type(kw["venue_registry"]))
        self.assertEqual(out["would"], ["renew BTC"], "回传判决结果")
        self.assertEqual(out["environment"], "demo")

    def test_each_venue_failure_is_recorded_and_never_looks_clean(self):
        """★ 「**读不到就如实登记，绝不假装该所干净**」：失败所既进错误表、快照也给空。"""
        ok = MagicMock()
        ok.positions.return_value = []
        boom = MagicMock()
        boom.positions.side_effect = RuntimeError("读不到")
        out = self._run({"okx": ok, "gate": boom, "binance": ok})
        self.assertEqual(sorted(out["snapshot_errors"]), ["gate"])
        self.assertIn("RuntimeError", out["snapshot_errors"]["gate"])
        snapshot = self.audit.call_args.args[0]
        self.assertEqual(snapshot["gate"], [], "失败所给空表（不是漏键、也不是假数据）")
        self.assertIn("binance", snapshot, "成功所仍在快照里")

    def test_only_non_zero_positions_count(self):
        ad = MagicMock()
        ad.positions.return_value = [{"size_signed": "2"}, {"size_signed": "0"},
                                   {"size_signed": "-1.5"}]
        self._run({"okx": ad, "gate": ad, "binance": ad})
        snapshot = self.audit.call_args.args[0]
        self.assertEqual(len(snapshot["binance"]), 2, "零仓行不算一仓")

    def test_watchdog_state_is_reported_so_off_is_distinguishable(self):
        """★ 开关状态回传：用来区分「**巡检没开**」与「**巡检开了但没发现问题**」。

        ⚠️ **未验证**：`watchdog_enabled` 的**第三态 `None`**（模块导入失败）本轮没有钉
        —— 驱动它需要在调用期让 `from scripts import ai_factor_trader` 真的失败，
        我没找到**干净**的办法（包属性已被缓存时改 `sys.modules` 不生效）。
        **宁可不写，也不写一条自欺的用例。**
        """
        import scripts.ai_factor_trader as aft
        with patch.object(aft, "R20_VENUE_PROTECTION_WATCHDOG", True, create=True):
            out = self._run({v: MagicMock() for v in ("okx", "gate", "binance")})
        self.assertIs(out["watchdog_enabled"], True)
        with patch.object(aft, "R20_VENUE_PROTECTION_WATCHDOG", False, create=True):
            out2 = self._run({v: MagicMock() for v in ("okx", "gate", "binance")})
        self.assertIs(out2["watchdog_enabled"], False, "关 与 开 必须可区分")

    def test_okx_is_absent_from_the_snapshot(self):
        """★★ **实测发现（新待议）**：快照**只含 gate 与 binance** —— **OKX 完全不在里面**。

        接口文档串写的是「每所一次持仓读取 + 每仓一次保护单列表」，但代码里只遍历
        `("gate", "binance")`。后果比"空表"更隐蔽：`snapshot` **没有 okx 这个键**，
        `snapshot_errors` 也不会提 OKX ⇒ 报告看起来是**完整的**。
        （「**缺席即缺席**」：静默缺席比显式的空表更难被发现。）
        本用例钉住现状；若将来补上 OKX，这条会**红**，正好提醒重新审视。
        """
        out = self._run({v: MagicMock() for v in ("okx", "gate", "binance")})
        snapshot = self.audit.call_args.args[0]
        self.assertEqual(sorted(snapshot), ["binance", "gate"],
                         "现状：OKX 不在被巡检的所里（列待议）")
        self.assertNotIn("okx", out["snapshot_errors"], "也没记进错误表 ⇒ 看不出缺席")


class ListingStatusTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)

    def _snap(self, **over):
        base = {"ok": True, "reason": "", "listed_count": 3, "sample_symbols": ["BTC"],
                "source": "rest", "checked_at": 123}
        base.update(over)
        return types.SimpleNamespace(**base)

    def test_authentication_precedes_parameter_validation(self):
        """★ 鉴权最先（对照 `/admin/okx/runtime` 的 refresh-before-auth）。"""
        self.auth.side_effect = HTTPException(status_code=403, detail="无权限")
        with self.assertRaises(HTTPException) as ctx:
            R.listing_status("乱写的档位", None, None)
        self.assertEqual(ctx.exception.status_code, 403, "鉴权失败先于档位 400")
        self.assertTrue(self.auth.called)

    def test_environment_is_normalised_then_validated(self):
        with self.assertRaises(HTTPException) as ctx:
            R.listing_status("livex", None, None)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_venue_specific_listing_environment_and_payload(self):
        seen = []
        with patch("r20_backend.exchanges.listing.listing_snapshot",
                   side_effect=lambda v, e: (seen.append((v, e)), self._snap())[1]):
            out = R.listing_status(" demo ", None, None)
        self.assertEqual(seen, [("okx", "demo"), ("gate", "sandbox"),
                                ("binance", "demo")],
                         "gate 的演示档叫 **sandbox**（各所叫法不同）")
        self.assertEqual(out["environment"], "demo", "回执用归一后的档位")
        self.assertEqual(sorted(out["venues"]), ["binance", "gate", "okx"])
        self.assertEqual(out["venues"]["okx"],
                         {"ok": True, "reason": "", "listed_count": 3,
                          "sample_symbols": ["BTC"], "source": "rest",
                          "checked_at": 123})

    def test_missing_sample_symbols_default_to_empty_list(self):
        snap = types.SimpleNamespace(ok=False, reason="上游超时", listed_count=0,
                                     source="rest", checked_at=0)
        with patch("r20_backend.exchanges.listing.listing_snapshot", return_value=snap):
            out = R.listing_status("live", None, None)
        self.assertEqual(out["venues"]["okx"]["sample_symbols"], [],
                         "缺字段不炸，给空表")
        self.assertEqual(out["venues"]["okx"]["reason"], "上游超时",
                         "失败必须带原因，不只给一个 ok=False")


if __name__ == "__main__":
    unittest.main()
