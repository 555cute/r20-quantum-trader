"""跨所保护单覆盖核验与临期续期（roadmap G8）契约测试。

全 mock、零网络、零写盘。守的是**三条安全铁律**（见 venue_protection 模块 docstring）：

1. **先挂新、后撤旧**：新腿挂失败时**旧腿必须还在**（绝不出现"撤了旧的、新的没挂上"）；
2. **宁可双、不可裸**：旧腿撤失败不回滚新腿，只登记；
3. **不可判定 ≠ 安全**：覆盖算不出来时给 None，人工腿/陌生腿**永不被撤**。
"""
from __future__ import annotations

import ast
import dis
import json
import os
import time
import shutil
import tempfile
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from scripts.trader import venue_protection
from scripts.trader.venue_protection import (
    DEFAULT_RENEW_WITHIN_S,
    attribute_protective_orders,
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

    def test_non_dict_snapshot_row_is_skipped(self):
        """快照里的非 dict 行跳过并**如实登记**（脏数据不得带偏巡检，也不能当成巡检过）。"""
        gate = MagicMock()
        gate.list_protective_orders.return_value = [gate_sl_row()]
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": ["垃圾", self._row()]}, venue_registry=reg, environment="demo", now_s=NOW)
        self.assertEqual(report["venues"]["gate"]["checked"], 1, "只应巡检那条真仓")
        self.assertTrue(report["skipped"], "跳过的行要登记（不装作巡检过）")

    def test_dry_run_reports_verify_for_unknown_expiry(self):
        """dry-run 是开闸前的**唯一安全取证**：到期不可判定 ⇒ `would=verify`（不写单）。"""
        gate = MagicMock()
        gate.list_protective_orders.return_value = [gate_sl_row(expiration=None)]
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [self._row()]}, venue_registry=reg, environment="demo", now_s=NOW,
            dry_run=True)
        self.assertEqual([w["would"] for w in report["would"]], ["verify"])
        self.assertEqual(report["actions"], [], "dry-run 绝不写单")
        gate.attach_protective_orders.assert_not_called()

    def test_ensure_exception_is_registered_and_does_not_stop_the_venue(self):
        """巡检**自身**异常不上抛（它只是加固层），逐所隔离、如实登记后继续。"""
        gate = MagicMock()
        gate.list_protective_orders.return_value = [gate_sl_row(created=NOW - (604800 - 60))]
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        # 让 ensure 本身抛（巡检层只该加固、不该被它带崩）
        from unittest.mock import patch as _patch
        with _patch("scripts.trader.venue_protection.ensure_venue_protection",
                    side_effect=RuntimeError("boom")):
            report = audit_cross_venue_protection(
                {"gate": [self._row()]}, venue_registry=reg, environment="demo", now_s=NOW)
        self.assertGreaterEqual(report["venues"]["gate"]["errors"], 1, "必须登记错误")
        stages = [e.get("stage") for e in report["errors"]]
        self.assertIn("ensure", stages, f"错误要标明阶段：{report['errors']}")

    def test_repair_is_counted_separately_from_renew(self):
        """补挂（repair）与续期（renew）**分开计数** —— 两者是不同动作，合并会说谎。"""
        gate = MagicMock()
        # 腿量不足（**非整仓平**且量小于仓位）⇒ needs_repair 而非 needs_renew
        # ⚠️ `gate_sl_row` 默认 `close=True`（整仓平腿）⇒ 那种腿天然"覆盖全部"，
        # 只能靠 `close=False` + 小 size 才构造出"覆盖不全"。
        gate.list_protective_orders.return_value = [gate_sl_row(size=1, close=False)]
        gate.attach_protective_orders.return_value = {"sl": "new-sl"}
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [self._row(size=10.0)]}, venue_registry=reg, environment="demo", now_s=NOW)
        self.assertEqual(report["venues"]["gate"]["repaired"], 1, f"{report['venues']['gate']}")
        self.assertEqual(report["venues"]["gate"]["renewed"], 0, "补挂不等于续期")

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

    def test_binance_style_adapter_cancels_via_its_own_capability(self):
        """第一百九十一刀：撤腿必须**按能力探针**（Binance 没有 `cancel_price_order`）。

        真机核对：`cancel_price_order` 只有 Gate 有、`cancel_algo_order` 只有 Binance 有。
        此前孤儿腿清理直接调 `cancel_price_order` ⇒ 对 **Binance 恒 AttributeError**
        ⇒ 腿留在场内（而 Binance 恰是孤儿腿最多的所，真机 17 条）。
        """
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs
        calls = []

        class _BinanceLike:
            def list_protective_orders(self, symbol):
                # 真机 Binance 腿**没有** `text` 字段（实测：raw 键里只有 clientAlgoId 等）；
                # 标签的真实落点就是 `raw.clientAlgoId`（写入侧打标签方案用的也是它）。
                return [{"id": "o-1", "symbol": "DOGEUSDT",
                         "raw": {"algoId": "o-1", "orderType": "TAKE_PROFIT_MARKET",
                                 "clientAlgoId": "t-r20tp261158", "quantity": "0"}}]

            def cancel_algo_order(self, *, algo_id=None):
                calls.append(algo_id)
                return {"algoId": algo_id}

        rep = cancel_orphan_attributed_legs(_BinanceLike(), positions=[],
                                            symbols=["DOGE_USDT"], dry_run=False)
        self.assertEqual(calls, ["o-1"], "Binance 只能走 cancel_algo_order；没撤 ⇒ 清理对该所是空转")
        self.assertEqual([c["id"] for c in rep["cancelled"]], ["o-1"])
        self.assertEqual(rep["errors"], [], "不该再报'没有该方法'的错")

    def test_adapter_with_no_cancel_capability_is_reported_not_silent(self):
        """三个撤腿方法都没有 ⇒ 必须**报错**（不许静默当作已撤）。"""
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs

        class _NoCapability:
            def list_protective_orders(self, symbol):
                return [{"id": "o-1", "symbol": "DOGEUSDT",
                         "raw": {"algoId": "o-1", "orderType": "TAKE_PROFIT_MARKET",
                                 "clientAlgoId": "t-r20tp261158", "quantity": "0"}}]

        rep = cancel_orphan_attributed_legs(_NoCapability(), positions=[],
                                            symbols=["DOGE_USDT"], dry_run=False)
        self.assertEqual(rep["cancelled"], [])
        self.assertTrue(rep["errors"], "撤不动却不报错 ⇒ 静默失败")

    def test_probe_prefers_the_first_available_capability(self):
        from scripts.trader.venue_protection import cancel_protective_leg
        seen = []

        class _Gate:
            def cancel_price_order(self, oid): seen.append(("price", oid)); return {}

        class _Binance:
            def cancel_algo_order(self, *, algo_id=None): seen.append(("algo", algo_id)); return {}

        class _Legacy:
            def cancel_order(self, symbol, oid): seen.append(("order", symbol, oid)); return {}

        class _Nothing:
            pass

        self.assertTrue(cancel_protective_leg(_Gate(), "g1"))
        self.assertTrue(cancel_protective_leg(_Binance(), "b1"))
        self.assertTrue(cancel_protective_leg(_Legacy(), "l1", symbol="BTC_USDT"))
        self.assertFalse(cancel_protective_leg(_Nothing(), "n1"))
        self.assertFalse(cancel_protective_leg(_Gate(), ""), "空 id 不得撤")
        self.assertEqual(seen, [("price", "g1"), ("algo", "b1"), ("order", "BTC_USDT", "l1")])

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

