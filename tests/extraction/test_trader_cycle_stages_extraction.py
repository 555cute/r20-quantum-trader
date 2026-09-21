r"""cycle_stages 抽取对拍门（结构优化阶段 4·B3 第九十一刀）。

`execute_portfolio` 的三个相位段 **纯搬家**到 `scripts/trader/cycle_stages.py`：

| 函数 | 原相位 |
|---|---|
| `preflight_reconcile_and_housekeeping` | 0/0a（引擎就绪闸 + 挂单对账 + 陈旧单回收 + 舆情） |
| `fetch_universe_and_manage_positions` | 2-3（并发取因子 + 逐仓追踪退出） |
| `persist_state_and_sync_ledger` | 5-6（面板持久化 + 台账/SQLite 同步） |

判据同第九十刀：段体 **AST 逐字**、调用点**逐个同名恰好一次**、
自由名全可解析；另加**中止哨兵行为**（段内 `return None` = 本周期中止）
与两个 smoke 例（副作用全部替身/指向临时目录 —— §104.2 教训）。
"""
from __future__ import annotations

import ast
import builtins
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRE = "d90fac5"          # 本刀动工前最后提交（第九十刀收口）
MOD = "scripts/trader/cycle_stages.py"
SPECS = {  # 函数名 -> (该刀动工前的提交, 基线 execute_portfolio 的语句下标区间)
    # 每刀基线不同（分段逐个抽出，下标随之前移）⇒ 每项自带版本，避免"用错基线"。
    "scan_risk_gates_and_ai_brain": ("672b3e5", 7, 11),        # 第九十三刀：相位 4 前段
    "fetch_positions_and_reconcile": ("d90fac5", 11, 39),      # 第九十二刀：相位 1
    "preflight_reconcile_and_housekeeping": ("d90fac5", 0, 10),  # 第九十一刀
    "fetch_universe_and_manage_positions": ("d90fac5", 40, 46),  # 第九十一刀
    "persist_state_and_sync_ledger": ("d90fac5", 53, 58),        # 第九十一刀
}


