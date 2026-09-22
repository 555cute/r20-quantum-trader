"""三个小模块的残余分支收口 —— 第 323 刀。

一刀清三个模块的尾巴，共 18 行：

| 模块 | 未命中 | 性质 |
|---|---|---|
| `scripts/order_risk.py` | 8 → 0（含 1 行死代码）| 报价几何 / 盈亏比硬闸 |
| `scripts/risk_constants.py` | 5 → 0 | 风控常量的**导入期**解析与交叉守卫 |
| `scripts/generate_snapshots.py` | 5 → 0 | 净值快照生成与 fail-closed CLI |

## `risk_constants` 的两行是**导入期**代码

第 62 行（`MIN_LEVERAGE = MAX_LEVERAGE`）与第 72 行
（`MAX_RISK_REWARD_RATIO = MIN_RISK_REWARD_RATIO`）是模块顶层的交叉守卫，
只在**特定环境变量组合下**才会执行 ⇒ 必须在受控命名空间里重新 `exec` 源码才能覆盖。
第 29 行（`load_dotenv` 导入失败）同理。
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.generate_snapshots as gs  # noqa: E402
import scripts.order_risk as order_risk  # noqa: E402
import scripts.risk_constants as rc  # noqa: E402


# ───────────────────────── order_risk ─────────────────────────
class QuoteGeometryTests(unittest.TestCase):
    def _check(self, action, entry, tp, sl, **kw):
        return order_risk.validate_quote_geometry_and_rr(action, entry, tp, sl, **kw)

    def test_an_unsupported_action_is_refused(self):
        # ★ 第 24 行
        for action in ("", None, "HOLD", "buy", "SELL_LONG"):
            with self.subTest(action=action):
                ok, reason, rr = self._check(action, 100, 110, 95)
                self.assertFalse(ok)
                self.assertIn("不支持的开仓方向", reason)
                self.assertEqual(rr, 0.0)

    def test_non_numeric_prices_are_refused(self):
        # ★ 第 31 行
        for bad in ("abc", None, {}, []):
            with self.subTest(bad=bad):
                ok, reason, rr = self._check("BUY_LONG", bad, 110, 95)
                self.assertFalse(ok)
                self.assertIn("必须是有效数字", reason)
                self.assertEqual(rr, 0.0)

    def test_nan_and_inf_are_refused(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                ok, reason, _ = self._check("BUY_LONG", 100, bad, 95)
                self.assertFalse(ok)
                self.assertIn("有限数值", reason)

    def test_non_positive_prices_are_refused(self):
        # ★ 第 37 行
        for entry, tp, sl in ((0, 110, 95), (100, 0, 95), (100, 110, 0),
                              (-1, 110, 95)):
            with self.subTest(entry=entry, tp=tp, sl=sl):
                ok, reason, _ = self._check("BUY_LONG", entry, tp, sl)
                self.assertFalse(ok)
                self.assertIn("必须大于 0", reason)

    def test_a_valid_long_passes_and_reports_the_ratio(self):
        ok, reason, rr = self._check("BUY_LONG", 100, 110, 95)
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        self.assertAlmostEqual(rr, 10 / 5)

    def test_a_valid_short_passes_and_reports_the_ratio(self):
        ok, _, rr = self._check("SELL_SHORT", 100, 90, 105)
        self.assertTrue(ok)
        self.assertAlmostEqual(rr, 10 / 5)

    def test_bad_long_geometry_is_refused_with_the_numbers(self):
        ok, reason, _ = self._check("BUY_LONG", 100, 90, 95)
        self.assertFalse(ok)
        self.assertIn("买多几何不合法", reason)
        self.assertIn("95", reason)

    def test_bad_short_geometry_is_refused(self):
        ok, reason, _ = self._check("SELL_SHORT", 100, 110, 105)
        self.assertFalse(ok)
        self.assertIn("卖空几何不合法", reason)

    def test_the_floor_is_enforced(self):
        ok, reason, rr = self._check("BUY_LONG", 100, 101, 99)
        self.assertFalse(ok)
        self.assertIn("盈亏比不足", reason)
        self.assertLess(rr, rc.MIN_RISK_REWARD_RATIO)

    def test_the_result_ratio_is_returned_even_when_rejected_for_low_rr(self):
        # (100, 101, 99)：reward 1 / risk 1 = 1.0 ⇒ 低于底线 2.0 被拒，但仍回传真实比值
        _, _, rr = self._check("BUY_LONG", 100, 101, 99)
        self.assertAlmostEqual(rr, 1.0)

    def test_an_overflowing_ratio_is_refused(self):
        # ★ 第 55 行 —— 极值下 `reward / risk` 溢出为 inf ⇒ 必须拒（不是放行）
        ok, reason, rr = self._check("BUY_LONG", 1e-323, 1.7e308, 5e-324)
        self.assertFalse(ok)
        self.assertIn("盈亏比计算异常", reason)
        self.assertEqual(rr, 0.0)

    def test_a_ratio_just_above_the_floor_passes(self):
        # floor 2.0（出厂）⇒ 2.0/1.0 恰好等于底线的**边界**要放行
        ok, _, rr = self._check("BUY_LONG", 100, 102, 99)
        self.assertTrue(ok, f"rr={rr}")
        self.assertAlmostEqual(rr, 2.0)

    def test_the_max_rr_gate_is_off_by_default(self):
        ok, _, _ = self._check("BUY_LONG", 100, 1000, 99)
        self.assertTrue(ok, "不传 enforce_max_rr 时不许拿上限拒单")

    def test_the_max_rr_gate_is_enforced_when_requested(self):
        # ★ 第 61/62/63 行
        ok, reason, rr = self._check("BUY_LONG", 100, 1000, 99, enforce_max_rr=True)
        self.assertFalse(ok)
        self.assertIn("盈亏比超出上限", reason)
        self.assertIn("止盈过远拒单", reason)
        self.assertGreater(rr, rc.MAX_RISK_REWARD_RATIO)

    def test_a_ratio_at_the_max_boundary_passes(self):
        # 容差 1e-4：恰好等于上限时放行
        top = rc.MAX_RISK_REWARD_RATIO
        ok, _, rr = self._check("BUY_LONG", 100, 100 + top * 10, 90,
                                enforce_max_rr=True)
        self.assertTrue(ok, f"rr={rr} 上限={top}")

    def test_a_zero_max_rr_means_no_ceiling(self):
        # `_cur_max_rr > 0` 判据 ⇒ 配置成 0 等于关掉上限
        with patch.object(order_risk, "MAX_RISK_REWARD_RATIO", 0.0):
            ok, _, _ = self._check("BUY_LONG", 100, 1000, 99, enforce_max_rr=True)
        self.assertTrue(ok)

    def test_a_non_numeric_max_rr_crashes_the_validator(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 61 行是
        #    `float(MAX_RISK_REWARD_RATIO or 0.0)` —— 没有 try/except ⇒
        #    配置成非数值字符串时 `ValueError` 会**冒出**风控校验函数，
        #    而不是像同文件其它回落点那样退化为"不限上限"。
        #    （对比：`risk_constants._env_float` 会回落；这里直接用全局名。）
        with patch.object(order_risk, "MAX_RISK_REWARD_RATIO", "junk"):
            with self.assertRaises(ValueError):
                self._check("BUY_LONG", 100, 1000, 99, enforce_max_rr=True)

    def test_the_negative_risk_guard_is_dead_code(self):
        # ⚠️ 实测（本刀已机器验证）：第 50–51 行的 `if risk <= 0` **不可达** ——
        #    IEEE754 下 `s < e` 蕴含 `e - s > 0`（同理 `t > e` 蕴含 `t - e > 0`），
        #    而前面第 40/45 行的几何检查已经保证了这两个不等式。
        #    穷举 10 个量级的两两组合，`s < e` 但 `e - s <= 0` 出现了 **0 次**。
        #    保留为防御性代码是合理的，但它是**死代码**。
        vals = [5e-324, 1e-320, 1e-308, 1e-300, 1e-10, 1.0, 1e10, 1e300, 1e307, 1.7e308]
        violated = [(s, e) for s in vals for e in vals if s < e and not (e - s) > 0]
        self.assertEqual(violated, [], "若出现违反，说明第 51 行可达，结论需修正")

    def test_the_geometry_checks_make_the_risk_guard_unnecessary(self):
        # 行为证据：任何能通过几何检查的报价，其 risk 都严格为正
        for entry, tp, sl in ((100, 110, 95), (100, 102, 99.999), (1e-323, 1e10, 5e-324)):
            with self.subTest(entry=entry, tp=tp, sl=sl):
                if sl < entry < tp:
                    self.assertGreater(entry - sl, 0)


# ───────────────────────── risk_constants ─────────────────────────
class EnvParsingTests(unittest.TestCase):
    def test_a_float_is_parsed(self):
        with patch.dict(os.environ, {"R20_T": "2.5"}):
            self.assertEqual(rc._env_float("R20_T", 9.9), 2.5)

    def test_a_missing_float_uses_the_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("R20_T", None)
            self.assertEqual(rc._env_float("R20_T", 9.9), 9.9)

    def test_an_empty_float_uses_the_default(self):
        with patch.dict(os.environ, {"R20_T": ""}):
            self.assertEqual(rc._env_float("R20_T", 9.9), 9.9)

    def test_an_unparsable_float_uses_the_default(self):
        # ★ 第 36 行
        for bad in ("abc", "1,5", "--", "null"):
            with self.subTest(bad=bad):
                with patch.dict(os.environ, {"R20_T": bad}):
                    self.assertEqual(rc._env_float("R20_T", 9.9), 9.9)

    def test_an_int_is_parsed(self):
        with patch.dict(os.environ, {"R20_T": "7"}):
            self.assertEqual(rc._env_int("R20_T", 9), 7)

    def test_a_float_string_is_truncated_to_int(self):
        with patch.dict(os.environ, {"R20_T": "7.9"}):
            self.assertEqual(rc._env_int("R20_T", 9), 7)

    def test_a_missing_int_uses_the_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("R20_T", None)
            self.assertEqual(rc._env_int("R20_T", 9), 9)

    def test_an_unparsable_int_uses_the_default(self):
        # ★ 第 43 行
        for bad in ("abc", "1,5", ""):
            with self.subTest(bad=bad):
                with patch.dict(os.environ, {"R20_T": bad}):
                    self.assertEqual(rc._env_int("R20_T", 9), 9)

    def test_the_int_parser_also_accepts_scientific_notation(self):
        with patch.dict(os.environ, {"R20_T": "1e2"}):
            self.assertEqual(rc._env_int("R20_T", 9), 100)


class ImportTimeGuardTests(unittest.TestCase):
    """三行**导入期**代码：`load_dotenv` 失败（29）、杠杆交叉守卫（62）、`R:R` 交叉守卫（72）。"""

    def _reexec(self, env):
        import types
        src = Path(rc.__file__).read_text(encoding="utf-8")
        name = "risk_constants_probe"
        probe = types.ModuleType(name)
        probe.__file__ = rc.__file__
        # 模块自身会 `sys.modules.setdefault(_alias, sys.modules[__name__])`
        # ⇒ 必须**先把探针注册进 sys.modules**，否则 KeyError
        sys.modules[name] = probe
        self.addCleanup(sys.modules.pop, name, None)
        clean = {k: v for k, v in os.environ.items() if not k.startswith("R20_")}
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            exec(compile(src, rc.__file__, "exec"), probe.__dict__)  # noqa: S102
        return probe.__dict__

    def test_an_unimportable_load_dotenv_is_swallowed(self):
        # ★ 第 29 行 —— 后端不在路径时不许让整个常量模块导入失败
        with patch.dict(sys.modules, {"r20_backend.config": None}):
            ns = self._reexec({})
        self.assertIn("MAX_LEVERAGE", ns)

    def test_the_leverage_cross_guard_clamps_the_floor(self):
        # ★ 第 62 行 —— 下限越过上限时把**下限**压到上限（更保守）
        ns = self._reexec({"R20_MIN_LEVERAGE": "9", "R20_MAX_LEVERAGE": "5"})
        self.assertEqual(ns["MAX_LEVERAGE"], 5.0)
        self.assertEqual(ns["MIN_LEVERAGE"], 5.0)

    def test_a_normal_leverage_pair_is_left_alone(self):
        ns = self._reexec({"R20_MIN_LEVERAGE": "2", "R20_MAX_LEVERAGE": "6"})
        self.assertEqual((ns["MIN_LEVERAGE"], ns["MAX_LEVERAGE"]), (2.0, 6.0))

    def test_the_rr_cross_guard_clamps_the_ceiling(self):
        # ★ 第 72 行 —— 上限低于下限时把**上限**抬到下限（否则 R:R 闸门自相矛盾）
        ns = self._reexec({"R20_MIN_RISK_REWARD": "4", "R20_MAX_RISK_REWARD": "1.5"})
        self.assertEqual(ns["MIN_RISK_REWARD_RATIO"], 4.0)
        self.assertEqual(ns["MAX_RISK_REWARD_RATIO"], 4.0)

    def test_a_normal_rr_pair_is_left_alone(self):
        ns = self._reexec({"R20_MIN_RISK_REWARD": "2", "R20_MAX_RISK_REWARD": "3.5"})
        self.assertEqual((ns["MIN_RISK_REWARD_RATIO"], ns["MAX_RISK_REWARD_RATIO"]),
                         (2.0, 3.5))

    def test_an_equal_pair_is_not_guarded(self):
        # `<` 而非 `<=` ⇒ 相等时不触发
        ns = self._reexec({"R20_MIN_RISK_REWARD": "3", "R20_MAX_RISK_REWARD": "3"})
        self.assertEqual((ns["MIN_RISK_REWARD_RATIO"], ns["MAX_RISK_REWARD_RATIO"]),
                         (3.0, 3.0))


class LiveConstantSanityTests(unittest.TestCase):
    def test_the_live_leverage_floor_never_exceeds_the_ceiling(self):
        self.assertLessEqual(rc.MIN_LEVERAGE, rc.MAX_LEVERAGE)

    def test_the_live_rr_ceiling_is_at_least_the_floor(self):
        self.assertGreaterEqual(rc.MAX_RISK_REWARD_RATIO, rc.MIN_RISK_REWARD_RATIO)

    def test_the_live_floor_is_positive(self):
        self.assertGreater(rc.MIN_RISK_REWARD_RATIO, 0)


# ───────────────────────── generate_snapshots ─────────────────────────
class _SnapSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snapshots = self.root / "snapshots.json"
        for name, value in (("SNAPSHOTS_FILE", str(self.snapshots)),
                            ("ACCOUNT_INIT_FILE", str(self.root / "account_initial_state.json"))):
            p = patch.object(gs, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.printed: list = []
        pr = patch.object(gs, "print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
        pr.start()
        self.addCleanup(pr.stop)

    def _env(self, configured=True, mode="live"):
        from types import SimpleNamespace
        return SimpleNamespace(configured=configured, mode=mode, fingerprint="FP",
                               simulated=False)

    def _run(self, *, bills=None, balances=None, configured=True, init=None,
             balances_exc=None):
        if init is not None:
            Path(gs.ACCOUNT_INIT_FILE).write_text(json.dumps(init), encoding="utf-8")

        def _bills(limit=100):
            return list(bills or [])

        def _balances():
            if balances_exc is not None:
                raise balances_exc
            return balances if balances is not None else []

        with patch.object(gs.okx_runtime, "current_environment",
                          lambda: self._env(configured)), \
             patch.object(gs.okx_rest, "bills", _bills), \
             patch.object(gs.okx_rest, "balances", _balances):
            gs.generate_live_snapshots()
        return json.loads(self.snapshots.read_text(encoding="utf-8"))

    def _bill(self, when, bal_chg):
        import datetime
        tz = datetime.timezone(datetime.timedelta(hours=8))
        ts = int(datetime.datetime.strptime(when, "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=tz).timestamp() * 1000)
        return {"ts": str(ts), "balChg": str(bal_chg)}


class SnapshotGenerationTests(_SnapSandbox, unittest.TestCase):
    def test_unconfigured_keys_raise_before_touching_the_file(self):
        with self.assertRaises(gs.okx_rest.OKXNotConfigured):
            self._run(configured=False)
        self.assertFalse(self.snapshots.exists(), "fail-closed：不许清空快照文件")

    def test_a_minimal_run_writes_the_anchor_and_the_current_point(self):
        rows = self._run()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["total_eq"], 10000.0)
        self.assertEqual(rows[0]["pnl"], 0.0)
        self.assertEqual(rows[-1]["pnl"], 0.0)

    def test_bills_before_the_reset_time_are_skipped(self):
        # ★ 第 67 行
        rows = self._run(bills=[self._bill("2026-08-01 00:00:00", 100.0),
                                self._bill("2026-09-01 00:00:00", 50.0)])
        self.assertEqual(len(rows), 3, "锚点 + 1 条 reset 后的 + 当前点")
        self.assertEqual(rows[1]["pnl"], 50.0)

    def test_bills_are_walked_chronologically(self):
        rows = self._run(bills=[self._bill("2026-09-03 00:00:00", 10.0),
                                self._bill("2026-09-02 00:00:00", 5.0)])
        points = rows[1:-1]
        self.assertEqual([p["time"] for p in points],
                         ["2026-09-02 00:00:00", "2026-09-03 00:00:00"])
        self.assertEqual([p["pnl"] for p in points], [5.0, 15.0])

    def test_roi_is_a_percentage_of_the_initial_capital(self):
        rows = self._run(bills=[self._bill("2026-09-01 00:00:00", 200.0)])
        self.assertEqual(rows[1]["roi"], 2.0)

    def test_the_reset_time_comes_from_the_account_init_file(self):
        rows = self._run(init={"reset_time": "2026-09-10 00:00:00",
                               "initial_capital": 5000.0},
                         bills=[self._bill("2026-09-05 00:00:00", 999.0),
                                self._bill("2026-09-11 00:00:00", 100.0)])
        self.assertEqual(rows[0]["time"], "2026-09-10 00:00:00")
        self.assertEqual(rows[0]["total_eq"], 5000.0)
        self.assertEqual(rows[1]["pnl"], 100.0, "reset 之前那条要被跳过")

    def test_a_corrupt_account_init_file_falls_back_to_defaults(self):
        # ★ 第 30 行
        Path(gs.ACCOUNT_INIT_FILE).write_text("{ broken", encoding="utf-8")
        rows = self._run()
        self.assertEqual(rows[0]["total_eq"], 10000.0)

    def test_the_live_equity_overrides_the_initial_capital_on_the_last_point(self):
        rows = self._run(balances=[{"details": [{"ccy": "USDT", "eq": "12345.67"}]}])
        self.assertEqual(rows[-1]["total_eq"], 12345.67)
        self.assertEqual(rows[-1]["pnl"], 2345.67)

    def test_a_balance_fetch_failure_falls_back_to_the_initial_capital(self):
        # ★ 第 46 行
        rows = self._run(balances_exc=RuntimeError("网络不通"))
        self.assertEqual(rows[-1]["total_eq"], 10000.0)

    def test_a_balance_row_without_usdt_keeps_the_initial_capital(self):
        rows = self._run(balances=[{"details": [{"ccy": "BTC", "eq": "1"}]}])
        self.assertEqual(rows[-1]["total_eq"], 10000.0)

    def test_empty_balance_rows_are_tolerated(self):
        rows = self._run(balances=[])
        self.assertEqual(rows[-1]["total_eq"], 10000.0)

    def test_a_bill_without_bal_change_is_treated_as_zero(self):
        rows = self._run(bills=[{"ts": self._bill("2026-09-01 00:00:00", 0)["ts"]}])
        self.assertEqual(rows[1]["pnl"], 0.0)

    def test_the_written_file_is_plain_utf8_json(self):
        self._run()
        raw = self.snapshots.read_text(encoding="utf-8")
        self.assertIn("total_eq", raw)
        self.assertIn("\n", raw, "带缩进")

    def test_the_success_line_reports_the_point_count(self):
        self._run()
        self.assertTrue(any("Generated 2 clean snapshots" in line
                            for line in self.printed), self.printed)


class SnapshotCliTests(_SnapSandbox, unittest.TestCase):
    """`if __name__ == "__main__"` 那段守卫（第 98 / 102 行）。

    ## ⚠️ 为什么必须把脚本**复制到临时树**里跑

    `runpy.run_path(gs.__file__)` 会**重新执行模块源码** ⇒ 产生一个**全新的模块对象**，
    它对 `SNAPSHOTS_FILE` / `ACCOUNT_INIT_FILE` 的取值来自它自己的 `__file__`。
    对本进程里那个已导入的 `gs` 模块打补丁**对那份新副本无效**。

    本刀实测到过这个后果：**测试直接把生产 `data/snapshots.json` 覆盖成 2 个点**。
    隔离方式是复制一份脚本到 `<tmp>/scripts/`，于是它的 `_REPO_ROOT` = `<tmp>`、
    `DATA_DIR` = `<tmp>/data`，写入天然落进沙箱。
    （`scripts.okx_rest` / `scripts.okx_runtime` 仍从 `sys.modules` 复用，
    所以对它们的补丁照常生效。）
    """

    def _stage_copy(self):
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        copy = scripts / "generate_snapshots.py"
        copy.write_text(Path(gs.__file__).read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        # 副本会在自己的模块头部 `sys.path.insert(0, _REPO_ROOT)` ⇒ 把临时根留在
        # `sys.path` 里（泄漏自检实测到）。用完必须摘掉。
        staged = str(self.root)
        if staged not in sys.path:
            self.addCleanup(sys.path.remove, staged)
        return copy

    def test_the_staged_copy_writes_into_the_sandbox_not_production(self):
        import runpy
        copy = self._stage_copy()
        with patch.object(gs.okx_runtime, "current_environment",
                          lambda: self._env(True)), \
             patch.object(gs.okx_rest, "bills", lambda limit=100: []), \
             patch.object(gs.okx_rest, "balances", lambda: []):
            runpy.run_path(str(copy), run_name="__main__")
        sandboxed = self.root / "data" / "snapshots.json"
        self.assertTrue(sandboxed.exists(), "必须写进沙箱")
        rows = json.loads(sandboxed.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 2)

    def test_the_main_guard_exits_three_when_keys_are_missing(self):
        # ★ 第 98/102 行
        import runpy
        copy = self._stage_copy()
        with patch.object(gs.okx_runtime, "current_environment",
                          lambda: self._env(False)), \
             patch.object(gs.okx_rest, "bills", lambda limit=100: []):
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_path(str(copy), run_name="__main__")
        self.assertEqual(ctx.exception.code, 3)
        self.assertFalse((self.root / "data" / "snapshots.json").exists(),
                         "fail-closed：不许产出文件")

    def test_the_main_guard_succeeds_with_credentials(self):
        import runpy
        copy = self._stage_copy()
        with patch.object(gs.okx_runtime, "current_environment",
                          lambda: self._env(True)), \
             patch.object(gs.okx_rest, "bills", lambda limit=100: []), \
             patch.object(gs.okx_rest, "balances", lambda: []):
            runpy.run_path(str(copy), run_name="__main__")
        self.assertTrue((self.root / "data" / "snapshots.json").exists())

    def test_the_main_guard_modules_the_not_ready_banner(self):
        # `[NOT READY]` 前缀是运维脚本的既定识别串
        src = Path(gs.__file__).read_text(encoding="utf-8")
        self.assertIn('print(f"[NOT READY] {exc}")', src)
        self.assertIn("raise SystemExit(3)", src)


if __name__ == "__main__":
    unittest.main()