class TriggerPxTypePerVenueTest(unittest.TestCase):
    """第一百七十三刀：触发价类型取数点扩到三所 —— **原样透传，不猜映射**。"""

    def test_okx_words_are_passed_through(self):
        from scripts.trader.venue_protection import trigger_px_type
        self.assertEqual(trigger_px_type({"slTriggerPxType": "MARK"}), "mark")
        self.assertEqual(trigger_px_type({"raw": {"tpTriggerPxType": "index"}}), "index")

    def test_binance_working_type_is_passed_through_as_its_own_literal(self):
        from scripts.trader.venue_protection import trigger_px_type
        self.assertEqual(trigger_px_type({"raw": {"workingType": "CONTRACT_PRICE"}}), "contract_price")
        self.assertEqual(trigger_px_type({"workingType": "MARK_PRICE"}), "mark_price")

    def test_gate_numeric_code_is_disclosed_verbatim_not_translated(self):
        """Gate 的 `trigger.price_type` 是**数字码**：本仓未核实官方映射 ⇒ 原样带字段名。

        若哪天有人按记忆把它翻成 `mark`/`last`，本用例会翻红 —— 那正是"用没核实的东西
        当事实"的入口。
        """
        from scripts.trader.venue_protection import trigger_px_type
        self.assertEqual(trigger_px_type({"trigger": {"price_type": 0}}), "price_type:0")
        self.assertEqual(trigger_px_type({"trigger": {"price_type": 2}}), "price_type:2")
        self.assertNotIn("mark", str(trigger_px_type({"trigger": {"price_type": 1}})))

    def test_missing_type_is_none_not_a_guess(self):
        from scripts.trader.venue_protection import trigger_px_type
        self.assertIsNone(trigger_px_type({"raw": {"orderType": "STOP_MARKET"}}))
        self.assertIsNone(trigger_px_type({}))

    def test_normalized_leg_carries_the_type(self):
        """归一化腿必须带上类型（跨所路径的展示依赖它）。"""
        from scripts.trader.venue_protection import scan_protective_orders
        rows = [{"symbol": "DOGE_USDT", "type": "CONDITIONAL",
                 "initial": {"contract": "DOGE_USDT", "size": 0, "text": "t-r20sl1",
                             "is_close": True},
                 "trigger": {"price_type": 0, "price": "0.08"}}]
        v = scan_protective_orders(rows, symbol="DOGE", pos_side="long", position_size=1.0,
                                   now_s=1_700_000_000.0)
        self.assertTrue(v.get("ours"), "夹具没被判为本方腿")
        self.assertEqual(v["ours"][0].get("trigger_px_type"), "price_type:0")

class LedgerEvidenceTest(unittest.TestCase):
    """第一百七十四刀：台账取证 —— 只读、三态、**读不到就不产生证据**。"""

    def test_reader_three_states(self):
        import json, tempfile, pathlib as _p
        from scripts.trader.venue_protection import read_ledger_rows
        with tempfile.TemporaryDirectory() as d:
            missing = _p.Path(d) / "nope.json"
            self.assertIsNone(read_ledger_rows(missing), "文件不存在 ⇒ None（不是空台账）")
            bad = _p.Path(d) / "bad.json"
            bad.write_text("{oops", encoding="utf-8")
            warns = []
            self.assertIsNone(read_ledger_rows(bad, log=warns.append), "坏 JSON ⇒ None")
            self.assertTrue(warns, "读失败必须告警（不得静默当'没有记录'）")
            ok = _p.Path(d) / "ok.json"
            ok.write_text(json.dumps([{"inst": "XRP", "side": "空", "sz": 826.5}]), encoding="utf-8")
            rows = read_ledger_rows(ok)
            self.assertEqual(len(rows), 1)
            wrapped = _p.Path(d) / "wrapped.json"
            wrapped.write_text(json.dumps({"trades": [{"inst": "ARB"}]}), encoding="utf-8")
            self.assertEqual(len(read_ledger_rows(wrapped)), 1, "dict 包裹也要能读")
            notlist = _p.Path(d) / "notlist.json"
            notlist.write_text(json.dumps({"nope": 1}), encoding="utf-8")
            self.assertIsNone(read_ledger_rows(notlist, log=lambda *_: None))

    def test_ledger_evidence_promotes_an_unattributed_leg(self):
        """台账有同币同向同量已平记录 ⇒ 腿从"归属不可判定"升为"可归因孤儿"（证据 ledger）。"""
        from scripts.trader.venue_protection import attribute_protective_orders
        leg = {"id": "b-1", "symbol": "XRPUSDT", "type": "TAKE_PROFIT_MARKET",
               "raw": {"orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "1.3255",
                       "quantity": "826.5"}}
        without = attribute_protective_orders([], [leg], None)
        self.assertEqual(len(without["orphan_unattributed"]), 1, "无台账 ⇒ 归属不可判定")
        with_ledger = attribute_protective_orders(
            [], [leg], [{"inst": "XRP", "side": "空", "sz": 826.5, "status": "closed",
                         "id": "binance_closed_1"}])
        self.assertEqual(len(with_ledger["orphan_attributed"]), 1, "有台账 ⇒ 可归因")
        self.assertEqual(with_ledger["orphan_attributed"][0].get("evidence"), "ledger")

    def test_ledger_with_wrong_size_does_not_create_evidence(self):
        """量对不上 ⇒ **不**产生证据（宁可留在不可判定，也不许"看起来像"就当证据）。"""
        from scripts.trader.venue_protection import attribute_protective_orders
        leg = {"id": "b-2", "symbol": "XRPUSDT", "type": "TAKE_PROFIT_MARKET",
               "raw": {"orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "1.5",
                       "quantity": "826.5"}}
        rep = attribute_protective_orders([], [leg], [{"inst": "XRP", "side": "空", "sz": 100.0}])
        self.assertEqual(rep["orphan_attributed"], [])
        self.assertEqual(len(rep["orphan_unattributed"]), 1)

    def test_stage_forwards_ledger_rows_to_the_audit(self):
        """舞台必须把 `ledger_rows` 透传给审计层（漏传＝取证能力形同不存在）。"""
        from scripts.trader.cycle_stages import venue_protection_watchdog_stage
        seen = {}

        def _fake_audit(snapshot, **kw):
            seen.update(kw)
            return {"venues": {}, "would": [], "critical": [], "errors": [], "skipped": [],
                    "actions": [], "attribution": {}, "dry_run": kw.get("dry_run")}

        class _Env:
            mode = "demo"

        venue_protection_watchdog_stage(
            xv_positions_by_venue={"gate": [], "binance": []},
            executed_actions=[], venue_registry=object(),
            current_environment=lambda: _Env(),
            R20_VENUE_PROTECTION_WATCHDOG=True,
            audit_cross_venue_protection=_fake_audit,
            ledger_rows=[{"inst": "XRP", "side": "空", "sz": 826.5}],
        )
        self.assertIn("ledger_rows", seen, "舞台没把台账行透传给审计层")
        self.assertEqual(seen["ledger_rows"][0]["inst"], "XRP")

    def test_production_call_site_passes_ledger_rows(self):
        """活路径（ai_factor_trader）必须真的传台账行 —— 否则线上取证永远空转。"""
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2] / "scripts" / "ai_factor_trader.py").read_text(
            encoding="utf-8")
        call = src.split("_wd_report = venue_protection_watchdog_stage(")[1].split("\n    )")[0]
        self.assertIn("ledger_rows=read_ledger_rows(LEDGER_JSON_FILE)", call,
                      "活调用点未传台账行（或写法变了 ⇒ 请同步本门）")

