"""仪表盘载荷拆分的「薄壳纪律」回归闸（结构优化阶段 2 / B2）。

## 背景：为什么需要这道闸

`r20_backend/dashboard_cache.py` 的域代码正被逐步迁到 `r20_backend/dashboard_payload/`。迁移用
**薄壳 + 核心**：核心收显式参数，门面薄壳在**调用时**解析门面模块全局（路径常量、
甚至可调用对象）并注入。

这么做的唯一理由是**保留测试注入接缝**：测试与 `tests/config_sandbox.py` 靠
`patch.object(dashboard, "LOG_FILE"/"STATE_JSON_FILE"/"DATA_DIR"/…)` 把读写重定向到沙箱。
核心若在导入期绑定这些名字，补丁就**静默失效**、读到真实项目文件 —— 那正是
"测试写生产 data/" 的事故形态。

## 本闸钉的四件事（每一条都由实际踩过的坑得来）

1. **薄壳不得自递归**。生成薄壳时漏掉 `_core_` 前缀会写成
   `return load_position_trackers(POSITION_TRACKER_FILE)` —— 无限递归，
   且只有在真的调用并带上多余实参时才暴露（`TypeError: takes 0 positional
   arguments but 1 was given`）。
2. **实参个数必须等于核心形参个数**。B6 曾出现核心新增 `root` 参数而薄壳没跟上。
3. **实参顺序必须与核心形参逐位对齐**。B2 第三刀把
   `(factor_file, decisions_file, state_file)` 传成了
   `(AI_DECISIONS_FILE, FACTOR_LIBRARY_FILE, STATE_JSON_FILE)` ——
   三个路径整体错配，症状是"因子库读到了决策文件"，而**单测全绿**
   （单测要么只喂 tracker、要么不校验这三个文件的具体来源），只有逐例差分才发现。
4. **核心模块不得反向 import `r20_backend.dashboard_cache`**（会与 `routers/dashboard.py` 的
   `import r20_backend.dashboard_cache` 构成循环）。

另外钉住门面公开面：测试直接经 `dashboard.<name>` 调用的那批符号必须仍在。
"""
from __future__ import annotations

import ast
import inspect
import sys
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import r20_backend.dashboard_cache as app  # noqa: E402
from r20_backend import dashboard_payload as payload_pkg  # noqa: E402
from r20_backend.dashboard_payload import (  # noqa: E402
    bills as bills_mod, cache, cache_payload, factors, health, integrity_sidecars,
    local_reads, market, order_view, position_view, slim, trade_stats,
    trader_leaderboard,
)

PAYLOAD_DIR = ROOT / "r20_backend" / "dashboard_payload"

# 测试与沙箱会直接访问的门面符号（实测清单：tests/*.py 的 dashboard.<name>）
FACADE_SURFACE = [
    # 路径常量（沙箱重定向目标）
    "DATA_DIR", "LOG_FILE", "STATE_JSON_FILE", "DASHBOARD_CACHE_FILE",
    "AI_DECISIONS_FILE", "AI_HISTORY_FILE", "AI_LAST_PROMPT_FILE", "AI_MEMORY_MD_FILE",
    "NEWS_SENTIMENT_FILE", "REPORT_JSON_FILE", "LEDGER_JSON_FILE",
    "POSITION_TRACKER_FILE", "FACTOR_LIBRARY_FILE", "SNAPSHOTS_JSON_FILE",
    # 已迁出的函数（必须仍可从 r20_backend.dashboard_cache 取到）
    "slim_payload", "load_position_trackers", "enrich_position_risk_fields",
    "_load_local_factor_library", "_build_factors_from_local_files",
    "_load_cross_venue_data", "load_trading_memory_md", "build_ai_health",
    "load_persisted_dashboard_cache", "persist_dashboard_cache",
    "_inject_local_data_into_stale", "_is_meaningful_dashboard_snapshot",
    "_safe_float", "_global_env_axis", "_load_portfolio_risk_data",
    "_load_multi_venue_portfolio", "get_target_instruments",
    # 载荷装配主体
    "update_cache_cycle", "refresh_cache_if_needed", "get_cache_lock",
]

