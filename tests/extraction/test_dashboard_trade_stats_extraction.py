"""`r20_backend/dashboard_payload/trade_stats.py`（B3 第二十四刀）回归。

## 这个测试在守什么

`aggregate_trade_stats` 把「分钟+币种」平仓聚合滚动成仪表盘 KPI 需要的
当日/累计胜负统计。三处最容易错：

1. **尘埃过滤的双条件**：`abs(net) < 0.01 AND abs(gross_pnl) < 0.01`。
   只判一个会误删"净额小而毛额大"的真实交易（手续费吃掉全部利润、
   但方向确实盈利的单子）。
2. **过滤发生在 `by_inst` 记账之后** —— 所以分币种的 `trades` 计数
   **包含**尘埃单，全局 win/loss 计数**不含**。两者口径不同是**故意的**，
   不是 bug。若"顺手统一"，分币种表笔数会与前端预期不符。
3. **`today_bj_str in t_time` 是子串判断**（与 `bills.py` 同款），
   且 `today_realized_gross` 用 `gross_pnl`（不含手续费），而胜负用
   `pnl`（含手续费）—— 两个口径不能互相替代。
"""

from __future__ import annotations

import ast
import builtins
import random
import subprocess
import sys
import unittest
from pathlib import Path

from r20_backend.dashboard_payload.trade_stats import aggregate_trade_stats

# 第 143 刀：本模块从 `dashboard/app.py` 迁到 `r20_backend/dashboard_cache.py`。
# **对拍基线必须按历史路径取**（旧 revision 里只有 dashboard/app.py），
# LIVE 文件走新路径 —— 两者不可混用，否则基线取不到、对拍门必然失真。
PRE_MOVE_PATH = "dashboard/app.py"

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "r20_backend" / "dashboard_cache.py"
MODULE = ROOT / "r20_backend" / "dashboard_payload" / "trade_stats.py"

TODAY = "2026-09-14"


def _legacy(orders_by_key, *, today_bj_str):
    """搬走前 update_cache_cycle 里的内联平仓聚合（逐字原样）。"""
    today_win_trades = 0
    today_loss_trades = 0
    all_win_trades = 0
    all_loss_trades = 0
    all_win_amt = 0.0
    all_loss_amt = 0.0
    by_inst = {}
    today_realized_gross = 0.0

    for agg_k, o in orders_by_key.items():
        net = o["pnl"]
        inst = o["inst"]
        t_time = o["time"]
        if inst not in by_inst:
            by_inst[inst] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        by_inst[inst]["trades"] += 1
        by_inst[inst]["pnl"] += net
        if abs(net) < 0.01 and abs(o.get("gross_pnl", 0.0)) < 0.01:
            continue
        if net > 0:
            all_win_trades += 1
            all_win_amt += net
            by_inst[inst]["wins"] += 1
            if today_bj_str in t_time:
                today_win_trades += 1
        elif net < 0:
            all_loss_trades += 1
            all_loss_amt += abs(net)
            by_inst[inst]["losses"] += 1
            if today_bj_str in t_time:
                today_loss_trades += 1
        if today_bj_str in t_time:
            today_realized_gross += o["gross_pnl"]

    return {
        "by_inst": by_inst,
        "today_realized_gross": today_realized_gross,
        "today_win_trades": today_win_trades,
        "today_loss_trades": today_loss_trades,
        "all_win_trades": all_win_trades,
        "all_loss_trades": all_loss_trades,
        "all_win_amt": all_win_amt,
        "all_loss_amt": all_loss_amt,
    }


def _order(inst="BTC", time="2026-09-14 10:00", pnl=0.0, gross=None):
    return {"inst": inst, "time": time, "pnl": pnl,
            "gross_pnl": pnl if gross is None else gross}


def _call(orders, today=TODAY):
    return aggregate_trade_stats(orders, today_bj_str=today)


