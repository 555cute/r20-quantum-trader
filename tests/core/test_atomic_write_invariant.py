"""原子写门：`_atomic_write*` 必须真的原子；敏感状态文件不得被直写（第一百五十四刀）。

## 为什么

本仓吃过这类亏好几次（注释里都写着）：台账/熔断/冷却/状态文件一旦被 `open("w")` 直写，
**并发读者会读到半截 JSON** —— 后果不是崩溃而是**误判**：误停开仓、推"0 胜 0 负"假研报、
止损冷却静默解除。这就是"读者撞上半截 JSON 后按默认值降级"的沉默伤害。

## 本次审计结果（先查后钉）

全仓 7 个 `_atomic_write*` 辅助函数逐个体检：**全部**是
`mkstemp` + `flush/fsync` + `os.replace` ⇒ **今天没有直写**（我"存在非原子写者"的
假设被证伪）。两处没显式 `chmod 0600`，但 `mkstemp` **默认就是 0600**，`os.replace`
保留临时文件权限 ⇒ 也不构成泄漏面。

既然此刻是对的，就把它钉住：

1. **辅助函数**：凡名如 `_atomic_write*` 者，函数体必须同时含 `mkstemp`、`fsync`、
   `os.replace`/`os.rename`，且**不得**对目标路径直接 `open(..., "w")`；
2. **敏感文件**：`circuit_breaker.json` / `stop_cooldown.json` / `ledger_sync_status.json` /
   `position_trackers.json` / `open_order_intents.json` / `cycle_disclosure.json` /
   `market_data_health.json` 这些"读者按默认值降级"的文件，**不得**被
   `open(<该文件名或绑定它的模块常量>, "w"/"a")` 直写。

自检：**非空**（必须真扫到 ≥6 个 atomic 辅助函数）+ **有牙齿**（把 mkstemp+replace 换成
`open(path,"w")` 必须翻红；直写敏感文件也必须翻红）。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (ROOT / "scripts", ROOT / "r20_backend", ROOT / "r20_gateway", ROOT / "plugins")

ATOMIC_NAME = re.compile(r"^_?atomic_write")
SENSITIVE = (
    "circuit_breaker.json",
    "stop_cooldown.json",
    "ledger_sync_status.json",
    "position_trackers.json",
    "open_order_intents.json",
    "cycle_disclosure.json",
    "market_data_health.json",
)


def _atomic_helper_violations(name: str, src: str) -> "list[str]":
    problems: "list[str]" = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return problems
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and ATOMIC_NAME.match(fn.name)):
            continue
        body = ast.unparse(fn)
        # 临时文件机制等价：`mkstemp` 或 `NamedTemporaryFile(delete=False)`（两者都是"另写一份再换"）
        has_temp = "mkstemp" in body or "NamedTemporaryFile" in body
        if not has_temp:
            problems.append(f"{name}:{fn.lineno}: {fn.name} 缺临时文件机制（mkstemp/NamedTemporaryFile）")
        if "fsync" not in body:
            problems.append(f"{name}:{fn.lineno}: {fn.name} 缺 fsync（rename 后断电可能留下空/截断文件）")
        if "os.replace" not in body and "os.rename" not in body:
            problems.append(f"{name}:{fn.lineno}: {fn.name} 缺 os.replace/os.rename（非原子替换）")
        # 目标路径直写：`open(<expr>, "w")` 且该表达式不是临时文件变量
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "open" and len(call.args) >= 2):
                continue
            mode = call.args[1]
            if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                    and ("w" in mode.value or "a" in mode.value)):
                continue
            target = ast.unparse(call.args[0])
            if not re.search(r"(tmp|temp|fd)", target, re.I):
                problems.append(f"{name}:{call.lineno}: {fn.name} 直写目标 {target}（应写临时文件）")
    return problems


def _sensitive_direct_writes(name: str, src: str) -> "list[str]":
    problems: "list[str]" = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return problems
    # 模块常量 → 是否绑定到敏感文件名
    bound: "dict[str, str]" = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            text = ast.unparse(node.value)
            for base in SENSITIVE:
                if base in text:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if isinstance(t, ast.Name):
                            bound[t.id] = base
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "open" and len(call.args) >= 2):
            continue
        mode = call.args[1]
        if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                and ("w" in mode.value or "a" in mode.value)):
            continue
        target = ast.unparse(call.args[0])
        hit = next((b for b in SENSITIVE if b in target), None)
        if hit is None and isinstance(call.args[0], ast.Name):
            hit = bound.get(call.args[0].id)
        if hit:
            problems.append(f"{name}:{call.lineno}: 直写敏感文件 {hit}（必须走原子写）")
    return problems


def _sources() -> "dict[str, str]":
    out: "dict[str, str]" = {}
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" not in str(path):
                out[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    return out


class AtomicWriteInvariantTest(unittest.TestCase):
    def test_helpers_are_counted(self):
        """非空自检：辅助函数数量够多才说明扫描有效。"""
        found = 0
        for src in _sources().values():
            tree = ast.parse(src)
            found += sum(1 for fn in ast.walk(tree)
                         if isinstance(fn, ast.FunctionDef) and ATOMIC_NAME.match(fn.name))
        self.assertGreaterEqual(found, 6, f"只扫到 {found} 个原子写辅助函数，扫描可能失效")

    def test_every_atomic_helper_is_really_atomic(self):
        problems: "list[str]" = []
        for name, src in _sources().items():
            problems += _atomic_helper_violations(name, src)
        self.assertEqual(problems, [], "原子写辅助函数不原子：\n" + "\n".join(problems))

    def test_no_sensitive_file_is_written_directly(self):
        problems: "list[str]" = []
        for name, src in _sources().items():
            problems += _sensitive_direct_writes(name, src)
        self.assertEqual(problems, [], "敏感状态文件被直写：\n" + "\n".join(problems))

    def test_gate_has_teeth(self):
        bad_helper = (
            "import json\n"
            "def _atomic_write_json(path, payload):\n"
            "    with open(path, 'w') as f:\n"
            "        json.dump(payload, f)\n"
        )
        self.assertTrue(_atomic_helper_violations("x.py", bad_helper),
                        "把原子写换成直写必须被抓")
        good_helper = (
            "import json, os, tempfile\n"
            "def _atomic_write_json(path, payload):\n"
            "    fd, tmp = tempfile.mkstemp(dir='.')\n"
            "    with os.fdopen(fd, 'w') as f:\n"
            "        json.dump(payload, f)\n"
            "        f.flush(); os.fsync(f.fileno())\n"
            "    os.replace(tmp, path)\n"
        )
        self.assertEqual(_atomic_helper_violations("y.py", good_helper), [])
        # `NamedTemporaryFile(delete=False)` 是等价机制（我第一版只认 mkstemp ⇒ 假阳性）
        named_tmp = (
            "import json, os, tempfile\n"
            "def _atomic_write_json(path, payload):\n"
            "    with tempfile.NamedTemporaryFile('w', dir='.', delete=False) as tf:\n"
            "        json.dump(payload, tf)\n"
            "        tf.flush(); os.fsync(tf.fileno())\n"
            "        name = tf.name\n"
            "    os.replace(name, path)\n"
        )
        self.assertEqual(_atomic_helper_violations("nt.py", named_tmp), [])
        direct = (
            "import json\n"
            "CIRCUIT_BREAKER_FILE = '/tmp/circuit_breaker.json'\n"
            "def save(payload):\n"
            "    with open(CIRCUIT_BREAKER_FILE, 'w') as f:\n"
            "        json.dump(payload, f)\n"
        )
        self.assertTrue(_sensitive_direct_writes("z.py", direct),
                        "直写敏感文件必须被抓（含经模块常量的间接写法）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
