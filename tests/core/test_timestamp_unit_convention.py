"""时间戳字段的**单位约定**门：`_ms` = 毫秒、`_ts` = 秒（第一百五十二刀）。

## 为什么

本仓到处传时间戳：`expires_at_ts`（秒）、`written_at_ms`（毫秒）、`ts`（秒）、
`last_success_ms`、`captured_at_ms` …… 混用单位**不报错**，后果却是沉默的：

- 冷却"还剩多久"算成天量级 ⇒ 冷却实际永不生效（或立刻失效）；
- 快照"多旧"算成负数/巨数 ⇒ 新鲜度判断永远通过；
- 熔断"是否过期"算错 ⇒ 该盯的盯不住。

所以约定必须**可执行**：字段名后缀就是单位契约，生产与消费两侧都要自洽。

## 判据（AST，空白不敏感）

1. **生产者**：`x["…_ms"] = <expr>` 的 `<expr>` 必须是毫秒量级（含 `*1000`）；
   `x["…_ts"] = <expr>` 不得含 `*1000`（那是秒字段）；
2. **消费者**：`…_ms` 字段与 `time.time()` 的比较必须带 `*1000` 或 `/1000` 之一；
   `…_ts` 字段与 `time.time() * 1000` 的比较必须带 `/1000`。

## 本次审计结果（先查后钉）

按上述规则扫全仓：**0 处真违规**。（我第一版扫描报了 2 处，都是**假阳性**：
`max(1, int(round((time.time() - t_okx0) * 1000)))` 与 `int(_time.time() * 1000)`
都**确实是毫秒**，只是写成 `* 1000`（带空格），而我的初版匹配写死了 `*1000`。
教训：单位检查里"文本匹配"必须空白不敏感，否则会制造假警报 —— 本门已修正。）

自检两条：**非空**（必须真扫到足够多的 `_ms`/`_ts` 生产点）；**有牙齿**
（把 `*1000` 去掉、或给 `_ts` 加上 `*1000`，都必须被抓）。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (ROOT / "scripts", ROOT / "r20_backend", ROOT / "r20_gateway", ROOT / "plugins")
MS = re.compile(r"\*\s*1000")
DIV_MS = re.compile(r"/\s*1000")


def _field_key(target: ast.AST) -> "str | None":
    """取 `x["key"]` 的字符串键。"""
    if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) \
            and isinstance(target.slice.value, str):
        return target.slice.value
    return None


def _read_keys(node: ast.AST) -> "list[str]":
    """取节点内所有 `x["key"]` / `x.get("key")` 的键。"""
    keys: "list[str]" = []
    for sub in ast.walk(node):
        key = _field_key(sub)
        if key:
            keys.append(key)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr == "get" and sub.args \
                and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
            keys.append(sub.args[0].value)
    return keys


#: **已知例外**：字段名与单位不符、但所有写入者单位一致的字段。
#:
#: 第一百六十九刀（用户拍板"按推荐继续"）把**最后一个**例外正名了：
#: `last_sync_ts` → `last_sync_ms`（值一直是毫秒，名字在说谎；本仓无旧名读者）。
#: ⇒ 本表现在**为空**，`test_known_misnomers_keep_a_consistent_unit` 会**显式断言它为空**
#: （避免该用例悄悄退化成空转装饰）；若将来有人再登记例外，那里的**伴生不变式**
#: （每个写入点必须写毫秒或显式 None）会立刻生效。
KNOWN_MISNOMERS: "dict[str, str]" = {}

#: 已正名的旧字段：**不得**在任何源码里重新出现（防止旧名悄悄回流成第二个同义字段）。
RENAMED_AWAY = {
    "last_sync_ts": "last_sync_ms（第一百六十九刀正名：值一直为毫秒；本仓无旧名读者）",
}


def _copies_from_ms(node: ast.AST) -> bool:
    """RHS 是否是"从另一个毫秒字段复制"（`x["..._ms"]` / `x.get("..._ms")`）？"""
    return any(k.endswith("_ms") for k in _read_keys(node))


def unit_violations(sources: "dict[str, str]") -> "list[str]":
    problems: "list[str]" = []
    for name, src in sorted(sources.items()):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    key = _field_key(target)
                    if not key:
                        continue
                    code = ast.unparse(node.value)
                    if key.endswith("_ms") and not MS.search(code) \
                            and not _copies_from_ms(node.value) and code != "None":
                        # 允许两类写法（都是**有意的**，不是放宽）：
                        #   ① "毫秒字段 ← 另一个毫秒字段"的纯复制（如 avg_ms ← latency_ms）：
                        #      单位由来源保证，硬要求 `*1000` 会制造假阳性；
                        #   ② 显式 `None`：表示"未知/尚未同步"的复位（第一百六十九刀正名
                        #      `last_sync_ts` → `last_sync_ms` 后，该写法进入通用规则）。
                        #      **秒值仍然必须翻红**（牙齿用例守着这一点）。
                        problems.append(
                            f"{name}:{node.lineno}: `{key}`（毫秒）却写成非毫秒: {code[:60]}")
                    if key.endswith("_ts") and MS.search(code) and key not in KNOWN_MISNOMERS:
                        problems.append(
                            f"{name}:{node.lineno}: `{key}`（秒）却写成毫秒: {code[:60]}")
            if isinstance(node, ast.Compare):
                text = ast.unparse(node)
                if "time.time()" not in text:
                    continue
                for side in [node.left] + list(node.comparators):
                    for key in _read_keys(side):
                        if key.endswith("_ms") and not (MS.search(text) or DIV_MS.search(text)):
                            problems.append(
                                f"{name}:{node.lineno}: `{key}`（毫秒）与秒比较未换算: {text[:70]}")
                        if key.endswith("_ts") and MS.search(text) and not DIV_MS.search(text):
                            problems.append(
                                f"{name}:{node.lineno}: `{key}`（秒）与毫秒比较未换算: {text[:70]}")
    return problems


def _sources() -> "dict[str, str]":
    out: "dict[str, str]" = {}
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" not in str(path):
                out[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8")
    return out


def known_misnomer_writers(sources: "dict[str, str]") -> "dict[str, list[str]]":
    """已知例外字段 → 各写入点的代码文本（用于钉"写入者单位一致"）。"""
    out: "dict[str, list[str]]" = {k: [] for k in KNOWN_MISNOMERS}
    for src in sources.values():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    key = _field_key(target)
                    if key in out:
                        out[key].append(ast.unparse(node.value))
            if isinstance(node, ast.Dict):
                for k_node, v_node in zip(node.keys, node.values):
                    if isinstance(k_node, ast.Constant) and k_node.value in out:
                        out[k_node.value].append(ast.unparse(v_node))
    return out


class TimestampUnitConventionTest(unittest.TestCase):
    def test_scan_is_not_vacuous(self):
        found = 0
        for src in _sources().values():
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        key = _field_key(target)
                        if key and (key.endswith("_ms") or key.endswith("_ts")):
                            found += 1
                if isinstance(node, ast.Dict):
                    for k_node in node.keys:
                        if isinstance(k_node, ast.Constant) and isinstance(k_node.value, str) \
                                and (k_node.value.endswith("_ms") or k_node.value.endswith("_ts")):
                            found += 1
        self.assertGreaterEqual(found, 20, f"只扫到 {found} 个时间戳生产点，扫描可能失效")

    def test_known_misnomers_keep_a_consistent_unit(self):
        """例外字段也必须有**伴生不变式**：所有写入者单位一致（此处 = 毫秒）。

        第一百六十九刀后例外表**为空** ⇒ 先断言这一点：表非空说明有人重新登记了例外，
        那时必须**有意识地**回来确认下面的伴生不变式仍然真的在检查东西。
        """
        self.assertEqual(KNOWN_MISNOMERS, {},
                         "例外表非空 ⇒ 请确认下面的伴生不变式仍在执行检查，并更新本断言")
        writers = known_misnomer_writers(_sources())
        for key, reason in KNOWN_MISNOMERS.items():
            with self.subTest(field=key):
                self.assertTrue(writers[key], f"{key} 一个写入点都没扫到？例外表过期了（{reason}）")
                for code in writers[key]:
                    # 允许显式 `None`（"未知/尚未同步"的复位），但**不允许秒值**悄悄混进来
                    self.assertTrue(MS.search(code) or code == "None",
                                    f"{key} 的写入者单位不一致（应全为毫秒或 None）: {code[:60]}")

    def test_no_unit_mixing_in_the_tree(self):
        problems = unit_violations(_sources())
        self.assertEqual(problems, [], "时间戳单位混用：\n" + "\n".join(problems))

    def test_renamed_away_fields_do_not_creep_back(self):
        """旧名不得回流：`last_sync_ts` 已正名为 `last_sync_ms`，源码里不得再写它。

        否则接口会同时存在"旧名（秒语义的谎）"和"新名"两个字段 —— 同语义两处写，
        正是本会话反复修的那类病。
        """
        for name, src in _sources().items():
            for old_key in RENAMED_AWAY:
                self.assertNotIn(f'"{old_key}"', src,
                                 f"{name} 仍写旧字段 {old_key} ⇒ 应使用 {RENAMED_AWAY[old_key]}")

    def test_renamed_field_is_written_as_ms(self):
        """正名后该字段受**通用** `_ms` 规则保护：写入点必须是 `*1000`。"""
        found = []
        for name, src in _sources().items():
            if "last_sync_ms" not in src:
                continue
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if _field_key(target) == "last_sync_ms":
                            found.append((name, ast.unparse(node.value)))
                if isinstance(node, ast.Dict):
                    for k_node, v_node in zip(node.keys, node.values):
                        if isinstance(k_node, ast.Constant) and k_node.value == "last_sync_ms":
                            found.append((name, ast.unparse(v_node)))
        self.assertGreaterEqual(len(found), 4, f"只扫到 {len(found)} 个 last_sync_ms 写入点，扫描可能失效")
        for name, code in found:
            self.assertTrue(MS.search(code) or code == "None",
                            f"{name} 的 last_sync_ms 写入点单位可疑: {code[:60]}")

    def test_gate_has_teeth(self):
        good_ms = 'x = {}\nx["written_at_ms"] = int(time.time() * 1000)\n'
        good_ts = 'x = {}\nx["expires_at_ts"] = int(time.time()) + 60\n'
        self.assertEqual(unit_violations({"a.py": good_ms, "b.py": good_ts}), [])
        # 去掉 *1000 ⇒ 毫秒字段写了秒
        bad_ms = good_ms.replace(" * 1000", "")
        self.assertTrue(unit_violations({"a.py": bad_ms}), "毫秒字段写秒必须被抓")
        # 显式 None（未知/尚未同步）允许；秒值不允许 —— 两者必须是不同的判定
        none_ms = 'x = {}\nx["written_at_ms"] = None\n'
        self.assertEqual(unit_violations({"c.py": none_ms}), [], "显式 None 复位不该翻红")
        self.assertTrue(unit_violations({"d.py": 'x["written_at_ms"] = int(time.time())'}),
                        "秒值必须翻红（别把 None 的放宽变成整体放宽）")
        # 秒字段写毫秒
        bad_ts = 'x = {}\nx["expires_at_ts"] = int(time.time() * 1000)\n'
        self.assertTrue(unit_violations({"b.py": bad_ts}), "秒字段写毫秒必须被抓")
        # 消费侧：_ms 与秒比较未换算
        bad_cmp = ('x = {}\n'
                   'if x.get("written_at_ms", 0) > time.time():\n    pass\n')
        self.assertTrue(unit_violations({"c.py": bad_cmp}), "毫秒与秒比较必须被抓")
        # 空白不敏感：`* 1000` 必须被认成毫秒（我第一版正是在这里报了假阳性）
        spaced = 'x = {}\nx["written_at_ms"] = int(time.time() * 1000)\n'
        self.assertEqual(unit_violations({"d.py": spaced}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