# 核心模块白名单：新迁出的域模块都应在此，且必须被门面导入（否则 sandbox 覆盖不到）
CORE_MODULES = (slim, market, factors, health, cache, local_reads, bills_mod,
                position_view, order_view, trade_stats, trader_leaderboard,
                integrity_sidecars, cache_payload)


def _core_aliases() -> dict[str, object]:
    """收集 r20_backend.dashboard_cache 里 `from ...dashboard_payload.X import Y as _core_...` 的别名。"""
    out: dict[str, object] = {}
    for name, value in vars(app).items():
        if name.startswith("_core") and callable(value):
            out[name] = value
    return out


_STOP = {"file", "dir", "path", "json", "md", "a", "the", "of", "for", "in", "to"}


def _tokens(name: str) -> set[str]:
    """把标识符拆成有意义的小词（丢掉 file/dir/json/md 之类后缀词）。"""
    import re
    parts = re.split(r"[^a-z0-9]+", name.lower())
    return {p for p in parts if p and p not in _STOP}


class ShellDisciplineTests(unittest.TestCase):
    def setUp(self):
        self.aliases = _core_aliases()
        self.assertTrue(self.aliases, "r20_backend.dashboard_cache 里找不到任何 _core_* 薄壳别名")

    # ── 1/2/3. 薄壳调用形态 ────────────────────────────────────
    @staticmethod
    def _is_thin_shell(node: ast.AST, aliases: dict) -> ast.Call | None:
        """「薄壳」= 函数体只有一条 `return _core_xxx(...)`（可带 docstring）。

        纪律只钉薄壳：普通调用方（如 update_cache_cycle）会把**自己算出来的局部变量**
        传进核心，那是正常调用，不该按"注入门面全局"的规则要求它。
        """
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]
        if len(body) != 1 or not isinstance(body[0], ast.Return):
            return None
        call = body[0].value
        if not isinstance(call, ast.Call):
            return None
        if not (isinstance(call.func, ast.Name) and call.func.id in aliases):
            return None
        return call

    def test_every_shell_forwards_correctly(self):
        problems: list[str] = []
        checked = 0
        app_module_name = getattr(app, "__name__", "r20_backend.dashboard_cache")
        for fname, fobj in vars(app).items():
            if fname.startswith("__") or not inspect.isfunction(fobj):
                continue
            # 只审查**定义在本门面模块**里的函数：全量跑时别的测试会往 r20_backend.dashboard_cache
            # 上挂函数（inspect.getsource 拿到的首语句是赋值而非 def），
            # 单独跑该用例时不存在 → 曾导致"单跑通过、全量失败"。
            if getattr(fobj, "__module__", None) != app_module_name:
                continue
            try:
                src = textwrap.dedent(inspect.getsource(fobj))
                node = ast.parse(src).body[0]
            except (OSError, SyntaxError, IndexError, TypeError):
                continue
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            shell_params = [a.arg for a in node.args.args]

            # 1) 自递归必须在**薄壳判定之前**检查 —— 薄壳若写成 `return <同名函数>(...)`，
            #    它根本不带 _core_ 前缀，放在下面就会被整段跳过（负向验证抓到过）。
            for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
                if isinstance(call.func, ast.Name) and call.func.id == fname:
                    problems.append(f"{fname}: 调用了自己（无限递归）")

            call = self._is_thin_shell(node, self.aliases)
            if call is None:
                continue
            checked += 1
            core = self.aliases[call.func.id]
            sig_params = list(inspect.signature(core).parameters.values())
            core_params = [p.name for p in sig_params]

            # 2) 实参个数：有默认值的形参可以合法省略（如 disk_path="/"），
            #    故区间是「必填数 ≤ 实参数 ≤ 全部参数」。
            required = [
                p for p in sig_params
                if p.default is inspect.Parameter.empty
                and p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                               inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            if not (len(required) <= len(call.args) <= len(core_params)):
                problems.append(
                    f"{fname}: 实参 {len(call.args)} 个，核心 {call.func.id} "
                    f"必填 {len(required)} / 全部 {len(core_params)} 个 {core_params}")
                continue

            # 3) 顺序：门面自身参数必须**按原顺序**出现在实参尾部
            tail = [a.id for a in call.args[-len(shell_params):] if isinstance(a, ast.Name)] \
                if shell_params else []
            if shell_params and tail != shell_params:
                problems.append(
                    f"{fname}: 透传参数顺序 {tail} != 门面形参顺序 {shell_params}")

            # 3b) 注入项必须与核心形参名逐位对应
            for arg, pname in zip(call.args, core_params):
                if not isinstance(arg, ast.Name):
                    problems.append(f"{fname}: 实参 {ast.unparse(arg)} 非简单名字，无法核对")
                    continue
                if arg.id in shell_params:
                    continue
                if not hasattr(app, arg.id) and not arg.id.isupper():
                    problems.append(
                        f"{fname}: 注入项 {arg.id}（对应核心形参 {pname}）在门面不存在")
                if not (_tokens(arg.id) & _tokens(pname)):
                    problems.append(
                        f"{fname}: 注入项 {arg.id} 与核心形参 {pname} 名称不相干 —— "
                        f"很可能是实参顺序错位（本阶段第三刀就是被这个坑到的）")
        self.assertGreater(checked, 0, "未找到任何薄壳调用，检查器失效")
        self.assertEqual(problems, [], "薄壳转发不合规：\n  " + "\n  ".join(problems))

    # ── 4. 无循环依赖 ─────────────────────────────────────────
    def test_core_modules_do_not_import_dashboard_app(self):
        offenders = []
        for path in sorted(PAYLOAD_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == "r20_backend.dashboard_cache" or a.name.startswith("dashboard."):
                            offenders.append(f"{path.name}:{node.lineno} import {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == "dashboard" or mod.startswith("dashboard."):
                        offenders.append(f"{path.name}:{node.lineno} from {mod} import ...")
        self.assertEqual(offenders, [], "载荷模块反向 import 了 dashboard 包（循环依赖）:\n  "
                         + "\n  ".join(offenders))

    def test_core_modules_are_imported_by_facade(self):
        """新模块必须被门面导入 —— config_sandbox 只重定向 sys.modules 里已有模块的属性。"""
        missing = [m.__name__ for m in CORE_MODULES
                   if not any(getattr(v, "__module__", None) == m.__name__
                              for v in vars(app).values())]
        self.assertEqual(missing, [], f"这些域模块未被 r20_backend.dashboard_cache 导入，沙箱覆盖不到: {missing}")

    # ── 4b. 迁移完整性：导入的 _core_* 别名必须真的被调用 ──────────
    def test_every_core_alias_is_used(self):
        """门面导入的每个 `_core_*` 别名，必须在 r20_backend/dashboard_cache.py 里被**真正引用**。

        来历：第九刀替换 Phase 2 段落时，替换区间误把夹在中间的
        「2.5 Multi-Venue Parity」调用点一并删掉 —— 导入语句还在、函数也还在，
        只是再也没人调用它。症状极其隐蔽：接口仍 200、载荷结构完整，
        只有 binance/gate 的持仓**静默消失**（该段整体被 try/except 包着，
        连异常都不会有）。

        实测是靠"直调适配器返回 2 条仓位 vs 线上 payload 0 条"才坐实的，
        故这里把它钉死：只导入不调用 = 迁移没做完。
        """
        src = (ROOT / "r20_backend" / "dashboard_cache.py").read_text(encoding="utf-8")
        # 按 AST 行号精确剔除导入语句本身（含括号多行形态），再数引用
        tree = ast.parse(src)
        drop: set[int] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "r20_backend.dashboard_payload"):
                drop.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        body = "\n".join(
            line for i, line in enumerate(src.splitlines(), 1) if i not in drop
        )
        unused = []
        for alias in sorted(self.aliases):
            if alias not in body:
                unused.append(alias)
        self.assertEqual(
            unused, [],
            "这些 _core_* 别名只被导入、在 r20_backend/dashboard_cache.py 里从不引用 —— "
            "很可能是替换段落时把调用点一起删掉了（静默丢失该域数据）: " + str(unused),
        )

    # ── 5. 公开面 ─────────────────────────────────────────────
    def test_facade_surface_intact(self):
        missing = [n for n in FACADE_SURFACE if not hasattr(app, n)]
        self.assertEqual(missing, [], f"r20_backend.dashboard_cache 缺少测试直接引用的符号: {missing}")

    # ── 6. 接缝传导（最关键）：patch 门面路径常量必须影响已迁出的核心 ──
    def test_path_constant_patch_reaches_moved_cores(self):
        import json
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="r20-dash-seam-"))
        (tmp / "t.json").write_text(json.dumps({"BTC-USDT-SWAP_long": {"strategy_tag": "SEAM"}}),
                                    encoding="utf-8")
        with patch.object(app, "POSITION_TRACKER_FILE", str(tmp / "t.json")):
            got = app.load_position_trackers()
        self.assertEqual(got.get("BTC-USDT-SWAP_long", {}).get("strategy_tag"), "SEAM",
                         "patch 门面 POSITION_TRACKER_FILE 未传导到已迁出的核心")

        # 三个路径各带**可区分标记**：只要实参顺序错位，任一断言都会失败
        # （只断言其中一个文件是抓不到错位的 —— 本阶段第三刀正是如此漏过的）。
        (tmp / "state.json").write_text(json.dumps({
            "timestamp": "TS-SEAM", "instruments": [{"instId": "BTC-USDT-SWAP", "desc": "FROM_STATE"}]}),
            encoding="utf-8")
        (tmp / "factor.json").write_text(json.dumps({
            "instruments": [{"instId": "BTC-USDT-SWAP", "chg24h": 42.5}]}), encoding="utf-8")
        (tmp / "dec.json").write_text(json.dumps({
            "BTC-USDT-SWAP": {"decision": {"action": "BUY_LONG", "summary_reason": "FROM_DECISIONS"}}}),
            encoding="utf-8")
        with patch.object(app, "STATE_JSON_FILE", str(tmp / "state.json")), \
             patch.object(app, "FACTOR_LIBRARY_FILE", str(tmp / "factor.json")), \
             patch.object(app, "AI_DECISIONS_FILE", str(tmp / "dec.json")):
            flist, state = app._build_factors_from_local_files([], "NOW")
        self.assertEqual(state.get("timestamp"), "TS-SEAM",
                         "patch 门面 STATE_JSON_FILE 未传导（实参顺序错位？）")
        btc = next((x for x in flist if x.get("instId") == "BTC-USDT-SWAP"), None)
        self.assertIsNotNone(btc, "沙箱 state.json 未被用于枚举标的")
        self.assertEqual(btc.get("action"), "BUY_LONG",
                         "AI_DECISIONS_FILE 未传导（实参顺序错位？）")
        self.assertEqual(btc.get("chg24h"), 42.5,
                         "FACTOR_LIBRARY_FILE 未传导（实参顺序错位？）")

    def test_local_reads_paths_are_injected(self):
        """`load_local_reads`（B2 第六刀）读 6 个路径，必须全部由门面在调用时注入。"""
        import json
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="r20-dash-localreads-"))
        (tmp / "report.json").write_text(json.dumps({"marker": "REPORT"}), encoding="utf-8")
        (tmp / "snaps.json").write_text(json.dumps([{"time": "2026-01-01 00:00:00", "total_eq": 200.0}]),
                                        encoding="utf-8")
        (tmp / "news.json").write_text(json.dumps({"marker": "NEWS"}), encoding="utf-8")
        (tmp / "prompt.txt").write_text("PROMPT", encoding="utf-8")
        (tmp / "hist.json").write_text(json.dumps([{"time": "T1"}]), encoding="utf-8")
        (tmp / "factor.json").write_text(json.dumps({"instruments": [{"instId": "SEAM"}]}),
                                         encoding="utf-8")
        pin = ("REPORT_JSON_FILE", "SNAPSHOTS_JSON_FILE", "NEWS_SENTIMENT_FILE",
               "AI_LAST_PROMPT_FILE", "AI_HISTORY_FILE", "FACTOR_LIBRARY_FILE")
        files = ("report.json", "snaps.json", "news.json", "prompt.txt", "hist.json", "factor.json")
        with patch.object(app, pin[0], str(tmp / files[0])), \
             patch.object(app, pin[1], str(tmp / files[1])), \
             patch.object(app, pin[2], str(tmp / files[2])), \
             patch.object(app, pin[3], str(tmp / files[3])), \
             patch.object(app, pin[4], str(tmp / files[4])), \
             patch.object(app, pin[5], str(tmp / files[5])):
            out = app._core_load_local_reads(
                app.REPORT_JSON_FILE, app.SNAPSHOTS_JSON_FILE, app.NEWS_SENTIMENT_FILE,
                app.AI_LAST_PROMPT_FILE, app.AI_HISTORY_FILE, app.FACTOR_LIBRARY_FILE,
                app.load_trading_memory_md, "2025-01-01 00:00:00", 100.0, 150.0, "NOW")
        self.assertEqual(out["review_data"].get("marker"), "REPORT", "REPORT_JSON_FILE 未传导")
        self.assertEqual(out["news_data"].get("marker"), "NEWS", "NEWS_SENTIMENT_FILE 未传导")
        self.assertEqual(out["factor_lib_snapshot"]["instruments"][0]["instId"], "SEAM",
                         "FACTOR_LIBRARY_FILE 未传导")
        self.assertEqual(out["ai_last_prompt_text"], "PROMPT", "AI_LAST_PROMPT_FILE 未传导")
        self.assertEqual(len(out["ai_history_list"]), 1, "AI_HISTORY_FILE 未传导")
        # 快照过滤：沙箱里那条 2026 年快照 + 追加的实时点 = 2
        self.assertEqual(len(out["snapshots_list"]), 2, "SNAPSHOTS_JSON_FILE 未传导")

    def test_missing_production_write_guard(self):
        """沙箱外的真实路径必须是生产 data/ —— 若核心持有自己的副本，写盘会落到生产。"""
        self.assertTrue(str(app.DATA_DIR).endswith("data") or "data" in str(app.DATA_DIR))


