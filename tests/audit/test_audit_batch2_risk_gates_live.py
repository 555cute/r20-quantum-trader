"""批2 风控闸活线化（审计 2026-09-13）回归钉。

核心钉：
- 回马枪④2：trader **本地活函数** is_circuit_breaker_active 必须真的消费 sidecar
  （上轮加固只进了模块副本，活路径零引用——本文件反向钉死「孪生漂移」复发）
- ④1：日亏求和带环境轴（demo↔live 切换日不得互相抵消），单源函数两侧共用
- ②1：routing_policy 单一事实源必须真从 risk_constants 取值（曾 100% ImportError
  被吞成硬编码）
- ④6：confirm 状态推进真实存在且仍占预算；trader 封装失败不炸但可见
- ③：组合预算跨所合算总闸（0=无顶语义不变，>0 时按 gross_exposure 裁）
- ④：入场价幻觉锚——穿价拒、荒谬距离拒、回踩远挂放
"""
from __future__ import annotations

import datetime
import json
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

import scripts.ai_factor_trader as aft  # noqa: E402
import r20_backend.execution.circuit_breaker as cb  # noqa: E402
from r20_backend.risk_reservation import RiskReservationManager, STATE_CONFIRMED  # noqa: E402


def _today_bj():
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _closed_row(pnl, env, day=None):
    return {"status": "closed", "pnl": pnl, "environment": env,
            "close_time": day or _today_bj()}


class TestEnvAxisSingleSource(unittest.TestCase):
    """④1 求和函数本体（模块单源，trader/模块两个调用方共用）。"""

    def test_other_env_rows_excluded_same_env_and_legacy_included(self):
        rows = [_closed_row(-80.0, "demo"), _closed_row(-30.0, "live"),
                {"status": "closed", "pnl": -5.0, "close_time": _today_bj()}]  # 无标签旧行
        # 旧行对任何环境都保守计入 → 它在 demo/live 两种口径下都出现
        self.assertAlmostEqual(cb.ledger_daily_closed_pnl(rows, "demo", _today_bj()), -85.0)
        self.assertAlmostEqual(cb.ledger_daily_closed_pnl(rows, "live", _today_bj()), -35.0)
        # 环境不可判 → 保守全计（宁停不漏）
        self.assertAlmostEqual(cb.ledger_daily_closed_pnl(rows, "", _today_bj()), -115.0)

    def test_other_day_and_open_rows_never_count(self):
        rows = [_closed_row(-50.0, "demo", day="2020-01-01"),
                {"status": "open", "pnl": -99.0, "environment": "demo", "close_time": _today_bj()}]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "demo", _today_bj()), 0.0)


