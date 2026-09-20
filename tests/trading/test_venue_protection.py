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
    audit_cross_venue_protection,
    ensure_venue_protection,
    scan_protective_orders,
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
        gate = MagicMock()
        gate.list_protective_orders.return_value = [gate_sl_row()]
        reg = self._registry({"gate": gate, "binance": MagicMock()})
        report = audit_cross_venue_protection(
            {"gate": [{"inst_id": "BTC_USDT"}]},   # 缺 side/size
            venue_registry=reg, environment="demo", now_s=NOW)
        self.assertTrue(report["skipped"])
        gate.list_protective_orders.assert_not_called()


class WatchdogStageTest(unittest.TestCase):
    """接线层：默认关闭＝零副作用；开启＝巡检并把结论写进 executed_actions。"""

    def _stage(self, flag, report=None, raises=None):
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
        )
        return out, calls, actions

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


if __name__ == "__main__":
    unittest.main()
