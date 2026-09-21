"""账户快照与 `/venue_accounts`：**fail-closed**、**所失败显式化**（第二百二十八刀）。

先读 `return` 再动笔（上一刀我正是在这里栽了两次）。实测到的形状：

```
{environment, environment_id: "okx:{mode}:{fingerprint}", credential_source,
 positions, orders, venue_errors, captured_at_ms}          # /admin/okx/account-snapshot
{environment, venues, portfolio_summary, captured_at_ms}    # /venue_accounts
```

| 语义 | 口径 |
|---|---|
| ★ fail-closed | `env.configured=False` **且**三个所都空空如也 ⇒ **503**，文案写明「V5 直签是唯一私有通道（fail-closed，无 CLI 回退）」|
| ★ 降级可用 | 未配置但**有**任何数据 ⇒ **不** 503（照常返回，把缺口交给 `venue_errors`）|
| ★ 所失败显式化 | 某所抛错 ⇒ 写进 `venue_errors[name]`（含类型名），响应**仍成功** —— **不抹平成"完整"**（审计 C6）|
| 保证金口径 | 有 `imr` 用 `imr`；`imr<=0` 但 notional 与 lever 都 >0 ⇒ `notional/lever`（2 位）；都拿不到 ⇒ `0` |
| 归属不覆盖 | 仓位/挂单补 `venue` 默认 `"okx"`，已有值不覆盖 |
| 时点 | `captured_at_ms` 为**毫秒**（与字段名相符）|
| `/venue_accounts` | `environment` 只允许 `demo`/`live` ⇒ 否则 **400**；合法 ⇒ 三所 map + 组合汇总 |
"""

import types
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from r20_backend.routers import exchanges as R


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)

    def _env(self, configured=True):
        return types.SimpleNamespace(mode="demo", configured=configured,
                                     simulated=False, fingerprint="FP-9")

    def _run(self, snap, configured=True, adapters_raise=True):
        p = patch("scripts.okx_runtime.current_environment",
                  return_value=self._env(configured))
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(R, "app_attr", side_effect=lambda name, default: lambda: snap)
        p2.start()
        self.addCleanup(p2.stop)
        exc = RuntimeError("所不可用") if adapters_raise else None
        p3 = patch("r20_backend.exchanges.get_adapter",
                   side_effect=exc if exc else MagicMock())
        p3.start()
        self.addCleanup(p3.stop)
        try:
            return R.admin_okx_account_snapshot(None, None), None
        except HTTPException as exc2:
            return None, exc2

    def test_return_shape_includes_identity_and_millisecond_capture(self):
        out, exc = self._run({"positions": [], "orders": []})
        self.assertIsNone(exc)
        self.assertEqual(out["environment"], "demo")
        self.assertEqual(out["environment_id"], "okx:demo:FP-9",
                         "身份三元组：所 + 档位 + 指纹")
        self.assertEqual(out["credential_source"], "multi-venue-aggregator")
        self.assertGreater(out["captured_at_ms"], 1_600_000_000_000, "毫秒量级")
        self.assertIn("venue_errors", out)

    def test_margin_prefers_exchange_imr_then_backfills(self):
        out, _ = self._run({"positions": [
            {"instId": "BTC", "notionalUsd": "1000", "lever": "3"},
            {"instId": "ETH", "imr": "12.5", "notionalUsd": "999", "lever": "9"},
            {"instId": "SOL", "notionalUsd": "0", "lever": "3"}], "orders": []})
        rows = {p["instId"]: p for p in out["positions"]}
        self.assertEqual(rows["BTC"]["margin"], 333.33, "缺失 ⇒ notional/lever（2 位）")
        self.assertEqual(rows["ETH"]["margin"], 12.5, "有 imr 就用 imr")
        self.assertEqual(rows["SOL"]["margin"], 0, "都拿不到 ⇒ 0（前端据此回落原生张数）")

    def test_venue_default_is_added_without_overwriting(self):
        out, _ = self._run({"positions": [{"instId": "A"}, {"instId": "B", "venue": "gate"}],
                            "orders": [{"ordId": "1"}]})
        self.assertEqual(out["positions"][0]["venue"], "okx")
        self.assertEqual(out["positions"][1]["venue"], "gate")
        self.assertEqual(out["orders"][0]["venue"], "okx")

    def test_venue_failures_are_recorded_not_flattened(self):
        """★ 审计 C6：所失败**显式化**（`venue_errors`），响应仍成功 —— 不假装"完整"。"""
        out, exc = self._run({"positions": [], "orders": []})
        self.assertIsNone(exc, "单所失败不整体失败")
        self.assertEqual(sorted(out["venue_errors"]), ["binance", "gate"])
        self.assertIn("RuntimeError", out["venue_errors"]["binance"],
                      "记下异常类型名便于定位")

    def test_unconfigured_and_empty_is_fail_closed_503(self):
        _out, exc = self._run({"positions": [], "orders": []}, configured=False)
        self.assertIsNotNone(exc, "没配置又没数据 ⇒ 明确失败")
        self.assertEqual(exc.status_code, 503)
        self.assertIn("fail-closed", exc.detail)
        self.assertIn("无 CLI 回退", exc.detail, "要点明为什么不能退到别的通道")

    def test_unconfigured_but_with_data_still_returns(self):
        """降级可用：未配置但**有**数据 ⇒ 照常返回（缺口交给 venue_errors）。"""
        out, exc = self._run({"positions": [{"instId": "A"}], "orders": []},
                             configured=False)
        self.assertIsNone(exc)
        self.assertEqual(len(out["positions"]), 1)


