"""分批止盈（`execute_scale_out_if_eligible`）的准入守卫、按所路由与旧保护单清理（第二百二十三刀）。

这一格是"真金白银的减仓"，它的每一条分支都值得钉住：

- **未启用 / 行情数据不完整 / 无持仓 / 持仓均价无效** ⇒ 一律不动手（返回原因，不猜）；
- 切片算出来后**低于最小精度** ⇒ 降级为全仓追踪（`scale_out_phase = -1`）并留痕，
  **绝不提交一个交易所会拒绝或会留下碎片的单**；
- 按所路由：Binance **hedge**（`positionSide` 非空）走 `position_side=`，**one-way** 才走
  `reduce_only=True`（hedge 下传 reduceOnly 会被拒）；Gate 与"多所兜底"路径同理；
- **没有适配器注册表** / **下单抛异常** ⇒ 如实说明是哪一所失败，绝不假装成功；
- 下单成功后清掉**旧保护单**（避免超额单量穿仓反向开单与旧止损残留），清理异常只告警。
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts.trader import scale_out as so

FACTOR = {"instId": "BTC-USDT-SWAP", "name": "BTC", "price": 100.0, "atr": 2.0,
          "precision": 2, "ctVal": 1.0, "minSz": 0.1, "market_data_valid": True}
POS = {"pos": "3.4", "side": "long", "avgPx": 90.0, "venue": "binance"}
KEY = "BTC-USDT-SWAP_long"


class _Ad:
    def __init__(self, *, venue="binance", raises=False, cancel_raises=False):
        self.venue = venue
        self.raises = raises
        self.cancel_raises = cancel_raises
        self.placed = []
        self.cancelled = []
        self.attached = []

    def native_symbol(self, symbol):
        return f"{symbol}_NATIVE"

    def place_order(self, symbol, side, qty, **kw):
        if self.raises:
            raise RuntimeError("下单被拒")
        self.placed.append((symbol, side, qty, kw))
        return {"ordId": "x1"}

    def cancel_protective_orders(self, symbol):
        if self.cancel_raises:
            raise RuntimeError("取消失败")
        self.cancelled.append(symbol)

    def cancel_all_algo_open_orders(self, *, symbol):
        self.cancelled.append(symbol)

    def attach_protective_orders(self, name, pos_side, *, tp_px, sl_px, contracts):
        self.attached.append((name, pos_side, tp_px, sl_px, contracts))
        return {"sl": "new"}


class _AdOnlyProtective(_Ad):
    """只实现 `cancel_protective_orders`（老适配器）⇒ 必须走那一条分支。"""

    def cancel_all_algo_open_orders(self, *, symbol):      # 故意不可用
        raise AttributeError("本适配器没有这个方法")


class _AdOnlyAlgoBulk:
    """**没有** `cancel_protective_orders` 属性（不是"有但会抛"）⇒ 必须走 `elif` 那一条。

    ⚠️ 第一版让它继承 `_Ad` 并把方法写成"调用即抛 AttributeError"——但 `hasattr` 看的是
    **属性是否存在**，于是仍走 217 分支、方法抛错被吞 ⇒ 218 永远不执行。
    "能力探测"要用**真的没有那个属性**来测。
    """

    def __init__(self):
        self.placed = []
        self.cancelled = []
        self.attached = []

    def place_order(self, symbol, side, qty, **kw):
        self.placed.append((symbol, side, qty, kw))
        return {"ordId": "x1"}

    def native_symbol(self, symbol):
        return f"{symbol}_NATIVE"

    def cancel_all_algo_open_orders(self, *, symbol):
        self.cancelled.append(symbol)

    def attach_protective_orders(self, name, pos_side, *, tp_px, sl_px, contracts):
        self.attached.append((name, pos_side, tp_px, sl_px, contracts))
        return {"sl": "new"}


class _Registry:
    def __init__(self, ads):
        self.ads = ads

    def get_adapter(self, venue, environment=None):
        if venue not in self.ads:
            raise KeyError(venue)
        return self.ads[venue]


class _Okx:
    def __init__(self):
        self.placed = []
        self.cancelled = []

    def place_order(self, inst_id, side, sz, **kw):
        self.placed.append((inst_id, side, sz, kw))
        return {"ordId": "okx1"}

    def pending_algo_orders(self, inst_id):
        return []

    def cancel_algo_orders(self, ids, *, inst_id):
        self.cancelled.append((ids, inst_id))


class Base(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.printed = io.StringIO()

    def _run(self, *, factor=None, pos=None, trackers=None, ads=None, registry=True,
             okx=None, ratio="0.9", enabled=True, protection=None, **kw):
        f = dict(FACTOR if factor is None else factor)
        p = dict(POS if pos is None else pos)
        tr = {KEY: {"scale_out_phase": 0, "takeProfitPx": 0.0, "stage_desc": ""}} \
            if trackers is None else trackers
        reg = _Registry(ads if ads is not None else {}) if registry else None
        with patch.object(so, "SCALE_OUT_ENABLED", enabled), \
             patch.object(so, "SCALE_OUT_RATIO", float(ratio)), \
             patch.object(so, "SCALE_OUT_TRIGGER_ATR", 1.2):
            with redirect_stdout(self.printed):
                return so.execute_scale_out_if_eligible(
                    f, p, tr, "2026-09-21 12:00:00", self.actions,
                    okx_rest=okx if okx is not None else _Okx(),
                    venue_registry=reg, record_trade=lambda *a, **k: None,
                    notify_trade_close=lambda **k: None,
                    close_fee=lambda *a, **k: 0.5,
                    close_trade_payload=lambda **k: k,
                    ensure_cloud_position_protection=protection or (lambda *a, **k: (True, "ok")),
                    **kw)


class GuardTest(Base):
    def test_disabled_does_nothing(self):
        ok, why = self._run(enabled=False)
        self.assertFalse(ok)
        self.assertIn("未启用", why)

    def test_incomplete_market_data(self):
        ok, why = self._run(factor={**FACTOR, "market_data_valid": False})
        self.assertFalse(ok)
        self.assertIn("行情数据不完整", why)

    def test_no_position(self):
        ok, why = self._run(pos={**POS, "pos": "0"})
        self.assertFalse(ok)
        self.assertIn("无持仓", why)

    def test_invalid_entry_price(self):
        ok, why = self._run(pos={**POS, "avgPx": "0"})
        self.assertFalse(ok)
        self.assertIn("持仓均价无效", why)

    def test_slice_below_min_size_downgrades_instead_of_submitting(self):
        """切片/余量低于最小精度 ⇒ 降级全仓追踪并留痕，**绝不提交碎单**。"""
        ad = _Ad()
        tr = {KEY: {"scale_out_phase": 0, "takeProfitPx": 0.0}}
        ok, why = self._run(factor={**FACTOR, "minSz": 1.0}, trackers=tr,
                            ads={"binance": ad})
        self.assertFalse(ok)
        self.assertIn("最小精度", why)
        self.assertEqual(ad.placed, [], "低于精度的切片绝不能提交")
        self.assertEqual(tr[KEY]["scale_out_phase"], -1, "要标记为已评估不可切分（防每轮重复提示）")
        self.assertTrue(any("降级全仓追踪" in a for a in self.actions), self.actions)


class BinanceRoutingTest(Base):
    def test_hedge_uses_position_side_and_not_reduce_only(self):
        ad = _Ad()
        self._run(pos={**POS, "positionSide": "LONG"}, ads={"binance": ad})
        self.assertEqual(len(ad.placed), 1, f"应当提交一笔平仓：{ad.placed}")
        symbol, side, qty, kw = ad.placed[0]
        self.assertEqual(side, "sell", "多头减仓方向是卖")
        self.assertEqual(kw.get("position_side"), "LONG",
                         "Hedge 模式必须用 positionSide 表达方向")
        self.assertNotIn("reduce_only", kw,
                         "Hedge 模式传 reduceOnly 会被交易所拒（审计 §2）")

    def test_one_way_uses_reduce_only(self):
        ad = _Ad()
        self._run(ads={"binance": ad})
        _, _, _, kw = ad.placed[0]
        self.assertTrue(kw.get("reduce_only"), "one-way 模式必须带 reduceOnly=True")
        self.assertNotIn("position_side", kw)

    def test_raw_position_side_is_also_honoured(self):
        ad = _Ad()
        self._run(pos={**POS, "raw": {"positionSide": "short"}}, ads={"binance": ad})
        self.assertEqual(ad.placed[0][3].get("position_side"), "SHORT",
                         "positionSide 也可能只在 raw 里，必须照样认")

    def test_order_failure_is_reported_with_the_venue(self):
        ad = _Ad(raises=True)
        ok, why = self._run(ads={"binance": ad})
        self.assertFalse(ok)
        self.assertEqual(why, "平仓提交失败")
        self.assertTrue(any("BINANCE" in a and "失败" in a for a in self.actions), self.actions)

    def test_missing_registry_is_reported(self):
        ok, why = self._run(registry=False)
        self.assertFalse(ok)
        self.assertTrue(any("未提供 BINANCE 适配器注册表" in a for a in self.actions),
                        f"没有注册表要说清是哪一所：{self.actions}")


class GateAndGenericRoutingTest(Base):
    def _pos(self, venue):
        return {**POS, "venue": venue}

    def test_gate_success(self):
        ad = _Ad(venue="gate")
        self._run(pos=self._pos("gate"), ads={"gate": ad})
        self.assertEqual(len(ad.placed), 1)
        self.assertTrue(ad.placed[0][3].get("reduce_only"), "Gate 走 reduce_only")

    def test_gate_failure_and_missing_registry(self):
        ok, _ = self._run(pos=self._pos("gate"), ads={"gate": _Ad(venue="gate", raises=True)})
        self.assertFalse(ok)
        self.assertTrue(any("GATE" in a and "失败" in a for a in self.actions), self.actions)

    def test_gate_missing_registry_is_reported(self):
        ok, _ = self._run(pos=self._pos("gate"), registry=False)
        self.assertFalse(ok)
        self.assertTrue(any("未提供 GATE 适配器注册表" in a for a in self.actions), self.actions)

    def test_generic_venue_success(self):
        ad = _Ad(venue="kraken")
        self._run(pos=self._pos("kraken"), ads={"kraken": ad})
        self.assertEqual(len(ad.placed), 1, "多所兜底路径要能下单")
        self.assertTrue(ad.placed[0][3].get("reduce_only"))

    def test_generic_venue_failure_and_missing_registry(self):
        ok, _ = self._run(pos=self._pos("kraken"),
                          ads={"kraken": _Ad(venue="kraken", raises=True)})
        self.assertFalse(ok)
        self.assertTrue(any("KRAKEN" in a and "失败" in a for a in self.actions), self.actions)
        ok2, _ = self._run(pos=self._pos("kraken"), registry=False)
        self.assertFalse(ok2)
        self.assertTrue(any("未提供 KRAKEN 适配器注册表" in a for a in self.actions), self.actions)


class ProtectionCleanupTest(Base):
    def test_old_protection_is_cancelled_before_rebuilding(self):
        ad = _Ad()
        ok, why = self._run(ads={"binance": ad})
        self.assertTrue(ok, why)
        self.assertEqual(ad.cancelled, ["BTC"],
                         "下单成功后必须清掉旧保护单（否则超额单量可能反向开仓）")
        self.assertEqual(len(ad.attached), 1, "并且要为剩余仓位重建保护")

    def test_cleanup_failure_only_warns(self):
        ad = _Ad(cancel_raises=True)
        ok, _ = self._run(ads={"binance": ad})
        self.assertTrue(ok, "旧保护单清理失败只告警，不该让已成交的减仓判失败")
        self.assertIn("旧保护单异常", self.printed.getvalue())

    def test_older_adapter_without_algo_bulk_cancel_still_cleans_up(self):
        """能力只在 `cancel_protective_orders` 上的适配器也要能清旧保护单（能力探测分支）。"""
        ad = _AdOnlyProtective()
        ok, why = self._run(ads={"binance": ad})
        self.assertTrue(ok, why)
        self.assertEqual(ad.cancelled, ["BTC"])

    def test_adapter_with_only_algo_bulk_cancel_still_cleans_up(self):
        """能力只在 `cancel_all_algo_open_orders` 上的适配器也要能清旧保护单。"""
        ad = _AdOnlyAlgoBulk()
        ok, why = self._run(ads={"binance": ad})
        self.assertTrue(ok, why)
        self.assertEqual(ad.cancelled, ["BTC_NATIVE"], "走 elif 分支时传的是本所符号")

    def test_trackers_are_locked_after_a_successful_scale_out(self):
        tr = {KEY: {"scale_out_phase": 0, "takeProfitPx": 0.0}}
        self._run(trackers=tr, ads={"binance": _Ad()})
        self.assertEqual(tr[KEY]["scale_out_phase"], 1)
        self.assertEqual(tr[KEY]["scale_count"], 999, "分批后要永久互斥金字塔加仓")


class ConfigFallbackTest(unittest.TestCase):
    def test_constants_fall_back_when_config_import_fails(self):
        """`scripts.risk_constants` 导入失败时必须有可用兜底（否则整个模块 import 就崩）。"""
        import importlib
        import sys
        with patch.dict(sys.modules, {"scripts.risk_constants": None}):
            try:
                mod = importlib.reload(so)
                self.assertTrue(mod.SCALE_OUT_ENABLED, "兜底：默认启用")
                self.assertEqual(mod.SCALE_OUT_RATIO, 0.50)
                self.assertEqual(mod.SCALE_OUT_TRIGGER_ATR, 1.20)
            finally:
                importlib.reload(so)          # 复原模块（含真实配置值）


if __name__ == "__main__":
    unittest.main()