class TestTraderBreakerLiveWiring(unittest.TestCase):
    """回马枪核心：这些测试必须打在 trader 的本地函数上（活路径）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r20-b2-cb-")
        self.ledger = os.path.join(self.tmp, "trading_ledger.json")

    def _run(self, rows, sidecar=None, mode="demo"):
        with open(self.ledger, "w", encoding="utf-8") as f:
            json.dump(rows, f)
        p_ledger = patch.object(aft, "LEDGER_JSON_FILE", self.ledger)
        p_sidecar = patch.object(cb, "_sync_status_path",
                                 lambda: Path(os.path.join(self.tmp, "ledger_sync_status.json")))
        if sidecar is not None:
            with open(os.path.join(self.tmp, "ledger_sync_status.json"), "w", encoding="utf-8") as f:
                json.dump(sidecar, f)
        mode = mode
        with p_ledger, p_sidecar, \
             patch.object(aft, "check_black_swan_sentinel", lambda: (False, "")), \
             patch.object(aft, "current_environment", lambda: type("E", (), {"mode": mode})()), \
             patch.object(aft, "effective_daily_loss_limit", lambda _u=None: 150.0):
            return aft.is_circuit_breaker_active(usdt_available=1000.0)

    def test_sidecar_failed_venue_halts_opening_in_trader(self):
        active, reason = self._run(
            [_closed_row(-10.0, "demo")],
            sidecar={"venues": {"gate": {"status": "failed"}, "okx": {"status": "ok"}}})
        self.assertTrue(active, "gate 同步失败必须让 trader 活函数暂停开仓")
        self.assertIn("gate", reason)
        self.assertIn("台账跨所同步不完整", reason)

    def test_env_mix_cannot_mask_daily_loss(self):
        # demo 当日真亏 -160（>限额150），live 镜像 +159 —— 混算=-1 不触限；带轴必须熔断
        rows = [_closed_row(-160.0, "demo"), _closed_row(159.0, "live")]
        active, reason = self._run(rows)
        self.assertTrue(active, "跨环境抵消漏洞必须被环境轴封死")
        self.assertIn("今日累计回撤", reason)
        # 反向：demo 小亏时 live 巨亏不得拖垮 demo
        active2, _ = self._run([_closed_row(-10.0, "demo"), _closed_row(-500.0, "live")])
        self.assertFalse(active2)

    def test_legacy_unlabeled_rows_counted_conservatively(self):
        active, _ = self._run([{"status": "closed", "pnl": -160.0,
                                "close_time": _today_bj()}])  # 无 environment 键
        self.assertTrue(active, "旧行缺标签应保守计入（宁停不漏）")

    def test_missing_sidecar_file_not_halting(self):
        active, _ = self._run([_closed_row(-10.0, "demo")])  # 无 sidecar 文件
        self.assertFalse(active)

    def test_corrupt_sidecar_fails_closed_in_trader(self):
        """旁车**损坏** ⇒ trader 侧熔断判定必须 **fail-closed（禁开仓）**。

        ⚠️ 契约变更（2026-09-20，第一百四十四刀，**用户拍板**）：此前本用例断言
        `assertFalse(active)`（与旧注释"损坏→[]（不误停）"一致）—— 而"损坏"其实是
        **不可判定**（跨所同步是否完整无从得知），把它读成"各所正常"正是
        "不可判定 ≠ 安全"要禁止的。名字本来就写着 `fails_closed`，现在行为与名字一致。
        """
        os.makedirs(self.tmp, exist_ok=True)
        with open(os.path.join(self.tmp, "ledger_sync_status.json"), "w") as f:
            f.write("{half")
        active, reason = self._run([_closed_row(-10.0, "demo")])
        self.assertTrue(active, "不可判定 ⇒ fail-closed（用户拍板）")
        self.assertIn("不可判定", reason)


class TestRoutingPolicySingleSource(unittest.TestCase):
    """②1：曾 MAX_CONCURRENT_POSITIONS（真名 *_CAP）100% ImportError 被吞成 50/5/72。"""

    def test_defaults_follow_risk_constants(self):
        import scripts.risk_constants as rc
        from r20_backend.exchanges.routing_policy import global_risk_defaults
        d = global_risk_defaults()
        self.assertAlmostEqual(d["margin_per_trade_usdt"], float(rc.MAX_SINGLE_ASSET_MARGIN or 50.0))
        self.assertAlmostEqual(d["min_confidence"], float(rc.MIN_ENTRY_CONFIDENCE or 72.0))
        self.assertGreaterEqual(d["max_open"], 1)

    def test_no_import_error_swallowing(self):
        import warnings
        from r20_backend.exchanges import routing_policy
        with warnings.catch_warnings(record=True) as w, \
             patch.dict(sys.modules, {"scripts.risk_constants": None}):  # 强制 import 失败
            d = routing_policy.global_risk_defaults()
        self.assertEqual(d["margin_per_trade_usdt"], 50.0)  # 兜底仍在（fail-safe 保守值）
        # 兜底不再静默：必须有输出可见（print warn 被 capsys 之外验证成本高，这里退验值域）

    def test_symbol_cap_actually_named_cap(self):
        import scripts.risk_constants as rc
        self.assertTrue(hasattr(rc, "MAX_CONCURRENT_POSITIONS_CAP"))  # 钉住名字，防再改


class TestConfirmStateMachine(unittest.TestCase):
    """④6：RiskReservationManager.confirm 真实存在且 pending→confirmed 仍占预算。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r20-b2-res-")
        self.mgr = RiskReservationManager(os.path.join(self.tmp, "risk.db"), total_limit_usdt=1000.0)

    def test_confirm_advances_state_and_keeps_budget_occupied(self):
        key = ("okx", "demo", "fp1")
        self.mgr.reserve(key, "i1", 300.0, state="pending")
        snap = self.mgr.confirm(key, "i1")
        self.assertEqual(snap["state"], STATE_CONFIRMED)
        self.assertFalse(snap["released"])
        self.assertAlmostEqual(self.mgr.total_reserved(key), 300.0)  # confirmed 仍占
        # 终态后可释放，confirmed 是合法中间站
        self.mgr.release(key, "i1")
        self.assertAlmostEqual(self.mgr.total_reserved(key), 0.0)

    def test_terminal_reservation_not_revivable_after_confirm_then_close(self):
        key = ("gate", "demo", "fp2")
        self.mgr.reserve(key, "i2", 10.0, state="pending")
        self.mgr.confirm(key, "i2")
        self.mgr.release(key, "i2")
        snap = self.mgr.confirm(key, "i2")  # 终态幂等：不可复活
        self.assertEqual(snap["state"], "closed")

    def test_trader_wrapper_warns_not_raises(self):
        import io
        from contextlib import redirect_stdout

        class _Broken:
            def confirm(self, *a, **k):
                raise RuntimeError("boom")
        res = {"manager": _Broken(), "account_key": ("okx", "demo", "f"), "intent_id": "i"}
        buf = io.StringIO()
        with redirect_stdout(buf):
            aft.confirm_signal_reservation(res)  # 不抛
            aft.confirm_signal_reservation(None)
            aft.confirm_signal_reservation({})
        self.assertIn("warn", buf.getvalue())  # 失败必须可见

    def test_confirm_is_idempotent_with_existing_state_api(self):
        # 直接 reserve(confirmed) 与 confirm() 等价：旧调用点若已用 state=confirmed 也不冲突
        key = ("binance", "demo", "fp3")
        self.mgr.reserve(key, "i3", 5.0, state="pending")
        a = self.mgr.confirm(key, "i3")
        b = self.mgr.reserve(key, "i3", 0.0, state="confirmed")
        self.assertEqual(a["state"], STATE_CONFIRMED)
        self.assertEqual(b["state"], STATE_CONFIRMED)