class ExpiredLegIsNotCoverageTest(unittest.TestCase):
    """第一百七十八刀：**已过期 ≠ 覆盖**（真机反例：Gate 过期止损腿被算成"覆盖满量+有活止损"）。

    为什么这刀重要：修复前，一条已过期的止损腿会让 `has_live_sl=True`、`covered_size` 满量、
    `needs_repair=False` ⇒ `protected_now=True`，审计只报 "renew" 而**不进 critical**
    —— 一个**裸奔**的仓位被系统报成"已保护"。到期信息是**确知**的（不是不可判定）。
    """

    @staticmethod
    def _gate_sl(*, age_s, expiration_s=3600, is_close=True, size=None):
        now = time.time()
        row = {"id": "L1", "symbol": "BTC_USDT",
               "initial": {"contract": "BTC_USDT", "size": 0 if is_close else (size or 1),
                           "text": "t-r20sl1", "is_close": bool(is_close)},
               "trigger": {"price": "60000", "expiration": expiration_s},
               "create_time": (now - age_s) * 1000}
        return row, now

    def _scan(self, row, now, size=1.0):
        return scan_protective_orders([row], symbol="BTC", pos_side="long",
                                     position_size=size, now_s=now)

    def test_expired_leg_is_not_coverage_and_not_a_live_sl(self):
        row, now = self._gate_sl(age_s=7200)          # 2 小时前建、1 小时到期 ⇒ 已过期
        v = self._scan(row, now)
        self.assertEqual(v["has_live_sl"], False, "已过期腿被算成'有活止损'⇒ 假安心")
        self.assertEqual(v["covered_size"], 0.0, "已过期腿仍计入覆盖 ⇒ 假安心")
        self.assertFalse(v["coverage_ok"])
        self.assertTrue(v["needs_repair"], "没有活止损就必须需要修复")
        self.assertEqual(v["missing_size"], 1.0)

    def test_expiring_but_not_expired_still_covers(self):
        """反向：**临期但未过期**仍算覆盖（不许过度保守把好腿也算成没有）。"""
        row, now = self._gate_sl(age_s=3000)          # 还有 600s 到期
        v = self._scan(row, now)
        self.assertEqual(v["has_live_sl"], True)
        self.assertEqual(v["covered_size"], 1.0)
        self.assertTrue(v["coverage_ok"])
        self.assertTrue(v["needs_renew"], "临期仍要续期")

    def test_expired_partial_leg_does_not_add_to_covered(self):
        """**非**整仓平的过期腿（`is_close=False`、量可读）也不得计入覆盖。

        ⚠️ 这条是反向验证逼出来的：我第一版只用了 `is_close=True` 的夹具 ⇒ 它走的是
        `full_close_leg` 那条守卫，于是"`elif` 计入覆盖"这条守卫**反向验证时没翻红**
        （等于没测到）。两个守卫各需一个用例。
        """
        row, now = self._gate_sl(age_s=7200, is_close=False, size=1)
        v = self._scan(row, now, size=1.0)
        self.assertEqual(v["covered_size"], 0.0, "过期腿（非整仓平）仍计入覆盖 ⇒ 假安心")
        self.assertEqual(v["missing_size"], 1.0)
        self.assertFalse(v["coverage_ok"])

    def test_expired_full_close_leg_does_not_fake_full_coverage(self):
        """`is_close` 腿会走 `covered = max(covered, size)`；过期时**不得**再补满覆盖。"""
        row, now = self._gate_sl(age_s=7200, is_close=True)
        v = self._scan(row, now, size=5.0)
        self.assertEqual(v["covered_size"], 0.0)
        self.assertEqual(v["missing_size"], 5.0)

    def test_explicit_never_expires_is_live_without_verify_noise(self):
        """**显式**永不过期（`expiration=0` / GTC）算活，且**不**产生复验噪音。"""
        now = time.time()
        row = {"id": "L2", "symbol": "BTC_USDT",
               "initial": {"contract": "BTC_USDT", "size": 0, "text": "t-r20sl1", "is_close": True},
               "trigger": {"price": "60000", "expiration": 0}, "create_time": (now - 9999) * 1000}
        v = self._scan(row, now)
        self.assertEqual(v["has_live_sl"], True)
        self.assertEqual(v["covered_size"], 1.0)
        self.assertEqual(v["expiry_unknown"], [], "显式永不过期不是'不可判定'")
        self.assertFalse(v["needs_verify"], "明确形态不该每周期都要求复验（噪音会淹没真信号）")

    def test_unknown_expiry_counts_but_asks_for_verification(self):
        """口径明确：到期**不可判定** ⇒ 仍算覆盖（交易所列着它），但必须 `needs_verify`。

        这条是**取舍**：算死（不覆盖）会让每个缺字段的所每周期都"需要修复"；算活而不复验
        则是"不可判定当安全"。取"算活 + 要求复验"—— 既不过度报警，也不假装知道。
        """
        from scripts.trader.venue_protection import scan_protective_orders as _scan
        now = time.time()
        row = {"id": "L3", "symbol": "BTC_USDT",   # 完全没有到期字段 ⇒ exp_state = unknown
               "initial": {"contract": "BTC_USDT", "size": 0, "text": "t-r20sl1", "is_close": True},
               "trigger": {"price": "60000"}}
        v = _scan([row], symbol="BTC", pos_side="long", position_size=1.0, now_s=now)
        self.assertEqual(v["has_live_sl"], True, "不可判定到期**不**等于没有保护腿")
        self.assertEqual(len(v["expiry_unknown"]), 1)
        self.assertTrue(v["needs_verify"], "到期不可判定必须要求复验（不可判定≠安全）")

    def test_audit_calls_expired_only_protection_critical(self):
        """端到端：只有过期腿的仓位必须是 **critical/repair**（此前只说 renew，不进 critical）。"""
        row, now = self._gate_sl(age_s=7200)
        gate = MagicMock()
        gate.list_protective_orders.return_value = [row]
        reg = MagicMock()
        reg.get_adapter.side_effect = lambda v, environment=None: {"gate": gate}[v]
        rep = audit_cross_venue_protection(
            {"gate": [{"venue": "gate", "inst_id": "BTC_USDT", "base": "BTC", "side": "long",
                       "size_signed": 1.0}]},
            venue_registry=reg, environment="demo", now_s=now, dry_run=True)
        crit = rep["critical"]
        self.assertEqual(len(crit), 1, "裸奔仓位必须进 critical")
        self.assertEqual(crit[0]["would"], "repair")
        self.assertEqual(crit[0]["expired"], ["L1"])

