"""B3（交易员侧第十七块）`scripts/trader/cycle_snapshot.py` 的抽取回归。

## 这个测试在守什么

两段从 `execute_portfolio`（原本 652 行）里搬出的长块：

1. `collect_pending_inst_ids` —— 外所挂单枚举与去重计数（35 行裸循环）。
   含一个**易漏的过滤不变量**：只有 `side` 属于 `buy`/`sell` 且 `reduce_only`
   不为真时才计入。减仓/保护单若被计入，会让 `reserved_slot_count` 虚高，
   **该开的仓开不出来** —— 这是"少赚/不赚"级的静默故障。
2. `build_state_payload` —— 面板/巡检读取的 17 字段快照。
   字段名或四舍五入精度错一个，面板就显示错。

## 为什么给 `collect_pending_inst_ids` 写这么多用例

原块是裸循环 + 两处 `continue` + 一层 try/except，分支组合多。而"挂单枚举"
是**跨所去重**的关键：漏算 → 重复开仓（超仓）；多算 → 该开不开。
两种都不会抛异常，只会让行为悄悄偏。故每种过滤各测一条。
"""

from __future__ import annotations

import ast
import types
import unittest
from pathlib import Path

from scripts.trader import cycle_snapshot

ROOT = Path(__file__).resolve().parents[2]
FACADE = ROOT / "scripts" / "ai_factor_trader.py"
SUBMODULE = ROOT / "scripts" / "trader" / "cycle_snapshot.py"

AUTH = ("401", "unauthorized", "invalid api", "signature")


class _FakeRegistry:
    def __init__(self, open_venues=("gate", "binance"), adapter=None):
        self._open = set(open_venues)
        self._adapter = adapter
        self.calls = []

    def execution_open(self, venue, mode):
        self.calls.append(("execution_open", venue))
        return venue in self._open

    def get_adapter(self, venue, environment=None):
        self.calls.append(("get_adapter", venue))
        return self._adapter


class _FakeAdapter:
    def __init__(self, per_base=None, open_orders=None):
        self._per_base = per_base or {}
        self._open_orders = open_orders if open_orders is not None else []
        self.listed = []

    def open_orders(self):
        return self._open_orders

    def list_open_orders(self, base):
        self.listed.append(base)
        return self._per_base.get(base, [])


def _run(adapter, *, venues=("gate", "binance"), mode="live", broken=(),
         instruments=(), warn=None, registry=None):
    reg = registry if registry is not None else _FakeRegistry(adapter=adapter)
    return cycle_snapshot.collect_pending_inst_ids(
        venues=venues, venue_mode=mode, broken_venues=set(broken),
        venue_registry=reg, load_instruments=lambda: list(instruments),
        auth_markers=AUTH, warn=warn)