def _baseline_portfolio(rev: str = PRE) -> ast.FunctionDef:
    r = subprocess.run(["git", "show", f"{rev}:scripts/ai_factor_trader.py"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"基线取不到({rev})：{r.stderr[:200]}"
    t = ast.parse(r.stdout)
    return next(n for n in t.body if isinstance(n, ast.FunctionDef)
                and n.name == "execute_portfolio")


def _module_tree() -> ast.Module:
    return ast.parse((ROOT / MOD).read_text(encoding="utf-8"))


def _func(name: str) -> ast.FunctionDef:
    for n in _module_tree().body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} 不在 {MOD} 顶层")


def _seg_stmts(fn: ast.FunctionDef) -> list:
    """去掉本刀新增的 docstring 与末尾追加的 return。"""
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    if body and isinstance(body[-1], ast.Return):
        body = body[:-1]
    return body


#: ⚠️ **文档化差异**（第一百二十六刀新增本表）：抽取门默认要求段体与基线
#: **同一棵 AST**；某一段若确需**有意的行为修复**，必须在此登记"旧文本 → 新文本"，
#: 于是"基线 + 差异 == 新段"，表外任何改动照旧翻红。
#:
#: 本刀唯一一条：预留对账的调用点必须把**跨所实况是否核验成功**交给对账器。
#: 缺陷形状（实测）：`fetch_other_venue_positions` 读取失败返回 `(False, {}, err)`，
#: 调用点原样把**空字典**透传 ⇒ 对账器据 `{}` 判"外所无仓无挂"，把**活仓的外所预留**
#: （binance 726U）按超 TTL 释放成 `closed`（释放不可逆 ⇒ 台账少算活仓）。
SEGMENT_DELTAS = {
    "fetch_positions_and_reconcile": [
        # ---- 第一百二十七刀：挂单枚举失败也要进"实况是否核验" -------------------
        # `xv_ok` 只覆盖**持仓**读取；只核验持仓时，某所一笔**未成交**的入场单
        # （尚无持仓）会被对账器判"无仓无挂"并误释放（释放不可逆 ⇒ 台账少算在场活单）。
        ("pending_inst_ids, pending_long_count, pending_short_count = "
         "collect_pending_inst_ids(venues=('gate', 'binance'), venue_mode=_gv_mode, "
         "broken_venues=_BROKEN_VENUES, venue_registry=venue_registry, "
         "load_instruments=load_instruments, auth_markers=_auth_markers, warn=print)",
         "_pending_enum_errors: list = []\n"
         "\n"
         "def _pending_warn(_msg):\n"
         "    _pending_enum_errors.append(_msg)\n"
         "    print(_msg)\n"
         "pending_inst_ids, pending_long_count, pending_short_count = "
         "collect_pending_inst_ids(venues=('gate', 'binance'), venue_mode=_gv_mode, "
         "broken_venues=_BROKEN_VENUES, venue_registry=venue_registry, "
         "load_instruments=load_instruments, auth_markers=_auth_markers, "
         "warn=_pending_warn)"),
        # ---- 第一百二十六/二十七刀：把"跨所实况是否核验成功"交给对账器 ----------
        ("reconcile_reservation_ledger(real_pos_dict, pending_inst_ids, _xv_env, "
         "venue_snapshot=xv_positions_by_venue)",
         "reconcile_reservation_ledger(real_pos_dict, pending_inst_ids, _xv_env, "
         "venue_snapshot=xv_positions_by_venue, "
         "venue_snapshot_verified=xv_ok and (not _pending_enum_errors))"),
        # ---- 第一百三十一刀：凭证已死场所**每周期明说"未计入"** ----------------
        # 此类所被 `venue_execution_ready` 否决 ⇒ `fetch_other_venue_positions` 也跳过，
        # 且返回 ok=True **无错误** ⇒ 其持仓/挂单不进配额与敞口，而跨所笔数看似完整。
        # 判据抽成纯函数 `broken_execution_venues`（可单元测试），此处只接线 + 告警。
        ("_gv_mode = ''",
         "_gv_mode = ''\n"
         "try:\n"
         "    _xv_broken = list(broken_execution_venues(('gate', 'binance'), _gv_mode, "
         "venue_registry=venue_registry, venue_execution_ready=venue_execution_ready))\n"
         "except Exception as _bv_exc:\n"
         "    _xv_broken = []\n"
         "    print(f'[跨所封顶] warn 坏所探测异常（不影响本周期）: {_bv_exc}')\n"
         "if _xv_broken:\n"
         "    print(f\"[跨所封顶] warn {'/'.join(_xv_broken)} 凭证已死（执行闸开着却不可就绪）"
         "——该所持仓/挂单**未计入**本周期配额与敞口（跨所笔数不含该所），"
         "修好密钥后自动恢复；请勿据面板跨所笔数当作全景\")"),
        # ---- 第一百二十八刀：槽位计数**少算**时的如实告知（零行为变更） ---------
        # 外所挂单枚举失败 ⇒ `reserved_*_count` 少算该所在场单，而执行层开仓闸用的
        # 正是它们 ⇒ 可能超发槽位。本行只把后果讲明（是否改 fail-closed 待人工拍板）。
        ("reserved_short_count = short_count + pending_short_count",
         "reserved_short_count = short_count + pending_short_count\n"
         "if _pending_enum_errors:\n"
         "    print(f'[跨所封顶] warn 外所挂单未枚举成功（{len(_pending_enum_errors)} 所）"
         "——本周期槽位/同向占用**少算**该所在场单（{reserved_slot_count} 为下限），"
         "若照常放行新开仓可能突破仓位上限（仅仓位数口径；USDT 预算不受影响）')"),
        # ---- 第二百二十刀：恢复 OKX 在途挂单进槽位/对账（**回归修复**）------------
        # 抽取（bb6cb57）把原来「OKX loop 建基准 + 外所枚举 add 进来」写成了**整体赋值**
        # ⇒ OKX 在途挂单被静默丢弃：① 槽位/同向少算 ⇒ 开仓闸可能超发；
        # ② `reconcile_reservation_ledger` 拿不到 OKX 在场活单 ⇒ 据「无仓无挂」
        # 当陈旧占用**释放**（释放不可逆）。此处恢复为并集。
        ("pending_inst_ids, pending_long_count, pending_short_count = "
         "collect_pending_inst_ids(venues=('gate', 'binance'), venue_mode=_gv_mode, "
         "broken_venues=_BROKEN_VENUES, venue_registry=venue_registry, "
         "load_instruments=load_instruments, auth_markers=_auth_markers, "
         "warn=_pending_warn)",
         "_xv_pending_ids, _xv_pending_long, _xv_pending_short = "
         "collect_pending_inst_ids(venues=('gate', 'binance'), venue_mode=_gv_mode, "
         "broken_venues=_BROKEN_VENUES, venue_registry=venue_registry, "
         "load_instruments=load_instruments, auth_markers=_auth_markers, "
         "warn=_pending_warn)\n"
         "pending_inst_ids |= {str(_x) for _x in _xv_pending_ids or set() if _x}\n"
         "pending_long_count += int(_xv_pending_long or 0)\n"
         "pending_short_count += int(_xv_pending_short or 0)"),
    ],
}


class ReconcileCallContractTest(unittest.TestCase):
    def test_release_requires_both_position_and_order_sides_verified(self):
        """跨所实况的**持仓侧与挂单侧都核验成功**，才允许对账器释放预留。

        只核验持仓时，一笔**未成交**的入场单（尚无持仓）会被判"无仓无挂"而误释放；
        释放不可逆 ⇒ 预算台账少算在场活单。本断言把这条语义钉在**调用点**上，
        防止有人改回只传 `xv_ok`。
        """
        fn = _func("fetch_positions_and_reconcile")
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "reconcile_reservation_ledger"]
        self.assertEqual(len(calls), 1, "对账调用点应恰 1 处")
        kw = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
        self.assertIn("venue_snapshot_verified", kw)
        self.assertIn("xv_ok", kw["venue_snapshot_verified"], "持仓侧核验必须参与")
        self.assertIn("_pending_enum_errors", kw["venue_snapshot_verified"],
                      "挂单枚举失败也必须挡住释放（否则在场活单的预留会被误释放）")