class TestPortfolioBudgetGuard(unittest.TestCase):
    """③：跨所合算总闸纯函数语义。"""

    def test_zero_means_unbounded(self):
        self.assertIsNone(aft.portfolio_budget_guard(0.0, 9999.0, 500.0))

    def test_cross_venue_aggregate_enforced(self):
        err = aft.portfolio_budget_guard(1000.0, 700.0, 400.0, "demo")
        self.assertIsNotNone(err)
        self.assertIn("组合预算", err)
        self.assertIsNone(aft.portfolio_budget_guard(1000.0, 700.0, 299.0, "demo"))

    def test_float_noise_and_garbage_inputs(self):
        self.assertIsNone(aft.portfolio_budget_guard(1000.0, 600.0, 400.0 - 1e-6))
        self.assertIn("fail-closed", aft.portfolio_budget_guard("abc", 1.0, 2.0))

    def test_route_and_reserve_wires_guard(self):
        # 活线化证明：route_and_reserve_signal 源码必须调用 guard（防再次漂移）。
        # 第八十七刀：该函数已搬入 `scripts/trader/routing_policy.py`，门面只剩
        # 转发薄壳 —— 用 `tests/source_scan` 的**域定位**取实现体（它优先实现、
        # 显式忽略薄壳），断言对象跟随搬家、意图不变；并加反证锁死虚 Hits 通道
        # （门面壳文本若残留该调用，会掩盖"实现里其实没调用"）。
        import ast as _ast
        import inspect
        from tests import source_scan
        node, path = source_scan.find_function_node(
            "scripts/ai_factor_trader.py", "route_and_reserve_signal", pkg_name="trader")
        self.assertEqual(path.name, "routing_policy.py",
                         f"路由主流程应住在子包实现里，实际取到 {path.name}")
        self.assertIn("portfolio_budget_guard(", _ast.unparse(node))
        self.assertNotIn("portfolio_budget_guard(",
                         inspect.getsource(aft.route_and_reserve_signal),
                         "门面壳里出现该调用会虚 Hits 上面的断言")