class LegDirectionIsCoverageTest(unittest.TestCase):
    """第一百七十九刀：方向判据必须用**唯一来源** `_leg_position_side`。

    真机反例：Gate 的 6 条腿**一条都读不出 `side`**（旧过滤只看 `side`/`order_side`）
    ⇒ "方向过滤"对 Gate **完全失效**：一条平**空**腿会被算进**多**仓的覆盖。
    （本仓 attribution 一直读得到 Gate 方向，所以它早就报得出 side_mismatch —— 只有覆盖这条链在瞎。）
    """

    @staticmethod
    def _gate_leg(auto="close_long", *, tag="t-r20sl1", expired=False, size=0):
        now = time.time()
        return {"symbol": "BTC_USDT", "direction": "short",
                "initial": {"contract": "BTC_USDT", "size": size, "text": tag, "is_close": False,
                            "is_reduce_only": True, "auto_size": auto},
                "trigger": {"price": "60000", "expiration": 0},
                "create_time": (now - 7200 if expired else now - 60) * 1000}

    def _scan(self, rows, pos_side="long", size=1.0):
        return scan_protective_orders(rows, symbol="BTC", pos_side=pos_side,
                                     position_size=size, now_s=time.time())

    def test_same_direction_leg_covers(self):
        v = self._scan([self._gate_leg("close_long")])
        self.assertTrue(v["coverage_ok"])
        self.assertEqual(v["covered_size"], 1.0)
        self.assertTrue(v["has_live_sl"])

    def test_opposite_direction_leg_does_not_cover(self):
        """**本刀的核心**：保护空仓的腿不得算进多仓的覆盖（修复前 Gate 上必然发生）。"""
        v = self._scan([self._gate_leg("close_long")], pos_side="short")
        self.assertEqual(v["covered_size"], 0.0, "反向腿被算进覆盖 ⇒ 假安心")
        self.assertFalse(v["has_live_sl"])
        self.assertTrue(v["needs_repair"])
        v2 = self._scan([self._gate_leg("close_short")], pos_side="long")
        self.assertEqual(v2["covered_size"], 0.0)
        self.assertFalse(v2["coverage_ok"])

    def test_binance_close_side_still_works(self):
        """Binance 走 `side`（平仓方向）：sell 平多 ⇒ 覆盖多仓；buy 平空 ⇒ 不覆盖多仓。"""
        now = time.time()
        def bn(side):
            return {"symbol": "BTCUSDT", "side": side, "type": "STOP_MARKET",
                    "raw": {"orderType": "STOP_MARKET", "quantity": "1", "reduceOnly": "true"}}
        self.assertEqual(self._scan([bn("sell")], pos_side="long")["covered_size"], 1.0)
        self.assertEqual(self._scan([bn("buy")], pos_side="long")["covered_size"], 0.0)

    def test_undecidable_direction_keeps_the_historic_permissive_stance(self):
        """方向读不出 ⇒ **照旧计入**覆盖（本仓一直以来的口径，6 个既有用例钉着它）。

        ⚠️ 这是**有意的取舍**，也是一个已登记的残留口子：不报方向的所会留着
        "反向腿被算成覆盖"的风险。今天不可达（真机 Gate 6/6、Binance 18/18 都读得出方向）；
        将来若接入不报方向的所，必须改成 `coverage_unknown`。本用例把"当前口径"写死，
        免得有人以为它是无意的。
        """
        now = time.time()
        row = {"symbol": "BTC_USDT",
               "initial": {"contract": "BTC_USDT", "size": 1, "text": "t-r20sl1", "is_close": False},
               "trigger": {"price": "60000", "expiration": 0}, "create_time": (now - 60) * 1000}
        v = self._scan([row])
        self.assertEqual(v["covered_size"], 1.0, "方向不可判定时**仍计入**（既有口径）")
        # 方向判据本身必须来自唯一来源，不能是第二次拼写
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2] / "scripts" / "trader"
               / "venue_protection.py").read_text(encoding="utf-8")
        fn = src[src.index("def scan_protective_orders("):]
        fn = fn[:fn.index("\ndef ")]
        self.assertIn("_leg_position_side(row)", fn)

    def test_side_filter_uses_the_single_source_helper(self):
        """源码级：覆盖链必须用 `_leg_position_side`（不是第二次拼写 `_row_close_side`）。"""
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2] / "scripts" / "trader"
               / "venue_protection.py").read_text(encoding="utf-8")
        fn = src[src.index("def scan_protective_orders("):]
        fn = fn[:fn.index("\ndef ")]
        self.assertIn("_leg_position_side(row)", fn)
        self.assertNotIn("side != want_close_side", fn,
                         "旧的不全方向判据又回来了（Gate 上会静默失效）")

    def test_live_gate_legs_resolve_their_direction(self):
        """真机轻量回归：Gate 腿的方向必须读得出（今天 100% 可读；读不出会让覆盖整体变不可判定）。"""
        from scripts.trader.venue_protection import _leg_position_side
        leg = {"initial": {"contract": "BTC_USDT", "auto_size": "close_long", "text": "t-r20sl1"}}
        self.assertEqual(_leg_position_side(leg), "long")
        leg2 = {"initial": {"contract": "BTC_USDT", "auto_size": "close_short"}}
        self.assertEqual(_leg_position_side(leg2), "short")

