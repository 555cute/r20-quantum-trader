"""`r20_backend/execution/risk_gates.py`（阶段 4·B3 第三十八刀）回归。

## 抽了什么

`r20_backend/execution_router.py::open_protected_position` 的 L108–173（67 行）
—— 该函数 293 行里最大的一块内聚逻辑，三道**发送前风控闸门**：

| | 之前 | 之后 |
|---|---|---|
| `open_protected_position()` | 293 行 | **243 行** |
| `execution_router.py` | 388 行 | **343 行** |
| 新模块 | — | `risk_gates.py` 164 行 |

## ⚠️ 本刀**发现并修复了一个真实 bug**（抽取时才暴露）

`check_total_exposure` 里那句比较：

    row_action = "buy" if row_side in ("long","buy") else ...   # 小写
    if row_action != action:        # action 是 decision["action"].upper() → "BUY_LONG"
        continue

**大小写永不相等** → 每条同向持仓都被跳过 → `same_side` 恒为 0 → 敞口闸门
**从未真正生效过**。而它旁边那行
`_all_positions if _all_positions is not None else ad.positions()` 里的
`_all_positions` 是**局部变量**（真正的赋值在 L209），故一进那条分支就抛
`UnboundLocalError`，被裸 `except Exception` 吞掉、返回 `_fail("exposure", ...)`。

两个缺陷**互相掩盖**：既有测试断言 `ok is False` + `stage == "exposure"`，
恰好被异常兜底满足 —— 断言全中，闸门却是死的。见
`tests/audit/test_audit_config_p4_cleanup.py::ExposureCapTests::test_router_refuses_when_projected_exposure_exceeds_cap`
现已被改写为真正走闸门并断言理由文案。

**实盘影响**：生产 `R20_MAX_TOTAL_EXPOSURE_USDT` 未配置 → `TOTAL_EXPOSURE_CAP = 0.0`
→ 闸门仍**停用**，故本次修复**不改变当前实盘行为**（由 `ProductionCapStillDisabledTest` 守住）。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "r20_backend" / "execution" / "risk_gates.py"
FACADE = ROOT / "r20_backend" / "execution_router.py"

from r20_backend.execution.risk_gates import (  # noqa: E402
    check_total_exposure,
    clamp_leverage,
    clamp_margin,
)


_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（第二百三十七刀）：
    本文件把**线上 `.env`** 的 `R20_MAX_TOTAL_EXPOSURE_USDT` 与契约值对照，验证线上配置与
    抽取后实现一致 —— 不读生产就无法成立，属**有意的线上守卫**。

    只读、不改；声明在此把「依赖线上配置内容」从**静默**变成**可审计**
    （未声明时 `R20_TESTS_STRICT_READS=1` 会报错）。
    """
    global _READ_SCOPE
    from tests import allow_real_data_reads
    _READ_SCOPE = allow_real_data_reads()
    _READ_SCOPE.__enter__()


def tearDownModule():
    global _READ_SCOPE
    if _READ_SCOPE is not None:
        _READ_SCOPE.__exit__(None, None, None)
        _READ_SCOPE = None


def _fail(stage, detail, venue="gate", **extra):
    return {"ok": False, "stage": stage, "detail": detail, "venue": venue, **extra}


# ------------------------------------------------------------ 杠杆闸门


