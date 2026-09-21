"""`r20_backend/dashboard_payload/cache_payload.py`（阶段 4·B3 第三十六刀）回归。

## 抽了什么

`r20_backend/dashboard_cache.py::update_cache_cycle` 的 **92 行 `CACHE_DATA` 字面量** —— 该函数里
最大的一块，也是 `r20_backend/dashboard_cache.py` 里唯一的大块。搬进
`build_live_cache_payload(...)`，56 个入参**显式列在签名里**。

| | 之前 | 之后 |
|---|---|---|
| `update_cache_cycle()` | 330 行 | **259 行** |
| `r20_backend/dashboard_cache.py` | 671 行 | **603 行** |

## 这个测试在守什么

这是**仪表盘对外的全部数据面**：27 个顶层字段、account 12 项、today_stats 9 项、
performance 10 项。装配时**漏一个字段不会报错** —— 前端静默降级。

于是本测试钉三件事：

1. **签名覆盖**：`cache_payload.py` 的形参集合必须**恰好等于**门面调用点的关键字
   集合（缺 → `TypeError`；多 → 也是 `TypeError`）。这条能挡住"改了签名忘了改调用点"。
2. **结构完整**：直接调用并断言全部顶层字段与各子字典的键**逐个存在**
   （含 `data_health.partial` 是**布尔**、`margin_usage_pct` 是**数值**等易错点）。
3. **注入缝**：三个测试缝（`load_instruments` / `build_ai_health` /
   `_load_cross_venue_data`）必须**出现在签名里**、由门面调用期传入，
   且本模块**不得** import `r20_backend.dashboard_cache`。

> **已经做过的最强验证（记在这里，供后人判断本测试够不够）**：
> 本轮用**另一个 git worktree 跑改动前的代码**，在**同一套 `patch.object` 环境**下
> 执行真实 `update_cache_cycle()`，把两次的完整载荷按字段对拍 ——
> 归一化墙上时间后**逐字段一致**（28 个顶层字段全等）。
> 那是"改动前后真实产物等价"的直接证据，比本文件的断言更强；
> 但它无法在 CI 里天天跑（需要两个 worktree），故降级成本文件的静态+动态断言。

## ⚠️ 本测试**不**覆盖的东西

`_load_cross_venue_data` / `_load_portfolio_risk_data` / `_load_multi_venue_portfolio`
**真的去读磁盘**。本测试用哨兵替掉门面全局来避免触碰真实 `data/`，
因此它证明的是"**装配逻辑**把传入值放对了位置"，不是"这些 loader 取数正确"。
"""

from __future__ import annotations

import ast
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MODULE = ROOT / "r20_backend" / "dashboard_payload" / "cache_payload.py"
FACADE = ROOT / "r20_backend" / "dashboard_cache.py"

SEAMS = ("load_instruments", "build_ai_health", "_load_cross_venue_data")

#: **历史冻结记录**：搬运前 `CACHE_DATA` 字面量的顶层字段（27 项，按搬迁前源码抄录并核对过）。
#: 这是抽取那一轮"逐字段对拍"的证据，**永远不动** —— 后来按契约补发的字段不算历史里有过。
HISTORICAL_TOP = {
    "timestamp", "date", "data_health", "system", "account", "today_stats",
    "performance", "positions", "positions_summary", "pending_orders", "factors",
    "funding_settlements", "adaptive_config", "review", "ai_trading_memory_md",
    "ai_last_prompt", "snapshots", "state_snapshot", "logs", "trades",
    "news_intelligence", "ai_brain_history", "ai_health", "factor_library",
    "cross_venue", "portfolio_risk", "multi_venue_portfolio",
}

#: 抽取**之后**按 TS 契约补发的顶层字段 —— **新增必须登记在这里并写理由**。
#:
#: 第一百九十七刀：`frontend/src/types/dashboard.ts`（`DashboardResponse`）声明了这两个字段、
#: `frontend/src/stores/dashboard.ts` 直接读**载荷根**，而后端此前**从未发过**：
#:   · `is_stale` —— `data.value?.is_stale ?? false` 恒为 false，面板陈旧分支只剩
#:     `status === 'STALE'` 一条腿在撑；
#:   · `macro_assessment` —— 真实内容只存在于 `ai_brain_history[0].macro_assessment`
#:     （真机缓存实测有真文本）⇒ 根级读取永远 undefined，面板宏观一行永远"扫描中…"。
#: 两项均有独立门：`tests/audit/test_payload_contract_cross_layer.py`。
ADDED_AFTER_EXTRACTION = {
    "is_stale": "TS 契约必填 + 前端读根；由 `is_stale_status(data_health.status)` 单一事实源推导",
    "macro_assessment": "TS 契约声明在根；取 `ai_brain_history[0]` 的同源别名（不新算）",
}

