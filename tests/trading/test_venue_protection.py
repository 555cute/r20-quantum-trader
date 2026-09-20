"""跨所保护单覆盖核验与临期续期（roadmap G8）契约测试。

全 mock、零网络、零写盘。守的是**三条安全铁律**（见 venue_protection 模块 docstring）：

1. **先挂新、后撤旧**：新腿挂失败时**旧腿必须还在**（绝不出现"撤了旧的、新的没挂上"）；
2. **宁可双、不可裸**：旧腿撤失败不回滚新腿，只登记；
3. **不可判定 ≠ 安全**：覆盖算不出来时给 None，人工腿/陌生腿**永不被撤**。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import re
import unittest
from unittest.mock import MagicMock, call

from scripts.trader.venue_protection import (
    DEFAULT_RENEW_WITHIN_S,
    audit_cross_venue_protection,
    ensure_venue_protection,
    scan_protective_orders,
    watchdog_debounce_step,
    watchdog_gap_key,
)

NOW = 1_789_000_000.0          # 固定"现在"，避免用例依赖时钟


def gate_sl_row(oid="sl1", *, size=0, close=True, created=None, expiration=604800,
                text="t-r20sl12345", contract="BTC_USDT", status="open", trigger="78000"):
    """Gate `price_orders` 形状：标签在 initial.text，到期在 trigger.expiration。"""
    return {
        "id": oid, "status": status, "contract": contract,
        "initial": {"contract": contract, "size": size, "close": close, "text": text},
        "trigger": {"price": trigger, "rule": 2, "expiration": expiration},
        "create_time": NOW - 100 if created is None else created,
    }


def binance_leg_row(oid="b1", *, kind="STOP_MARKET", size=3, trigger=78000, side="SELL",
                    time_in_force="GTC", good_till_date=0, close_position=False):
    """Binance `algoOrder` 形状 —— **照适配器真实回包写**（本机实跑核对）：

    ⚠️ 顶层**没有** `size`：数量在 `raw.quantity`（`actualQty` 是已成交量，不能用）；
    到期语义在 `raw.timeInForce`（GTC = 撤销前一直有效）/ `raw.goodTillDate`（GTD 时间戳）。
    第一版夹具偷懒把 size 放在顶层，于是漏掉了真实回包这一层 —— 巡检在**真数据**上
    会把每个币安仓位都判成"覆盖不可判定"。
    """
    return {
        "id": oid, "algo_id": oid, "symbol": "BTCUSDT", "side": side,
        "type": kind, "trigger_price": trigger,
        "raw": {
            "algoId": oid, "symbol": "BTCUSDT", "side": side, "positionSide": "BOTH",
            "timeInForce": time_in_force, "quantity": str(size), "actualQty": "0.0",
            "triggerPrice": str(trigger), "reduceOnly": True,
            "closePosition": close_position, "algoStatus": "NEW",
            "goodTillDate": good_till_date, "createTime": int(NOW * 1000),
        },
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


class BinanceRealShapeTest(unittest.TestCase):
    """币安 `algoOrder` 真实回包的两个坑（本机实跑真单核对后补的回归）。"""

    def test_size_read_from_raw_quantity(self):
        scan = scan_protective_orders([binance_leg_row("b1", size=82)], symbol="BTCUSDT",
                                      pos_side="long", position_size=82, now_s=NOW)
        self.assertEqual(scan["covered_size"], 82.0,
                         "没读到 raw.quantity ⇒ 覆盖永远'不可判定'，巡检不敢动手")
        self.assertTrue(scan["coverage_ok"])

    def test_actual_qty_is_not_used_as_coverage(self):
        """`actualQty` 是**已成交**量：新挂的单它是 0，不能拿它当覆盖量。"""
        row = binance_leg_row("b1", size=5)
        row["raw"]["actualQty"] = "0.0"
        row["raw"].pop("quantity")
        scan = scan_protective_orders([row], symbol="BTCUSDT", pos_side="long",
                                      position_size=5, now_s=NOW)
        self.assertIsNone(scan["covered_size"], "只有 actualQty 时必须判为不可判定")

    def test_gtc_means_never_expire_not_unknown(self):
        """GTC 是明确语义（撤销前一直有效）⇒ 不能当'到期不可判定'，否则全是噪音。"""
        scan = scan_protective_orders([binance_leg_row("b1")], symbol="BTCUSDT",
                                      pos_side="long", position_size=3, now_s=NOW)
        self.assertEqual(scan["needs_renew"], False)
        self.assertEqual(scan["needs_verify"], False)
        self.assertEqual(scan["ours"][0]["expiry_state"], "never")

    def test_good_till_date_is_absolute_expiry(self):
        row = binance_leg_row("b1", good_till_date=int((NOW + 600) * 1000))   # 毫秒
        scan = scan_protective_orders([row], symbol="BTCUSDT", pos_side="long",
                                      position_size=3, now_s=NOW)
        self.assertEqual([leg["id"] for leg in scan["expiring"]], ["b1"])
        self.assertTrue(scan["needs_renew"])

    def test_short_position_leg_side_is_buy(self):
        """方向守卫：做空仓的保护腿是 BUY；SELL 腿属于做多仓，不该被算进来。"""
        sell_leg = binance_leg_row("b1", side="SELL")
        buy_leg = binance_leg_row("b2", side="BUY")
        scan_short = scan_protective_orders([sell_leg, buy_leg], symbol="BTCUSDT",
                                            pos_side="short", position_size=3, now_s=NOW)
        self.assertEqual([leg["id"] for leg in scan_short["ours"]], ["b2"])

    def test_close_position_flag_counts_as_full_coverage(self):
        row = binance_leg_row("b1", close_position=True)
        row["raw"].pop("quantity")
        scan = scan_protective_orders([row], symbol="BTCUSDT", pos_side="long",
                                      position_size=42, now_s=NOW)
        self.assertTrue(scan["coverage_ok"], "closePosition=true = 整仓平，覆盖是全部")

    def test_missing_both_expiry_semantics_is_unknown(self):
        """既没有 expiration、也没有 GTC/GTD ⇒ 不可判定（不许假设安全）。"""
        row = binance_leg_row("b1")
        row["raw"].pop("timeInForce")
        scan = scan_protective_orders([row], symbol="BTCUSDT", pos_side="long",
                                      position_size=3, now_s=NOW)
        self.assertEqual(scan["ours"][0]["expiry_state"], "unknown")
        self.assertTrue(scan["needs_verify"])


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


class RenewReusesExistingPricesTest(unittest.TestCase):
    """续期只该**延长时间**，不该重新定价 —— 价位是策略决定。"""

    def _ad(self, rows):
        ad = MagicMock()
        ad.list_protective_orders.return_value = rows
        ad.attach_protective_orders.return_value = {"sl": "new-sl"}
        return ad

    def test_renew_without_passed_prices_reuses_leg_triggers(self):
        rows = [gate_sl_row("old-sl", created=NOW - (604800 - 60), trigger="77000")]
        ad = self._ad(rows)
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)   # 不传价位
        self.assertTrue(res["ok"], res["detail"])
        kwargs = ad.attach_protective_orders.call_args.kwargs
        self.assertEqual(kwargs["sl_px"], 77000.0, "续期改写了原有止损价 ⇒ 偷偷改策略")

    def test_scan_exposes_trigger_prices(self):
        scan = scan_protective_orders([gate_sl_row(trigger="77000")], symbol="BTC_USDT",
                                      pos_side="long", position_size=10, now_s=NOW)
        self.assertEqual(scan["ours"][0]["trigger_price"], 77000.0)

    def test_no_price_available_refuses_to_invent(self):
        """没有止损腿、又没传价位 ⇒ 绝不猜一个价位补挂。"""
        ad = self._ad([])
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)
        self.assertFalse(res["ok"])
        self.assertEqual(res["stage"], "no_price")
        ad.attach_protective_orders.assert_not_called()


class AuditCrossVenueTest(unittest.TestCase):
    """每周期巡检：逐所隔离、临期必续、缺腿必吼、绝不替人定价。"""

    def _registry(self, adapters):
        reg = MagicMock()
        reg.get_adapter.side_effect = lambda v, environment=None: adapters[v]
        return reg

    def _row(self, inst="BTC_USDT", side="long", size=1.0):
        return {"venue": "gate", "inst_id": inst, "base": inst.split("_")[0],
                "side": side, "size_signed": size}

    def test_expiring_leg_is_renewed_with_its_own_price(self):
        gate = MagicMock()
        gate.list_protective_orders.return_value = [
            gate_sl_row("old-sl", created=NOW - (604800 - 60), trigger="77000")]
        gate.attach_protective_orders.return_value = {"sl": "new-sl"}
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [self._row()]}, venue_registry=reg, environment="demo", now_s=NOW)
        self.assertEqual(report["venues"]["gate"]["renewed"], 1)
        self.assertEqual(len(report["actions"]), 1)
        self.assertEqual(report["critical"], [])
        gate.attach_protective_orders.assert_called_once()
        self.assertEqual(gate.cancel_price_order.call_args[0][0], "old-sl")

    def test_position_without_stop_leg_is_critical_and_not_written(self):
        gate = MagicMock()
        gate.list_protective_orders.return_value = []
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [self._row()]}, venue_registry=reg, environment="demo", now_s=NOW)
        self.assertEqual(len(report["critical"]), 1)
        self.assertEqual(report["venues"]["gate"]["missing"], 1)
        gate.attach_protective_orders.assert_not_called()
        gate.cancel_price_order.assert_not_called()

    def test_one_venue_failure_does_not_stop_the_other(self):
        gate = MagicMock()
        gate.list_protective_orders.side_effect = RuntimeError("gate 502")
        binance = MagicMock()
        binance.list_protective_orders.return_value = [binance_leg_row("b1", size=1)]
        reg = self._registry({"gate": gate, "binance": binance})
        report = audit_cross_venue_protection(
            {"gate": [self._row()], "binance": [self._row("BTCUSDT", size=1.0)]},
            venue_registry=reg, environment="demo", now_s=NOW)
        self.assertTrue(report["errors"], "gate 读失败必须登记")
        self.assertEqual(report["venues"]["binance"]["checked"], 1,
                         "一个所挂了不该让另一个所不被巡检")

    def test_adapter_unavailable_is_an_error_not_a_silent_pass(self):
        reg = MagicMock()
        reg.get_adapter.side_effect = RuntimeError("凭证未配置")
        report = audit_cross_venue_protection(
            {"gate": [self._row()]}, venue_registry=reg, environment="demo", now_s=NOW)
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["stage"], "adapter")

    def test_absent_venue_in_snapshot_is_skipped_not_assumed_clean(self):
        reg = self._registry({"gate": MagicMock(), "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": []}, venue_registry=reg, environment="demo", now_s=NOW)
        self.assertEqual(len(report["skipped"]), 1)
        self.assertIn("binance", report["skipped"][0]["venue"])

    def test_bad_rows_are_skipped_not_crashed(self):
        """字段不足的行必须只登记跳过，**不得**按该行去查保护腿。

        第一百一十二刀调整：本巡检新增"逐腿归属"（只读，用于暴露历史遗留腿），
        它会**每所一次** `list_protective_orders(None)` —— 这是场所级调用，
        与"照着坏行去查"是两件事。故断言改为：
        ① 该所只被调用一次、且参数是 `None`（场所级，不是坏行的 symbol）；
        ② 坏行照旧进 `skipped`。
        """
        gate = MagicMock()
        gate.list_protective_orders.return_value = [gate_sl_row()]
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [{"inst_id": "BTC_USDT"}]},   # 缺 side/size
            venue_registry=reg, environment="demo", now_s=NOW)
        self.assertTrue(report["skipped"])
        self.assertEqual(gate.list_protective_orders.call_args_list, [call(None)],
                         "只允许一次场所级归属读取，绝不允许照着坏行查 symbol")


class WatchdogStageTest(unittest.TestCase):
    """接线层：默认关闭＝零副作用；开启＝巡检并把结论写进 executed_actions。"""

    def _stage(self, flag, report=None, raises=None, dry_run=False):
        from scripts.trader.cycle_stages import venue_protection_watchdog_stage
        calls = []

        def fake_audit(snapshot, **kw):
            calls.append((snapshot, kw))
            if raises is not None:
                raise raises
            return report or {"venues": {}, "actions": [], "critical": [], "errors": [], "skipped": []}

        actions = []
        out = venue_protection_watchdog_stage(
            xv_positions_by_venue={"gate": [{"inst_id": "BTC_USDT"}]},
            executed_actions=actions,
            venue_registry=MagicMock(),
            current_environment=lambda: MagicMock(mode="demo"),
            R20_VENUE_PROTECTION_WATCHDOG=flag,
            audit_cross_venue_protection=fake_audit,
            dry_run=dry_run,
        )
        return out, calls, actions

    def test_dry_run_is_passed_through_and_reports_would(self):
        """预演模式：把 `dry_run=True` 透给审计层，并逐条报出"本来会做"的动作。

        这是 G8 从"默认关闭"走向"开闸"之间**唯一安全**的过渡档：
        判定照跑，但绝不写单 —— 把"一次误判"和"一串真实订单"隔开。
        """
        report = {
            "venues": {"gate": {"checked": 1}},
            "actions": [],                      # 预演下必须为空（审计层不写单）
            "would": [{"venue": "gate", "inst": "BTC_USDT", "stage": "renew",
                       "detail": "距到期 1.2 天，本来会续期"}],
            "critical": [], "errors": [], "skipped": [],
        }
        out, calls, actions = self._stage(True, report=report, dry_run=True)
        self.assertIsNotNone(out)
        self.assertTrue(calls[0][1].get("dry_run"), "dry_run 必须透传给审计层")
        self.assertTrue(any("预演" in a for a in actions), "必须把 would 报进 executed_actions")
        self.assertTrue(any("本来会续期" in a for a in actions))

    def test_dry_run_defaults_to_false(self):
        """不传 dry_run ⇒ 一律按真实模式（不给"悄悄预演"留后门）。"""
        out, calls, actions = self._stage(True, report={
            "venues": {}, "actions": [], "would": [], "critical": [], "errors": [],
            "skipped": []})
        self.assertFalse(calls[0][1].get("dry_run"))

    def test_flag_off_is_a_strict_noop(self):
        out, calls, actions = self._stage(False)
        self.assertIsNone(out)
        self.assertEqual(calls, [], "默认关闭时不得触发任何巡检（零网络、零写单）")
        self.assertEqual(actions, [])

    def test_flag_on_records_actions_and_critical(self):
        report = {
            "venues": {"gate": {"checked": 1}},
            "actions": [{"venue": "gate", "inst": "BTC_USDT", "detail": "续期完成"}],
            "critical": [{"venue": "gate", "inst": "ETH_USDT", "side": "short",
                          "detail": "无止损腿"}],
            "errors": [], "skipped": [],
        }
        out, calls, actions = self._stage(True, report=report)
        self.assertIsNotNone(out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["environment"], "demo", "环境轴必须传给巡检")
        self.assertTrue(any("续期完成" in a for a in actions))
        self.assertTrue(any("无止损腿" in a for a in actions))

    def test_audit_exception_is_swallowed(self):
        """加固层不得成为新的单点：巡检炸了也不能中断交易周期。"""
        out, _, actions = self._stage(True, raises=RuntimeError("boom"))
        self.assertIsNone(out)
        self.assertEqual(actions, [])

    def test_facade_flag_defaults_to_off(self):
        """源码钉：开关未设置时必须视为关闭（开闸需显式置 1）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("R20_VENUE_PROTECTION_WATCHDOG", "0")', src,
                      "默认值必须显式为 0（否则巡检会在无人知情时开闸）")
        # 第一百二十九刀：预演标志同样必须默认关 —— 否则总闸一开就是真实写单，
        # 而"先预演一轮"这道过渡闸形同虚设。
        self.assertIn('os.environ.get("R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN", "0")', src,
                      "预演标志默认值必须显式为 0")
        # 第一百三十刀：防抖默认 30 分钟，且**门面必须真的把状态路径与判定函数传下去**
        # —— 漏传会静默退化成"不防抖直通写单"，这正是本刀要防的事。
        self.assertIn('os.environ.get("R20_VENUE_PROTECTION_WATCHDOG_DEBOUNCE_MIN", "30")', src,
                      "防抖窗口默认值必须显式（30 分钟）")
        self.assertIn("state_path=VENUE_PROTECTION_WATCHDOG_STATE_FILE", src,
                      "门面必须把防抖状态路径传进巡检格")
        self.assertIn("debounce_step=watchdog_debounce_step", src,
                      "门面必须把防抖判定函数传进巡检格")
        self.assertIn("debounce_s=R20_VENUE_PROTECTION_WATCHDOG_DEBOUNCE_S", src,
                      "门面必须把防抖窗口传进巡检格（漏传=None ⇒ 退化成不防抖）")