class ReaderFamilyFailureSemanticsTest(unittest.TestCase):
    """面板侧**读取器家族**：缺失静默、读不出来必披露（第一百四十九刀）。

    缺陷形状：`readers.read_json` / `read_text` / `read_text_lines` 旧实现都是
    "任何失败 ⇒ 默认值/空"（`except (OSError, ValueError, UnicodeDecodeError): return default`），
    而同一模块里我在第 52 刀刚给 `load_json_dict_disclosed` 补了披露 ⇒ **一个模块两套失败语义**，
    正是本仓反复吃过的"同一语义两处写"。这些读取器喂的是面板区块（AI 决策、因子库、
    复盘报告、快讯、日志尾），静默失败等于把"读坏了"渲染成"确实没有"。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="reader-family-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _corrupt(self, name, text="{ 半截"):
        f = self.base / name
        f.write_text(text, encoding="utf-8")
        return f

    def test_json_reader_discloses_corrupt_but_is_silent_when_missing(self):
        import io
        from contextlib import redirect_stdout
        from r20_backend.dashboard_payload.readers import read_json
        buf = io.StringIO()
        with redirect_stdout(buf):
            missing = read_json(self.base / "nope.json", {"d": 1})
        self.assertEqual(missing, {"d": 1})
        self.assertEqual(buf.getvalue(), "", "文件不存在是合法空态，不应吵")
        buf = io.StringIO()
        with redirect_stdout(buf):
            broken = read_json(self._corrupt("broken.json"), {"d": 1})
        self.assertEqual(broken, {"d": 1})
        self.assertIn("[面板] warn", buf.getvalue())
        self.assertIn("请勿据此判断", buf.getvalue())

    def test_text_reader_discloses_corrupt_but_is_silent_when_missing(self):
        import io
        from contextlib import redirect_stdout
        from r20_backend.dashboard_payload.readers import read_text
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(read_text(self.base / "nope.txt", "d"), "d")
        self.assertEqual(buf.getvalue(), "")
        # 目录不是可读文本：存在但读不出来 ⇒ 必披露
        (self.base / "adir").mkdir()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(read_text(self.base / "adir", "d"), "d")
        self.assertIn("[面板] warn", buf.getvalue())

    def test_text_lines_reader_discloses_corrupt_but_is_silent_when_missing(self):
        import io
        from contextlib import redirect_stdout
        from r20_backend.dashboard_payload.readers import read_text_lines
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(read_text_lines(self.base / "nope.log", 10), [])
        self.assertEqual(buf.getvalue(), "")
        (self.base / "adir").mkdir()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(read_text_lines(self.base / "adir", 10), [])
        self.assertIn("[面板] warn", buf.getvalue())
        # 正常读取不得吵，且语义不变（strip / limit）
        f = self.base / "ok.log"
        f.write_text("a\n\n b \nc\n", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(read_text_lines(f, 2), ["b", "c"])
        self.assertEqual(buf.getvalue(), "")

if __name__ == "__main__":
    unittest.main(verbosity=2)

class DisclosedJsonReaderTest(unittest.TestCase):
    """面板侧读取必须**披露**而非静默返回空（第 52 刀）。

    背景：同一个"读追踪器"语义此前有**两份实现**（`factors` 吃文件路径、
    `ledger_view` 吃目录），且两份都是 `except Exception: return {}` ——
    本仓两个经典坑叠在一起：*同一语义两处写 ⇒ 必然漂移* + *读不到被渲染成"没有"*。
    本刀收敛到共享读取器并补披露（返回值不变，保持兼容）。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="disclosed-read-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_shared_reader_distinguishes_missing_from_unreadable(self):
        import io
        from contextlib import redirect_stdout
        from r20_backend.dashboard_payload.readers import load_json_dict_disclosed
        missing = self.base / "nope.json"
        buf = io.StringIO()
        with redirect_stdout(buf):
            data, err = load_json_dict_disclosed(missing)
        self.assertEqual((data, err), ({}, ""))
        self.assertEqual(buf.getvalue(), "", "文件不存在是合法空态，不应吵")

        broken = self.base / "broken.json"
        broken.write_text("{ 半截", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            data, err = load_json_dict_disclosed(broken)
        self.assertEqual(data, {})
        self.assertTrue(err, "读不出来必须给出原因（返回值不变，但要能区分）")
        self.assertIn("[面板] warn", buf.getvalue())
        self.assertIn("请勿据此判断", buf.getvalue(), "披露必须点明'空不等于没有'")

    def test_non_dict_json_is_reported_not_silently_emptied(self):
        import io
        from contextlib import redirect_stdout
        from r20_backend.dashboard_payload.readers import load_json_dict_disclosed
        f = self.base / "list.json"
        f.write_text("[1, 2, 3]", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            data, err = load_json_dict_disclosed(f)
        self.assertEqual(data, {})
        self.assertIn("顶层应为 dict", err)
        self.assertIn("形状", buf.getvalue())

    def test_valid_dict_returns_data_without_noise(self):
        import io
        import json as _json
        from contextlib import redirect_stdout
        from r20_backend.dashboard_payload.readers import load_json_dict_disclosed
        f = self.base / "ok.json"
        f.write_text(_json.dumps({"BTC-USDT-SWAP_long": {"scale_count": 1}}), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            data, err = load_json_dict_disclosed(f)
        self.assertEqual(err, "")
        self.assertEqual(data["BTC-USDT-SWAP_long"]["scale_count"], 1)
        self.assertEqual(buf.getvalue(), "")

    def test_both_tracker_loaders_agree_and_both_disclose(self):
        """防漂移的行为钉：两份实现（路径版 / 目录版）对同一输入必须一致。"""
        import io
        from contextlib import redirect_stdout
        from r20_backend.dashboard_payload.factors import load_position_trackers as by_path
        from r20_backend.dashboard_payload.ledger_view import load_position_trackers as by_dir
        (self.base / "position_trackers.json").write_text("{ 坏", encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            a = by_path(str(self.base / "position_trackers.json"))
            b = by_dir(str(self.base))
        self.assertEqual(a, b, "两份实现已经漂移（同输入不同结果）")
        self.assertEqual(a, {})
        self.assertGreaterEqual(buf.getvalue().count("[面板] warn"), 2,
                                "两份实现都必须披露，而不是只有一份")

    def test_multi_venue_fallback_discloses_instead_of_silently_empty(self):
        """源码钉：多所组合的兜底分支必须带披露语（此前是静默 `return {}`）。"""
        src = (Path(__file__).resolve().parents[2] / "r20_backend" / "dashboard_payload"
               / "market.py").read_text(encoding="utf-8")
        self.assertIn("多所组合读取失败", src)
        self.assertIn("请勿据此判断", src)

class ScaleOutSurfacedFromTrackerTest(unittest.TestCase):
    """切分止盈状态必须从 tracker 接到持仓行上（第一百九十八刀）。

    背景：`frontend/.../PositionsOrdersPanel.vue` 一直读 `p.scaleOutPhase`（决定切分徽标）
    与 `p.scaleOutTp`（决定 TP 显示），而这两个键**后端从未发过** —— 数据其实一直在
    tracker 里（`scripts/trader/scale_out.py` 写 `scale_out_phase`/`scale_out_tp`），
    只是没被接出来 ⇒ 徽标永远不亮。
    """

    def setUp(self):
        from r20_backend.dashboard_payload.factors import enrich_position_risk_fields
        self.enrich = enrich_position_risk_fields

    def _pos(self, inst="X-USDT-SWAP", side="long"):
        return [{"instId": inst, "posSide": side, "pos": "10", "markPx": "2.0", "lever": "5"}]

    def test_tracker_scale_out_state_reaches_the_row(self):
        trackers = {"X-USDT-SWAP_long": {"scale_out_phase": 1, "scale_out_tp": 2.34}}
        row = self.enrich("unused.json", self._pos(), trackers)[0]
        self.assertEqual(row.get("scaleOutPhase"), 1)
        self.assertAlmostEqual(row.get("scaleOutTp"), 2.34)

    def test_absent_state_stays_absent_not_zero(self):
        """**缺席即缺席**：没 tracker 就不写这两个键 —— 写成 0 等于替币安/Gate 行
        断言"切分未开始"（读不到 ≠ 没有）。"""
        rows = self.enrich("unused.json", self._pos("Y-USDT-SWAP"), {})
        self.assertNotIn("scaleOutPhase", rows[0])
        self.assertNotIn("scaleOutTp", rows[0])

    def test_producer_and_consumer_names_agree(self):
        """源码钉：tracker 侧写 `scale_out_phase`、行上给 `scaleOutPhase` —— 两侧名字都必须真实存在。"""
        root = Path(__file__).resolve().parents[2]
        producer = (root / "scripts" / "trader" / "scale_out.py").read_text(encoding="utf-8")
        self.assertIn('t["scale_out_phase"]', producer)
        self.assertIn('t["scale_out_tp"]', producer)
        facet = (root / "r20_backend" / "dashboard_payload" / "factors.py").read_text(encoding="utf-8")
        self.assertIn('"scaleOutPhase"', facet)
        self.assertIn('"scaleOutTp"', facet)
        ui = (root / "frontend" / "src" / "components" / "dashboard"
              / "PositionsOrdersPanel.vue").read_text(encoding="utf-8")
        self.assertIn("scaleOutPhase", ui)
        self.assertIn("scaleOutTp", ui)
