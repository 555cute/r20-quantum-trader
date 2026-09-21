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

class ProtectiveRollbackTest(unittest.TestCase):
    """保护腿挂失败后的**双段清理**（审计④#9）。

    铁律是「任一步失败 → 已挂触发单回滚 + 撤入场单」。旧实现只撤入场单 ⇒
    tp 挂成、sl 失败时 tp 变孤儿留到 expiration（**无仓挂保护单不可对账**）。
    现有两段：①已知 legs 逐腿 best-effort 撤；②枚举该资产残留触发单，
    **只撤带 r20 前缀的本系统单**（用户手动保护单绝不触碰），枚举失败则**如实标注**。
    """

    def _run(self, ad):
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            return router.open_protected_position(_decision(), adapter=ad, price_ref=79000.0)

    def test_attach_failure_rolls_back_without_known_legs(self):
        ad = _StubAdapter(fail_attach=True)
        r = self._run(ad)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "protective")
        self.assertIn(("cancel_entry", "BTC", "9001"), ad.calls, "绝不留裸仓")

    def test_residue_enumeration_touches_only_r20_labelled_orders(self):
        """孤儿清理**只认本系统标签**；用户手单、非 dict、无 id 的行一律不碰。"""
        ad = _StubAdapter(fail_verify=True)
        ad.list_protective_orders = lambda symbol: [
            {"id": "r1", "text": "t-r20tp"},           # 本系统 ⇒ 撤
            {"text": "t-r20sl"},                       # 无 id ⇒ 跳过
            "垃圾行",                                   # 非 dict ⇒ 跳过
            {"id": "u1", "text": "user-manual"},       # 用户手单 ⇒ 绝不触碰
        ]
        r = self._run(ad)
        self.assertFalse(r["ok"])
        cancelled = [c for c in ad.calls if c[0] == "cancel_entry"]
        self.assertIn(("cancel_entry", "BTC", "r1"), cancelled, "本系统孤儿要清")
        self.assertNotIn(("cancel_entry", "BTC", "u1"), cancelled, "用户手单绝不触碰")

    def test_residue_enumeration_failure_is_disclosed_not_hidden(self):
        ad = _StubAdapter(fail_verify=True)

        def boom(symbol):
            raise RuntimeError("列表端点炸了")

        ad.list_protective_orders = boom
        r = self._run(ad)
        self.assertFalse(r["ok"])
        self.assertIn("未能枚举", r["detail"], "枚举失败必须如实标注（不能假装清干净了）")


class CancelProvenOwnLegsTest(unittest.TestCase):
    """平仓后只撤**可证明属于本系统**的腿。

    ⚠️ 三轮才找对真因（前两轮猜测都错，记在这免得再走）：行形状**没问题**
    （`leg_base`/`_leg_kind`/`_row_text` 实测都正常）。真因见下面"死分支"注释。
    """

    ROW = {"id": "g1", "initial": {"contract": "BTC_USDT", "text": "t-r20sl"}}

    def _adapter(self):
        ad = _StubAdapter()
        ad.cancelled = []
        ad.cancel_price_order = lambda oid: ad.cancelled.append(oid)
        ad.list_protective_orders = lambda symbol: [dict(self.ROW)]
        return ad

    # ⚠️⚠️ **第二百四十三刀：发现一处死分支（有实测证据，未擅自改行为）**
    # `_cancel_proven_own_legs` 里写的是 `own_position = dict(before_position or {"base": base})`
    # ⇒ 平仓前事实为**空**时会被兜成 `{"base": …}`，而带 base 的字典在
    # `attribute_protective_orders` 眼里就是**一笔有效仓** ⇒ 腿被归到 `size_mismatch`
    # （仓量 0 vs 腿量）而**不是** `orphan_attributed`：
    #   select_legs_to_cancel_after_close({"base":"BTC"}, [带标签腿], []) → to_cancel 0 / not_touched 1
    #   select_legs_to_cancel_after_close({},             [带标签腿], []) → to_cancel 1 / not_touched 0
    # ⇒ 该函数 docstring 承诺的第②类「孤儿但带本系统标签 ⇒ 撤」**永远走不到**
    # （好在落进 `not_touched` 会如实报「未撤」，不至于静默误判成"已清理"）。
    # 修它会**改变平仓后的实际撤腿行为**（开始真的撤这类腿）⇒ 必须单独一刀评估，
    # 本刀只留证据与结论，**不写猜出来的绿、也不顺手改钱路**。

    def test_ledger_evidence_alone_does_not_auto_cancel(self):
        """**仅台账证据 ⇒ 不自动撤**（保守）。

        台账是本地记录、可能与交易所不一致（本仓已有"账实不符"实例）⇒ 只有交易所侧
        的 `tag`（`t-r20sl/t-r20tp`）才算"可证明"，台账证据只交归属审计。
        """
        from r20_backend.execution_router import _cancel_proven_own_legs
        import r20_backend.execution.own_records as own
        ad = self._adapter()
        with patch.object(own, "load_ledger",
                          return_value=[{"id": "l1", "inst": "BTC_USDT", "side": "long",
                                         "sz": 0.0, "status": "holding"}]):
            note = _cancel_proven_own_legs(ad, "BTC", {})
        self.assertEqual(ad.cancelled, [], "只有台账证据 ⇒ 不撤（可能账实不符）")
        self.assertIn("未撤", note, f"但要如实报数：{note}")

    def test_untagged_leg_for_another_contract_is_left(self):
        from r20_backend.execution_router import _cancel_proven_own_legs
        import r20_backend.execution.own_records as own
        ad = _StubAdapter()
        ad.cancelled = []
        ad.cancel_price_order = lambda oid: ad.cancelled.append(oid)
        ad.list_protective_orders = lambda symbol: [
            {"id": "eth1", "initial": {"contract": "ETH_USDT", "text": "t-r20sl"}}]
        with patch.object(own, "load_ledger", return_value=[]):
            note = _cancel_proven_own_legs(ad, "BTC", {})
        self.assertEqual(ad.cancelled, [], "别的合约的腿不碰")
        self.assertIn("已撤 0 张", note, f"如实报数：{note}")