class QuotaUnderCountIsDisclosedTest(unittest.TestCase):
    """槽位计数**少算**时必须如实告知，且"输入失败语义表"必须留在 docstring 里。

    背景（第一百二十八刀逐项实测）：外所挂单枚举失败时，`reserved_slot_count` /
    `reserved_long_count` / `reserved_short_count` **少算**该所的在场单，而执行层的
    开仓闸用的正是它们（`reserved_slot_count < MAX_CONCURRENT_POSITIONS`）⇒ 可能超发槽位。
    持仓侧失败会 `entries_blocked=True`，**挂单侧目前不拦**（唯一残留缺口，待人工拍板）。

    本门钉两件事：① 少算的那一刻有明确告知（不许静默）；② 审计表随代码走
    （谁改了语义就必须更新表，否则门会指向这里）。
    """

    def test_under_count_is_disclosed_at_the_quota_computation(self):
        fn = _func("fetch_positions_and_reconcile")
        hits = []
        for node in ast.walk(fn):
            if isinstance(node, ast.If) and "_pending_enum_errors" in ast.unparse(node.test):
                body_src = "\n".join(ast.unparse(s) for s in node.body)
                if "print" in body_src:
                    hits.append(body_src)
        self.assertTrue(hits, "挂单枚举失败时必须在计数处给出告知（零行为变更但不得静默）")
        self.assertTrue(any("少算" in h for h in hits),
                        "告知文案必须讲明'少算'及其口径（仓位数，不涉及 USDT 预算）")

    def test_failure_semantics_table_is_kept(self):
        doc = ast.get_docstring(_func("fetch_positions_and_reconcile")) or ""
        self.assertIn("输入失败语义表", doc, "逐项失败语义表必须随函数走")
        for must in ("整周期 abort", "entries_blocked=True", "残留缺口"):
            self.assertIn(must, doc, f"审计表缺少关键结论：{must}")