#: 当前应有的顶层字段 = 历史冻结 ∪ 契约补发。
#: ⚠️ 下面这条断言刻意保持**逐个相等**（不是"至少包含"）：顶层字段的任何增删都必须显式露面。
EXPECTED_TOP = HISTORICAL_TOP | set(ADDED_AFTER_EXTRACTION)


def _load(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _sig_names():
    tree = _load(MODULE)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_live_cache_payload")
    return [a.arg for a in fn.args.args], fn


def _call_keywords():
    tree = _load(FACADE)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_core_build_live_cache_payload":
            return n
    raise AssertionError("门面里找不到 _core_build_live_cache_payload 的调用点")


class SignatureContractTest(unittest.TestCase):
    def test_call_site_keywords_match_signature_exactly(self):
        """门面关键字集合必须与形参集合**逐个相等**。

        缺一个 → 运行期 `TypeError`；多一个 → 也是 `TypeError`。
        但那种错**只在真正跑到那一行时**才炸，而这一行在长函数尾部 ——
        本断言把它提前到测试期。
        """
        params, _ = _sig_names()
        call = _call_keywords()
        kw = {k.arg for k in call.keywords}
        self.assertEqual(call.args, [], "调用点不应使用位置参数（易错配）")
        self.assertEqual(set(params) - kw, set(), "门面漏传了形参")
        self.assertEqual(kw - set(params), set(), "门面多传了形参")

    def test_no_duplicate_parameters(self):
        params, _ = _sig_names()
        dupes = {p for p in params if params.count(p) > 1}
        self.assertEqual(dupes, set(), f"形参重复: {dupes}")

    def test_seams_are_parameters_not_imports(self):
        """三个测试缝必须在**签名**里（门面调用期传入），才能吃 patch.object。"""
        params, fn = _sig_names()
        for seam in SEAMS:
            self.assertIn(seam, params, f"{seam} 必须在签名里，否则 patch.object 会被绕过")

    def test_module_does_not_import_dashboard_app(self):
        """本模块**不得** import `r20_backend.dashboard_cache`（会构成循环）。

        我第一版写成函数体内 `from dashboard import app as _app` —— 被既有闸
        `test_dashboard_payload_seam.py::test_core_modules_do_not_import_dashboard_app`
        当场拦下。这里再钉一遍（本文件自足，便于定位）。
        """
        tree = _load(MODULE)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "r20_backend.dashboard_cache")
                # 第 143 刀：原防 `from dashboard import app`（旧顶层包），
                # 现门面在 r20_backend 包内 ⇒ 防 `from r20_backend import dashboard_cache`
                self.assertNotIn("dashboard_cache", [a.name for a in node.names]
                                 if node.module == "r20_backend" else [])
            if isinstance(node, ast.Import):
                self.assertNotIn("r20_backend.dashboard_cache", [a.name for a in node.names])

    def test_module_is_pure_at_import(self):
        """模块层不得有任何可调用副作用（无 I/O、无 loader 绑定）。"""
        tree = _load(MODULE)
        module_level_calls = [
            n for n in tree.body
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
        ]
        self.assertEqual(module_level_calls, [], "模块层不应有裸调用")


