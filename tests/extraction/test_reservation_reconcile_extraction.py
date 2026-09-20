"""预留对账簇外提（结构优化阶段 4·B3 第五十八刀）。

## 抽了什么

`scripts/ai_factor_trader.py`（2851 行）是**唯一**一个"看起来拆不动"的文件：
`tests/risk_test_env.py::pin_baseline_risk_env()` 的重载名单只有
`risk_constants` / `ai_factor_trader` / `ai_brain_trader`——**不含子模块**，
故子模块若在 import 期绑定风控常量，reload 后会读到旧值。

本刀先跑传递纯度扫描，结论是：该文件 33 个"纯函数"里**只有预留对账这一组**
是自洽且高内聚的（`_utc_age_seconds` 11 行 + `reconcile_reservation_ledger`
74 行）；其余分散在场所、组合预算等不同关注点，凑一起只会造出杂物模块。

外提到 `scripts/trader/reservation_reconcile.py`，门面 **2851 → 2801 行**。

## ⚠️ 依赖全部**调用期注入**（本刀能成立的前提）

子模块不在 reload 名单里，故它**不 import 门面任何东西**，四个依赖由门面
在调用时传入。其中两个是**测试接缝**：
`tests/core/test_reservation_reconcile.py` 用
`patch.object(trader, "reservation_manager", …)` 与
`patch.object(trader, "fetch_other_venue_positions", …)` 换掉它们 ——
本文件的 `CallTimeInjectionTest` 专门钉住这条，若改成 import 期绑定，
那 9 条既有用例会翻红，且**会真的出网**。

## ⚠️ 本刀顺带补上一个**从未被测过**的关键函数

`_utc_age_seconds` 此前**没有任何直接测试**（既有 9 条用例只间接经过它），
而它承载一条"方向绝不能反"的红线：

> 不可解析 = **`-inf`**（年龄未知按"没到对账窗口"处理，**永不释放**）；
> 返回 `+inf` 会把脏时间戳当成超旧而**错杀活占用**。

故本文件对它做**穷尽式**测试，包括边界与各种畸形输入 ——
这类"fail-closed 方向"一旦反了，症状是"合法开仓被挡死"，
在实盘里很难归因，值得钉死。
"""

from __future__ import annotations

import ast
import datetime
import inspect
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FACADE = ROOT / "scripts" / "ai_factor_trader.py"
MODULE = ROOT / "scripts" / "trader" / "reservation_reconcile.py"
SUBPKG_INIT = ROOT / "scripts" / "trader" / "__init__.py"

import scripts.ai_factor_trader as trader  # noqa: E402
import scripts.trader.reservation_reconcile as rr  # noqa: E402
from unittest.mock import patch  # noqa: E402


def _code(p: Path) -> str:
    text = p.read_text(encoding="utf-8")
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            if j == -1:
                break
            out.append("\n")
            i = j + 1
            continue
        if c == '"' and text[i:i + 3] == '"""':
            j = text.find('"""', i + 3)
            if j == -1:
                break
            out.append("\n" * text.count("\n", i, j))
            i = j + 3
            continue
        out.append(c)
        i += 1
    return "".join(out)