class DryRunTest(unittest.TestCase):
    """开闸前的只读预演：只判定、绝不动单（这是线上唯一安全的取证方式）。"""

    def _registry(self, adapters):
        reg = MagicMock()
        reg.get_adapter.side_effect = lambda v, environment=None: adapters[v]
        return reg

    def test_dry_run_reports_would_renew_without_writing(self):
        gate = MagicMock()
        gate.list_protective_orders.return_value = [
            gate_sl_row("old-sl", created=NOW - (604800 - 60), trigger="77000")]
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [{"inst_id": "BTC_USDT", "side": "long", "size_signed": 1.0}]},
            venue_registry=reg, environment="demo", now_s=NOW, dry_run=True)
        self.assertTrue(report["dry_run"])
        self.assertEqual([i["would"] for i in report["would"]], ["renew"])
        self.assertEqual(report["actions"], [])
        gate.attach_protective_orders.assert_not_called()
        gate.cancel_price_order.assert_not_called()

    def test_dry_run_flags_missing_leg_as_critical(self):
        gate = MagicMock()
        gate.list_protective_orders.return_value = []
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [{"inst_id": "BTC_USDT", "side": "long", "size_signed": 1.0}]},
            venue_registry=reg, environment="demo", now_s=NOW, dry_run=True)
        self.assertEqual([i["would"] for i in report["critical"]], ["repair"])
        gate.attach_protective_orders.assert_not_called()

    def test_dry_run_noop_when_healthy(self):
        gate = MagicMock()
        gate.list_protective_orders.return_value = [gate_sl_row()]
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [{"inst_id": "BTC_USDT", "side": "long", "size_signed": 10.0}]},
            venue_registry=reg, environment="demo", now_s=NOW, dry_run=True)
        self.assertEqual(report["would"], [])
        self.assertEqual(report["critical"], [])
        self.assertEqual(report["venues"]["gate"]["checked"], 1)

    def test_non_dry_run_actually_writes(self):
        """对照组：同一输入、dry_run=False 时必须真的动单（否则预演成了唯一行为）。"""
        gate = MagicMock()
        gate.list_protective_orders.return_value = [
            gate_sl_row("old-sl", created=NOW - (604800 - 60), trigger="77000")]
        gate.attach_protective_orders.return_value = {"sl": "new-sl"}
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [{"inst_id": "BTC_USDT", "side": "long", "size_signed": 1.0}]},
            venue_registry=reg, environment="demo", now_s=NOW, dry_run=False)
        self.assertEqual(len(report["actions"]), 1)
        gate.attach_protective_orders.assert_called_once()

    def test_dry_run_listing_failure_is_reported_not_faked(self):
        gate = MagicMock()
        gate.list_protective_orders.side_effect = RuntimeError("gate 502")
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [{"inst_id": "BTC_USDT", "side": "long", "size_signed": 1.0}]},
            venue_registry=reg, environment="demo", now_s=NOW, dry_run=True)
        self.assertEqual(report["errors"][0]["stage"], "list")
        self.assertEqual(report["critical"], [], "读不到不等于没有止损腿，不许当成 critical")