class BasicTest(unittest.TestCase):
    def test_empty_returns_zeroes(self):
        out = _call({})
        self.assertEqual(out["by_inst"], {})
        for k in ("today_realized_gross", "all_win_amt", "all_loss_amt"):
            self.assertEqual(out[k], 0.0)
        for k in ("today_win_trades", "today_loss_trades",
                  "all_win_trades", "all_loss_trades"):
            self.assertEqual(out[k], 0)

    def test_win_counted(self):
        out = _call({"k": _order(pnl=10.0)})
        self.assertEqual(out["all_win_trades"], 1)
        self.assertEqual(out["all_win_amt"], 10.0)
        self.assertEqual(out["today_win_trades"], 1)

    def test_loss_counted_with_absolute_amount(self):
        out = _call({"k": _order(pnl=-7.5)})
        self.assertEqual(out["all_loss_trades"], 1)
        self.assertEqual(out["all_loss_amt"], 7.5, "亏损额取绝对值")

    def test_zero_pnl_counts_as_neither(self):
        out = _call({"k": _order(pnl=0.0, gross=5.0)})
        self.assertEqual(out["all_win_trades"], 0)
        self.assertEqual(out["all_loss_trades"], 0)

    def test_all_keys_present(self):
        out = _call({})
        self.assertEqual(set(out), {
            "by_inst", "today_realized_gross", "today_win_trades",
            "today_loss_trades", "all_win_trades", "all_loss_trades",
            "all_win_amt", "all_loss_amt"})


class TodaySubstringTest(unittest.TestCase):
    def test_today_is_substring_match(self):
        out = _call({"k": _order(time="2026-09-14 10:00", pnl=1.0)})
        self.assertEqual(out["today_win_trades"], 1)

    def test_substring_vs_equality_is_distinguishable(self):
        """**唯一能区分 `in` 与 `==` 的输入**：`time` 带时间部分。

        `"2026-09-14" in "2026-09-14 10:00"` → True（正确）
        `"2026-09-14" == "2026-09-14 10:00"` → False（若实现写成 ==，这条会红）

        我第一版**没写这条**：负向验证把 `in` 改成 `==` 时测试**依然全绿**，
        因为我的所有 fixture 都是带时间部分的字符串 —— 而那种输入下
        `today_bj_str in t_time` 与 `== t_time` 结果**恰好相同**，
        差分是盲的。补上这条后该缺陷才被抓住。
        """
        self.assertIn(TODAY, "2026-09-14 10:00")
        self.assertNotEqual(TODAY, "2026-09-14 10:00")
        out = _call({"k": _order(time="2026-09-14 10:00", pnl=1.0)})
        self.assertEqual(out["today_win_trades"], 1,
                         "若实现是 ==，这里会是 0")

    def test_exact_date_only_time_string_also_matches(self):
        """`time` 恰好只含日期时 `in` 与 `==` 都成立 —— 故这条**不能**用来区分两者。"""
        out = _call({"k": _order(time=TODAY, pnl=1.0)})
        self.assertEqual(out["today_win_trades"], 1)
        # 第二百零三刀：这里原本是 `assertIn(TODAY, TODAY)` + `assertEqual(TODAY, TODAY)`
        # —— 同一个纯表达式两侧相等 ⇒ **恒真**，只是把"日期串上 in 与 == 恰好同真"这句话
        # 写成了断言。它没法验证任何东西（那种"恰好同真"正是本用例标题说的情况：
        # 这条用例**不能**用来区分 in 与 ==，故上面只断言真实计数）。
        self.assertEqual(TODAY, "2026-09-14", "TODAY 常量本身变了，本用例前提失效")

    def test_other_day_not_counted_as_today(self):
        out = _call({"k": _order(time="2026-09-13 23:59", pnl=1.0)})
        self.assertEqual(out["today_win_trades"], 0)
        self.assertEqual(out["all_win_trades"], 1, "但计入累计")

    def test_today_realized_gross_only_from_today(self):
        out = _call({
            "a": _order(time="2026-09-14 10:00", pnl=1.0, gross=10.0),
            "b": _order(time="2026-09-13 10:00", pnl=1.0, gross=100.0),
        })
        self.assertEqual(out["today_realized_gross"], 10.0,
                         "只累加当日行")

    def test_today_realized_gross_uses_gross_not_net(self):
        """`today_realized_gross` 用 `gross_pnl`（不含手续费），胜负用 `pnl`。"""
        out = _call({"k": _order(time="2026-09-14 10:00", pnl=1.0, gross=42.0)})
        self.assertEqual(out["today_realized_gross"], 42.0)
        self.assertEqual(out["all_win_amt"], 1.0)

    def test_gross_accumulated_even_for_losses(self):
        out = _call({"k": _order(time="2026-09-14 10:00", pnl=-5.0, gross=-5.0)})
        self.assertEqual(out["today_realized_gross"], -5.0)