class UtcAgeSecondsTest(unittest.TestCase):
    """⚠️ 本刀之前**从未直接测过**这个函数，而它承载一条方向红线。"""

    def _age(self, ts, now=1_600_000_000.0):
        return rr.utc_age_seconds(ts, now)

    def test_normal_case(self):
        # 2020-09-13 12:26:40 UTC == 1600000000
        self.assertAlmostEqual(self._age("2020-09-13 12:26:40"), 0.0, places=3)

    def test_one_hour_ago(self):
        base = datetime.datetime(2020, 9, 13, 12, 26, 40, tzinfo=datetime.timezone.utc)
        older = (base - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertAlmostEqual(self._age(older), 3600.0, places=3)

    def test_future_timestamp_clamps_to_zero(self):
        """未来时间戳 → 0（`max(0.0, …)`），不是负数。"""
        future = (datetime.datetime(2020, 9, 13, 12, 26, 40, tzinfo=datetime.timezone.utc)
                  + datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(self._age(future), 0.0)

    def test_subsecond_truncated_to_19_chars(self):
        """实现只取前 19 字符 —— 带毫秒/时区的串也能解析。"""
        self.assertAlmostEqual(self._age("2020-09-13 12:26:40.123456+00:00"), 0.0, places=3)

    def test_whitespace_stripped(self):
        self.assertAlmostEqual(self._age("  2020-09-13 12:26:40  "), 0.0, places=3)

    def test_unparseable_returns_minus_inf(self):
        """⚠️ **方向红线**：不可解析 = `-inf`（永不释放），绝不是 `+inf`。"""
        for bad in ("", "garbage", "not-a-date", "2020/09/13 12:26:40",
                    "2020-13-45 99:99:99", "None", "NaN"):
            with self.subTest(ts=bad):
                got = self._age(bad)
                self.assertEqual(got, float("-inf"),
                                 f"{bad!r} 应返回 -inf（保守永不释放）")
                self.assertNotEqual(got, float("inf"))

    def test_none_and_non_string_are_minus_inf(self):
        for bad in (None, 12345.6, [], {}, object()):
            with self.subTest(ts=type(bad).__name__):
                self.assertEqual(self._age(bad), float("-inf"))

    def test_minus_inf_is_below_any_ttl(self):
        """与 TTL 比较时的**实际后果**：-inf < ttl → 走 `continue`（不释放）。"""
        ttl = trader.RESERVATION_RECONCILE_TTL_S
        self.assertLess(self._age("garbage"), ttl)
        self.assertFalse(self._age("garbage") >= ttl,
                         "-inf 必须判定为『未到对账窗口』")

    def test_returns_float(self):
        self.assertIsInstance(self._age("2020-01-01 00:00:00"), float)


class FacadeShellTest(unittest.TestCase):
    def test_shells_are_definitions_not_aliases(self):
        self.assertIsNot(trader._utc_age_seconds, rr.utc_age_seconds)
        self.assertIsNot(trader.reconcile_reservation_ledger, rr.reconcile_reservation_ledger)

    def test_public_signature_unchanged(self):
        """⚠️ `test_reservation_reconcile.py` 用**位置参数**调用，签名不得变。"""
        sig = inspect.signature(trader.reconcile_reservation_ledger)
        # 第一百二十六刀：**只允许追加带默认值的形参**（位置调用方一字不受影响）。
        # `venue_snapshot_verified` 必须一路透传到对账器 —— 跨所读取失败时
        # `venue_snapshot` 是空字典，对账器会据此误判"外所无仓无挂"并释放活仓预留。
        self.assertEqual(list(sig.parameters),
                         ["real_pos_dict", "pending_inst_ids", "environment",
                          "ttl_s", "venue_snapshot", "venue_snapshot_verified"])
        self.assertIs(sig.parameters["venue_snapshot_verified"].default, True)

    def test_shell_delegates_with_all_dependencies(self):
        """四个依赖必须都在门面壳里**调用时**传入。"""
        tree = ast.parse(FACADE.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "reconcile_reservation_ledger")
        src = ast.unparse(fn)
        for dep in ("reservation_manager=", "fetch_other_venue_positions=",
                    "state_closed=", "default_ttl_s="):
            self.assertIn(dep, src, f"门面壳缺少调用期注入 {dep}")

    def test_module_does_not_import_the_facade(self):
        """反向依赖会成环，且会破坏 reload 语义。"""
        src = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.assertNotIn("ai_factor_trader", a.name)
                    self.assertNotIn("risk_constants", a.name)
            elif isinstance(n, ast.ImportFrom):
                self.assertNotIn("ai_factor_trader", str(n.module))
                self.assertNotIn("risk_constants", str(n.module))

    def test_module_imports_only_stdlib(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module.split(".")[0])
        self.assertTrue(mods <= {"datetime", "time", "typing", "__future__"},
                        f"出现了非标准库依赖: {sorted(mods)}")


class CallTimeInjectionTest(unittest.TestCase):
    """⚠️ 本刀能成立的前提：两个模块级名字必须**调用时**解析。

    `patch.object(trader, "reservation_manager", …)` 与
    `patch.object(trader, "fetch_other_venue_positions", …)` 是既有 9 条
    用例的接缝；若子模块 import 期绑一份，接缝失效 ——
    测试会真的去建真实台账、真的出网。
    """

    def test_reservation_manager_patch_is_observed(self):
        sentinel = {"called": 0}

        class _Mgr:
            def list_unreleased(self, env):
                sentinel["called"] += 1
                return []

        with patch.object(trader, "reservation_manager", lambda: _Mgr()):
            n = trader.reconcile_reservation_ledger({}, set(), "demo",
                                                    venue_snapshot={})
        self.assertEqual(n, 0)
        self.assertEqual(sentinel["called"], 1,
                         "子模块没有用 patch 后的 reservation_manager")

    def test_fetch_patch_is_observed_and_never_goes_to_network(self):
        sentinel = {"called": 0}

        def _fake_fetch(env):
            sentinel["called"] += 1
            return True, {"gate": [{"base": "BTC", "side": "long"}]}, ""

        with patch.object(trader, "fetch_other_venue_positions", _fake_fetch), \
                patch.object(trader, "reservation_manager", lambda: _NoRows()):
            trader.reconcile_reservation_ledger({}, set(), "demo")  # snapshot=None
        self.assertEqual(sentinel["called"], 1,
                         "子模块没有用 patch 后的 fetch_other_venue_positions（会真出网）")


class _NoRows:
    def list_unreleased(self, env):
        return []


class DocsRegisteredTest(unittest.TestCase):
    def test_module_registered_in_subpackage_docs(self):
        """`test_directory_docs_current.py` 要求受管子包的新模块必须登记。"""
        self.assertIn("reservation_reconcile.py", SUBPKG_INIT.read_text(encoding="utf-8"))


class BehaviourPreservedTest(unittest.TestCase):
    """⚠️ 抽离只允许复制：实现体必须与搬迁前逐字一致（除注入形参改名）。"""

    PRE = "a6ba8ec"   # 第五十七刀提交 —— 本刀之前

    # 搬迁中有意替换的名字（注入形参 / 依赖改名）。
    # ⚠️ 我为这条断言连撞两次，都是"断言集/形态写错"而非搬移出错：
    #    ① 漏了 `RESERVATION_RECONCILE_TTL_S` → `default_ttl_s`；
    #    ② 写成 `("_utc_age_seconds(", "utc_age_seconds(")` —— 但 `ast.dump`
    #       把函数名渲染成 `Name(id='_utc_age_seconds', …)`，**名字后面没有 `(`**，
    #       于是这条替换从未生效。
    #    改用 `ast.unparse` 做比较（它保留 `(`），同时保留原名形式兼容两种。
    RENAME = [
        ("_utc_age_seconds", "utc_age_seconds"),
        ("RESERVATION_RECONCILE_TTL_S", "default_ttl_s"),
        ("risk_reservation.STATE_CLOSED", "state_closed"),
    ]

    #: ⚠️ **文档化差异**（第一百一十五刀，2026-09-20）：本函数体除"搬迁改名"外，
    #: 只允许下面这一处**有意的行为修复**——挂单保留判据补上"各所拼写归一的挂单基名"。
    #:
    #: 原判据 `(venue == "okx" and inst_id in pending)` 把外所整体排除在"有挂单则保留"
    #: 之外。⚠️ 校正（第一百一十六刀）：我上一刀把成因写成"拼写混装"是**错的** ——
    #: 生产侧 `collect_pending_inst_ids` 统一归一成 OKX 拼写；真正的成因只有那条
    #: `venue == "okx"`。基名归一这一 delta 现按**防御性**保留（注入集合可能给原生
    #: 拼写，且基名匹配对非 USDT 报价更稳）。后果：派往 gate/binance 的**未成交挂单**，
    #: 其预留一过 TTL(2h) 就被释放，而单还挂在场内 —— 成交后这笔占用不在台账上
    #: （预算/敞口少算）。方向纪律：**保留是保守的**（多占只压缩额度），
    #: 释放不可逆（活单失去登记）⇒ 按基名匹配、不要求方向一致。
    #:
    #: 本表写成"旧体 + 这两处编辑 == 新体"，于是**任何其他改动都会让断言失败**；
    #: 行为面由 `tests/core/test_reservation_reconcile.py` 的
    #: `CrossVenuePendingKeepTest` 正向钉住。
    DELTA_EDITS = [
        ("pending = {str(x) for x in pending_inst_ids or set()}",
         "pending = {str(x) for x in pending_inst_ids or set()}\n"
         "pending_bases = set()\n"
         "for _p_inst in pending:\n"
         "    _p_base = str(_p_inst).split('-')[0].split('_')[0].upper()\n"
         "    for _p_quote in ('USDT', 'USDC', 'USD'):\n"
         "        if _p_base.endswith(_p_quote) and len(_p_base) > len(_p_quote):\n"
         "            _p_base = _p_base[:-len(_p_quote)]\n"
         "    if _p_base:\n"
         "        pending_bases.add(_p_base)"),
        ("or (venue == 'okx' and inst_id in pending)",
         "or (venue == 'okx' and inst_id in pending) or base in pending_bases"),
        # ---- 第一百二十六刀：两处 fail-closed 修改 -------------------------------
        # 缺陷：跨所实况**未核验**时，原实现把"读不到"当成"没有仓" ——
        # `fetch_other_venue_positions` 失败返回 `(False, {}, err)`，调用点把那个
        # **空字典**原样透传，本函数便据 `{}` 判定"外所无仓无挂" ⇒ 把**活仓的外所
        # 预留**按超 TTL 释放成 closed（实测 binance 一笔 726U 活仓预留被释放，
        # 日志还打印"无仓无挂"这一假陈述）。方向纪律见模块 docstring：
        # 「保留是保守的（多占只压缩额度），释放是不可逆的」⇒ 未知必须保留。
        ("now_utc = time.time()",
         "now_utc = time.time()\n"
         "if not venue_snapshot_verified:\n"
         "    print('[预留对账] warn 跨所实况未核验——本周期不释放任何预留"
         "（释放不可逆，宁可慢一轮；下周期核验通过再回笼）')\n"
         "    return 0"),
        ("    try:\n"
         "        _xv_ok, venue_snapshot, _ = fetch_other_venue_positions(environment)\n"
         "        if not _xv_ok:\n"
         "            venue_snapshot = {}\n"
         "    except Exception:\n"
         "        venue_snapshot = {}",
         "    try:\n"
         "        _xv_ok, venue_snapshot, _xv_err = fetch_other_venue_positions(environment)\n"
         "    except Exception as _xv_exc:\n"
         "        _xv_ok, venue_snapshot, _xv_err = (False, {}, str(_xv_exc))\n"
         "    if not _xv_ok:\n"
         "        print(f\"[预留对账] warn 跨所实况自取失败（{_xv_err or '未知原因'}）"
         "——本周期不释放任何预留\")\n"
         "        return 0"),
    ]

    def _body(self, src: str, name: str) -> str:
        """函数体的可执行骨架（剥 docstring）。

        ⚠️ 用 **`ast.unparse`** 而不是 `ast.dump` —— `dump` 会把
        `risk_reservation.STATE_CLOSED` 展开成
        `Attribute(value=Name(id='risk_reservation'), attr='STATE_CLOSED')` 这种
        嵌套结构，字符串层面的改名替换**够不着**，于是永远"有差异"。
        `unparse` 保留源码形态（`risk_reservation.STATE_CLOSED`），
        改名替换一步到位。
        """
        tree = ast.parse(src)
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        module = ast.Module(
            body=[s for s in fn.body
                  if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))],
            type_ignores=[])
        return ast.unparse(module)

    def test_moved_bodies_are_verbatim(self):
        import subprocess
        old = subprocess.run(["git", "show", f"{self.PRE}:scripts/ai_factor_trader.py"],
                             capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(old.returncode, 0, old.stderr)
        new = MODULE.read_text(encoding="utf-8")
        for old_name, new_name in (("_utc_age_seconds", "utc_age_seconds"),
                                   ("reconcile_reservation_ledger",
                                    "reconcile_reservation_ledger")):
            a = self._body(old.stdout, old_name)
            b = self._body(new, new_name)
            for src_tok, dst_tok in self.RENAME:
                a = a.replace(src_tok, dst_tok)
            # 注入形参在 AST 里表现为 Name(id='reservation_manager')，
            # 而原文是函数调用 Name(id='reservation_manager') —— 名字相同，故无需改。
            # 第一百一十五刀：把**文档化差异**应用到旧体上，要求"旧体 + 差异 == 新体"，
            # 于是允许表之外的任何改动都会在此翻红。
            a_before_delta = a
            for src_tok, dst_tok in self.DELTA_EDITS:
                a = a.replace(src_tok, dst_tok)
            self.assertEqual(
                a.count("pending_bases"), b.count("pending_bases"),
                "文档化差异未按预期生效（旧体里没找到锚点？）——差异必须是唯一改动")
            self.assertEqual(a, b, f"{old_name} 的函数体在搬移中被改写了")
            # 钉住"差异之外逐字未动"：把差异从**新体**里撤掉后应与旧体完全相同
            b_stripped = b
            for _src_tok, dst_tok in self.DELTA_EDITS:
                b_stripped = b_stripped.replace(dst_tok, _src_tok)
            self.assertEqual(b_stripped, a_before_delta,
                             "除文档化差异外，函数体还有别的改动")


if __name__ == "__main__":
    unittest.main()