class CollectPendingTest(unittest.TestCase):
    def test_open_orders_are_converted_to_swap_symbols(self):
        ad = _FakeAdapter(open_orders=[
            {"side": "buy", "inst_id": "BTC-USDT-SWAP"},
            {"side": "sell", "contract": "ETH_USDT", "raw": {"symbol": "SOLUSDT"}},
        ])
        ids, longs, shorts = _run(ad)
        self.assertEqual(ids, {"BTC-USDT-SWAP", "ETH-USDT-SWAP"})
        self.assertEqual((longs, shorts), (1, 1))

    def test_reduce_only_orders_are_excluded(self):
        """减仓/保护单不属入场生命周期管辖 —— 计入会让预占槽位虚高。"""
        ad = _FakeAdapter(open_orders=[
            {"side": "buy", "inst_id": "BTC-USDT-SWAP", "reduce_only": True},
            {"side": "buy", "inst_id": "ETH-USDT-SWAP", "reduce_only": "true"},
            {"side": "sell", "inst_id": "SOL-USDT-SWAP", "reduce_only": "1"},
            {"side": "buy", "inst_id": "XRP-USDT-SWAP", "reduce_only": False},
            {"side": "buy", "inst_id": "ADA-USDT-SWAP", "reduce_only": None},
        ])
        ids, longs, shorts = _run(ad)
        self.assertEqual(ids, {"XRP-USDT-SWAP", "ADA-USDT-SWAP"})
        self.assertEqual((longs, shorts), (2, 0))

    def test_reduce_only_from_raw_is_honoured(self):
        ad = _FakeAdapter(open_orders=[
            {"side": "buy", "inst_id": "BTC-USDT-SWAP", "raw": {"reduce_only": True}},
            {"side": "buy", "inst_id": "ETH-USDT-SWAP", "raw": {"reduce_only": False}},
        ])
        ids, _, _ = _run(ad)
        self.assertEqual(ids, {"ETH-USDT-SWAP"})

    def test_side_must_be_buy_or_sell(self):
        ad = _FakeAdapter(open_orders=[
            {"side": "BUY", "inst_id": "BTC-USDT-SWAP"},
            {"side": None, "inst_id": "ETH-USDT-SWAP"},
            {"side": "", "inst_id": "SOL-USDT-SWAP"},
            {"inst_id": "XRP-USDT-SWAP"},
        ])
        ids, longs, shorts = _run(ad)
        self.assertEqual(ids, {"BTC-USDT-SWAP"}, "只有明确的 buy/sell 才算")
        self.assertEqual((longs, shorts), (1, 0))

    def test_side_falls_back_to_raw(self):
        ad = _FakeAdapter(open_orders=[
            {"raw": {"side": "sell", "contract": "BTC_USDT"}},
        ])
        ids, longs, shorts = _run(ad)
        self.assertEqual(ids, {"BTC-USDT-SWAP"})
        self.assertEqual((longs, shorts), (0, 1))

    def test_non_dict_entries_are_skipped(self):
        ad = _FakeAdapter(open_orders=[
            "not-a-dict", 123, None,
            {"side": "buy", "inst_id": "BTC-USDT-SWAP"},
        ])
        ids, _, _ = _run(ad)
        self.assertEqual(ids, {"BTC-USDT-SWAP"})

    def test_base_without_resolvable_symbol_is_skipped(self):
        ad = _FakeAdapter(open_orders=[
            {"side": "buy", "inst_id": "", "raw": {}},
            {"side": "buy", "inst_id": "_USDT"},
        ])
        ids, _, _ = _run(ad)
        self.assertEqual(ids, set(), "推不出基础币种时不得生成半个 symbol")

    def test_explicit_base_field_wins_and_is_uppercased(self):
        ad = _FakeAdapter(open_orders=[
            {"side": "buy", "base": "btc", "inst_id": "IGNORED-USDT-SWAP"},
        ])
        ids, _, _ = _run(ad)
        self.assertEqual(ids, {"BTC-USDT-SWAP"})

    def test_gate_path_enumerates_every_instrument(self):
        """gate 无全量挂单接口，必须逐标的查询（原实现的 list_open_orders 路径）。"""
        ad = _FakeAdapter(per_base={"BTC": [{"side": "buy", "inst_id": "BTC-USDT-SWAP"}],
                                    "ETH": [{"side": "sell", "inst_id": "ETH-USDT-SWAP",
                                             "reduce_only": True}]})
        insts = [{"instId": "BTC-USDT-SWAP"}, {"instId": "ETH-USDT-SWAP"}]
        ids, longs, shorts = _run(ad, venues=("gate",), instruments=insts,
                                  registry=_FakeRegistry(adapter=ad,
                                                         open_venues=("gate",)))
        self.assertEqual(ad.listed, ["BTC", "ETH"])
        self.assertEqual(ids, {"BTC-USDT-SWAP"})
        self.assertEqual((longs, shorts), (1, 0))

    def test_binance_path_uses_bulk_open_orders(self):
        ad = _FakeAdapter(open_orders=[{"side": "buy", "inst_id": "BTC-USDT-SWAP"}])
        ids, _, _ = _run(ad, venues=("binance",), registry=_FakeRegistry(
            adapter=ad, open_venues=("binance",)))
        self.assertEqual(ad.listed, [], "binance 走批量接口，不逐标的查")
        self.assertEqual(ids, {"BTC-USDT-SWAP"})

    def test_broken_venue_is_skipped_entirely(self):
        ad = _FakeAdapter(open_orders=[{"side": "buy", "inst_id": "BTC-USDT-SWAP"}])
        reg = _FakeRegistry(adapter=ad)
        ids, _, _ = _run(ad, venues=("binance",), broken=("binance",), registry=reg)
        self.assertEqual(ids, set())
        self.assertEqual(reg.calls, [], "已判死的所不得再探")

    def test_venue_not_execution_open_is_skipped(self):
        ad = _FakeAdapter(open_orders=[{"side": "buy", "inst_id": "BTC-USDT-SWAP"}])
        reg = _FakeRegistry(adapter=ad, open_venues=())
        ids, _, _ = _run(ad, venues=("binance",), registry=reg)
        self.assertEqual(ids, set())
        self.assertNotIn(("get_adapter", "binance"), reg.calls)

    def test_no_venue_mode_skips_everything(self):
        ad = _FakeAdapter(open_orders=[{"side": "buy", "inst_id": "BTC-USDT-SWAP"}])
        reg = _FakeRegistry(adapter=ad)
        ids, _, _ = _run(ad, venues=("binance",), mode=None, registry=reg)
        self.assertEqual(ids, set())
        self.assertEqual(reg.calls, [])

    def test_duplicate_symbols_count_once_but_tally_twice(self):
        """去重集合与方向计数是两个口径：同标的两单要计两次方向数。"""
        ad = _FakeAdapter(open_orders=[
            {"side": "buy", "inst_id": "BTC-USDT-SWAP"},
            {"side": "buy", "inst_id": "BTC-USDT-SWAP"},
        ])
        ids, longs, shorts = _run(ad)
        self.assertEqual(ids, {"BTC-USDT-SWAP"})
        self.assertEqual((longs, shorts), (2, 0))

    def test_venue_exception_is_swallowed_and_warned(self):
        class _Boom:
            def open_orders(self):
                raise RuntimeError("upstream 500")

        warned = []
        ids, longs, shorts = _run(_Boom(), venues=("binance",), warn=warned.append)
        self.assertEqual((ids, longs, shorts), (set(), 0, 0))
        self.assertEqual(len(warned), 1)
        self.assertIn("挂单枚举失败", warned[0])

    def test_auth_failure_is_swallowed_silently(self):
        """认证类失败不刷屏 —— 回收侧已另行把关（原实现语义）。"""
        class _Boom:
            def open_orders(self):
                raise RuntimeError("401 unauthorized")

        warned = []
        ids, _, _ = _run(_Boom(), venues=("binance",), warn=warned.append)
        self.assertEqual(ids, set())
        self.assertEqual(warned, [], "认证失败应静默")

    def test_warn_none_is_silent(self):
        class _Boom:
            def open_orders(self):
                raise RuntimeError("boom")

        ids, _, _ = _run(_Boom(), venues=("binance",), warn=None)
        self.assertEqual(ids, set())

    def test_one_venue_failure_does_not_lose_the_other(self):
        class _Mixed(_FakeAdapter):
            def open_orders(self):
                raise RuntimeError("binance down")

        ad = _Mixed(per_base={"BTC": [{"side": "buy", "inst_id": "BTC-USDT-SWAP"}]})
        ids, longs, _ = _run(ad, venues=("gate", "binance"),
                             instruments=[{"instId": "BTC-USDT-SWAP"}],
                             warn=lambda *_: None)
        self.assertEqual(ids, {"BTC-USDT-SWAP"}, "一所挂掉不得丢掉另一所的计数")
        self.assertEqual(longs, 1)