class VenueAccountsRouteTest(unittest.TestCase):
    def setUp(self):
        p = patch.object(R, "require_admin_header")
        self.auth = p.start()
        self.addCleanup(p.stop)

    def test_environment_must_be_demo_or_live(self):
        for bad in ("", "livex", "demo live", None):
            with self.subTest(value=bad):
                with self.assertRaises(HTTPException) as ctx:
                    R.venue_accounts(bad, None, None)
                self.assertEqual(ctx.exception.status_code, 400)

    def test_environment_is_normalised_before_dispatch(self):
        """★ 大小写与首尾空白**先归一再看档位**（我原以为 "  demo  " 会被拒 —— 错：它被受理）。"""
        with patch.object(R, "_venue_accounts_okx", return_value={}) as a, \
             patch.object(R, "_venue_accounts_gate", return_value={}), \
             patch.object(R, "_venue_accounts_binance", return_value={}), \
             patch("r20_backend.portfolio_aggregator.aggregate_venue_accounts",
                   return_value={}):
            out = R.venue_accounts("  DEMO  ", None, None)
        self.assertEqual(out["environment"], "demo", "回执里的档位是归一后的值")
        self.assertEqual(a.call_args.args[0], "demo", "三所收到的是归一后的档位")

    def test_valid_environment_aggregates_all_three_venues(self):
        sentinel = {"三所汇总": True}
        with patch.object(R, "_venue_accounts_okx", return_value={"status": "ready"}) as a, \
             patch.object(R, "_venue_accounts_gate", return_value={"status": "ready"}) as b, \
             patch.object(R, "_venue_accounts_binance", return_value={"status": "ready"}) as c, \
             patch("r20_backend.portfolio_aggregator.aggregate_venue_accounts",
                   return_value=sentinel) as agg:
            out = R.venue_accounts("live", None, None)
        self.assertEqual(sorted(out["venues"]), ["binance", "gate", "okx"])
        self.assertIs(out["portfolio_summary"], sentinel, "汇总来自聚合器（不自己算）")
        self.assertEqual([a.call_args.args[0], b.call_args.args[0], c.call_args.args[0]],
                         ["live", "live", "live"], "三所都收到同一个档位")
        self.assertEqual(out["environment"], "live")
        self.assertTrue(self.auth.called)


if __name__ == "__main__":
    unittest.main()
