"""跨所保护单覆盖核验与临期续期（roadmap G8）契约测试。

全 mock、零网络、零写盘。守的是**三条安全铁律**（见 venue_protection 模块 docstring）：

1. **先挂新、后撤旧**：新腿挂失败时**旧腿必须还在**（绝不出现"撤了旧的、新的没挂上"）；
2. **宁可双、不可裸**：旧腿撤失败不回滚新腿，只登记；
3. **不可判定 ≠ 安全**：覆盖算不出来时给 None，人工腿/陌生腿**永不被撤**。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from scripts.trader.venue_protection import (
    DEFAULT_RENEW_WITHIN_S,
    ensure_venue_protection,
    scan_protective_orders,
)

NOW = 1_789_000_000.0          # 固定"现在"，避免用例依赖时钟


def gate_sl_row(oid="sl1", *, size=0, close=True, created=None, expiration=604800,
                text="t-r20sl12345", contract="BTC_USDT", status="open"):
    """Gate `price_orders` 形状：标签在 initial.text，到期在 trigger.expiration。"""
    return {
        "id": oid, "status": status, "contract": contract,
        "initial": {"contract": contract, "size": size, "close": close, "text": text},
        "trigger": {"price": "78000", "rule": 2, "expiration": expiration},
        "create_time": NOW - 100 if created is None else created,
    }


def binance_leg_row(oid="b1", *, kind="STOP_MARKET", size=3, trigger=78000, side="SELL"):
    return {
        "id": oid, "algo_id": oid, "symbol": "BTCUSDT", "side": side,
        "type": kind, "trigger_price": trigger,
        "raw": {"orderType": kind, "quantity": size, "algoId": oid},
        "size": size,
    }


class ScanTest(unittest.TestCase):
    def test_gate_close_all_leg_counts_as_full_coverage(self):
        """Gate `size=0 + close=true` = 整仓平 ⇒ 覆盖是**全部**，不能当"不可判定"。"""
        scan = scan_protective_orders([gate_sl_row()], symbol="BTC_USDT", pos_side="long",
                                      position_size=170, now_s=NOW)
        self.assertTrue(scan["coverage_ok"])
        self.assertEqual(scan["missing_size"], 0)
        self.assertTrue(scan["has_live_sl"])
        self.assertFalse(scan["needs_repair"])
        self.assertFalse(scan["needs_verify"])

    def test_binance_sized_legs_sum_and_report_gap(self):
        rows = [binance_leg_row("s1", size=2), binance_leg_row("s2", size=3)]
        scan = scan_protective_orders(rows, symbol="BTCUSDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertEqual(scan["covered_size"], 5)
        self.assertEqual(scan["missing_size"], 5)
        self.assertFalse(scan["coverage_ok"])
        self.assertTrue(scan["needs_repair"], "缺 5 张必须报缺口")

    def test_float_noise_within_tolerance_is_not_a_gap(self):
        rows = [binance_leg_row("s1", size=9.99999)]
        scan = scan_protective_orders(rows, symbol="BTCUSDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertTrue(scan["coverage_ok"], "千分之一以内的浮点噪音不该触发补挂")

    def test_tp_only_is_not_loss_protection(self):
        """只有止盈腿不算保护（止盈触发不了 = 亏损无人接）。"""
        row = gate_sl_row(text="t-r20tp12345")
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertFalse(scan["has_live_sl"])
        self.assertTrue(scan["needs_repair"])

    def test_foreign_manual_legs_are_counted_but_never_ours(self):
        """人工挂的保护单：登记为 foreign，不进 needs_renew，也绝不在续期名单里。"""
        manual = {"id": "m1", "status": "open", "contract": "BTC_USDT",
                  "initial": {"contract": "BTC_USDT", "size": 0, "close": True, "text": "manual"},
                  "trigger": {"price": "90000", "expiration": 60},
                  "create_time": NOW - 10}
        scan = scan_protective_orders([manual], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertEqual(scan["foreign_count"], 1)
        self.assertEqual(scan["ours"], [])
        self.assertFalse(scan["needs_renew"], "人工腿临期也不该由我们续期")
        self.assertTrue(scan["needs_repair"], "没有我方止损腿 ⇒ 必须补挂")

    def test_reverse_side_leg_is_ignored(self):
        """方向不对的腿（做多仓却挂 BUY 平仓腿）不算覆盖。"""
        row = binance_leg_row("b1", kind="STOP_MARKET", size=3, side="BUY")
        scan = scan_protective_orders([row], symbol="BTCUSDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertEqual(scan["ours"], [])
        self.assertTrue(scan["needs_repair"])


class ExpiryTest(unittest.TestCase):
    def test_far_from_expiry_no_renew(self):
        row = gate_sl_row(created=NOW - 100, expiration=604800)   # 还剩 ~7 天
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertFalse(scan["needs_renew"])
        self.assertEqual(scan["expiring"], [])
        self.assertEqual(scan["expired"], [])

    def test_within_24h_window_is_expiring(self):
        row = gate_sl_row(created=NOW - (604800 - 3600), expiration=604800)   # 还剩 1 小时
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertTrue(scan["needs_renew"])
        self.assertEqual([leg["id"] for leg in scan["expiring"]], ["sl1"])
        self.assertLess(scan["expiring"][0]["remaining_s"], DEFAULT_RENEW_WITHIN_S)
        self.assertTrue(scan["coverage_ok"], "临期不等于没覆盖")

    def test_already_expired_is_separated_from_expiring(self):
        row = gate_sl_row(created=NOW - (604800 + 60), expiration=604800)
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertEqual([leg["id"] for leg in scan["expired"]], ["sl1"])
        self.assertEqual(scan["expiring"], [])
        self.assertTrue(scan["needs_renew"])

    def test_expiration_zero_means_never(self):
        row = gate_sl_row(expiration=0)
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertFalse(scan["needs_renew"], "Gate expiration=0 = 永不过期")
        self.assertEqual(scan["expiry_unknown"], [])
        self.assertFalse(scan["needs_verify"])

    def test_missing_expiration_is_unknown_not_safe(self):
        row = gate_sl_row(expiration=None)
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertEqual([leg["id"] for leg in scan["expiry_unknown"]], ["sl1"])
        self.assertTrue(scan["needs_verify"], "到期不可判定 ⇒ 必须标记待复验")
        self.assertFalse(scan["needs_renew"], "不可判定不擅自写单")

    def test_expiration_without_create_time_is_unknown(self):
        row = gate_sl_row()
        row.pop("create_time")
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertEqual([leg["id"] for leg in scan["expiry_unknown"]], ["sl1"])

    def test_absolute_expiration_supported(self):
        row = gate_sl_row(expiration=NOW + 600)     # 绝对值 > 1e9
        scan = scan_protective_orders([row], symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertEqual([leg["id"] for leg in scan["expiring"]], ["sl1"])


class EnsureActionTest(unittest.TestCase):
    def _ad(self, rows, *, attach_raises=None, cancel_raises=None):
        ad = MagicMock()
        ad.list_protective_orders.return_value = rows
        if attach_raises is not None:
            ad.attach_protective_orders.side_effect = attach_raises
        else:
            ad.attach_protective_orders.return_value = {"tp": "new-tp", "sl": "new-sl"}
        ad.cancel_price_order.side_effect = cancel_raises
        return ad

    def test_no_action_when_coverage_healthy(self):
        ad = self._ad([gate_sl_row()])
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertTrue(res["ok"])
        self.assertEqual(res["stage"], "noop")
        ad.attach_protective_orders.assert_not_called()
        ad.cancel_price_order.assert_not_called()

    def test_repair_when_no_sl_leg(self):
        ad = self._ad([])
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertTrue(res["ok"])
        ad.attach_protective_orders.assert_called_once()
        self.assertEqual(res["placed"], {"tp": "new-tp", "sl": "new-sl"})

    def test_renew_places_new_before_cancelling_old(self):
        """铁律①：新腿必须先挂上，旧腿才允许撤。"""
        order = []
        ad = self._ad([gate_sl_row("old-sl", created=NOW - (604800 - 60))])
        ad.attach_protective_orders.side_effect = lambda *a, **k: order.append("attach") or {"sl": "new-sl"}
        ad.cancel_price_order.side_effect = lambda oid: order.append(f"cancel:{oid}")
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertTrue(res["ok"])
        self.assertEqual(order, ["attach", "cancel:old-sl"], "撤旧发生在新挂之前 ⇒ 裸仓窗口")
        self.assertEqual(res["cancelled"], ["old-sl"])

    def test_attach_failure_keeps_old_legs(self):
        """铁律①（失败分支）：挂新失败 ⇒ 绝不撤旧（旧腿到期前仍在保护）。

        `ok=False` 是刻意的：旧腿 60 秒后就没**，不能因为"此刻还有覆盖"就让调用方
        以为这次续期成功了（`protected_now=True` 才描述当下）。
        """
        ad = self._ad([gate_sl_row("old-sl", created=NOW - (604800 - 60))],
                      attach_raises=RuntimeError("gate 502"))
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertFalse(res["ok"], "续期没达成 ⇒ ok 必须为 False")
        self.assertTrue(res["protected_now"], "旧腿此刻仍在保护 ⇒ protected_now 为 True")
        self.assertEqual(res["stage"], "attach")
        ad.cancel_price_order.assert_not_called()
        self.assertEqual(res["kept_old"], ["old-sl"])
        self.assertIn("gate 502", res["detail"])

    def test_cancel_failure_keeps_new_leg_and_warns(self):
        """铁律②：撤旧失败只登记，不回滚新腿（宁可双、不可裸）。"""
        warned = []
        ad = self._ad([gate_sl_row("old-sl", created=NOW - (604800 - 60))],
                      cancel_raises=RuntimeError("net down"))
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW,
                                      log=warned.append)
        self.assertTrue(res["ok"], "新腿已生效 ⇒ 仓位是受保护的")
        self.assertEqual(res["kept_old"], ["old-sl"])
        self.assertEqual(len(warned), 1)
        self.assertIn("warn", warned[0])

    def test_foreign_leg_is_never_cancelled(self):
        """铁律③：人工腿即使临期也不许撤（我们只维护自己的单）。"""
        manual = {"id": "m1", "status": "open", "contract": "BTC_USDT",
                  "initial": {"contract": "BTC_USDT", "size": 0, "close": True, "text": "manual"},
                  "trigger": {"price": "90000", "expiration": 60}, "create_time": NOW - 10}
        ad = self._ad([manual])
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        ad.cancel_price_order.assert_not_called()
        self.assertEqual(res["cancelled"], [])
        self.assertTrue(res["ok"], "补挂了我方止损腿 ⇒ 已受保护")

    def test_list_failure_never_writes(self):
        """读不到保护单时不可判定 ⇒ 绝不去写单（避免重复挂），如实上报失败。"""
        ad = MagicMock()
        ad.list_protective_orders.side_effect = RuntimeError("auth failed")
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "list")
        ad.attach_protective_orders.assert_not_called()

    def test_unknown_coverage_does_not_write(self):
        """覆盖不可判定（拿不到 size 且不是整仓平）⇒ 只标记待复验，不擅自写单。"""
        row = gate_sl_row(size=0, close=False)
        ad = self._ad([row])
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "verify")
        ad.attach_protective_orders.assert_not_called()


class WiringTest(unittest.TestCase):
    def test_module_does_not_bind_facade_names_at_import_time(self):
        """本仓纪律：子模块不得在 import 期绑定门面名字（否则 patch 面会静默失效）。"""
        import inspect
        from scripts.trader import venue_protection
        src = inspect.getsource(venue_protection)
        for forbidden in ("from scripts.ai_factor_trader import", "import scripts.ai_factor_trader",
                          "from scripts.risk_constants import", "from r20_backend.exchanges import"):
            self.assertNotIn(forbidden, src, f"import 期绑定了门面名字：{forbidden}")

    def test_registered_in_subpackage_manifest(self):
        from pathlib import Path
        init = Path(__file__).resolve().parents[2] / "scripts" / "trader" / "__init__.py"
        self.assertIn("venue_protection.py", init.read_text(encoding="utf-8"),
                      "新模块必须登记进 scripts/trader/__init__.py 的模块清单")


if __name__ == "__main__":
    unittest.main()