class ContractMatchRobustnessTest(unittest.TestCase):
    """第一百八十刀：合约匹配的**双保险**（防"别的币的腿算进本仓覆盖"）。

    背景：`scan_protective_orders` 的 `symbol` 默认**不参与过滤**（给"已按合约取腿"的调用方用）。
    一旦调用方是"一次拉全量"，忘了开 `require_symbol_match` 就会串币 —— 本仓真机出过
    （面板侧注释记着"UNI 空仓一度算到 11 张腿，其中大部分是 ETH/SOL/XRP 的"）。
    本刀把审计/ensure 两处也开上该开关，并先修好**合成 id 的币种解析**，否则一开就会
    全丢（`GATE:BTC_USDT` → `GATE:BTC` ⇒ 假缺口 ⇒ 线上重复挂腿）。
    """

    @staticmethod
    def _leg(contract, *, auto="close_long", size=0, tag="t-r20sl1"):
        now = time.time()
        return {"symbol": contract, "direction": "short",
                "initial": {"contract": contract, "size": size, "text": tag, "is_close": False,
                            "is_reduce_only": True, "auto_size": auto},
                "trigger": {"price": "60000", "expiration": 0},
                "create_time": (now - 60) * 1000}

    def test_base_of_strips_venue_prefix_and_normalizes_spelling(self):
        from scripts.trader.venue_protection import _base_of
        self.assertEqual(_base_of("GATE:BTC_USDT"), "BTC")
        # 第一百八十五刀：本函数改为**委派**给 `canonical_base`（唯一实现），于是
        # `BINANCE:XRPUSDT` 现在也剥掉计价币 → `XRP`（此前是 `XRPUSDT`，那是第二份拼写）
        self.assertEqual(_base_of("BINANCE:XRPUSDT"), "XRP")
        self.assertEqual(_base_of("BTCUSDT"), "BTC")
        self.assertEqual(_base_of("BTC_USDT"), "BTC")
        self.assertEqual(_base_of("ETH-USDT-SWAP"), "ETH")
        self.assertEqual(_base_of(""), "")

    def test_composite_id_still_matches_its_own_legs(self):
        """回归：合成 id + 开启合约匹配**不得**把本仓自己的腿全丢掉（那会报假缺口）。"""
        now = time.time()
        for sym in ("GATE:BTC_USDT", "BINANCE:BTCUSDT", "GATE:BTC_USDT"):
            with self.subTest(sym=sym):
                v = scan_protective_orders([self._leg("BTC_USDT")], symbol=sym, pos_side="long",
                                          position_size=1.0, now_s=now, require_symbol_match=True)
                self.assertEqual(v["covered_size"], 1.0, f"{sym} 匹配不到自己的腿 ⇒ 假缺口")
                self.assertTrue(v["coverage_ok"])

    def test_other_symbol_is_not_counted(self):
        now = time.time()
        v = scan_protective_orders([self._leg("ETH_USDT")], symbol="GATE:BTC_USDT", pos_side="long",
                                   position_size=1.0, now_s=now, require_symbol_match=True)
        self.assertEqual(v["covered_size"], 0.0, "别的币的腿被算进本仓覆盖 ⇒ 假安心")
        self.assertEqual(v["ours"], [])

    def test_wrapped_coin_does_not_match_the_underlying(self):
        """前缀相等（而非原始子串）：`WBTCUSDT` 不得被当成 `BTC` 的腿。"""
        now = time.time()
        v = scan_protective_orders([self._leg("WBTC_USDT")], symbol="GATE:BTC_USDT",
                                   pos_side="long", position_size=1.0, now_s=now,
                                   require_symbol_match=True)
        self.assertEqual(v["covered_size"], 0.0)

    def test_the_scan_default_still_ignores_symbol(self):
        """默认口径不变（既有调用方依赖它），只有显式开启才过滤。"""
        now = time.time()
        v = scan_protective_orders([self._leg("ETH_USDT")], symbol="BTC", pos_side="long",
                                   position_size=1.0, now_s=now)
        self.assertEqual(v["covered_size"], 1.0)

    def test_audit_is_robust_to_an_adapter_that_ignores_the_symbol_argument(self):
        """**本刀重点**：适配器忽略 `symbol`（返回全量腿）时，审计也不得把别币的腿算进覆盖。"""
        now = time.time()
        # ⚠️ 夹具必须避开 `auto_size`/`is_close`：那表示"**平掉全部**仓位"（`_is_full_close`），
        # 覆盖会正确地补满（我第一版用了 `auto_size=close_long` ⇒ 审计判"已保护"是对的，
        # 是我的夹具错了）。这里要的是**部分量**腿 ⇒ 只用 `direction` 表方向。
        def _partial(contract, size, tag):
            return {"symbol": contract,
                    "initial": {"contract": contract, "size": size, "text": tag,
                                "is_close": False, "direction": "short"},
                    "trigger": {"price": "60000", "expiration": 0},
                    "create_time": (now - 60) * 1000}
        partial = _partial("BTC_USDT", 4, "t-r20sl1")
        eth_big = _partial("ETH_USDT", 90, "t-r20sl2")
        gate = MagicMock()                      # 故意**无视** symbol 参数，返回全量腿
        gate.list_protective_orders.return_value = [partial, eth_big]
        reg = MagicMock()
        reg.get_adapter.side_effect = lambda v, environment=None: {"gate": gate}[v]
        rep = audit_cross_venue_protection(
            {"gate": [{"venue": "gate", "inst_id": "GATE:BTC_USDT", "base": "BTC",
                       "side": "long", "size_signed": 10.0}]},
            venue_registry=reg, environment="demo", now_s=now, dry_run=True)
        self.assertEqual(rep["critical"], [],
                         "critical 的语义是'**完全没有**止损腿'；这里有活止损，不该进 critical")
        items = rep["would"] or []
        self.assertTrue(items, "覆盖不足必须被**看见**（此前落成 noop ⇒ 不在 critical/would 里，静默）")
        item = items[0]
        self.assertEqual(item["covered_size"], 4.0,
                         "ETH 的 90 张腿被算进 BTC 仓位 ⇒ 串币假安心（本刀要防的正是它）")
        self.assertFalse(item["coverage_ok"])
        self.assertEqual(item["would"], "repair")
        self.assertIn("覆盖不足", item.get("why") or "", "要让运营一眼看出是'量不够'而不是'没有腿'")

    def test_both_writer_sites_pass_the_symbol_flag(self):
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2] / "scripts" / "trader"
               / "venue_protection.py").read_text(encoding="utf-8")
        # 注意：docstring 里也提到该开关 ⇒ 只数**调用实参**那种写法（带右括号）
        self.assertEqual(src.count("require_symbol_match=True)"), 2,
                         "ensure 与 audit 两个站点都应显式开启合约匹配")

class AmbiguousPositionsTest(unittest.TestCase):
    """第一百八十三刀：同币多仓（对冲/异常数据）必须**披露**，不能静默只用一个。"""

    def test_same_base_multiple_positions_are_disclosed(self):
        rep = attribute_protective_orders(
            [{"base": "BTC", "side": "long", "size_signed": 1.0},
             {"base": "BTC", "side": "short", "size_signed": 1.0}], [], None)
        self.assertEqual(rep["ambiguous_positions"], ["BTC"],
                         "同币两仓只在报告里给一个仓位 ⇒ 另一侧静默消失")
        self.assertIn("ambiguous_positions", rep)

    def test_single_position_is_not_flagged(self):
        rep = attribute_protective_orders([{"base": "BTC", "side": "long", "size_signed": 1.0}],
                                          [], None)
        self.assertEqual(rep["ambiguous_positions"], [])

    def test_audit_report_carries_it_through(self):
        """端到端：审计的 attribution 段也要带上该字段（运营看的正是这里）。"""
        import time
        now = time.time()
        gate = MagicMock()
        gate.list_protective_orders.return_value = []
        reg = MagicMock()
        reg.get_adapter.side_effect = lambda v, environment=None: {"gate": gate}[v]
        rows = [{"venue": "gate", "inst_id": "BTC_USDT", "base": "BTC", "side": "long",
                 "size_signed": 1.0},
                {"venue": "gate", "inst_id": "BTC_USDT", "base": "BTC", "side": "short",
                 "size_signed": 1.0}]
        rep = audit_cross_venue_protection({"gate": rows}, venue_registry=reg,
                                          environment="demo", now_s=now, dry_run=True)
        self.assertEqual((rep.get("attribution") or {}).get("gate", {}).get("ambiguous_positions"),
                         ["BTC"])


