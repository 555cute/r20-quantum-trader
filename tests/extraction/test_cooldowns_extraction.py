"""止损冷却单一事实源（结构优化阶段 4·B3 第五十刀）。

## 修了什么

`scripts/ai_factor_trader.py`（活交易路径）与
`r20_backend/execution/circuit_breaker.py`（后端风控面）各自内联了同一套
止损冷却读写。两文件同名的顶层函数有 7 个，其中 3 个是**逐字/等价重复**：

| 函数 | 状态 |
|---|---|
| `is_in_stop_cooldown` | **逐字节相同**（10 行 × 2） |
| `_read_stop_cooldowns_state` | 等价（差在 `os.path.exists` vs `Path.exists` 与类型注解） |
| `load_stop_cooldowns` | 等价（均为 `_read_stop_cooldowns_state()[0]`） |

**此刻不是 bug**，但它是「孪生漂移」现场：冷却判定直接决定
「硬止损后能否**立即同向重进**」。两份拷贝若漂移，就会出现
**交易侧认为可重进、风控面认为仍在冷却**（或反之）的不一致。

本仓 `r20_backend/execution/sizing.py` 的注释已记录过同源问题并用同样办法修过
（审计 P1-1：两条 `min()` 口径曾在同两个文件各存一份拷贝）。

现收敛到 `r20_backend/execution/cooldowns.py`。

## ⚠️ 为什么参数是显式传入的（本刀最关键的约束）

两个调用方各自持有**可被 patch 的**模块级全局：

- `scripts/ai_factor_trader.py`：`STOP_COOLDOWN_FILE`（**str**）
- `r20_backend/execution/circuit_breaker.py`：`STOP_COOLDOWN_FILE`（**Path**）

测试**同时** patch 两边（`tests/audit/test_audit_batch3_persistence_atomic.py` 里
`patch.object(aft, "STOP_COOLDOWN_FILE", f)` 与
`patch.object(cb, "STOP_COOLDOWN_FILE", Path(f))` 并列出现）。

若共享模块自己 import 任一方的常量，就会在 import 期烘焙副本 →
**patch 静默失效**（读真实 `data/stop_cooldown.json`），
属于本仓已实证的"测试写进生产 data/"事故类型。

故两侧都保留**同名薄壳**，在**调用时**解析自己的全局并传入。
本文件的 `StrPathParityTest` 专门钉住"str 与 Path 两种形态都成立"。
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

MODULE = ROOT / "r20_backend" / "execution" / "cooldowns.py"
AFT = ROOT / "scripts" / "ai_factor_trader.py"
CB = ROOT / "r20_backend" / "execution" / "circuit_breaker.py"
PRE_EXTRACTION_COMMIT = "39fb81f"

SHELL_NAMES = ("_read_stop_cooldowns_state", "load_stop_cooldowns", "is_in_stop_cooldown")

from r20_backend.execution import cooldowns as cd  # noqa: E402


class ReadStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.file = self.tmp / "stop_cooldown.json"

    def test_missing_file_is_not_corrupt(self):
        """⚠️ 文件**不存在** → `({}, False)`：真的没有冷却记录。"""
        data, corrupt = cd.read_stop_cooldowns_state(self.file)
        self.assertEqual(data, {})
        self.assertFalse(corrupt, "缺失不得当成损坏（否则永远无法开新仓）")

    def test_bad_json_is_corrupt(self):
        self.file.write_text("{ not json", encoding="utf-8")
        data, corrupt = cd.read_stop_cooldowns_state(self.file)
        self.assertEqual(data, {})
        self.assertTrue(corrupt)

    def test_non_dict_is_corrupt(self):
        for payload in ("[1,2,3]", '"hello"', "123", "null"):
            self.file.write_text(payload, encoding="utf-8")
            data, corrupt = cd.read_stop_cooldowns_state(self.file)
            self.assertEqual(data, {}, payload)
            self.assertTrue(corrupt, payload)

    def test_valid_dict_is_returned(self):
        self.file.write_text(json.dumps({"a_long": {"ts": 1}}), encoding="utf-8")
        data, corrupt = cd.read_stop_cooldowns_state(self.file)
        self.assertEqual(data, {"a_long": {"ts": 1}})
        self.assertFalse(corrupt)

    def test_accepts_str_and_path(self):
        self.file.write_text(json.dumps({"a_long": {"ts": 1}}), encoding="utf-8")
        self.assertEqual(cd.read_stop_cooldowns_state(str(self.file)),
                         cd.read_stop_cooldowns_state(self.file))


class IsInCooldownTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.file = self.tmp / "stop_cooldown.json"
        self.now = int(time.time())
        self.secs = 30 * 60

    def _write(self, payload):
        self.file.write_text(json.dumps(payload), encoding="utf-8")

    def test_recently_triggered_is_in_cooldown(self):
        self._write({"BTC-USDT-SWAP_long": {"ts": self.now}})
        self.assertTrue(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs))

    def test_expired_is_not_in_cooldown(self):
        self._write({"BTC-USDT-SWAP_long": {"ts": self.now - self.secs - 10}})
        self.assertFalse(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs))

    def test_exactly_at_boundary_is_expired(self):
        """⚠️ 原式是 `rem_sec = secs - elapsed`，`rem_sec > 0` 才算冷却中 ——
        故 `elapsed == secs` 时 `rem_sec == 0`，**不在**冷却中（边界为闭区间外）。"""
        self._write({"BTC-USDT-SWAP_long": {"ts": self.now - self.secs}})
        self.assertFalse(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs))

    def test_corrupt_fails_closed(self):
        """⚠️ 本刀保护的核心语义：损坏 → 按「仍在冷却」处理。

        旧实现损坏时返回 `{}`，等价于「无冷却」→ 硬止损后可**立即同向重进**。"""
        self.file.write_text("{ not json", encoding="utf-8")
        self.assertTrue(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs))

    def test_missing_file_is_not_in_cooldown(self):
        self.assertFalse(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs))

    def test_missing_ts_key_is_treated_as_epoch_zero(self):
        """⚠️ 保留既有的怪癖行为：`get("ts", 0)` → 缺 `ts` 视为 epoch 0 → 早已过期。

        这是**有意保留**的原有行为（不是本刀引入的），勿"顺手"改成 fail-closed。
        """
        self._write({"BTC-USDT-SWAP_long": {}})
        self.assertFalse(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs))

    def test_key_is_inst_underscore_side(self):
        """键的构造是 `f"{inst_id}_{side}"` —— 其他标的/方向不得互相影响。"""
        self._write({"BTC-USDT-SWAP_long": {"ts": self.now}})
        self.assertFalse(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "short", self.file, self.secs))
        self.assertFalse(cd.is_in_stop_cooldown("ETH-USDT-SWAP", "long", self.file, self.secs))
        self.assertTrue(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs))

    def test_environment_isolation_prevents_cross_contamination(self):
        """环境隔离：demo 盘的冷却记录不污染 live 盘，反之亦然；历史无标签记录保守生效。"""
        self._write({"BTC-USDT-SWAP_long": {"ts": self.now, "environment": "demo"}})
        self.assertFalse(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs, environment="live"))
        self.assertTrue(cd.is_in_stop_cooldown("BTC-USDT-SWAP", "long", self.file, self.secs, environment="demo"))

        self._write({"ETH-USDT-SWAP_long": {"ts": self.now}})
        self.assertTrue(cd.is_in_stop_cooldown("ETH-USDT-SWAP", "long", self.file, self.secs, environment="live"))


class LoadStopCooldownsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)
        self.file = self.tmp / "stop_cooldown.json"

    def test_returns_data_only(self):
        self.file.write_text(json.dumps({"a_long": {"ts": 5}}), encoding="utf-8")
        self.assertEqual(cd.load_stop_cooldowns(self.file), {"a_long": {"ts": 5}})

    def test_corrupt_yields_empty_dict(self):
        """⚠️ 本函数是**只读展示面**，丢弃 corrupt 信号 —— 故损坏时给 `{}`。
        风控判定**不得**用它（它拿不到 corrupt）。"""
        self.file.write_text("{ not json", encoding="utf-8")
        self.assertEqual(cd.load_stop_cooldowns(self.file), {})


class ShellDisciplineTest(unittest.TestCase):
    """⚠️ 两侧必须保留**同名薄壳**（不是别名赋值），且只在调用时解析全局。"""

    def test_both_modules_still_define_the_names(self):
        for path in (AFT, CB):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
            for want in SHELL_NAMES:
                self.assertIn(want, names, f"{path.name} 缺少 {want}")

    def test_shells_are_definitions_not_aliases(self):
        """⚠️ 不能写成 `is_in_stop_cooldown = _cooldowns_is_in` ——
        那会让 `patch.object(mod, "is_in_stop_cooldown")` 之类的门面接缝失效。"""
        for path in (AFT, CB):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("is_in_stop_cooldown = ", src, path.name)

    def test_shells_resolve_globals_at_call_time(self):
        """薄壳的可执行体必须把 `STOP_COOLDOWN_FILE` 作为**实参**传入
        （调用时解析），而不是在模块层提前取好。"""
        for path in (AFT, CB):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and n.name in SHELL_NAMES:
                    body = [s for s in n.body
                            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
                    self.assertEqual(len(body), 1, f"{path.name}::{n.name} 壳不止一条语句")
                    code = ast.unparse(body[0])
                    self.assertIn("STOP_COOLDOWN_FILE", code,
                                  f"{path.name}::{n.name} 未在调用时传入路径全局")
                    self.assertIn("_cooldowns_", code,
                                  f"{path.name}::{n.name} 未转调共享实现")

    def test_no_inline_implementation_left(self):
        """全仓（非测试/归档）不应再有读 `stop_cooldown` 的**内联**实现。"""
        offenders = []
        for p in ROOT.rglob("*.py"):
            if any(x in p.parts for x in (".venv", "plan_local", "__pycache__",
                                          "node_modules", ".git", ".archive", "tests")):
                continue
            if p == MODULE:
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and n.name in SHELL_NAMES:
                    body = [s for s in n.body
                            if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
                    code = ast.unparse(ast.Module(body=body, type_ignores=[]))
                    if "rem_sec" in code or "isinstance(data, dict)" in code:
                        offenders.append(f"{p}::{n.name}")
        self.assertEqual(offenders, [], f"仍有内联实现: {offenders}")

    def test_shared_module_imports_only_stdlib(self):
        """⚠️ 共享模块不得 import `scripts` 或任何门面 ——
        否则 import 期就可能烘焙副本，让 patch 静默失效。"""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        self.assertEqual(imported - {"__future__"}, {"json", "os", "time", "pathlib", "typing"},
                         f"出现非标准库依赖: {imported}")


class BehavioralParityWithPreExtractionTest(unittest.TestCase):
    """⚠️ 把"新共享实现 == 提取前两份旧实现"钉成可执行证据。

    旧实现从 `PRE_EXTRACTION_COMMIT` 取（**不是** `HEAD:` —— 那会在下一次提交后
    自我失效，第四十六刀踩过这个坑）。
    """

    NAMES = SHELL_NAMES

    def _old_ns(self, relpath):
        import subprocess
        import typing
        src = subprocess.run(
            ["git", "show", f"{PRE_EXTRACTION_COMMIT}:{relpath}"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(src.returncode, 0, src.stderr)
        tree = ast.parse(src.stdout)
        keep = [n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name in self.NAMES]
        self.assertEqual(len(keep), 3, f"{relpath} 未取到三个函数")
        ns = {"json": json, "os": os, "time": time, "Path": Path,
              "Tuple": typing.Tuple, "Dict": typing.Dict, "Any": typing.Any}
        exec(compile(ast.unparse(ast.Module(body=keep, type_ignores=[])), "<old>", "exec"), ns)
        return ns

    def test_three_implementations_agree_on_every_branch(self):
        old_aft = self._old_ns("scripts/ai_factor_trader.py")
        old_cb = self._old_ns("r20_backend/execution/circuit_breaker.py")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        f = tmp / "cd.json"
        now = int(time.time())
        secs, mins = 30 * 60, 30

        cases = [
            ("缺失文件", None),
            ("空 dict", {}),
            ("刚触发", {"BTC-USDT-SWAP_long": {"ts": now}}),
            ("刚好过期", {"BTC-USDT-SWAP_long": {"ts": now - secs}}),
            ("早过期", {"BTC-USDT-SWAP_long": {"ts": now - 99999}}),
            ("缺 ts", {"BTC-USDT-SWAP_long": {}}),
            ("ts=0", {"BTC-USDT-SWAP_long": {"ts": 0}}),
            ("他标的", {"ETH-USDT-SWAP_long": {"ts": now}}),
            ("他方向", {"BTC-USDT-SWAP_short": {"ts": now}}),
            ("坏 JSON", "{ not json"),
            ("JSON list", "[1,2,3]"),
            ("JSON str", '"hello"'),
        ]
        checked = 0
        for label, payload in cases:
            if payload is None:
                if f.exists():
                    f.unlink()
            else:
                f.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                             encoding="utf-8")
            for inst, side in (("BTC-USDT-SWAP", "long"), ("ETH-USDT-SWAP", "short")):
                for ns, as_path in ((old_aft, False), (old_cb, True)):
                    ns["STOP_COOLDOWN_FILE"] = f if as_path else str(f)
                    ns["STOP_COOLDOWN_MINUTES"] = mins
                got = (old_aft["is_in_stop_cooldown"](inst, side),
                       old_cb["is_in_stop_cooldown"](inst, side),
                       cd.is_in_stop_cooldown(inst, side, f, secs))
                self.assertEqual(len(set(got)), 1,
                                 f"{label} {inst}/{side}: 旧aft={got[0]} 旧cb={got[1]} 新={got[2]}")
                loads = (old_aft["load_stop_cooldowns"](),
                         old_cb["load_stop_cooldowns"](),
                         cd.load_stop_cooldowns(f))
                self.assertEqual(len(set(map(str, loads))), 1,
                                 f"{label} {inst}/{side} load: {loads}")
                checked += 1
        self.assertGreaterEqual(checked, 20, "对拍覆盖不足")


class BothConsumersStillRepointTest(unittest.TestCase):
    """⚠️ 两侧 patch 各自的 `STOP_COOLDOWN_FILE` 后，判定必须**跟着走**。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, ignore_errors=True)

    def test_patching_each_module_repoints_its_own_lookup(self):
        from unittest.mock import patch
        import ai_factor_trader as aft
        from r20_backend.execution import circuit_breaker as cb

        f = self.tmp / "cd.json"
        now = int(time.time())
        f.write_text(json.dumps({"BTC-USDT-SWAP_long": {"ts": now}}), encoding="utf-8")

        with patch.object(aft, "STOP_COOLDOWN_FILE", str(f)), \
             patch.object(cb, "STOP_COOLDOWN_FILE", f):
            self.assertTrue(aft.is_in_stop_cooldown("BTC-USDT-SWAP", "long"),
                            "aft 侧的 patch 未生效（import 期烘焙了副本？）")
            self.assertTrue(cb.is_in_stop_cooldown("BTC-USDT-SWAP", "long"),
                            "cb 侧的 patch 未生效（import 期烘焙了副本？）")
            self.assertEqual(aft.load_stop_cooldowns(), cb.load_stop_cooldowns())

    def test_str_and_path_both_work(self):
        """两种形态（`aft` 用 str、`cb` 用 Path）都必须成立。"""
        f = self.tmp / "cd.json"
        f.write_text(json.dumps({"X_long": {"ts": int(time.time())}}), encoding="utf-8")
        self.assertTrue(cd.is_in_stop_cooldown("X", "long", str(f), 1800))
        self.assertTrue(cd.is_in_stop_cooldown("X", "long", f, 1800))


if __name__ == "__main__":
    unittest.main()