class PreflightEndpointTest(unittest.TestCase):
    """管理员预演端点：必须鉴权、且**只读**（不下单/不撤单）。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from fastapi.testclient import TestClient
        from tests.config_sandbox import isolate_config
        import r20_backend.app as app_module
        from r20_backend.admin_auth import AdminAuthStore

        isolate_config(self)
        self.temp = tempfile.TemporaryDirectory()
        self._orig = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        import r20_backend.app as app_module
        app_module.admin_auth = self._orig
        self.temp.cleanup()

    def _headers(self):
        r = self.client.post("/api/v1/admin/auth/login",
                             json={"username": "admin", "password": "InitialAdmin123456"})
        self.assertEqual(r.status_code, 200, r.text)
        return {"X-R20-Session": r.json()["session_token"]}

    def test_requires_admin(self):
        r = self.client.get("/api/v1/admin/venue-protection/scan")
        self.assertIn(r.status_code, (401, 403))

    def test_read_only_scan_never_writes(self):
        """把适配器全打桩：端点返回结构，且**一次写单调用都没有**。"""
        from unittest.mock import patch
        ad = MagicMock()
        ad.positions.return_value = [{"inst_id": "BTC_USDT", "base": "BTC", "side": "long",
                                      "size_signed": 1.0, "leverage": 5.0}]
        ad.list_protective_orders.return_value = [
            gate_sl_row("old-sl", created=NOW - (604800 - 60), trigger="77000")]
        with patch("r20_backend.exchanges.get_adapter", return_value=ad):
            r = self.client.get("/api/v1/admin/venue-protection/scan", headers=self._headers())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["dry_run"], "预演端点必须是 dry_run —— 否则面板点一下就会真下单")
        for key in ("would", "critical", "errors", "venues", "snapshot_errors"):
            self.assertIn(key, body)
        ad.attach_protective_orders.assert_not_called()
        ad.cancel_price_order.assert_not_called()
        ad.cancel_algo_order.assert_not_called()


class WatchdogDebouncePureTest(unittest.TestCase):
    """防抖的纯逻辑（第一百三十刀）：缺口必须**持续**够久才算"合格"。"""

    def _rep(self, detail="距到期 1.2 天", venue="gate", inst="BTC_USDT", stage="renew"):
        return {"would": [{"venue": venue, "inst": inst, "stage": stage,
                           "detail": detail}]}

    def test_key_ignores_volatile_detail(self):
        """键必须忽略 `detail`（含"距到期 X 天"这类每轮都变的数字）。"""
        a = watchdog_gap_key(self._rep("距到期 1.2 天")["would"][0])
        b = watchdog_gap_key(self._rep("距到期 0.4 天")["would"][0])
        self.assertEqual(a, b)
        self.assertEqual(a, "gate|BTC_USDT|renew")

    def test_gap_qualifies_only_after_the_window(self):
        st, obs, q = watchdog_debounce_step(None, self._rep(), now_s=1000.0, debounce_s=1800.0)
        self.assertEqual(q, [], "首次出现不得立即动手")
        self.assertEqual(st, {"gate|BTC_USDT|renew": 1000.0}, "首见时刻必须记住")
        st, _, q = watchdog_debounce_step(st, self._rep("措辞变了"), now_s=2000.0,
                                          debounce_s=1800.0)
        self.assertEqual(q, [], "未到窗口不得动手")
        self.assertEqual(st["gate|BTC_USDT|renew"], 1000.0, "首见时刻不得被后续周期刷新")
        st, _, q = watchdog_debounce_step(st, self._rep(), now_s=2800.0, debounce_s=1800.0)
        self.assertEqual(q, ["gate|BTC_USDT|renew"], "持续够窗口才合格")

    def test_healed_gap_is_pruned(self):
        st, _, _ = watchdog_debounce_step(None, self._rep(), now_s=1000.0, debounce_s=1800.0)
        st2, obs, q = watchdog_debounce_step(st, {"would": []}, now_s=1200.0, debounce_s=1800.0)
        self.assertEqual(st2, {}, "缺口愈合 ⇒ 状态自清（不攒垃圾、不残留陈旧首见时刻）")
        self.assertEqual(obs, [])
        self.assertEqual(q, [])

    def test_zero_window_means_no_debounce(self):
        """`debounce_s<=0` 是**显式**不防抖（运营选择），不是默认值。"""
        _, _, q = watchdog_debounce_step(None, self._rep(), now_s=1000.0, debounce_s=0.0)
        self.assertEqual(q, ["gate|BTC_USDT|renew"])


class WatchdogStageDebounceTest(unittest.TestCase):
    """巡检格的防抖接线：观察轮不写单；够窗口才真写；状态不可读写 ⇒ 不写单。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wd-deb-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = os.path.join(self.tmp, "wd_state.json")

    def _stage(self, *, now_s, dry_run=False, report_would=True, state_path=None,
               real_actions=True, raises=None):
        from scripts.trader.cycle_stages import venue_protection_watchdog_stage
        calls = []

        def fake_audit(snapshot, **kw):
            calls.append(bool(kw.get("dry_run")))
            if raises is not None:
                raise raises
            would = ([{"venue": "gate", "inst": "BTC_USDT", "stage": "renew",
                       "detail": "距到期 1.2 天"}] if report_would else [])
            real = [{"venue": "gate", "inst": "BTC_USDT", "detail": "续期完成"}] \
                if real_actions else []
            return {"venues": {}, "would": would,
                    "actions": (real if not kw.get("dry_run") else []),
                    "critical": [], "errors": [], "skipped": []}

        actions = []
        out = venue_protection_watchdog_stage(
            xv_positions_by_venue={"gate": [{"inst_id": "BTC_USDT"}]},
            executed_actions=actions,
            venue_registry=MagicMock(),
            current_environment=lambda: MagicMock(mode="demo"),
            R20_VENUE_PROTECTION_WATCHDOG=True,
            audit_cross_venue_protection=fake_audit,
            dry_run=dry_run,
            state_path=(self.state if state_path is None else state_path),
            debounce_s=1800.0,
            debounce_step=watchdog_debounce_step,
            now_s=now_s,
        )
        return out, calls, actions

    def test_first_sighting_observes_and_never_writes(self):
        out, calls, actions = self._stage(now_s=1000.0)
        self.assertEqual(calls, [True], "首见只允许观察轮（dry_run=True），绝不允许真写")
        self.assertTrue(os.path.exists(self.state), "必须落盘首见时刻")
        self.assertTrue(any("观察" in a for a in actions))
        self.assertFalse(any("续期完成" in a for a in actions))

    def test_qualified_gap_triggers_one_real_pass(self):
        self._stage(now_s=1000.0)
        out, calls, actions = self._stage(now_s=3000.0)   # +2000s ≥ 1800s
        self.assertEqual(calls, [True, False], "够窗口 ⇒ 观察轮后接一次真写轮")
        self.assertTrue(any("续期完成" in a for a in actions))

    def test_healed_gap_clears_state_and_never_writes(self):
        self._stage(now_s=1000.0)
        out, calls, actions = self._stage(now_s=3000.0, report_would=False)
        self.assertEqual(calls, [True], "缺口已愈合 ⇒ 不得写单")
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["gaps"], {}, "愈合后状态必须清空")

    def test_unreadable_state_fails_closed(self):
        """状态不可读 ⇒ **绝不允许真写轮**（观察轮是只读的，跑无妨）。

        契约是"不知道这缺口持续多久就不动手"，不是"连只读判定都不做"。
        """
        with open(self.state, "w", encoding="utf-8") as f:
            f.write("{ 这不是 JSON")
        out, calls, actions = self._stage(now_s=3000.0)
        self.assertNotIn(False, calls, "状态不可读 ⇒ 不得出现真写轮（fail-closed）")
        self.assertEqual(calls, [True], "只允许只读的观察轮")
        self.assertEqual([a for a in actions if "续期完成" in a], [],
                         "状态不可读时不得报出任何真实动作")

    def test_dry_run_previews_qualification_without_writing(self):
        self._stage(now_s=1000.0)                     # 观察：记住首见
        out, calls, actions = self._stage(now_s=3000.0, dry_run=True)
        self.assertEqual(calls, [True], "预演下绝不出现真写轮")
        self.assertTrue(any("预演" in a for a in actions))
        self.assertFalse(any("续期完成" in a for a in actions))


