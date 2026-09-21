"""多所执行路由的**每一条拒开理由**都要有例（第二百三十九刀）。

`open_protected_position` 是「钱离开账户」的那一步。它的每个 `_fail(stage, …)` 都是一条
**在动手之前**把风险挡住的理由 —— 谁被挡、为什么挡、挡在哪一步，必须逐条可复现：

| stage | 触发条件 | 纪律 |
|---|---|---|
| `listing` | 目录核对说"已下架/未上市" | fail-closed 拒开；但**目录不可用** ⇒ fail-open 放行（对账是增强不是闸门）|
| `specs` | 拿不到合约规格 | 无规格 ⇒ 算不出张数 ⇒ 拒开 |
| `price` | 现价不可得 | **禁止盲单**（宁可不开）|
| `sizing` | 名义额不足最小下单量 | 拒开并写清名义额与最小量 |
| `precheck` | 既有持仓探针失败 | 排除不了外部仓 ⇒ 拒开 |
| `leverage` / `entry` | 设档 / 入场委托失败 | 未下单无风险 ⇒ 止步并如实回报 |
| （能力异常）| `ExchangeCapabilityError` | **必须原样上抛**，不许降级成普通 fail |
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from r20_backend import execution_router as router
from r20_backend.exchanges import ExchangeCapabilityError
from tests.test_gate_execution_router import _StubAdapter, _decision


class RefusalStageTest(unittest.TestCase):
    def _run(self, ad=None, *, decision=None, price_ref=79000.0):
        ad = ad or _StubAdapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.open_protected_position(
                decision if decision is not None else _decision(),
                adapter=ad, price_ref=price_ref)
        return r, ad

    def test_listing_gate_refuses_delisted_contract(self):
        ad = _StubAdapter()
        with patch("r20_backend.exchanges.listing.ensure_contract_listed",
                   return_value=SimpleNamespace(ok=False, reason="合约已下架")):
            r, ad = self._run(ad)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "listing")
        self.assertIn("合约对账拒绝", r["detail"])
        self.assertEqual(ad.calls, [], "下架合约绝不许走到任何下单 IO")

    def test_listing_gate_unavailable_is_fail_open(self):
        """目录拉不到 ⇒ 放行（对账是增强不是风控闸门，绝不阻塞交易）。"""
        ad = _StubAdapter()
        with patch("r20_backend.exchanges.listing.ensure_contract_listed",
                   side_effect=RuntimeError("目录服务挂了")):
            r, ad = self._run(ad)
        self.assertTrue(r["ok"], f"目录不可用不该阻塞交易：{r.get('detail')}")

    def test_missing_spec_refuses(self):
        ad = _StubAdapter()
        ad.fetch_instrument_spec = lambda symbol, refresh=False: None
        r, ad = self._run(ad)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "specs")
        self.assertIn("合约规格", r["detail"])

    def test_unavailable_price_refuses_blind_orders(self):
        ad = _StubAdapter()
        ad.fetch_ticker = lambda symbol: {}
        r, ad = self._run(ad, price_ref=0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "price")
        self.assertIn("禁止盲单", r["detail"])
        self.assertNotIn(("place", "BTC", "long", 0.0, 79000.0), ad.calls)

    def test_below_min_size_refuses_before_leverage(self):
        r, ad = self._run(decision=_decision(margin_usdt=0.01))
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "sizing")
        self.assertNotIn("leverage", [c[0] for c in ad.calls], "算不出张数就不该去设档")

    def test_precheck_probe_failure_refuses(self):
        ad = _StubAdapter(fail_positions=True)
        r, ad = self._run(ad)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "precheck")
        self.assertIn("探针失败", r["detail"])

    def test_capability_error_from_probe_is_reraised(self):
        """能力异常（场所不支持）必须**原样上抛** —— 降级成 fail 会把它伪装成"临时故障"。"""
        ad = _StubAdapter()
        ad.positions = lambda: (_ for _ in ()).throw(ExchangeCapabilityError("不支持"))
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            with self.assertRaises(ExchangeCapabilityError):
                router.open_protected_position(_decision(), adapter=ad, price_ref=79000.0)

    def test_leverage_failure_stops_before_entry(self):
        ad = _StubAdapter(fail_leverage=True)
        r, ad = self._run(ad)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "leverage")
        self.assertIn("设置杠杆失败", r["detail"])
        self.assertNotIn("place", [c[0] for c in ad.calls], "设档失败 ⇒ 绝不下单")

    def test_capability_error_from_leverage_is_reraised(self):
        ad = _StubAdapter()
        ad.set_leverage = lambda *a, **k: (_ for _ in ()).throw(ExchangeCapabilityError("不支持"))
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            with self.assertRaises(ExchangeCapabilityError):
                router.open_protected_position(_decision(), adapter=ad, price_ref=79000.0)

    def test_entry_failure_is_reported_after_leverage(self):
        ad = _StubAdapter(fail_place=True)
        r, ad = self._run(ad)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "entry")
        self.assertIn("入场委托提交失败", r["detail"])
        self.assertIn("leverage", [c[0] for c in ad.calls], "设档已发生 ⇒ 说明是止步在下单这一步")

class OwnLedgerVerdictTest(unittest.TestCase):
    """己仓归属：**读不到 ≠ 外部仓**（2026-09-20 实盘证据：UNI 是台账里的本方仓、
    ARB 是账实不符，旧文案一律报成「外部仓连坐拒开」＝**说谎的诊断**，会把运维引去找
    根本不存在的外部仓）。判定仍全部拒开，但**谁在持有/能不能判定必须说清楚**。"""

    def _run(self, *, existing=None, own_position=None, verdict=None, ledger_raises=False,
             classify_raises=False):
        ad = _StubAdapter(positions_rows=existing or [])
        patches = []
        records = router.own_position_records
        if ledger_raises:
            patches.append(patch.object(records, "load_ledger",
                                        side_effect=RuntimeError("台账读不了")))
        if verdict is not None:
            patches.append(patch.object(records, "classify_exchange_position",
                                        return_value=verdict))
        if classify_raises:
            patches.append(patch.object(records, "classify_exchange_position",
                                        side_effect=RuntimeError("判定件炸了")))
        for _p in patches:
            _p.start()
        try:
            with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
                r = router.open_protected_position(
                    _decision(), adapter=ad, price_ref=79000.0,
                    own_position=own_position)
        finally:
            for _p in patches:
                _p.stop()
        return r, ad

    EXISTING = [{"base": "BTC", "side": "long", "size_signed": 1.0}]

    def test_caller_record_mismatch_is_disclosed_as_such(self):
        r, _ = self._run(existing=self.EXISTING,
                         own_position={"size_signed": 2.0, "side": "long"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "precheck")
        self.assertEqual(r["own_verdict"], "mismatch")
        self.assertIn("调用方在管记录", r["detail"])

    def test_unreadable_ledger_says_undecidable_not_external(self):
        """台账读不出来 ⇒ 判**不可判定**，绝不判「外部仓」。"""
        r, _ = self._run(existing=self.EXISTING, ledger_raises=True)
        self.assertFalse(r["ok"], "判定不确定仍然拒开（本刀不改交易行为）")
        self.assertEqual(r["stage"], "precheck")
        # ⚠️ 不能只断言「不含『外部仓』字样」：文案里**合法地**出现这三个字
        # （「不得当成外部仓」「不宣称『外部仓』」）——那正是它在**否认**这件事。
        # 故断言正向语义：说了「不可判定」，且明说**不宣称**外部仓。
        self.assertIn("不可判定", r["detail"])
        self.assertIn("不宣称", r["detail"])
        self.assertIn("不得当成外部仓", r["detail"])

    def test_verdict_engine_exception_is_ledger_unavailable(self):
        """判定件自身炸了 ≠ 外部仓 ⇒ 记 `ledger_unavailable`（归属不可判定）。"""
        r, _ = self._run(existing=self.EXISTING, classify_raises=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["own_verdict"], "ledger_unavailable")
        self.assertIn("不可判定", r["detail"])

    def test_mismatch_verdict_is_disclosed(self):
        r, _ = self._run(existing=self.EXISTING,
                         verdict={"verdict": "mismatch", "reason": "台账行与实况不符"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["own_verdict"], "mismatch")
        self.assertIn("本方记录与交易所不符", r["detail"])

    def test_own_verdict_is_reported_and_still_refuses(self):
        """本方已在管 ⇒ 拒开（重复开仓=敞口翻倍），并把判定放进入参供巡检消费。"""
        r, _ = self._run(existing=self.EXISTING,
                         verdict={"verdict": "own", "reason": "台账 holding 行归属本方"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["own_verdict"], "own")
        self.assertIn("本方已在管该仓", r["detail"])


class ClosePositionEnvironmentTest(unittest.TestCase):
    def test_gate_sandbox_environment_is_normalised(self):
        """Gate 的沙盒族（demo 等）在平仓前**归一为 sandbox**（适配器按 sandbox 走）。"""
        ad = _StubAdapter()
        ad.environment = "demo"
        ad.fast_close_position = lambda symbol, **k: {"id": 7, "closed": True}
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = router.close_position("BTC", venue="gate", adapter=ad, environment="demo")
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(getattr(ad, "environment", None), "demo",
                         "归一的是 router 内部变量，不该改写适配器自身属性")


if __name__ == "__main__":
    unittest.main()