class ClampLeverageTest(unittest.TestCase):
    def _run(self, leverage, decision=None, lo=1.0, hi=10.0):
        return clamp_leverage(venue="gate", asset="BTC",
                              decision=decision if decision is not None else {},
                              leverage=leverage, min_leverage=lo,
                              max_leverage=hi)

    def test_within_range_untouched(self):
        lev, dec = self._run(5.0)
        self.assertEqual(lev, 5.0)
        self.assertEqual(dec, {}, "未夹取时不得改写 decision")

    def test_clamped_down_to_global_upper(self):
        lev, dec = self._run(25.0, hi=10.0)
        self.assertEqual(lev, 10.0)
        self.assertEqual(dec["leverage"], 10.0, "夹取后必须写回 decision")

    def test_clamped_up_to_global_lower(self):
        """审计 P2-8：1x 这种低于配置下限的杠杆会真的发出去。"""
        lev, dec = self._run(1.0, lo=3.0)
        self.assertEqual(lev, 3.0)
        self.assertEqual(dec["leverage"], 3.0)

    def test_per_instrument_cap_tightens_upper(self):
        """池内单标的硬上限比全局更严时生效。"""
        lev, _ = self._run(8.0, decision={"max_leverage": 4.0}, hi=10.0)
        self.assertEqual(lev, 4.0, "单标的上限必须收紧全局上限")

    def test_per_instrument_cap_cannot_loosen_upper(self):
        # ⚠️ 我第一版写 `self._run(8.0, ...)` 并断言 10.0 —— **错的**。
        # 8.0 本来就在 [1, 10] 区间内，不触发夹取，返回 8.0 才是对的。
        # 要验证"不能放宽"，入参必须**超过全局上限**。
        lev, _ = self._run(25.0, decision={"max_leverage": 50.0}, hi=10.0)
        self.assertEqual(lev, 10.0, "单标的上限更宽时不得放宽全局上限（仍夹到全局 10）")

    def test_zero_upper_means_no_clamp_not_zero(self):
        """⚠️ `MAX_LEVERAGE or leverage`：配置为 0 = 不夹，**不是**夹成 0。"""
        lev, dec = self._run(7.0, lo=0.0, hi=0.0)
        self.assertEqual(lev, 7.0, "上限为 0 时必须退化为不夹")
        self.assertEqual(dec, {})

    def test_zero_lower_means_no_clamp(self):
        lev, _ = self._run(7.0, lo=0.0, hi=10.0)
        self.assertEqual(lev, 7.0)

    def test_zero_lower_does_not_drag_leverage_to_zero(self):
        """下限为 0（配置不可得）时杠杆必须原样保留。

        ⚠️ **如实记录一个"不可测"的注入**：负向验证把
        `min(max(leverage, float(lo or 0.0) or leverage), _upper)` 里的
        `or leverage` 去掉后，**没有任何测试能区分**——
        因为对 `leverage >= 0` 两式**恒等**：`max(lev, 0 or lev) == max(lev, 0) == lev`。
        两者只在 `leverage < 0` 时不同，而上游已校验"必须为有限正数"，
        负数永远到不了这里（见 `open_protected_position` 的 `for tag, v in ...` 循环）。

        故 `or leverage` 在两个方向上都是**冗余**的，但**保留**：
        它把"配置不可得 → 不夹"这一意图写在脸上，且万一上游校验被移除，
        `MIN_LEVERAGE=0` 不会把杠杆意外夹到 0。这是**契约**，不是可测行为。
        """
        lev, dec = self._run(7.0, lo=0.0, hi=0.0)
        self.assertEqual(lev, 7.0, "下限/上限都为 0（配置不可得）时必须原样保留")
        self.assertEqual(dec, {})

    def test_lower_bound_actually_applies(self):
        """下限非 0 时**必须**真的生效（否则上面的"不夹"断言毫无约束力）。"""
        lev, dec = self._run(1.0, lo=3.0, hi=10.0)
        self.assertEqual(lev, 3.0)
        self.assertEqual(dec["leverage"], 3.0)

    def test_none_bounds_do_not_crash(self):
        lev, _ = self._run(7.0, lo=None, hi=None)
        self.assertEqual(lev, 7.0, "常量不可得时必须退化为 no-op，绝不臆造区间")

    def test_garbage_max_leverage_falls_back_to_global(self):
        lev, _ = self._run(8.0, decision={"max_leverage": "abc"}, hi=10.0)
        self.assertEqual(lev, 8.0, "非法 max_leverage 应视为 0（不收紧）")

    def test_print_fires_only_when_clamped(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._run(25.0, hi=10.0)
        self.assertIn("[杠杆闸门]", buf.getvalue())
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            self._run(5.0, hi=10.0)
        self.assertEqual(buf2.getvalue(), "", "未夹取时不应打印")


# ------------------------------------------------------------ 保证金闸门


class ClampMarginTest(unittest.TestCase):
    def _run(self, margin, *, caller=None, absolute=None, pool=None):
        return clamp_margin(venue="gate", asset="BTC", decision={}, margin=margin,
                            max_margin_usdt=caller, max_single_asset_margin=absolute,
                            max_margin_equity_ratio=0.20, pool=pool)

    def test_within_all_caps_untouched(self):
        m, dec, frm = self._run(50.0, caller=500.0, absolute=600.0)
        self.assertEqual(m, 50.0)
        self.assertEqual(frm, 0.0, "未夹取时 from 应为 0")
        self.assertEqual(dec, {})

    def test_takes_min_of_all_caps(self):
        m, dec, frm = self._run(5000.0, caller=300.0, absolute=600.0)
        self.assertEqual(m, 300.0, "必须取三道上限的最小值")
        self.assertEqual(frm, 5000.0)
        self.assertEqual(dec["margin_usdt"], 300.0)

    def test_zero_caps_are_ignored_not_treated_as_cap(self):
        """⚠️ `0` = 该上限不可用，不参与求最小 —— 否则会把保证金夹成 0。"""
        m, _, _ = self._run(50.0, caller=0.0, absolute=0.0)
        self.assertEqual(m, 50.0, "上限全为 0 时必须不夹")

    def test_zero_cap_does_not_drag_down_a_real_cap(self):
        m, _, _ = self._run(5000.0, caller=0.0, absolute=600.0)
        self.assertEqual(m, 600.0, "0 不得参与 min()，否则结果会是 0")

    def test_pool_budget_is_third_cap(self):
        m, _, _ = self._run(5000.0, caller=0.0, absolute=600.0,
                            pool={"margin_per_trade_usdt": 120.0})
        self.assertEqual(m, 120.0, "该所预算必须并入门禁")

    def test_empty_pool_does_not_block(self):
        """空 dict = 池配置读不到 → 加固层不可用，不阻拦。"""
        m, _, _ = self._run(5000.0, caller=0.0, absolute=600.0, pool={})
        self.assertEqual(m, 600.0)

    def test_decision_max_margin_used_when_caller_absent(self):
        m, _, _ = clamp_margin(venue="gate", asset="BTC",
                               decision={"max_margin_usdt": 250.0}, margin=5000.0,
                               max_margin_usdt=None, max_single_asset_margin=600.0,
                               max_margin_equity_ratio=0.20, pool=None)
        self.assertEqual(m, 250.0)

    def test_non_finite_cap_ignored(self):
        m, _, _ = self._run(5000.0, caller=float("inf"), absolute=600.0)
        self.assertEqual(m, 600.0, "inf 必须被 isfinite 过滤")

    def test_print_fires_only_when_clamped(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._run(5000.0, caller=300.0, absolute=600.0)
        self.assertIn("[保证金闸门]", buf.getvalue())


# ------------------------------------------------------------ 敞口闸门


class ExposureRow:
    """同向 1 张 × 100000 = 100000U 的已有持仓。"""

    base = "BTC"
    side = "long"
    size_signed = 1.0
    mark_price = 100000.0


class CheckTotalExposureTest(unittest.TestCase):
    def _run(self, *, action="BUY_LONG", margin=200.0, leverage=5.0, cap=50000.0,
             positions=None, reader=None, exc=None):
        def default_reader():
            if exc:
                raise exc
            return [{"base": "BTC", "side": "long", "size_signed": 1.0,
                     "mark_price": 100000.0}]
        return check_total_exposure(
            venue="gate", asset="BTC", action=action, margin=margin, leverage=leverage,
            total_exposure_cap=cap, all_positions=positions,
            positions_reader=reader or default_reader, fail_factory=_fail)

    # —— 回归守卫：本刀修掉的大小写 bug ——

    def test_buy_long_matches_a_long_position(self):
        """⚠️ 回归守卫：`action` 是大写 `"BUY_LONG"`，`row_action` 是小写 `"buy"`。

        原实现直接比较两者 → 永不相等 → 闸门恒不触发（死代码）。
        """
        r = self._run(action="BUY_LONG", cap=50000.0)
        self.assertIsNotNone(r, "BUY_LONG 必须能匹配 long 持仓；返回 None 说明闸门又失效了")
        self.assertEqual(r["stage"], "exposure")

    def test_sell_short_matches_a_short_position(self):
        r = check_total_exposure(
            venue="gate", asset="BTC", action="SELL_SHORT", margin=200.0, leverage=5.0,
            total_exposure_cap=50000.0, all_positions=None,
            positions_reader=lambda: [{"base": "BTC", "side": "short",
                                       "size_signed": -1.0, "mark_price": 100000.0}],
            fail_factory=_fail)
        self.assertIsNotNone(r, "SELL_SHORT 必须能匹配 short 持仓")
        self.assertEqual(r["stage"], "exposure")

    def test_opposite_direction_is_not_counted(self):
        """反向持仓不计入同向敞口（否则会误拒）。"""
        r = self._run(action="BUY_LONG",
                      positions=[{"base": "BTC", "side": "short",
                                  "size_signed": -1.0, "mark_price": 100000.0}])
        self.assertIsNone(r, "反向持仓不得计入同向敞口")

    def test_other_asset_is_not_counted(self):
        r = self._run(action="BUY_LONG",
                      positions=[{"base": "ETH", "side": "long",
                                  "size_signed": 10.0, "mark_price": 3000.0}])
        self.assertIsNone(r)

    def test_under_cap_passes(self):
        r = self._run(action="BUY_LONG", cap=200000.0)
        self.assertIsNone(r)

    def test_zero_cap_means_unlimited(self):
        r = self._run(action="BUY_LONG", cap=0.0)
        self.assertIsNone(r, "0 必须表示不限制（与其余风控键语义一致）")

    def test_negative_cap_means_unlimited(self):
        r = self._run(action="BUY_LONG", cap=-1.0)
        self.assertIsNone(r)

    def test_reject_is_a_refusal_not_a_clamp(self):
        """⚠️ 超限是**拒**不是**夹** —— 静默缩量会让"为什么只开一半"无从解释。"""
        r = self._run(action="BUY_LONG", cap=50000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "exposure")
        self.assertIn("同向敞口", r["detail"], "理由必须说明是敞口")
        self.assertNotIn("margin_usdt", r, "拒开结果不得携带被缩量后的保证金")

    def test_reject_reports_projected_and_cap(self):
        """⚠️ 必须**精确**断言 projected —— 负向验证暴露的覆盖盲区。

        此前只写 `assertGreaterEqual(projected, cap)`：已有持仓 100000U 单独就
        超过上限 50000U，故即使实现漏掉 `+ margin * leverage`（本单名义）也照样
        通过。现在钉死 = 100000 + 200×5 = 101000。
        """
        r = self._run(action="BUY_LONG", cap=50000.0)
        self.assertEqual(r["cap"], 50000.0)
        self.assertEqual(r["projected_exposure"], 101000.0,
                         "预计敞口必须含**本单名义** margin×leverage")

    def test_position_below_cap_but_full_projection_exceeds_it(self):
        """本单名义是压垮骆驼的那一根草时也必须拒 —— 单看已有持仓并不超限。"""
        # 已有 100 张 × 100 = 10000U；本单 5000 × 5 = 25000U；上限 20000U。
        r = self._run(action="BUY_LONG", margin=5000.0, leverage=5.0, cap=20000.0,
                      positions=[{"base": "BTC", "side": "long",
                                  "size_signed": 100.0, "mark_price": 100.0}])
        self.assertIsNotNone(r, "只有加上本单名义才超限时，仍必须拒开")
        self.assertEqual(r["projected_exposure"], 35000.0)

    def test_position_just_under_cap_after_adding_this_order_passes(self):
        """边界：预计敞口**恰好不超**上限时应放行（`>` 而非 `>=`）。"""
        r = self._run(action="BUY_LONG", margin=200.0, leverage=5.0, cap=101000.0)
        self.assertIsNone(r, "恰好等于上限不构成超限")

    def test_positions_reader_failure_is_a_refusal_not_fail_open(self):
        """⚠️ 读不到持仓时**不得** fail-open（那等于闸门失效）。"""
        r = self._run(action="BUY_LONG", exc=RuntimeError("adapter down"))
        self.assertIsNotNone(r, "读持仓失败必须拒开")
        self.assertEqual(r["stage"], "exposure")
        self.assertIn("无法读取持仓", r["detail"])

    def test_preread_positions_are_reused(self):
        """传入 `all_positions` 时不应再调 reader。"""
        called = []

        def reader():
            called.append(1)
            return []

        r = check_total_exposure(
            venue="gate", asset="BTC", action="BUY_LONG", margin=200.0, leverage=5.0,
            total_exposure_cap=50000.0,
            all_positions=[{"base": "BTC", "side": "long", "size_signed": 1.0,
                            "mark_price": 100000.0}],
            positions_reader=reader, fail_factory=_fail)
        self.assertIsNotNone(r)
        self.assertEqual(called, [], "已有 positions 时不得再取一次")

    def test_uses_entry_price_when_mark_price_missing(self):
        r = self._run(action="BUY_LONG",
                      positions=[{"base": "BTC", "side": "long",
                                  "size_signed": 1.0, "entry_price": 100000.0}])
        self.assertIsNotNone(r, "mark_price 缺失时应退回 entry_price")


# ------------------------------------------------------- 门面接线与实盘性


class FacadeWiringTest(unittest.TestCase):
    def test_facade_calls_all_three_gates(self):
        src = FACADE.read_text(encoding="utf-8")
        for name in ("_clamp_leverage(", "_clamp_margin(", "_check_total_exposure("):
            self.assertIn(name, src, f"门面丢了 {name}")

    def test_facade_imports_are_aliased_to_avoid_shadowing(self):
        src = FACADE.read_text(encoding="utf-8")
        self.assertIn("from .execution.risk_gates import (", src)

    def test_facade_no_longer_inlines_the_gate_bodies(self):
        src = FACADE.read_text(encoding="utf-8")
        self.assertNotIn("_margin_caps = [c for c in _caps", src,
                         "门面仍内联着保证金夹取")
        self.assertNotIn("same_side = 0.0", src, "门面仍内联着敞口核算")

    def test_facade_still_reads_the_venue_pool(self):
        """池配置在夹取与准入判定之间共用，搬走后必须在门面留一次读取。"""
        src = FACADE.read_text(encoding="utf-8")
        self.assertIn("pool = _load_venue_pool_soft(venue)", src)

    def test_module_has_no_module_level_side_effects(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        bare = [n for n in tree.body
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
        self.assertEqual(bare, [], "模块层不应有裸调用")


class ProductionCapStillDisabledTest(unittest.TestCase):
    """⚠️ 本护栏**已失效过**（第一百一十一刀查明），现改为诚实版。

    旧版断言 `scripts.risk_constants.MAX_TOTAL_EXPOSURE_USDT == 0.0`，并自称
    "若有人将来配置了它，这条会翻红"。但 pytest 做了**环境隔离**（`tests/__init__.py`），
    于是它读到的永远是 0.0 —— **看不见生产 `.env`**，而生产 `.env` 里
    `R20_MAX_TOTAL_EXPOSURE_USDT=3000.0`（`scripts/risk_constants.py` 在 cron/手动路径
    显式加载 `.env`，本机实测 `TOTAL_EXPOSURE_CAP == 3000.0`）。护栏"安全通过"，
    闸门却早已生效——而且是**只算一所的"跨所"闸门**（已由
    `tests/audit/test_cross_venue_exposure_gate.py` 修正语义并钉住）。
    """

    def test_isolated_env_still_reads_zero_here(self):
        """如实记录本环境事实：pytest 内该常量恒 0（≠ 生产事实）。"""
        import scripts.risk_constants as rc
        self.assertEqual(float(getattr(rc, "MAX_TOTAL_EXPOSURE_USDT", 0.0) or 0.0), 0.0)

    def test_production_env_cap_is_read_from_the_file_not_the_isolated_env(self):
        """诚实护栏：**直接读 `.env`** 才能看到生产真值，并要求闸门语义与之匹配。"""
        from pathlib import Path
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if not env_file.exists():
            self.skipTest("无 .env（干净检出）")
        cap = 0.0
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("R20_MAX_TOTAL_EXPOSURE_USDT="):
                try:
                    cap = float(line.split("=", 1)[1].strip().strip('"').strip("'"))
                except ValueError:
                    cap = 0.0
        if cap:
            src = (Path(__file__).resolve().parents[2] / "r20_backend" /
                   "execution_router.py").read_text(encoding="utf-8")
            self.assertIn("_exposure_venues(", src,
                          f"生产已配置上限 {cap}U ⇒ 闸门生效，必须跨所口径")


if __name__ == "__main__":
    unittest.main()