class CycleStagesVerbatimTest(unittest.TestCase):
    def test_segments_are_ast_identical_to_baseline(self):
        for name, (rev, lo, hi) in SPECS.items():
            with self.subTest(fn=name):
                base = _baseline_portfolio(rev)
                seg = base.body[lo:hi + 1]
                got = _seg_stmts(_func(name))
                # ⚠️ 用 `ast.unparse` 而不是 `ast.dump`：只有源码形态才做得了
                # "文档化差异"的文本替换（与本仓 reservation_reconcile 门同一手法）。
                # 两侧都来自 `ast.parse` ⇒ 仍是结构化比较，不受空白/换行影响。
                base_src = ast.unparse(ast.Module(body=seg, type_ignores=[]))
                got_src = ast.unparse(ast.Module(body=got, type_ignores=[]))
                for _old_tok, _new_tok in SEGMENT_DELTAS.get(name, []):
                    self.assertIn(_old_tok, base_src,
                                  f"{name} 的文档化差异锚点在基线里找不到"
                                  "（差异必须唯一且可核对）")
                    base_src = base_src.replace(_old_tok, _new_tok)
                self.assertEqual(
                    base_src, got_src,
                    f"{name} 段体与抽取前**不再同一棵 AST**（超出文档化差异）")

    def test_facade_calls_pass_every_parameter_once_same_name(self):
        facade = ast.parse((ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8"))
        for name in SPECS:
            with self.subTest(fn=name):
                params = [a.arg for a in _func(name).args.kwonlyargs]
                calls = [n for n in ast.walk(facade)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                         and n.func.id == name]
                self.assertEqual(len(calls), 1, f"{name} 调用点应恰 1 处")
                call = calls[0]
                self.assertEqual(call.args, [], f"{name} 应全关键字传参")
                self.assertEqual([k.arg for k in call.keywords], params,
                                 f"{name} 调用点参数与签名不一致（漏传=生产 NameError）")
                for k in call.keywords:
                    self.assertEqual(ast.unparse(k.value), k.arg,
                                     f"{name}.{k.arg} 未按同名传参")

    def test_no_undeclared_free_names(self):
        module = _module_tree()
        module_names = set()
        for n in module.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_names.add(n.name)
            elif isinstance(n, ast.Assign):
                for tg in n.targets:
                    if isinstance(tg, ast.Name):
                        module_names.add(tg.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    module_names.add(a.asname or a.name.split(".")[0])
        for name in SPECS:
            with self.subTest(fn=name):
                fn = _func(name)
                local = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
                for n in ast.walk(fn):
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        local.add(n.name); local |= {a.arg for a in n.args.args}
                    if isinstance(n, ast.Lambda):
                        local |= {a.arg for a in n.args.args}
                    if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                        local.add(n.id)
                    if isinstance(n, ast.ExceptHandler) and n.name:
                        local.add(n.name)
                    if isinstance(n, (ast.Import, ast.ImportFrom)):
                        for a in n.names:
                            local.add(a.asname or a.name.split(".")[0])
                reads = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)
                         and isinstance(n.ctx, ast.Load)}
                missing = sorted(reads - local - set(dir(builtins)) - module_names)
                self.assertEqual(missing, [], f"{name} 有解析不到的名字: {missing}")

    def test_preflight_abort_sentinel_returns_none(self):
        """段内 `return None` = 本周期中止 ⇒ helper 必须把 None 透出来。"""
        from scripts.trader import cycle_stages as cs
        env = types.SimpleNamespace(configured=False, mode="demo")
        got = cs.preflight_reconcile_and_housekeeping(
            WORKSPACE_DIR="/nonexistent", _run_captured=lambda *a, **k: None,
            clean_stale_open_orders=lambda **k: (True, ""),
            current_environment=lambda: env, datetime=__import__("datetime"),
            load_trackers=lambda: {}, os=os,
            reconcile_pending_orders=lambda **k: (True, set()))
        self.assertIsNone(got, "引擎未就绪必须中止（返回 None）")

    def test_fetch_universe_smoke_with_empty_universe(self):
        from scripts.trader import cycle_stages as cs
        class _Ex:
            def __init__(self, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def map(self, fn, items): return [fn(i) for i in items]
        got = cs.fetch_universe_and_manage_positions(
            all_positions=[], real_pos_dict={}, timestamp_full="2026-09-15 08:00:00",
            usdt_available=0.0, TARGET_INSTRUMENTS=[], ThreadPoolExecutor=_Ex,
            fetch_single_instrument_data=lambda *a, **k: None, load_trackers=lambda: {},
            manage_position_tp_and_trailing=lambda *a, **k: None,
            prune_trackers=lambda t, r: 0, save_trackers=lambda t: None)
        self.assertEqual(len(got), 3, "返回 (all_factors, executed_actions, trackers)")
        self.assertEqual(got[0], [], "空宇宙应得空因子表")
        self.assertEqual(got[2], {}, "追踪器应为空")

    def test_persist_smoke_writes_only_into_temp(self):
        """副作用替身 + LOG_FILE 指向临时目录（§104.2：替身清单照实现体抄）。"""
        from scripts.trader import cycle_stages as cs
        written = []
        with tempfile.TemporaryDirectory() as td:
            cs.persist_state_and_sync_ledger(
                _xv_total=0, active_pos_count=0, all_factors=[], cb_active=False,
                cb_reason="", executed_actions=[],
                long_count=0, short_count=0, timestamp_full="2026-09-15 08:00:00",
                DATA_DIR=td, LEDGER_AUTOSYNC_ENABLED=False,
                LOG_FILE=os.path.join(td, "t.log"), MAX_CONCURRENT_POSITIONS=6,
                WORKSPACE_DIR=td, __version__="test",
                _atomic_write_json=lambda path, payload: written.append(path),
                _run_captured=lambda *a, **k: None,
                build_state_payload=lambda **k: {"ok": True},
                evaluate_asset_signal=lambda *a, **k: None, os=os)
            self.assertEqual(written, [os.path.join(td, "trading_state.json")],
                             "面板状态必须写进（被替身捕获的）目标路径")
            self.assertFalse(os.path.exists(os.path.join(td, "trading_state.json")),
                             "替身后不得真的落盘")

    def test_positions_abort_sentinel_returns_none(self):
        """相位 1：查持仓失败必须中止本周期（段内 `return None` 语义）。"""
        from scripts.trader import cycle_stages as cs
        with tempfile.TemporaryDirectory() as td:
            got = cs.fetch_positions_and_reconcile(
                entries_blocked=False,
                _BROKEN_VENUES=set(), collect_pending_inst_ids=lambda **k: (set(), 0, 0),
                current_environment=lambda: types.SimpleNamespace(mode="demo", simulated=False),
                fetch_other_venue_positions=lambda env: (True, {}, ""),
                load_instruments=lambda: [], okx_rest=types.SimpleNamespace(),
                query_positions=lambda: (False, [], "no creds"),
                reconcile_reservation_ledger=lambda *a, **k: None,
                venue_execution_ready=lambda v, e: False,
                broken_execution_venues=lambda *a, **k: [],
                venue_registry=types.SimpleNamespace())
        self.assertIsNone(got, "查持仓失败必须中止（返回 None）")

    def test_dead_credential_venue_is_disclosed_as_excluded(self):
        """凭证已死的所必须**每周期明说"未计入"**（第一百三十一刀）。

        该所被 `venue_execution_ready` 否决 ⇒ 跨所取数也跳过它，且返回 `ok=True`
        无任何错误 ⇒ 它的持仓/挂单不进配额与敞口，而"跨所笔数"看起来完整。
        方向纪律：它**读不出来**（不是没有仓），所以只能说"未计入"，绝不装作干净。
        本用例用**真的** `broken_execution_venues`（不是桩），把判据也一并跑到。
        """
        import contextlib
        import io
        from scripts.trader import cycle_stages as cs
        from scripts.trader.cycle_snapshot import broken_execution_venues as real_broken
        okx = types.SimpleNamespace(balances=lambda: None, positions=lambda: None,
                                    pending_orders=lambda *a: [])
        reg = types.SimpleNamespace(execution_open=lambda v, e: True,     # 闸开着…
                                    get_adapter=lambda v, environment=None: None,
                                    is_registered=lambda k: True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = cs.fetch_positions_and_reconcile(
                entries_blocked=False,
                _BROKEN_VENUES={"binance"}, collect_pending_inst_ids=lambda **k: (set(), 0, 0),
                current_environment=lambda: types.SimpleNamespace(mode="demo", simulated=False),
                fetch_other_venue_positions=lambda env: (True, {}, ""),
                load_instruments=lambda: [], okx_rest=okx,
                query_positions=lambda: (True, [], ""),
                reconcile_reservation_ledger=lambda *a, **k: None,
                venue_execution_ready=lambda v, e: v != "binance",   # …却不可就绪
                broken_execution_venues=real_broken, venue_registry=reg)
        out = buf.getvalue()
        self.assertIsNotNone(got, "坏所不得中断周期（跳过 + 明说即可）")
        self.assertEqual(len(got), 13)
        self.assertIn("binance 凭证已死", out)
        self.assertIn("未计入", out)
        self.assertNotIn("gate 凭证已死", out, "就绪的所不得被误报")

    def test_positions_empty_world_returns_thirteen_outputs(self):
        """空世界 smoke：10 项注入全活 ⇒ 必须产出 13 项输出（含持仓/额度/预留计数）。"""
        from scripts.trader import cycle_stages as cs
        okx = types.SimpleNamespace(balances=lambda: None, positions=lambda: None,
                                    pending_orders=lambda *a: [])
        reg = types.SimpleNamespace(execution_open=lambda v, e: False,
                                    get_adapter=lambda v, environment=None: None,
                                    is_registered=lambda k: False)
        got = cs.fetch_positions_and_reconcile(
            entries_blocked=False,
            _BROKEN_VENUES=set(), collect_pending_inst_ids=lambda **k: (set(), 0, 0),
            current_environment=lambda: types.SimpleNamespace(mode="demo", simulated=False),
            fetch_other_venue_positions=lambda env: (True, {}, ""),
            load_instruments=lambda: [], okx_rest=okx,
            query_positions=lambda: (True, [], ""),
            reconcile_reservation_ledger=lambda *a, **k: None,
            venue_execution_ready=lambda v, e: False,
            broken_execution_venues=lambda *a, **k: [], venue_registry=reg)
        self.assertIsNotNone(got)
        self.assertEqual(len(got), 13, "13 项输出必须齐（调用点按序解包）")
        self.assertEqual(got[2], [], "all_positions 应为空")
        self.assertFalse(got[3], "entries_blocked 应为 False（对账成功）")

    def _scan_kwargs(self, **over):
        """相位 4 前段的替身集合（照实现体调用形状抄，§104.2）。"""
        from scripts.trader import cycle_stages as _cs  # noqa: F401
        base = dict(
            _xv_total=0, active_pos_count=0, all_factors=[], executed_actions=[],
            long_count=0, short_count=0, timestamp_full="2026-09-15 09:00:00",
            trackers={}, usdt_available=1000.0, xv_positions_by_venue={},
            MAX_CONCURRENT_POSITIONS=6,
            _collect_okx_position_payloads=lambda *a, **k: [],
            _merge_cross_venue_positions=lambda *a, **k: [],
            effective_single_asset_margin=lambda u: 123.0,
            execute_ai_position_management=lambda *a, **k: None,
            execute_batch_ai_brain_cycle=None,
            is_circuit_breaker_active=lambda u: (False, ""),
            pool_is_trustworthy=lambda: True, pool_state=lambda: {},
            query_positions=lambda: (True, [], ""), read_cycle_health=lambda: {},
            save_trackers=lambda t: None)
        base.update(over)
        return base

    def test_scan_circuit_breaker_active_skips_llm(self):
        from scripts.trader import cycle_stages as cs
        acts = []
        got = cs.scan_risk_gates_and_ai_brain(**self._scan_kwargs(
            is_circuit_breaker_active=lambda u: (True, "黑天鹅"),
            executed_actions=acts))
        self.assertEqual(len(got), 4, "返回 (ASSET_MARGIN_CAP, brain_cache, cb_active, cb_reason)")
        self.assertEqual(got[0], 123.0, "单标的保证金上限由 effective_single_asset_margin 决定")
        self.assertEqual(got[1], {}, "熔断时不得调用 LLM（brain_cache 保持空）")
        self.assertTrue(got[2]); self.assertEqual(got[3], "黑天鹅")
        self.assertEqual(acts, [], "熔断时不应产生池闸告警")

    def test_scan_pool_untrusted_appends_failclosed_warning(self):
        from scripts.trader import cycle_stages as cs
        acts = []
        got = cs.scan_risk_gates_and_ai_brain(**self._scan_kwargs(
            executed_actions=acts, pool_is_trustworthy=lambda: False,
            pool_state=lambda: {"status": "corrupt", "detail": "坏"}))
        self.assertFalse(got[2], "非熔断")
        self.assertEqual(len(acts), 1, "池不可信必须留一条告警（fail-closed 可追溯）")
        self.assertIn("标的池不可信", acts[0])
        self.assertIn("禁止开新仓", acts[0])

    def test_scan_brain_cache_path_manages_positions(self):
        """LLM 有返回 ⇒ 必须刷新真实持仓并交给主脑执行器（深路径）。"""
        from scripts.trader import cycle_stages as cs
        seen = {}
        acts = []
        got = cs.scan_risk_gates_and_ai_brain(**self._scan_kwargs(
            executed_actions=acts,
            execute_batch_ai_brain_cycle=lambda desc, pos, usdt_available=None: {"BTC": {"action": "hold"}},
            query_positions=lambda: (True, [{"instId": "BTC-USDT-SWAP", "pos": "1", "posSide": "long"}], ""),
            execute_ai_position_management=lambda d, t, ts, a: seen.update(d=d, a=a),
            _collect_okx_position_payloads=lambda f, t: [{"instId": "BTC-USDT-SWAP"}]))
        self.assertEqual(got[1], {"BTC": {"action": "hold"}}, "brain_cache 必须回传")
        self.assertEqual(list(seen.get("d", {})), ["BTC-USDT-SWAP"],
                         "刷新后的持仓字典应交给主脑执行器")
        self.assertIs(seen.get("a"), acts, "executed_actions 必须**原地**传入（副作用回传）")

    def test_judgment_actually_notices_a_change(self):
        base = _baseline_portfolio("d90fac5")
        got = _seg_stmts(_func("fetch_universe_and_manage_positions"))
        seg = base.body[40:47]
        self.assertEqual(ast.dump(ast.Module(body=got, type_ignores=[]), include_attributes=False),
                         ast.dump(ast.Module(body=seg, type_ignores=[]), include_attributes=False))
        self.assertNotEqual(
            ast.dump(ast.Module(body=seg + [ast.Pass()], type_ignores=[]), include_attributes=False),
            ast.dump(ast.Module(body=seg, type_ignores=[]), include_attributes=False),
            "自检：判据看不见语句增减")


if __name__ == "__main__":
    unittest.main()