class DustFilterTest(unittest.TestCase):
    def test_both_small_is_filtered(self):
        out = _call({"k": _order(pnl=0.005, gross=0.005)})
        self.assertEqual(out["all_win_trades"], 0, "两个都 < 0.01 → 尘埃，不计胜负")
        self.assertEqual(out["all_loss_trades"], 0)

    def test_small_net_but_large_gross_is_kept(self):
        """**双条件的关键**：净额小但毛额大 → 不是尘埃，要计入。

        这是"手续费吃掉全部利润、但方向确实盈利"的真实交易。
        若只判 `abs(net) < 0.01`，这笔会被误删。
        """
        out = _call({"k": _order(pnl=0.005, gross=12.0)})
        self.assertEqual(out["all_win_trades"], 1, "毛额大 → 是真交易，不是尘埃")

    def test_exactly_threshold_is_kept(self):
        """阈值是严格小于 0.01；等于 0.01 不过滤。"""
        out = _call({"k": _order(pnl=0.01, gross=0.01)})
        self.assertEqual(out["all_win_trades"], 1)

    def test_dust_still_counted_in_by_inst_trades(self):
        """⚠️ 口径差异：尘埃单**仍计入** `by_inst[inst]["trades"]`。

        过滤发生在 `by_inst` 记账**之后**，所以分币种笔数 ≥ 全局胜负笔数。
        这是**故意的**，不是 bug —— 若"顺手统一"，分币种表会与前端预期不符。
        """
        out = _call({"k": _order(inst="BTC", pnl=0.001, gross=0.001)})
        self.assertEqual(out["all_win_trades"], 0, "全局不计")
        self.assertEqual(out["by_inst"]["BTC"]["trades"], 1, "分币种仍计")
        self.assertEqual(out["by_inst"]["BTC"]["wins"], 0, "但 wins 不计")

    def test_dust_pnl_still_accumulated_in_by_inst_pnl(self):
        out = _call({"k": _order(inst="BTC", pnl=0.001, gross=0.001)})
        self.assertAlmostEqual(out["by_inst"]["BTC"]["pnl"], 0.001, places=10)


class ByInstTest(unittest.TestCase):
    def test_grouped_by_inst(self):
        out = _call({
            "a": _order(inst="BTC", pnl=10.0),
            "b": _order(inst="ETH", pnl=-3.0),
            "c": _order(inst="BTC", pnl=-1.0),
        })
        self.assertEqual(set(out["by_inst"]), {"BTC", "ETH"})
        self.assertEqual(out["by_inst"]["BTC"]["trades"], 2)
        self.assertEqual(out["by_inst"]["BTC"]["wins"], 1)
        self.assertEqual(out["by_inst"]["BTC"]["losses"], 1)
        self.assertAlmostEqual(out["by_inst"]["BTC"]["pnl"], 9.0, places=10)
        self.assertEqual(out["by_inst"]["ETH"]["trades"], 1)

    def test_by_inst_new_dict_each_call(self):
        """不得在多次调用间共享 `by_inst`（否则会跨周期累积）。"""
        a = _call({"k": _order(inst="BTC", pnl=1.0)})
        b = _call({"k": _order(inst="ETH", pnl=1.0)})
        self.assertEqual(set(a["by_inst"]), {"BTC"})
        self.assertEqual(set(b["by_inst"]), {"ETH"})
        self.assertIsNot(a["by_inst"], b["by_inst"])


class RandomParityTest(unittest.TestCase):
    def test_random_parity(self):
        rng = random.Random(20261001)
        for _ in range(5000):
            n = rng.randint(0, 6)
            orders = {}
            for i in range(n):
                orders[f"k{i}"] = _order(
                    inst=rng.choice(["BTC", "ETH", "SOL"]),
                    # 含"带时间部分"的形态（区分 in/== 的关键）与"仅日期"形态
                time=rng.choice(["2026-09-14 10:00", "2026-09-14 23:59",
                                 "2026-09-14", "2026-09-13 10:00",
                                 "2026-09-01 00:00", ""]),
                    pnl=rng.choice([-50.0, -0.005, 0.0, 0.005, 0.01, 1.0, 100.0]),
                    gross=rng.choice([-50.0, -0.005, 0.0, 0.005, 0.01, 12.0, 100.0]),
                )
            today = rng.choice(["2026-09-14", "2026-09-13", "2026-01-01"])
            got = _call(orders, today)
            exp = _legacy(orders, today_bj_str=today)
            self.assertEqual(got, exp, f"分叉: today={today} orders={orders}")