class WatchdogReportShapeContractTest(unittest.TestCase):
    """跨所保护**报告形状**契约（第一百四十刀，为 G8 开闸做准备）。

    背景：`venue_protection_watchdog_stage` 渲染 `report["actions"]` / `report["critical"]`
    时用的是**直接下标**（`item['venue']`/`['inst']`/`['detail']`/`['side']`），而这两个列表
    由 `venue_protection.py` 的巡检在**多条路径**上 append（扫描路径 / ensure 路径）。
    一旦某条路径的 item 少一个键，渲染处就 **KeyError** —— 该异常发生在**周期中途**
    （保护巡检之后还有落盘/台账/面板相位），且只在 watchdog **开闸后**才会走到
    （G8 正待开闸），属"开闸才炸"的隐患。

    本门从 AST 推导两边的形状并逐个 append 站点核对（含"`item = {...}` 后再 append"的
    数据流：取该 append **之前最近一次**赋值），判据随代码演进自动跟进。
    """

    _CONSUMER = ("scripts/trader/cycle_stages.py", "venue_protection_watchdog_stage")
    _PRODUCER = ("scripts/trader/venue_protection.py", "audit_cross_venue_protection")

    def _consumer_needs(self, key):
        import ast as _ast
        from pathlib import Path as _P
        rel, fn = self._CONSUMER
        src_text = (_P(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")
        tree = _ast.parse(src_text)
        node = next(n for n in _ast.walk(tree)
                    if isinstance(n, _ast.FunctionDef) and n.name == fn)
        for_loops = [n for n in _ast.walk(node)
                     if isinstance(n, _ast.For)
                     and isinstance(n.iter, _ast.BoolOp)
                     and any(isinstance(v, _ast.Call) and isinstance(v.func, _ast.Attribute)
                             and v.func.attr == "get"
                             and v.args and isinstance(v.args[0], _ast.Constant)
                             and v.args[0].value == key
                             for v in n.iter.values)]
        needs = set()
        for loop in for_loops:
            var = loop.target.id if isinstance(loop.target, _ast.Name) else None
            for n in _ast.walk(loop):
                if (isinstance(n, _ast.Subscript) and isinstance(n.ctx, _ast.Load)
                        and isinstance(n.value, _ast.Name) and n.value.id == var
                        and isinstance(n.slice, _ast.Constant)
                        and isinstance(n.slice.value, str)):
                    needs.add(n.slice.value)
            # ⚠️ 本仓是 Python 3.11：**f-string 内的表达式不是 AST 节点**（3.12 起才是），
            # 而这两处渲染正好写在 f-string 里 ⇒ 必须补一次源码文本扫描，否则
            # "啥都没抓到 ⇒ 空集恒过"（本门自检会翻红，此处即为该自检抓到的一次）。
            seg = _ast.get_source_segment(src_text, loop) or ""
            needs |= {m.group(1) for m in re.finditer(
                rf"\b{var}\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]", seg)}
        return needs

    def _producer_sites(self, key):
        import ast as _ast
        from pathlib import Path as _P
        rel, fn = self._PRODUCER
        tree = _ast.parse((_P(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8"))
        node = next(n for n in _ast.walk(tree)
                    if isinstance(n, _ast.FunctionDef) and n.name == fn)
        assigns = [(n.lineno, n.targets[0].id, {k.value for k in n.value.keys
                                                if isinstance(k, _ast.Constant)})
                   for n in _ast.walk(node)
                   if isinstance(n, _ast.Assign) and isinstance(n.value, _ast.Dict)
                   and len(n.targets) == 1 and isinstance(n.targets[0], _ast.Name)]
        out = []
        for call in _ast.walk(node):
            if not (isinstance(call, _ast.Call) and isinstance(call.func, _ast.Attribute)
                    and call.func.attr == "append"):
                continue
            sub = call.func.value
            if not (isinstance(sub, _ast.Subscript) and isinstance(sub.value, _ast.Name)
                    and sub.value.id == "report"
                    and isinstance(sub.slice, _ast.Constant) and sub.slice.value == key):
                continue
            arg = call.args[0] if call.args else None
            if isinstance(arg, _ast.Dict):
                keys = {k.value for k in arg.keys if isinstance(k, _ast.Constant)}
            elif isinstance(arg, _ast.Name):
                prior = [a for a in assigns if a[0] < call.lineno and a[1] == arg.id]
                # ⚠️ 必须按**行号**取最近一次赋值：ast.walk 是 BFS，顺序不等于行序
                # （同一函数里 `item` 被赋值两次：扫描路径 / ensure 路径）
                keys = max(prior, key=lambda a: a[0])[2] if prior else None
            else:
                keys = None
            out.append((call.lineno, keys))
        return out, {a[1] for a in assigns}

    def test_actions_and_critical_items_carry_every_key_the_renderer_derefs(self):
        for key, must_have in (("actions", {"venue", "inst", "detail"}),
                               ("critical", {"venue", "inst", "side"})):
            with self.subTest(report_key=key):
                needs = self._consumer_needs(key)
                self.assertTrue(must_have <= needs,
                                f"判据失效：没抓到 {key} 渲染处的下标（实际 {sorted(needs)}）")
                sites, _names = self._producer_sites(key)
                self.assertTrue(sites, f"判据失效：没找到 report['{key}'].append(...) 站点")
                for lineno, keys in sites:
                    self.assertIsNotNone(
                        keys, f"{self._PRODUCER[0]}:{lineno} 的 append 参数解析不出键集"
                              "（新增了别的形态？请扩展本门）")
                    self.assertEqual(
                        sorted(needs - keys), [],
                        f"{self._PRODUCER[0]}:{lineno} 这条路径的 item 缺 "
                        f"{sorted(needs - keys)} ⇒ 渲染处会在**周期中途** KeyError"
                        f"（且只在 watchdog 开闸后才会走到）")

if __name__ == "__main__":
    unittest.main()

class ScanDictShapeContractTest(unittest.TestCase):
    """`scan_protective_orders` 的返回形状 ⊇ 各消费点下标（第一百四十一刀）。

    同一"开闸才炸"类别：watchdog 巡检（G8 开闸后才走）对 `scan[...]` 是**直接下标**，
    生产侧就在同模块。缺一个键 ⇒ 跨所保护巡检**周期中途** KeyError
    ⇒ 其后的落盘/台账/面板相位全跳过（而它只是加固层，不该拖垮周期）。
    """

    _MOD = "scripts/trader/venue_protection.py"
    _CONSUMERS = ("ensure_venue_protection", "audit_cross_venue_protection")

    def test_scan_keys_cover_every_consumer(self):
        from tests import source_scan as ss
        provided = ss.dict_literal_keys(self._MOD, "scan_protective_orders")
        self.assertTrue({"has_live_sl", "needs_renew"} <= provided,
                        f"判据失效：生产侧键没抓到（实际 {sorted(provided)}）")
        for fn in self._CONSUMERS:
            with self.subTest(consumer=fn):
                needs = ss.load_subscripts(self._MOD, fn, "scan")
                self.assertTrue(needs, f"判据失效：{fn} 没抓到 scan[...] 下标")
                missing = sorted(needs - provided)
                self.assertEqual(missing, [],
                                 f"{fn} 读 scan[...] 的 {missing} 生产侧不提供 "
                                 "⇒ 跨所保护巡检会**周期中途** KeyError（后面相位全跳过）")

class CancelOrphanAttributedLegsTest(unittest.TestCase):
    """第一百七十刀：**有护栏的**孤儿腿撤销（G8 只报告不撤销，这一步是显式运营动作）。

    四条护栏各有一个用例；其中"归属不可判定绝不撤"是**安全底线**（可能是用户手单）。
    """

    def _ad(self, legs_by_symbol):
        calls = []

        class _Ad:
            def list_protective_orders(self, symbol):
                return [dict(r) for r in legs_by_symbol.get(symbol, [])]

            def cancel_price_order(self, order_id):
                calls.append(order_id)
                return {"id": order_id, "status": "finished"}

        ad = _Ad()
        ad.calls = calls
        return ad

    def _tagged_orphan(self):
        return {"id": "o-1", "initial": {"contract": "DOGE_USDT", "size": 0,
                                         "text": "t-r20tp261158", "is_close": True},
                "trigger": {"price": "0.0811"}}

    def test_tagged_orphan_is_cancelled_only_when_not_dry_run(self):
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs
        ad = self._ad({"DOGE_USDT": [self._tagged_orphan()]})
        dry = cancel_orphan_attributed_legs(ad, positions=[], symbols=["DOGE_USDT"], dry_run=True)
        self.assertEqual(dry["would_cancel"][0]["id"], "o-1")
        self.assertEqual(ad.calls, [], "dry-run 绝不能撤单")
        live = cancel_orphan_attributed_legs(ad, positions=[], symbols=["DOGE_USDT"], dry_run=False)
        self.assertEqual(live["cancelled"][0]["id"], "o-1")
        self.assertEqual(ad.calls, ["o-1"], "非 dry-run 必须**逐腿按 id** 撤")

    def test_unattributed_leg_is_never_cancelled(self):
        """安全底线：没有标签也没有台账证据 ⇒ 归属不可判定 ⇒ 绝不撤（可能是用户手单）。"""
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs
        leg = {"id": "u-1", "type": "STOP_MARKET", "symbol": "ETHUSDT",
               "raw": {"orderType": "STOP_MARKET", "triggerPrice": "2555", "quantity": "0.532"}}
        ad = self._ad({"ETH_USDT": [leg]})
        rep = cancel_orphan_attributed_legs(ad, positions=[], symbols=["ETH_USDT"], dry_run=False)
        self.assertEqual(ad.calls, [], "归属不可判定的腿被撤了 ⇒ 安全底线破了")
        self.assertEqual(rep["cancelled"], [])
        self.assertTrue(any(n["bucket"] == "orphan_unattributed" for n in rep["not_touched"]))

    def test_symbol_with_a_live_position_is_skipped_whole(self):
        """合约仍有活动持仓 ⇒ 整合约跳过（孤儿判定可能只是取数缺失，宁留腿不裸奔）。"""
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs
        ad = self._ad({"DOGE_USDT": [self._tagged_orphan()]})
        rep = cancel_orphan_attributed_legs(
            ad, positions=[{"base": "DOGE", "side": "long", "size_signed": 100}],
            symbols=["DOGE_USDT"], dry_run=False)
        self.assertEqual(ad.calls, [])
        self.assertTrue(rep["not_touched"][0]["why"].startswith("该合约仍有活动持仓"))

    def test_cancel_failure_is_recorded_not_raised(self):
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs

        class _Ad:
            def list_protective_orders(self, symbol):
                return [{"id": "o-2", "initial": {"contract": "DOGE_USDT", "size": 0,
                                                  "text": "t-r20sl1", "is_close": True}}]

            def cancel_price_order(self, order_id):
                raise RuntimeError("network down")

        rep = cancel_orphan_attributed_legs(_Ad(), positions=[], symbols=["DOGE_USDT"], dry_run=False)
        self.assertEqual(rep["cancelled"], [])
        self.assertIn("network down", rep["errors"][0]["detail"])

    def test_list_failure_is_recorded_per_symbol(self):
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs

        class _Ad:
            def list_protective_orders(self, symbol):
                raise RuntimeError("venue 502")

            def cancel_price_order(self, order_id):   # pragma: no cover - 不该被调用
                raise AssertionError("读腿失败时不得撤单")

        rep = cancel_orphan_attributed_legs(_Ad(), positions=[], symbols=["DOGE_USDT"], dry_run=False)
        self.assertEqual(rep["errors"][0]["stage"], "list")
        self.assertEqual(rep["cancelled"], [])

    def test_function_is_exported(self):
        import scripts.trader.venue_protection as vp
        self.assertIn("cancel_orphan_attributed_legs", vp.__all__)