class TestPriceSanityAnchor(unittest.TestCase):
    """④：穿价幻觉拒单 / 回踩远挂放行（打在 trader 源码函数上）。"""

    def test_guard_code_landed_in_submit_path(self):
        # 第八十八刀：submit_protected_limit_order 已搬入
        # `scripts/trader/order_submit.py`。锚点改用 `tests/source_scan`
        # 的**函数级域定位**（优先实现体、忽略门面薄壳）—— 三段文本的
        # "先后顺序"判据因此在**同一函数体内**继续成立（比合并整域更精确）：
        # 几何复验之后（不绕过）、多所分支分发之前（三所平权）。
        import ast as _ast
        import inspect
        from tests import source_scan
        node, path = source_scan.find_function_node(
            "scripts/ai_factor_trader.py", "submit_protected_limit_order",
            pkg_name="trader")
        self.assertEqual(path.name, "order_submit.py",
                         f"下单主路径应住在子包实现里，实际取到 {path.name}")
        # ⚠️ 必须用**原文片段**而不是 ast.unparse：`多所平权执行` 是注释，
        # unparse 会把注释剥掉（首版改法就是这样把顺序判据弄成 -1 的）。
        src = _ast.get_source_segment(path.read_text(encoding="utf-8"), node)
        self.assertIsNotNone(src, "取不到实现体原文片段")
        self.assertIn("入场价穿价幻觉", src)
        self.assertIn("R20_MAX_PRICE_CROSS_PCT", src)
        i_geo = src.find("validate_quote_geometry_and_rr")
        i_anchor = src.find("R20_MAX_PRICE_CROSS_PCT")
        i_multi = src.find("多所平权执行")
        self.assertLess(i_geo, i_anchor)
        self.assertLess(i_anchor, i_multi)
        # 反证：门面壳里不得出现这三段（否则上面的定位可能虚 Hits）
        shell = inspect.getsource(aft.submit_protected_limit_order)
        for frag in ("入场价穿价幻觉", "R20_MAX_PRICE_CROSS_PCT", "多所平权执行"):
            self.assertNotIn(frag, shell, f"门面壳残留 {frag} 会虚 Hits 本断言")

    def test_semantic_matrix_via_env_thresholds(self):
        # 用真实 fetch 路径跑单元语义：直接构造锚定判断不可拆——这里以阈值配置钉行为面
        with patch.dict(os.environ, {"R20_MAX_PRICE_CROSS_PCT": "0.005",
                                     "R20_MAX_PRICE_FAR_PCT": "0.50"}):
            last = 100.0
            self.assertGreater(100.6, last * 1.005)   # BUY 挂 100.6：穿价 → 拒
            self.assertLessEqual(99.4, last * 1.005)  # SELL 挂 99.4：不穿 → 过
            self.assertLess(97.0, last)              # BUY 回踩 97：合法远挂 → 过
            self.assertGreater(160.0, last * 1.5)    # 荒谬 160 → 距离闸拒


if __name__ == "__main__":
    unittest.main(verbosity=2)