class BuildStatePayloadTest(unittest.TestCase):
    def _factor(self, **over):
        f = dict(name="BTC", instId="BTC-USDT-SWAP", type="swap", price=79000.0,
                 rsi=55.123, rsi_7=51.987, vwap_bias=0.4567, macd_hist=1.5,
                 macd_accel=0.25, obv_flow="INFLOW", bb_bandwidth=0.03,
                 vol_ratio=1.8, market_regime="TREND", structure_1h="BULL",
                 trend_1h_bullish=True, position=1)
        f.update(over)
        return f

    def _build(self, factors=None, **over):
        kw = dict(timestamp_full="2026-09-14 12:00:00", active_pos_count=2,
                  max_positions=8, long_count=1, short_count=1, cb_active=False,
                  cb_reason="", executed_actions=["a1"], all_factors=factors or [],
                  evaluate_asset_signal=lambda f: (88.5, "BUY_LONG", ["r"], "T", "D"))
        kw.update(over)
        return cycle_snapshot.build_state_payload(**kw)

    def test_top_level_fields(self):
        p = self._build()
        self.assertEqual(p["timestamp"], "2026-09-14 12:00:00")
        self.assertEqual(p["active_positions_count"], 2)
        self.assertEqual(p["max_positions"], 8)
        self.assertEqual(p["long_count"], 1)
        self.assertEqual(p["short_count"], 1)
        self.assertEqual(p["circuit_breaker"], {"active": False, "reason": ""})
        self.assertEqual(p["executed_actions"], ["a1"])
        self.assertEqual(p["instruments"], [])

    def test_field_order_is_stable(self):
        """键顺序即 `json.dump` 的输出顺序 —— 面板 diff / 人工比对都依赖它。"""
        p = self._build()
        self.assertEqual(list(p.keys()), [
            "timestamp", "active_positions_count", "max_positions", "long_count",
            "short_count", "circuit_breaker", "executed_actions", "instruments"])

    def test_instrument_field_order_and_precision(self):
        p = self._build(factors=[self._factor()])
        inst = p["instruments"][0]
        self.assertEqual(list(inst.keys()), [
            "name", "instId", "type", "price", "rsi", "rsi_7", "vwap_bias",
            "macd_hist", "macd_accel", "obv_flow", "bb_bandwidth", "vol_ratio",
            "market_regime", "structure_1h", "trend_1h", "trend_4h",
            "score", "action", "strategy", "desc", "position"])
        self.assertEqual(inst["rsi"], 55.1, "rsi 必须 round 到 1 位")
        self.assertEqual(inst["rsi_7"], 52.0, "rsi_7 必须 round 到 1 位")
        self.assertEqual(inst["vwap_bias"], 0.46, "vwap_bias 必须 round 到 2 位")

    def test_trend_labels_come_from_boolean_flags(self):
        p = self._build(factors=[self._factor(trend_1h_bullish=True,
                                              trend_4h_bullish=False)])
        self.assertEqual(p["instruments"][0]["trend_1h"], "多头")
        self.assertEqual(p["instruments"][0]["trend_4h"], "空头")

    def test_missing_optional_fields_use_defaults(self):
        bare = dict(name="X", instId="X-USDT-SWAP", type="swap", price=1.0,
                    rsi=50.0, position=0)
        p = self._build(factors=[bare])
        inst = p["instruments"][0]
        self.assertEqual(inst["rsi_7"], 50.0)
        self.assertEqual(inst["vwap_bias"], 0.0)
        self.assertEqual(inst["macd_hist"], 0.0)
        self.assertEqual(inst["macd_accel"], 0.0)
        self.assertEqual(inst["obv_flow"], "NEUTRAL")
        self.assertEqual(inst["bb_bandwidth"], 0.0)
        self.assertEqual(inst["vol_ratio"], 1.0)
        self.assertEqual(inst["market_regime"], "CHOP")
        self.assertEqual(inst["structure_1h"], "CHOP")
        self.assertEqual(inst["trend_1h"], "空头")

    def test_signal_evaluator_is_called_once_per_factor(self):
        calls = []

        def ev(f):
            calls.append(f["name"])
            return (1.0, "HOLD", [], "T", "D")

        p = self._build(factors=[self._factor(name="A"), self._factor(name="B")],
                        evaluate_asset_signal=ev)
        self.assertEqual(calls, ["A", "B"])
        self.assertEqual(len(p["instruments"]), 2)
        self.assertEqual(p["instruments"][0]["score"], 1.0)
        self.assertEqual(p["instruments"][0]["action"], "HOLD")

    def test_reasons_is_dropped_from_payload(self):
        """`reasons` 参与了求值但**不**进快照 —— 原实现如此，别顺手加。"""
        p = self._build(factors=[self._factor()])
        self.assertNotIn("reasons", p["instruments"][0])