class CancelOrphanSafetyTest(unittest.TestCase):
    """孤儿腿撤销的**护栏分支**（第二百零七刀：由轻量覆盖率探针发现这些分支从未被执行）。

    被覆盖的三条都不是"顺路"，而是**安全契约**：

    1. 该合约**仍有活动持仓** ⇒ 整合约跳过（宁可留腿，不可裸奔）；
    2. 腿**没有 id** ⇒ 跳过（绝不退化成"按合约全撤"——那会撤掉别人的腿）；
    3. 以 **tag 证据**归属的腿 ⇒ 才允许逐腿撤（`reason="tag"`）。
    """

    def setUp(self):
        from scripts.trader.venue_protection import cancel_orphan_attributed_legs
        self.cancel_orphans = cancel_orphan_attributed_legs

    def _ad(self, rows, *, cancel_raises=None, list_raises=None):
        ad = MagicMock()
        if list_raises is not None:
            ad.list_protective_orders.side_effect = list_raises
        else:
            ad.list_protective_orders.return_value = rows
        ad.cancel_price_order.side_effect = cancel_raises
        return ad

    def test_live_position_blocks_the_whole_contract(self):
        ad = self._ad([gate_sl_row("sl-mine")])
        # positions 里混入**非 dict** 行（真机上可能出现 None/字符串）⇒ 必须被安全跳过，
        # 不能让整个撤销流程炸掉（这条分支由覆盖率探针发现从未被执行）
        rep = self.cancel_orphans(
            ad, positions=[None, "junk", {"symbol": "BTC_USDT", "size_signed": 5.0}],
            symbols=["BTC_USDT"], dry_run=False)
        self.assertEqual(rep["cancelled"], [])
        self.assertEqual(rep["would_cancel"], [])
        self.assertTrue(any(x["symbol"] == "BTC" for x in rep["not_touched"]),
                        f"仍有活动持仓的合约必须整条跳过：{rep}")
        ad.cancel_price_order.assert_not_called()
        # 更严的一条：既然整条跳过，就**不该去读腿**（少一次对外调用）
        ad.list_protective_orders.assert_not_called()

    def test_leg_without_id_is_skipped_not_cancelled_by_symbol(self):
        row = gate_sl_row("sl-mine")
        row["id"] = ""                       # 真机上可能缺 id
        ad = self._ad([row])
        rep = self.cancel_orphans(
            ad, positions=[], symbols=["BTC_USDT"], dry_run=False)
        self.assertEqual(rep["cancelled"], [])
        self.assertTrue(any("没有 id" in x.get("why", "") for x in rep["skipped"]),
                        f"缺 id 的腿必须登记为跳过而不是按合约全撤：{rep}")
        ad.cancel_price_order.assert_not_called()

    def test_unresolvable_symbol_is_skipped(self):
        ad = self._ad([])
        rep = self.cancel_orphans(ad, positions=[], symbols=["___"], dry_run=False)
        self.assertEqual(rep["cancelled"], [])
        ad.cancel_price_order.assert_not_called()

    def test_list_failure_is_reported_not_fatal(self):
        ad = self._ad([], list_raises=RuntimeError("list boom"))
        rep = self.cancel_orphans(
            ad, positions=[], symbols=["BTC_USDT"], dry_run=False)
        self.assertEqual(rep["cancelled"], [])
        self.assertTrue(any(x.get("stage") == "list" for x in rep["errors"]),
                        f"读腿失败必须进 errors（不许静默当成没有腿）：{rep}")

    def test_tag_evidenced_leg_is_selected_for_cancel(self):
        """tag 分支：**平仓前基名对不上/无持仓**时，带量非 close 的 tag 腿才是"可撤的孤儿"。

        ⚠️ 本条提示了一个真实边界（写测试时才发现）：当 `own_position` 里**有**该基名
        （哪怕 size=0），带量腿会被判 `size_mismatch` ⇒ **保守不撤**（交归属审计）；
        `close=True` 的腿则一律 `matched`。⇒ 只有"基名不在持仓映射里"时才走 tag 孤儿分支。
        这在生产里由 `_cancel_proven_own_legs` 的 `leg_base(l) == canonical_inst(base)` 过滤
        与 `before_position` 共同保证 ⇒ 该分支是**防御性**的。
        """
        from scripts.trader.venue_protection import (attribute_protective_orders,
                                                     select_legs_to_cancel_after_close)
        legs = [gate_sl_row("sl-mine", close=False, size=5)]   # text = t-r20sl… ⇒ tag 证据

        # ① 归因层（真实流程）：无任何持仓 ⇒ 孤儿 + tag 证据
        att = attribute_protective_orders([], legs, None)
        tagged = [x for x in att.get("orphan_attributed", []) if x.get("evidence") == "tag"]
        self.assertTrue(tagged, f"无持仓时 tag 腿必须归成「可撤孤儿」：{att.get('counts')}")
        self.assertEqual(att["counts"]["orphan_attributed"], 1)

        # ② 撤销层（真实流程）：dry_run 下它进 would_cancel 且**不发撤单**
        ad = self._ad(legs)
        rep = self.cancel_orphans(ad, positions=[], symbols=["BTC_USDT"], dry_run=True)
        self.assertEqual([x["id"] for x in rep["would_cancel"]], ["sl-mine"])
        ad.cancel_price_order.assert_not_called()

        # ③ 选择器层（防御性分支）：基名对不上时也按 tag 撤
        sel = select_legs_to_cancel_after_close({"base": "ETH", "side": "long"}, legs, None)
        self.assertTrue(any(x.get("reason") == "tag" and x.get("id") == "sl-mine"
                            for x in sel["to_cancel"]),
                        f"tag 证据的腿必须可撤（这是唯一「敢撤自己腿」的依据）：{sel}")

class EnsureStaleCancelEdgeTest(unittest.TestCase):
    """续期的第②步「再撤旧」的两条边界。"""

    def test_new_leg_reusing_old_id_is_not_cancelled(self):
        """新腿**复用了旧腿的 id** ⇒ 绝不能把它当旧腿撤掉（否则刚挂上就被自己撤了）。"""
        ad = MagicMock()
        ad.list_protective_orders.return_value = [gate_sl_row("old-sl", created=NOW - (604800 - 60))]
        ad.attach_protective_orders.return_value = {"sl": "old-sl"}
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertNotIn("old-sl", res.get("cancelled") or [],
                         "新腿 id == 旧腿 id ⇒ 必须跳过（否则撤掉刚挂的保护腿）")
        ad.cancel_price_order.assert_not_called()

    def test_adapter_without_cancel_capability_registers_error_not_silence(self):
        """撤旧用能力探针；适配器**三样撤腿方法都没有** ⇒ 诚实登记错误，不静默当成功。"""
        ad = SimpleNamespace()
        ad.list_protective_orders = lambda symbol: [gate_sl_row("old-sl", created=NOW - (604800 - 60))]
        ad.attach_protective_orders = lambda *a, **k: {"sl": "new-sl"}
        # 既无 cancel_price_order / cancel_algo_order / cancel_order
        res = ensure_venue_protection(ad, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, tp_px=85000, sl_px=78000, now_s=NOW)
        self.assertEqual(res.get("cancelled") or [], [], "撤不掉就不能报成已撤")
        # 撤旧失败落在 kept_old（宁可双、不可裸：不回滚新腿），并**如实写进 detail**
        self.assertEqual(res.get("kept_old"), ["old-sl"])
        self.assertIn("未撤", res.get("detail") or "", f"必须如实披露：{res.get('detail')}")