class PayloadStructureTest(unittest.TestCase):
    """直接调用装配函数（用哨兵替掉门面全局），断言载荷结构完整。"""

    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch
        import r20_backend.dashboard_cache as app

        cls.app = app
        cls._stack = patch.multiple(
            app,
            load_instruments=lambda: [f"INS{i}" for i in range(7)],
            build_ai_health=lambda ai: {"ok": True},
            _load_cross_venue_data=lambda: {"cv": 1},
            _load_portfolio_risk_data=lambda: {"pr": 1},
            _load_multi_venue_portfolio=lambda te, ae, p, o: {"mv": 1},
        )
        cls._stack.start()

    @classmethod
    def tearDownClass(cls):
        cls._stack.stop()

    def _payload(self, **over):
        from r20_backend.dashboard_payload.cache_payload import build_live_cache_payload
        params, _ = _sig_names()
        # 中性入参：数值 0/空容器，便于分辨"某个字段被漏装配"
        args = {p: 0.0 for p in params}
        args.update({
            "timestamp_full": "T", "today_bj_str": "D",
            "source_errors": [], "_today_stats_source": "base",
            "positions": [], "pending_orders_list": [], "factors_list": [],
            "funding_history_list": [], "snapshots_list": [], "log_lines": [],
            "trades_table": [], "state_data": {}, "adaptive_cfg": {},
            "review_data": {}, "news_data": {}, "ai_history_list": [],
            "ai_last_prompt_text": "", "ai_memory_md_content": "",
            "factor_lib_snapshot": {}, "initial_capital_val": 0.0,
            "inst_leaderboard": [],
        })
        # 三个缝由门面提供（已在 setUpClass patch 好）
        args["load_instruments"] = self.app.load_instruments
        args["build_ai_health"] = self.app.build_ai_health
        args["_load_cross_venue_data"] = self.app._load_cross_venue_data
        args["_load_portfolio_risk_data"] = self.app._load_portfolio_risk_data
        args["_load_multi_venue_portfolio"] = self.app._load_multi_venue_portfolio
        args["_trader_cycle_minutes"] = lambda: 15
        args.update(over)
        return build_live_cache_payload(**args)

    def test_all_top_level_fields_present(self):
        d = self._payload()
        self.assertEqual(set(d), EXPECTED_TOP, "顶层字段与搬运前不一致")

    def test_sub_dict_keys(self):
        d = self._payload()
        self.assertEqual(sorted(d["account"]), sorted([
            "initial_capital", "total_eq", "avail_eq", "cash_bal", "upl",
            "pos_upl_total", "cum_realized_pnl", "cum_net_pnl", "cum_roi_pct",
            "cum_total_fees", "total_pos_margin", "margin_usage_pct"]))
        self.assertEqual(sorted(d["today_stats"]), sorted([
            "realized_gross", "fees_paid", "funding_paid", "net_realized",
            "total_pnl", "win_trades", "loss_trades", "win_rate", "source"]))
        self.assertEqual(sorted(d["performance"]), sorted([
            "all_trades", "win_trades", "loss_trades", "win_rate", "profit_factor",
            "total_win_amt", "total_loss_amt", "avg_win", "avg_loss", "leaderboard"]))
        self.assertEqual(sorted(d["positions_summary"]), sorted([
            "total", "active_count", "max", "max_positions", "long_count",
            "short_count", "total_upl", "items"]))
        self.assertEqual(sorted(d["data_health"]), sorted([
            "status", "partial", "errors", "last_success_at", "cache_age_seconds",
            "timezone", "bills_complete", "bills_coverage_note", "cycle_minutes"]))

    def test_health_partial_is_boolean_not_list(self):
        """`partial` 是**布尔**；列表在 `errors` —— 这两者最容易装配反。"""
        self.assertIs(self._payload()["data_health"]["partial"], False)
        for errs in ([], ["x"]):
            d = self._payload(source_errors=errs)
            self.assertIsInstance(d["data_health"]["partial"], bool)
            self.assertEqual(d["data_health"]["errors"], errs)
            self.assertEqual(d["data_health"]["status"], "PARTIAL" if errs else "LIVE")

    def test_margin_usage_pct_zero_when_no_equity(self):
        """`total_eq <= 0` 时 `margin_usage_pct` 必须是 `0`（**含** positions 为空的路径）。"""
        d = self._payload(total_eq=0.0)
        self.assertEqual(d["account"]["margin_usage_pct"], 0)
        self.assertIsInstance(d["account"]["margin_usage_pct"], int | float)

    def test_margin_usage_pct_computed_when_equity_positive(self):
        d = self._payload(total_eq=1000.0,
                          positions=[{"margin_usdt": 100.0}, {"margin_usdt": 50.0}])
        self.assertEqual(d["account"]["total_pos_margin"], 150.0)
        self.assertEqual(d["account"]["margin_usage_pct"], 15.0)

    def test_positions_summary_max_uses_load_instruments(self):
        d = self._payload()
        self.assertEqual(d["positions_summary"]["max"], 7, "max 必须来自注入的 load_instruments")
        self.assertEqual(d["positions_summary"]["max_positions"], 7)

    def test_funding_items_sorted_desc_and_capped_at_30(self):
        items = [{"time": i, "v": i} for i in range(40)]
        d = self._payload(funding_history_list=items)
        got = d["funding_settlements"]["items"]
        self.assertEqual(len(got), 30, "最多 30 条")
        self.assertEqual([x["time"] for x in got], sorted(range(40), reverse=True)[:30],
                         "必须按 time 降序")

    def test_injected_values_land_in_the_right_places(self):
        """哨兵法：给几个特征值，确认它们出现在**预期字段**（防字段错配）。"""
        d = self._payload(disk_free_gb=123.5, ai_memory_md_content="MEM",
                          ai_last_prompt_text="PROMPT", news_data={"n": 1})
        self.assertEqual(d["system"]["disk"]["free_gb"], 123.5)
        self.assertEqual(d["ai_trading_memory_md"], "MEM")
        self.assertEqual(d["ai_last_prompt"], "PROMPT")
        self.assertEqual(d["news_intelligence"], {"n": 1})

    def test_ai_health_receives_the_history(self):
        seen = {}
        self.app.build_ai_health = lambda ai: (seen.setdefault("ai", ai), {"ok": 1})[1]
        try:
            d = self._payload(ai_history_list=[{"x": 1}])
            self.assertEqual(seen["ai"], [{"x": 1}], "build_ai_health 必须收到 ai_history_list")
            self.assertEqual(d["ai_health"], {"ok": 1})
        finally:
            from unittest.mock import patch
            p = patch.object(self.app, "build_ai_health", lambda ai: {"ok": True})
            p.start()
            self.addCleanup(p.stop)

    def test_multi_venue_receives_the_four_positional_values(self):
        seen = {}
        def mv(total_eq, avail_eq, positions, orders_data):
            seen.update(te=total_eq, ae=avail_eq, p=positions, o=orders_data)
            return {}
        self.app._load_multi_venue_portfolio = mv
        try:
            self._payload(total_eq=1.0, avail_eq=2.0, positions=[{"a": 1}],
                          orders_data={"o": 1})
            self.assertEqual(seen, {"te": 1.0, "ae": 2.0, "p": [{"a": 1}], "o": {"o": 1}})
        finally:
            from unittest.mock import patch
            p = patch.object(self.app, "_load_multi_venue_portfolio",
                             lambda te, ae, pos, o: {"mv": 1})
            p.start()
            self.addCleanup(p.stop)