class WiringTest(unittest.TestCase):
    def test_impl_lives_in_submodule_not_facade(self):
        facade = FACADE.read_text(encoding="utf-8")
        sub = SUBMODULE.read_text(encoding="utf-8")
        for fn in ("collect_pending_inst_ids", "build_state_payload"):
            self.assertIn(f"def {fn}(", sub)
            self.assertNotIn(f"def {fn}(", facade)

    def test_execute_portfolio_shrank_and_calls_helpers(self):
        facade = FACADE.read_text(encoding="utf-8")
        tree = ast.parse(facade)
        # 用 AST 数**真实调用**，不用文本 count —— 文本会把 import 行也数进去
        # （`from ... import collect_pending_inst_ids`），得到的 2 没有意义。
        called = [n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        # 第九十二刀：`collect_pending_inst_ids` 的调用随相位 1 迁入 cycle_stages.py
        self.assertEqual(called.count("collect_pending_inst_ids"), 0,
                         "门面主流程已不再直接调用（随相位 1 迁出）")
        # 第九十一刀：`build_state_payload` 的调用随"相位 5"搬入 cycle_stages.py
        stages = ast.parse((ROOT / "scripts" / "trader" / "cycle_stages.py").read_text(encoding="utf-8"))
        stage_calls = [n.func.id for n in ast.walk(stages)
                       if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertEqual(stage_calls.count("build_state_payload"), 1,
                         "面板状态装配应恰有 1 处调用（现住 cycle_stages.persist_state_and_sync_ledger）")
        self.assertEqual(stage_calls.count("collect_pending_inst_ids"), 1,
                         "外所挂单枚举应恰有 1 处调用（现住 cycle_stages.fetch_positions_and_reconcile）")
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "execute_portfolio")
        lines = fn.end_lineno - fn.lineno + 1
        self.assertLess(lines, 640, f"execute_portfolio 应已明显变短，实际 {lines} 行")

    def test_old_venue_loop_is_gone_from_facade(self):
        facade = FACADE.read_text(encoding="utf-8")
        self.assertNotIn('for _gv in ("gate", "binance")', facade)
        self.assertNotIn('_gv_mode and venue_registry.execution_open', facade)

    def test_facade_keeps_the_reserved_count_arithmetic(self):
        """预占槽位算式 —— 它是"能不能再开仓"的直接依据，必须一眼可见。

        ⚠️ 第九十二刀：该算式随相位 1 整体搬入 `scripts/trader/cycle_stages.py`
        （`fetch_positions_and_reconcile` 段体 AST 逐字）。判定对象随实现迁移，
        并加反证：门面不得残留副本（残留=孪生）。
        """
        stages = (ROOT / "scripts" / "trader" / "cycle_stages.py").read_text(encoding="utf-8")
        facade = FACADE.read_text(encoding="utf-8")
        for line in ("reserved_slot_count = active_pos_count + len(pending_inst_ids)",
                     "reserved_long_count = long_count + pending_long_count",
                     "reserved_short_count = short_count + pending_short_count"):
            self.assertIn(line, stages, f"cycle_stages 缺 {line!r}")
            self.assertNotIn(line, facade, "门面残留该算式 ⇒ 出现孪生")

    def test_facade_keeps_the_atomic_write(self):
        """原子写（异常语义"照旧上抛"，属控制流不属装配）。

        ⚠️ 第九十一刀：该行随相位 5 整体搬入 `cycle_stages.py`
        （异常照旧上抛的语义未变：段体 AST 逐字）。判定对象随实现迁移，
        并加反证 —— 它不该出现在 `cycle_snapshot.py`（那是纯装配模块）。
        """
        stages = (ROOT / "scripts" / "trader" / "cycle_stages.py").read_text(encoding="utf-8")
        self.assertIn('_atomic_write_json(os.path.join(DATA_DIR, "trading_state.json")',
                      stages)
        self.assertNotIn("_atomic_write_json", SUBMODULE.read_text(encoding="utf-8"))

    def test_both_call_sites_define_every_name_they_pass(self):
        """路径感知判据：只沿到达该调用的唯一路径收集定义。"""
        from tests.source_scan import names_defined_at_call

        # 第九十二刀：两处调用现**同在** `cycle_stages.py` ——
        # `collect_pending_inst_ids` 随相位 1 入 `fetch_positions_and_reconcile`，
        # `build_state_payload` 随相位 5 入 `persist_state_and_sync_ledger`。
        # 判据分别在其**所属**函数内跑（路径感知：只看到达该调用的那条路径）。
        stages_tree = ast.parse((ROOT / "scripts" / "trader" / "cycle_stages.py").read_text(encoding="utf-8"))
        plans = [(stages_tree, next(n for n in ast.walk(stages_tree)
                                    if isinstance(n, ast.FunctionDef)
                                    and n.name == "fetch_positions_and_reconcile")),
                 (stages_tree, next(n for n in ast.walk(stages_tree)
                                    if isinstance(n, ast.FunctionDef)
                                    and n.name == "persist_state_and_sync_ledger"))]

        checked = 0
        work = []
        for tr, fn in plans:
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("collect_pending_inst_ids", "build_state_payload")):
                    work.append((tr, fn, node))
        for tree, func, node in work:
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("collect_pending_inst_ids",
                                         "build_state_payload")):
                continue
            passed = set()
            for arg in node.args:
                for nm in ast.walk(arg):
                    if isinstance(nm, ast.Name):
                        passed.add(nm.id)
            for kw in node.keywords:
                for nm in ast.walk(kw.value):
                    if isinstance(nm, ast.Name):
                        passed.add(nm.id)
            available = names_defined_at_call(func, node, module_tree=tree)
            missing = sorted(n for n in passed if n not in available)
            self.assertEqual(missing, [],
                             f"{node.func.id} 调用（L{node.lineno}）引用了未定义的名字 "
                             f"{missing}；实盘会 NameError")
            checked += 1
        self.assertEqual(checked, 2, f"应有 2 处调用，实际 {checked}")