class RowShapeRobustnessTest(unittest.TestCase):
    """★ 交易所回包形状**不保证** —— 非 dict / 非 live 的行必须被**跳过**而不是打挂巡检。

    这两态在 `scan_protective_orders` 里走的是同一行 `continue`（第 450 行）：
    `if not isinstance(row, dict) or not _is_live(row)`。判据是"**既不计入 foreign、
    也不崩**" —— 因为 `continue` 发生在 `_leg_kind` 之前，所以陌生腿的计数器不会被
    这两种噪音污染。下面用"陌生 dict 行 ⇒ foreign=1"作为**对照**，把这一点钉实。
    """

    def _scan(self, rows):
        return scan_protective_orders(rows, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)

    def test_non_dict_rows_are_dropped_before_classification(self):
        scan = self._scan(["junk", None, 42, [], ("a",), 3.14, True])
        self.assertEqual(scan["foreign_count"], 0,
                         "非 dict 行必须在 _leg_kind **之前**就被丢弃，不许计成陌生腿")
        self.assertEqual(scan["ours"], [])
        self.assertTrue(scan["needs_repair"], "没有任何我方止损腿 ⇒ 仍要报缺口")

    def test_dict_row_that_is_not_live_is_dropped(self):
        for status in ("canceled", "cancelled", "filled", "expired", "closed"):
            with self.subTest(status=status):
                row = gate_sl_row("sl-dead", size=10)
                row["status"] = status
                scan = self._scan([row])
                self.assertEqual(scan["foreign_count"], 0, status)
                self.assertFalse(scan["has_live_sl"], status)

    def test_live_status_synonyms_are_all_accepted(self):
        for status in ("live", "effective", "open", "new", "active"):
            with self.subTest(status=status):
                row = gate_sl_row("sl-live", size=10)
                row["status"] = status
                scan = self._scan([row])
                self.assertTrue(scan["has_live_sl"], status)

    def test_unknown_leg_is_counted_as_foreign_for_contrast(self):
        # 对照：**是** dict 且 live，但认不出是我方腿 ⇒ 才计入 foreign
        manual = {"id": "m1", "status": "open", "contract": "BTC_USDT",
                  "initial": {"contract": "BTC_USDT", "size": 10, "close": False,
                              "text": "manual"},
                  "trigger": {"price": "90000", "expiration": 604800},
                  "create_time": NOW - 10}
        scan = self._scan([manual])
        self.assertEqual(scan["foreign_count"], 1)

    def test_mixed_batch_keeps_only_the_classifiable_rows(self):
        rows = ["junk", None, gate_sl_row("sl-ok", size=10),
                {"id": "dead", "status": "canceled"}, {"id": "m", "status": "open"}]
        scan = self._scan(rows)
        self.assertTrue(scan["has_live_sl"])
        self.assertEqual([o["id"] for o in scan["ours"]], ["sl-ok"])
        self.assertEqual(scan["foreign_count"], 1)   # 只有那个可判定的陌生 dict

    def test_empty_and_none_row_lists_are_safe(self):
        for rows in (None, []):
            with self.subTest(rows=rows):
                self.assertTrue(self._scan(rows)["needs_repair"])


class WatchdogDebounceStepTest(unittest.TestCase):
    """★ 防抖的**纯函数**一步：输入容忍度 + 缺口身份稳定性。"""

    def test_non_dict_items_are_skipped_not_crashed(self):
        # 第 1197 行：`report["would"]` 里混进非 dict ⇒ 跳过而不是抛异常
        new_state, observed, qualified = watchdog_debounce_step(
            None, {"would": ["junk", None, 42, [], ("a",)]}, now_s=100.0, debounce_s=0.0)
        self.assertEqual((new_state, observed, qualified), ({}, [], []))

    def test_missing_or_none_would_list_is_safe(self):
        for report in (None, {}, {"would": None}, {"would": []}):
            with self.subTest(report=report):
                self.assertEqual(watchdog_debounce_step(None, report, now_s=1.0,
                                                        debounce_s=0.0), ({}, [], []))

    def test_duplicate_gap_in_one_report_is_counted_once(self):
        # 第 1200 行：同一份报告里重复出现同一缺口 ⇒ observed 只登记一次
        item = {"venue": "okx", "inst": "BTC-USDT-SWAP", "stage": "sl_missing"}
        new_state, observed, qualified = watchdog_debounce_step(
            None, {"would": [item, dict(item), dict(item)]}, now_s=100.0, debounce_s=0.0)
        self.assertEqual(observed, ["okx|BTC-USDT-SWAP|sl_missing"])
        self.assertEqual(len(observed), 1)
        self.assertEqual(new_state, {"okx|BTC-USDT-SWAP|sl_missing": 100.0})
        self.assertEqual(qualified, observed)

    def test_detail_wording_does_not_change_the_gap_identity(self):
        # ★ 模块 docstring 的显式警告：`detail` 含"距到期 1.2 天"这类**每周期都会变**的
        #   措辞，绝不能进键 —— 否则同一缺口每周期都算"新缺口"，防抖永不成立（等于没防抖）
        base = {"venue": "okx", "inst": "BTC-USDT-SWAP", "stage": "sl_missing"}
        first = dict(base, detail="距到期 1.2 天")
        second = dict(base, detail="距到期 0.9 天")
        self.assertEqual(watchdog_gap_key(first), watchdog_gap_key(second))
        _, observed, _ = watchdog_debounce_step(
            None, {"would": [first, second]}, now_s=100.0, debounce_s=0.0)
        self.assertEqual(len(observed), 1, "措辞变化不许产生第二个缺口身份")

    def test_gap_key_is_lowercased_venue_and_stable_across_calls(self):
        self.assertEqual(watchdog_gap_key({"venue": "OKX", "inst": "BTC", "stage": "S"}),
                         "okx|BTC|S")
        self.assertEqual(watchdog_gap_key({}), "||")

    def test_first_seen_timestamp_is_preserved_across_cycles(self):
        item = {"venue": "okx", "inst": "BTC", "stage": "sl_missing"}
        state, _, _ = watchdog_debounce_step(None, {"would": [item]}, now_s=100.0,
                                             debounce_s=0.0)
        state2, _, _ = watchdog_debounce_step(state, {"would": [item]}, now_s=999.0,
                                              debounce_s=0.0)
        self.assertEqual(state2["okx|BTC|sl_missing"], 100.0, "首见时间不许被后续周期改写")

    def test_healed_gap_is_dropped_from_state(self):
        item = {"venue": "okx", "inst": "BTC", "stage": "sl_missing"}
        state, _, _ = watchdog_debounce_step(None, {"would": [item]}, now_s=100.0,
                                             debounce_s=0.0)
        state2, observed, _ = watchdog_debounce_step(state, {"would": []}, now_s=200.0,
                                                     debounce_s=0.0)
        self.assertEqual(state2, {}, "缺口愈合 ⇒ 状态自清，不攒垃圾")
        self.assertEqual(observed, [])

    def test_debounce_gates_qualification_but_not_observation(self):
        item = {"venue": "okx", "inst": "BTC", "stage": "sl_missing"}
        _, observed, qualified = watchdog_debounce_step(None, {"would": [item]}, now_s=100.0,
                                                        debounce_s=600.0)
        self.assertEqual(observed, ["okx|BTC|sl_missing"], "新缺口仍要被观测到")
        self.assertEqual(qualified, [], "但未满防抖窗口 ⇒ 不许合格（不许触发真实写单）")
        # 用首见时间"喂"一次未来时刻 ⇒ 满窗口后可合格
        state = {"okx|BTC|sl_missing": 100.0}
        _, _, qualified2 = watchdog_debounce_step(state, {"would": [item]}, now_s=700.0,
                                                 debounce_s=600.0)
        self.assertEqual(qualified2, ["okx|BTC|sl_missing"])

    def test_zero_or_negative_debounce_qualifies_immediately(self):
        # 模块 docstring：「`debounce_s <= 0` 视为**不防抖**（立即合格）—— 这是显式运营选择」
        item = {"venue": "okx", "inst": "BTC", "stage": "sl_missing"}
        for debounce in (0.0, -1.0):
            with self.subTest(debounce=debounce):
                _, _, qualified = watchdog_debounce_step(None, {"would": [item]},
                                                         now_s=100.0, debounce_s=debounce)
                self.assertEqual(qualified, ["okx|BTC|sl_missing"])

    def test_none_debounce_is_also_no_debounce(self):
        item = {"venue": "okx", "inst": "BTC", "stage": "sl_missing"}
        _, _, qualified = watchdog_debounce_step(None, {"would": [item]}, now_s=100.0,
                                                 debounce_s=None)
        self.assertEqual(qualified, ["okx|BTC|sl_missing"])

    def test_input_state_is_not_mutated(self):
        original = {"okx|BTC|sl_missing": 100.0}
        snapshot = dict(original)
        watchdog_debounce_step(original, {"would": []}, now_s=200.0, debounce_s=0.0)
        self.assertEqual(original, snapshot, "纯函数不许改调用方的 state")