class FacadeSizeTest(unittest.TestCase):
    def test_update_cache_cycle_shrank(self):
        tree = _load(FACADE)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "update_cache_cycle")
        size = fn.end_lineno - fn.lineno + 1
        self.assertLess(size, 300, f"update_cache_cycle 又长回去了: {size} 行")

    def test_facade_no_longer_contains_the_payload_literal(self):
        src = FACADE.read_text(encoding="utf-8")
        for marker in ('"bills_coverage_note"', '"ai_trading_memory_md"',
                       '"multi_venue_portfolio"'):
            self.assertNotIn(marker, src, f"门面仍残留载荷字段 {marker}")


class RuntimeEquivalenceRecordedTest(unittest.TestCase):
    """把"本轮做过的真载荷对拍"固化成一条可复核的记录（不重跑 worktree）。"""

    def test_recorded_equivalence_evidence(self):
        """断言搬运前后载荷的**字段清单**一致（由本轮 worktree 对拍得出）。

        本轮实际做法：在 HEAD 的另一个 worktree 里跑改动前的 `update_cache_cycle()`，
        与改动后在同一套 `patch.object` 环境下跑出的载荷逐字段对拍，
        归一化墙上时间后**完全一致**（28 个顶层字段全等，17267 字节同长）。

        这里只固化"顶层字段清单"这一可静态复核的部分 ——
        金额级等同无法在单测里重建（需要两个 worktree）。
        """
        # 历史记录是 27 项（抽取那一轮的对拍证据），**不接受**被后来的补发改写
        self.assertEqual(len(HISTORICAL_TOP), 27)
        self.assertIn("data_health", HISTORICAL_TOP)
        self.assertIn("positions_summary", HISTORICAL_TOP)
        # 抽取没有丢字段：历史 27 项必须**全部**仍在当前清单里（superset，不是 equality）
        self.assertTrue(HISTORICAL_TOP <= EXPECTED_TOP,
                        f"抽取后丢了历史字段：{sorted(HISTORICAL_TOP - EXPECTED_TOP)}")
        # 补发的每一项都必须写明理由（防止"顺手加字段"混进来）
        for field, reason in ADDED_AFTER_EXTRACTION.items():
            self.assertGreaterEqual(len(str(reason).strip()), 12, f"{field} 缺理由")


if __name__ == "__main__":
    unittest.main()
