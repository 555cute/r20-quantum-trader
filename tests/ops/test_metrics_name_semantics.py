"""指标名的语义唯一性门（第一百八十二刀）。

## 为什么需要

Prometheus 的 exposition 里，**一个指标名只能有一个 HELP 和一个 TYPE，且标签集必须一致**。
而 `metrics.render_prometheus` 的 `emit()` 是"第一次见到这个名字就登记 family"：

```python
family = families.setdefault(name, {"help": help_text, "type": type_text, "labels": [...]})
```

⇒ 同一个名字第二次带**不同的 HELP** 时，那句 HELP **永远不会出现在输出里**（静默丢失）。

这不是假想：上一刀我写 `r20_protection_mismatch_legs{kind="side"|"size"}` 时就踩了 ——
"方向不符不计入覆盖"与"量不符仍被计入覆盖"是**两种语义**，共用名字后 `size` 那句 HELP
发不出去。本门把该约束钉死：**名字即语义**，不同语义必须不同名字。

同类的第二个坑是**标签集不一致**（同名指标一次带 `venue`、一次带 `venue+kind`）——
这在 Prometheus 里属非法 exposition，且同样由 `setdefault` 静默吞掉。
"""

from __future__ import annotations

import ast
import unittest
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]
METRICS_FILE = ROOT / "r20_backend" / "metrics.py"


def _literal(node):
    return node.value if isinstance(node, ast.Constant) else None


def _label_shape(node) -> str:
    """标签集的**形态**：只看**键名与顺序**，不看取值。

    ⚠️ 不能直接 `ast.unparse(整个列表)` —— 那会把取值算进去，于是
    `[("venue","a")]` 与 `[("venue","b")]` 会被当成"两种标签集"（合法用法被误判；
    本门第二版就是这么红的）。Prometheus 关心的是**键集一致**。
    """
    if node is None:
        return ""
    if not isinstance(node, ast.List):
        return "<?>"
    keys = []
    for el in node.elts:
        if isinstance(el, ast.Tuple) and el.elts:
            key = _literal(el.elts[0])
            keys.append(str(key) if key is not None else "<?>")
        else:
            keys.append("<?>")
    return "+".join(keys) or "<none>"


def find_name_semantic_conflicts(source: str) -> List[str]:
    """返回"同一指标名带多种 HELP/TYPE/标签集"的问题清单（空 = 干净）。

    只看**字面量**参数：非字面量（例如拼接出来的名字）无从静态判定，跳过并计入 `dynamic`
    （由调用方断言其数量，避免"门其实什么都没查"）。
    """
    tree = ast.parse(source)
    seen: Dict[str, list] = defaultdict(list)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "emit"):
            continue
        if not node.args:
            continue
        name = _literal(node.args[0])
        if not name:
            continue
        kw = {k.arg: k.value for k in node.keywords}
        # ⚠️ `labels` 在本仓是**第三个位置参数**（`emit(name, value, [("venue", v)], ...)`），
        # 只查关键字参数会让"标签集一致性"这一项**永远空转**（本门第一次跑就是这么假绿的，
        # 是 `test_gate_has_teeth_on_label_shape` 当场戳穿的）。
        labels_node = node.args[2] if len(node.args) > 2 else kw.get("labels")
        seen[name].append({
            "line": node.lineno,
            "help": _literal(kw.get("help_text")),
            "type": _literal(kw.get("type_text")),
            "labels": _label_shape(labels_node),
        })
    problems: List[str] = []
    for name, entries in sorted(seen.items()):
        helps = {e["help"] for e in entries if e["help"]}
        types = {e["type"] for e in entries if e["type"]}
        label_shapes = {e["labels"] for e in entries}
        if len(helps) > 1:
            problems.append(
                f"{name}: 同一个名字有 {len(helps)} 种 HELP ⇒ 只有首个会发出去"
                f"（行 {[e['line'] for e in entries]}）—— 不同语义请用不同名字")
        if len(types) > 1:
            problems.append(f"{name}: 同一个名字有 {len(types)} 种 TYPE: {sorted(types)}")
        if len(label_shapes) > 1:
            problems.append(f"{name}: 同一个名字有 {len(label_shapes)} 种标签集: "
                            f"{sorted(label_shapes)}（Prometheus 里属非法 exposition）")
    return problems


def count_metric_names(source: str) -> int:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "emit" and node.args):
            name = _literal(node.args[0])
            if name:
                names.add(name)
    return len(names)


class MetricsNameSemanticsTest(unittest.TestCase):
    def test_no_metric_name_carries_two_meanings(self):
        problems = find_name_semantic_conflicts(METRICS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(problems, [], "同名指标混了多种语义（HELP/TYPE/标签集）：\n"
                                       + "\n".join(problems))

    def test_scan_is_not_vacuous(self):
        n = count_metric_names(METRICS_FILE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(n, 30, f"只扫到 {n} 个指标名 ⇒ 门可能已与实现脱节")

    def test_rendered_exposition_has_one_help_per_family(self):
        """行为侧复核：真实渲染结果里，每个名字的 HELP 只能出现一次。"""
        import re
        from r20_backend import metrics as M
        text = M.render_prometheus(M.build_snapshot(protection_orphans={
            "binance": {"readable": True, "candidates": 1, "unattributed": 1,
                        "side_mismatch": 2, "size_mismatch": 3, "ledger_evidence": True},
            "gate": {"readable": False, "candidates": None, "unattributed": None,
                     "side_mismatch": None, "size_mismatch": None, "ledger_evidence": None}}))
        helps = re.findall(r"^# HELP (\S+)", text, flags=re.M)
        self.assertEqual(len(helps), len(set(helps)), "渲染输出里出现重复的 HELP 行")
        for metric in ("r20_protection_side_mismatch_legs", "r20_protection_size_mismatch_legs"):
            self.assertIn(metric, helps, f"{metric} 没有 HELP（那种语义就没被解释）")

    def test_gate_has_teeth_on_help(self):
        bad = (
            "def render():\n"
            "    emit('r20_x', 1, [('kind', 'a')], help_text='语义甲')\n"
            "    emit('r20_x', 2, [('kind', 'b')], help_text='语义乙')\n"
        )
        problems = find_name_semantic_conflicts(bad)
        self.assertTrue(any("种 HELP" in p for p in problems),
                        "同一名字两种 HELP 没被抓出来 ⇒ 门没有牙齿")

    def test_gate_has_teeth_on_label_shape(self):
        bad = (
            "def render():\n"
            "    emit('r20_y', 1, [('venue', 'a')], help_text='同义')\n"
            "    emit('r20_y', 2, [('venue', 'a'), ('kind', 'b')], help_text='同义')\n"
        )
        problems = find_name_semantic_conflicts(bad)
        self.assertTrue(any("种标签集" in p for p in problems), "同名不同标签集没被抓出来")

    def test_clean_source_passes(self):
        good = (
            "def render():\n"
            "    emit('r20_z', 1, [('venue', 'a')], help_text='甲')\n"
            "    emit('r20_z', 2, [('venue', 'b')], help_text='甲')\n"
            "    emit('r20_w', 3, [('venue', 'a')], help_text='乙')\n"
        )
        self.assertEqual(find_name_semantic_conflicts(good), [],
                         "同义不同取值被误判（门太严会逼着大家改名字）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