class CoverageOkTriStateTest(unittest.TestCase):
    """`coverage_ok` 是**三态**（True / False / None）—— 这是"不可判定 ≠ 安全"的落点。"""

    def _scan(self, rows):
        return scan_protective_orders(rows, symbol="BTC_USDT", pos_side="long",
                                      position_size=10, now_s=NOW)

    # ⚠️ Gate 的 `close=True` 是**整仓平**（`size` 无关紧要）：`gate_sl_row()` 默认即此，
    #    所以"按量覆盖"的用例必须显式 `close=False`，否则 size 写多少都是全覆盖。
    def test_true_when_fully_covered(self):
        self.assertIs(self._scan([gate_sl_row()])["coverage_ok"], True)
        self.assertIs(self._scan([gate_sl_row(close=False, size=10)])["coverage_ok"], True)

    def test_false_when_short(self):
        scan = self._scan([gate_sl_row(close=False, size=4)])
        self.assertIs(scan["coverage_ok"], False)
        self.assertEqual(scan["missing_size"], 6)

    def test_none_when_a_leg_size_is_undecidable(self):
        # 有腿但**量算不出来** ⇒ 不说够也不说不够
        row = gate_sl_row(close=False)
        del row["initial"]["size"]
        scan = self._scan([row])
        self.assertIsNone(scan["coverage_ok"])
        self.assertTrue(scan["needs_verify"], "不可判定必须要求人工核验")
        self.assertNotIn(row["id"], [r.get("id") for r in (scan.get("needs_renew") or [])],
                         "不可判定的腿绝不许进续期名单")

    def test_none_is_never_treated_as_safe(self):
        row = gate_sl_row(close=False)
        del row["initial"]["size"]
        scan = self._scan([row])
        self.assertIsNone(scan["coverage_ok"])
        self.assertIsNot(scan["coverage_ok"], True)


class UncoverableLineTest(unittest.TestCase):
    """★ 本模块有**一行物理上不可能被行覆盖**，此处把它变成**机器校验**而不是口头说明。

    `scan_protective_orders` 里的第 521 行是：

        coverage_ok: Optional[bool]

    这是**纯注解语句**（`AnnAssign` 且无值）。CPython 对**函数内**的局部变量注解
    **不生成任何字节码**（注解只对模块/类层级的 `__annotations__` 生效），所以任何
    基于行事件的追踪器都记不到它。这不是"没测到"，是"测不到"。

    把它写成断言而不是注释，是为了让下一个人**改不动这个借口**：如果哪天有人给它补上
    初值（`coverage_ok: Optional[bool] = None`），本用例会立刻变红并告诉他"现在可以覆盖了"。
    """

    SRC = Path(venue_protection.__file__).read_text(encoding="utf-8")

    def test_the_module_has_exactly_one_bare_annotation(self):
        tree = ast.parse(self.SRC)
        bare = [(n.lineno, n.target.id) for n in ast.walk(tree)
                if isinstance(n, ast.AnnAssign) and n.value is None]
        self.assertEqual(bare, [(521, "coverage_ok")],
                         "纯注解语句集合变了 —— 要么补了初值（那就该删掉本类），"
                         "要么新增了一处（那就该把它一起核查）")

    def test_that_line_really_has_no_bytecode(self):
        code = compile(self.SRC, venue_protection.__file__, "exec")

        def find(co, name):
            for const in co.co_consts:
                if hasattr(const, "co_name"):
                    if const.co_name == name:
                        return const
                    found = find(const, name)
                    if found is not None:
                        return found
            return None

        fn = find(code, "scan_protective_orders")
        self.assertIsNotNone(fn, "找不到目标函数的 code 对象")
        executable_lines = {i.starts_line for i in dis.get_instructions(fn)
                            if i.starts_line is not None}
        self.assertNotIn(521, executable_lines)
        # 对照：紧邻的两行**有**字节码 —— 证明"没字节码"是这一行的性质，
        # 而不是整个函数都没被编译
        self.assertIn(520, executable_lines)
        self.assertIn(522, executable_lines)

    def test_neighbouring_assignments_are_covered_by_other_tests(self):
        # 521 之所以是"孤例"，是因为它两侧都正常执行：520 算容差、522 判是否不可判定。
        # 本用例把两侧都实际跑一遍，确保"孤例"不是"整段都没跑到"。
        self.assertIsInstance(scan_protective_orders([], symbol="BTC_USDT", pos_side="long",
                                                     position_size=1, now_s=NOW)["coverage_ok"],
                              bool)