class WiringTest(unittest.TestCase):
    def test_impl_in_submodule_not_facade(self):
        app_src = APP.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        self.assertIn("def aggregate_trade_stats(", mod_src)
        self.assertNotIn("def aggregate_trade_stats(", app_src)
        # 第九十五刀：调用点随相位 4 聚合段迁入**同模块**的 aggregate_bills_and_metrics
        self.assertIn("_core_aggregate_trade_stats(", mod_src)
        self.assertIn("_core_aggregate_trade_stats=_core_aggregate_trade_stats", app_src,
                      "门面仍须注入实现（调用期解析 ⇒ patch 面有效）")

    def test_facade_no_longer_contains_the_inline_loop(self):
        app_src = APP.read_text(encoding="utf-8")
        for gone in ("friction dust", "all_win_amt += net", 'by_inst[inst]["losses"] += 1'):
            self.assertNotIn(gone, app_src, f"门面仍残留内联片段 {gone!r}")

    def test_today_accumulation_uses_plus_equal(self):
        """`today_realized_gross` 必须 `+=` 而不是 `=`。

        bills 段可能已经给了非零初值（虽然当前实现恒 0），用 `=` 会把它覆盖掉。
        这条钉住"不要为了简洁改成赋值"。
        """
        # 第九十五刀：该累加随聚合段迁入 MODULE（语义未变：仍是 `+=` 而非 `=`）
        mod_src = MODULE.read_text(encoding="utf-8")
        self.assertIn('today_realized_gross += _stats["today_realized_gross"]', mod_src)
        self.assertNotIn('today_realized_gross += _stats["today_realized_gross"]',
                         APP.read_text(encoding="utf-8"),
                         "门面不得残留该行（残留=孪生）")

    def test_module_uses_substring_not_equality_for_today(self):
        """直接在源码层面钉住 `in`（3 处）—— 与上面的行为测试互为补充。"""
        mod_src = MODULE.read_text(encoding="utf-8")
        body = mod_src.split('"""', 2)[-1]      # 跳过模块文档串（它"提到"了 in）
        self.assertEqual(body.count("if today_bj_str in t_time:"), 3,
                         "三处当日判定都必须是子串判断")
        self.assertNotIn("today_bj_str == t_time", body)

    def test_all_eight_outputs_consumed_by_aggregation_stage(self):
        """八个键一个都不能漏 —— 原意不变，接出点随相位 4 聚合段迁入 MODULE。

        （原名 `..._by_facade`；第九十五刀后这些 `_stats[...]` 取值住在
        `aggregate_bills_and_metrics` 里，判定对象随实现迁移。）
        """
        mod_src = MODULE.read_text(encoding="utf-8")
        for key in ("by_inst", "today_realized_gross", "today_win_trades",
                    "today_loss_trades", "all_win_trades", "all_loss_trades",
                    "all_win_amt", "all_loss_amt"):
            self.assertIn(f'_stats["{key}"]', mod_src, f"聚合段未取用 {key}")

    def test_module_is_pure_no_dashboard_import(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertFalse(a.name.startswith("r20_backend.dashboard_cache"),
                                     f"反向 import {a.name}")
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or "").startswith("r20_backend.dashboard_cache"),
                                 f"反向 import {node.module}")

    def test_module_does_not_read_external_state(self):
        """纯计算：除了入参，不得出现模块级/全局读取。"""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        # `tree.body[0]` 是模块文档串（Expr），要显式找函数
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
        assigned = {n.id for n in ast.walk(fn)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        loaded = {n.id for n in ast.walk(fn)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        import builtins
        free = loaded - assigned - params - set(dir(builtins))
        self.assertEqual(free, set(), f"存在未绑定的外部名: {sorted(free)}")


if __name__ == "__main__":
    unittest.main()


# ============================================================================
# 第九十五刀：相位 4 聚合段（票据聚合 + 派生指标）搬入本模块
# ============================================================================

AGG_PRE = "303c8dc"          # 该刀动工前最后提交（第九十四刀收口）
AGG_FN = "aggregate_bills_and_metrics"
AGG_SEG = (13, 40)           # 基线 update_cache_cycle 的语句下标区间
AGG_FIELDS = ("all_closed", "all_loss_amt", "all_loss_trades", "all_win_amt",
              "all_win_rate", "all_win_trades", "avg_loss", "avg_win", "cum_roi_pct",
              "cum_total_fees", "funding_history_list", "inst_leaderboard",
              "profit_factor", "today_fees", "today_funding", "today_loss_trades",
              "today_net_realized_pnl", "today_realized_gross", "today_win_rate",
              "today_win_trades", "total_cum_net_pnl", "total_cum_realized_pnl")


def agg_field(got, name):
    """按字段名取返回项（不靠人肉数下标 —— 第九十四刀的教训）。"""
    return got[AGG_FIELDS.index(name)]


def _agg_baseline_cycle() -> ast.FunctionDef:
    r = subprocess.run(["git", "show", f"{AGG_PRE}:{PRE_MOVE_PATH}"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"基线取不到：{r.stderr[:200]}"
    t = ast.parse(r.stdout)
    return next(n for n in t.body if isinstance(n, ast.FunctionDef)
                and n.name == "update_cache_cycle")


def _agg_impl() -> ast.FunctionDef:
    t = ast.parse(MODULE.read_text(encoding="utf-8"))
    return next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == AGG_FN)


def _agg_body(fn: ast.FunctionDef) -> list:
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    if body and isinstance(body[-1], ast.Return):
        body = body[:-1]
    return body


class AggregationStageTest(unittest.TestCase):
    def test_segment_is_ast_identical_to_baseline(self):
        base = _agg_baseline_cycle()
        seg = base.body[AGG_SEG[0]:AGG_SEG[1] + 1]
        self.assertEqual(
            ast.dump(ast.Module(body=_agg_body(_agg_impl()), type_ignores=[]),
                     include_attributes=False),
            ast.dump(ast.Module(body=seg, type_ignores=[]), include_attributes=False),
            "聚合段段体与抽取前**不再同一棵 AST**")

    def test_call_passes_every_parameter_once_same_name(self):
        params = [a.arg for a in _agg_impl().args.kwonlyargs]
        t = ast.parse(APP.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(t) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == AGG_FN]
        self.assertEqual(len(calls), 1, "应恰有 1 处调用")
        call = calls[0]
        self.assertEqual(call.args, [])
        self.assertEqual([k.arg for k in call.keywords], params,
                         "调用点参数与签名不一致（漏传=生产 NameError）")
        for k in call.keywords:
            self.assertEqual(ast.unparse(k.value), k.arg, f"{k.arg} 未按同名传参")

    def test_no_undeclared_free_names(self):
        fn = _agg_impl()
        module = ast.parse(MODULE.read_text(encoding="utf-8"))
        mod_names = set()
        for n in module.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mod_names.add(n.name)
            elif isinstance(n, ast.Assign):
                for tg in n.targets:
                    if isinstance(tg, ast.Name):
                        mod_names.add(tg.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    mod_names.add(a.asname or a.name.split(".")[0])
        local = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        for n in ast.walk(fn):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local.add(n.name); local |= {a.arg for a in n.args.args}
            if isinstance(n, ast.Lambda):
                local |= {a.arg for a in n.args.args}
            if isinstance(n, ast.comprehension):
                tg = n.target
                for e in (tg.elts if isinstance(tg, (ast.Tuple, ast.List)) else [tg]):
                    if isinstance(e, ast.Name):
                        local.add(e.id)
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                local.add(n.id)
            if isinstance(n, ast.ExceptHandler) and n.name:
                local.add(n.name)
        reads = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)
                 and isinstance(n.ctx, ast.Load)}
        missing = sorted(reads - local - set(dir(builtins)) - mod_names)
        self.assertEqual(missing, [], f"解析不到的名字（会 NameError）: {missing}")

    # ---------- 行为例：累加顺序 + 除零分支 ----------

    def _run(self, *, bills, stats, total_eq=1100.0, initial=1000.0, pos_upl=25.0):
        from r20_backend.dashboard_payload import trade_stats as TS
        return TS.aggregate_bills_and_metrics(
            bills_data=[], initial_capital_val=initial, reset_time_str="2026-09-15 00:00:00",
            today_bj_str="2026-09-15", total_eq=total_eq, total_pos_upl=pos_upl,
            tz_beijing=None,
            _core_aggregate_bills=lambda *a, **k: bills,
            _core_aggregate_trade_stats=lambda *a, **k: stats,
            _core_build_inst_leaderboard=lambda by_inst: [len(by_inst)],
            datetime=None)

    @staticmethod
    def _bills(gross=100.0):
        return {"orders_by_key": {"k": 1}, "today_realized_gross": gross, "today_fees": -10.0,
                "cum_total_fees": -99.0, "today_funding": 5.0, "funding_history_list": [1, 2]}

    @staticmethod
    def _stats(gross=50.0, win=3, loss=1, awt=10, alt=5, awa=1000.0, ala=400.0):
        return {"by_inst": {"BTC": 1}, "today_realized_gross": gross,
                "today_win_trades": win, "today_loss_trades": loss,
                "all_win_trades": awt, "all_loss_trades": alt,
                "all_win_amt": awa, "all_loss_amt": ala}

    def test_gross_accumulates_bills_then_stats(self):
        """`today_realized_gross` 先取票据、再累加统计 —— 顺序不可调换。"""
        got = self._run(bills=self._bills(100.0), stats=self._stats(gross=50.0))
        self.assertEqual(agg_field(got, "today_realized_gross"), 150.0)
        self.assertEqual(agg_field(got, "today_net_realized_pnl"), round(150.0 - 10.0 + 5.0, 2),
                         "净已实现 = 毛额 + 手续费 + 资金费（三项口径不混）")

    def test_ratios_and_means(self):
        got = self._run(bills=self._bills(), stats=self._stats())
        self.assertEqual(agg_field(got, "today_win_rate"), 75.0)
        self.assertEqual(agg_field(got, "all_win_rate"), round(10 / 15 * 100, 1))
        self.assertEqual(agg_field(got, "profit_factor"), round(1000.0 / 400.0, 2))
        self.assertEqual(agg_field(got, "avg_win"), round(1000.0 / 10, 2))
        self.assertEqual(agg_field(got, "avg_loss"), round(400.0 / 5, 2))
        self.assertEqual(agg_field(got, "today_win_trades"), 3)
        self.assertEqual(agg_field(got, "funding_history_list"), [1, 2])
        self.assertEqual(agg_field(got, "inst_leaderboard"), [1])

    def test_divide_by_zero_branches_are_preserved(self):
        """无成交/无亏损时的分支值：0.0 / 99.0 —— 前端按这些值渲染。"""
        got = self._run(bills=self._bills(0.0),
                        stats=self._stats(gross=0.0, win=0, loss=0, awt=0, alt=0,
                                          awa=0.0, ala=0.0))
        self.assertEqual(agg_field(got, "today_win_rate"), 0.0)
        self.assertEqual(agg_field(got, "all_win_rate"), 0.0)
        self.assertEqual(agg_field(got, "avg_win"), 0.0)
        self.assertEqual(agg_field(got, "avg_loss"), 0.0)
        self.assertEqual(agg_field(got, "profit_factor"), 0.0, "无盈利无亏损 ⇒ 0.0")
        only_win = self._run(bills=self._bills(0.0),
                             stats=self._stats(gross=0.0, awa=500.0, ala=0.0, alt=0))
        self.assertEqual(agg_field(only_win, "profit_factor"), 99.0,
                         "有盈利但无亏损 ⇒ 99.0（哨兵值，不是 inf）")

    def test_cumulative_pnl_uses_equity_and_base_capital(self):
        got = self._run(bills=self._bills(), stats=self._stats(),
                        total_eq=1100.0, initial=1000.0, pos_upl=25.0)
        self.assertEqual(agg_field(got, "total_cum_net_pnl"), 100.0)
        self.assertEqual(agg_field(got, "cum_roi_pct"), 10.0)
        self.assertEqual(agg_field(got, "total_cum_realized_pnl"), 75.0,
                         "累计已实现 = 累计净值 − 当前浮动盈亏")
        zero_base = self._run(bills=self._bills(), stats=self._stats(), initial=0.0)
        self.assertEqual(agg_field(zero_base, "cum_roi_pct"), 0.0, "本金为 0 ⇒ 0.0（不炸）")

    def test_judgment_actually_notices_a_change(self):
        base = _agg_baseline_cycle()
        seg = base.body[AGG_SEG[0]:AGG_SEG[1] + 1]
        self.assertNotEqual(
            ast.dump(ast.Module(body=seg + [ast.Pass()], type_ignores=[]), include_attributes=False),
            ast.dump(ast.Module(body=seg, type_ignores=[]), include_attributes=False),
            "自检：判据看不见语句增减")