class ImportFallbackConstantsTest(unittest.TestCase):
    """常量读不到时**绝不臆造区间**（导入期兜底）。

    `execution_router` 在 `scripts.risk_constants` 不可用时退到裸 `risk_constants`；
    再不可用 ⇒ `MAX_MARGIN_EQUITY_RATIO, MAX_SINGLE_ASSET_MARGIN = 0.20, 0.0` 且
    `MAX_LEVERAGE = MIN_LEVERAGE = None` —— **夹取退化为 no-op**（靠 `or leverage` 短路），
    绝不因为读不到配置就凭空放大或缩小杠杆。这条只能靠"把常量模块彻底拿掉再 reload"来验。
    """

    def test_missing_exposure_cap_alone_falls_back_to_zero(self):
        """模块在、但**缺 `MAX_TOTAL_EXPOSURE_USDT`** ⇒ 只有敞口帽退化为 0.0（其余常量照用）。

        ⚠️ 这是**内层**兜底：与"整个模块都没有"是两条不同的路径（前者 leverage 仍可用）。
        """
        import importlib
        import sys as _sys
        import types

        fake = types.ModuleType("scripts.risk_constants")
        fake.MAX_LEVERAGE, fake.MIN_LEVERAGE = 20, 1
        fake.MAX_MARGIN_EQUITY_RATIO, fake.MAX_SINGLE_ASSET_MARGIN = 0.20, 0.0
        try:
            with patch.dict(_sys.modules, {"scripts.risk_constants": fake,
                                           "risk_constants": fake}):
                mod = importlib.reload(router)
                self.assertEqual(mod.TOTAL_EXPOSURE_CAP, 0.0, "缺敞口帽 ⇒ 退化为 0（不是无上限）")
                self.assertEqual(mod.MAX_LEVERAGE, 20, "其余常量照用（这是内层兜底）")
        finally:
            importlib.reload(router)

    def test_missing_risk_constants_degrades_to_noop_not_invented_band(self):
        import importlib
        import sys as _sys

        before = (router.MAX_LEVERAGE, router.MIN_LEVERAGE, router.MAX_MARGIN_EQUITY_RATIO)
        blocked = {k: None for k in ("scripts.risk_constants", "risk_constants")}
        try:
            with patch.dict(_sys.modules, blocked):
                mod = importlib.reload(router)
                self.assertIsNone(mod.MAX_LEVERAGE, "常量为空 ⇒ 上限必须是 None（no-op）")
                self.assertIsNone(mod.MIN_LEVERAGE)
                self.assertEqual(mod.MAX_MARGIN_EQUITY_RATIO, 0.20)
                self.assertEqual(mod.MAX_SINGLE_ASSET_MARGIN, 0.0)
                self.assertEqual(mod.TOTAL_EXPOSURE_CAP, 0.0)
        finally:
            importlib.reload(router)          # 必须还原，否则污染后续用例
        self.assertEqual((router.MAX_LEVERAGE, router.MIN_LEVERAGE,
                          router.MAX_MARGIN_EQUITY_RATIO), before, "reload 后必须还原")


if __name__ == "__main__":
    unittest.main()