class BrokenExecutionVenuesTest(unittest.TestCase):
    """凭证已死场所的判据（第一百三十一刀）：**闸开着却不可就绪** ⇒ 未计入。

    这条判据存在的理由：此类所被 `venue_execution_ready` 否决 ⇒ 跨所取数也跳过它，
    且返回 `ok=True` **无任何错误** ⇒ 它的持仓/挂单不进配额与敞口，而"跨所笔数"
    看起来完整。所以判据必须精确（不误报结构性的"没这个所"），且异常时**不猜**。
    """

    def _reg(self, *, open_flags):
        return types.SimpleNamespace(
            execution_open=lambda v, e: bool(open_flags.get(v, False)))

    def test_flag_on_but_not_ready_is_reported(self):
        got = cycle_snapshot.broken_execution_venues(
            ("gate", "binance"), "demo",
            venue_registry=self._reg(open_flags={"gate": True, "binance": True}),
            venue_execution_ready=lambda v, e: v != "binance")
        self.assertEqual(got, ["binance"])

    def test_flag_off_is_not_reported(self):
        """闸没开 = 结构性无该所（不是"凭证已死"），不得误报。"""
        got = cycle_snapshot.broken_execution_venues(
            ("gate", "binance"), "demo",
            venue_registry=self._reg(open_flags={"gate": False, "binance": False}),
            venue_execution_ready=lambda v, e: False)
        self.assertEqual(got, [])

    def test_all_ready_reports_nothing(self):
        got = cycle_snapshot.broken_execution_venues(
            ("gate", "binance"), "demo",
            venue_registry=self._reg(open_flags={"gate": True, "binance": True}),
            venue_execution_ready=lambda v, e: True)
        self.assertEqual(got, [])

    def test_exception_on_one_venue_does_not_misreport_or_crash(self):
        """判据异常时**不猜**：跳过该所，也不影响其余所的判定。"""
        def _ready(v, e):
            if v == "gate":
                raise RuntimeError("能力表读取失败")
            return False

        got = cycle_snapshot.broken_execution_venues(
            ("gate", "binance"), "demo",
            venue_registry=self._reg(open_flags={"gate": True, "binance": True}),
            venue_execution_ready=_ready)
        self.assertEqual(got, ["binance"], "异常所不得被当成'已死'，其余所照常判定")

if __name__ == "__main__":
    unittest.main()