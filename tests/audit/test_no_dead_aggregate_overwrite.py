"""死写闸：增量被整体赋值抹掉（第二百二十一刀）。

## 为什么需要它

第二百二十刀修掉的那处回归是：抽取时把

    pending_inst_ids = set()          # 建基准
    for order in ...:
        pending_inst_ids.add(...)     # 增量
    ...
    pending_inst_ids, ... = collect(...)   # **整体赋值** ⇒ 中间的增量全被抹掉

写成了"整体赋值"。这类缺陷**不报错、不抛异常、测试也全绿**（旧值只是被丢掉），
只有"拿尺子去量那个数"才会露馅 —— 而它恰恰发生在主链的风控计数上。

本闸用 AST 找同一名字的**同一层块内**这种形状：

1. 先被整体赋值（`x = …`，建基准）；
2. 中间只被**增量修改**（`x.add/update/append`、`x += 1`、`x[k] = v`）；
3. 之后**又被整体赋值**，且**从未被真正读过**（没被消费过）。

⇒ 第 1 步建立、第 2 步累积的值被第 3 步抹掉 ⇒ 要么删掉死代码，要么把第 3 步改成并集/累加。

## 刻意不误杀

- `rows = sorted(rows)` / `total = total + extra`：整体赋值**读了旧值** ⇒ 不报（这是正常写法）；
- 循环变量、`with … as` 的正常重绑 ⇒ 不报（没有"先建基准再增量"的形状）；
- 嵌套函数体内的同名量各自独立 ⇒ 不进嵌套函数体（保守）。

发现即红；确属刻意的写法走 `ALLOW` 白名单并写明理由。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("scripts", "r20_backend", "plugins")
MIN_FILES = 200

MUTATORS = {"add", "update", "append", "extend", "insert", "setdefault", "add_all", "discard"}

# 白名单：(相对路径, 名字, 行号锚) -> 理由。确属刻意的"重建/清空"才登记。
ALLOW: dict = {}


def _walk_no_funcs(node):
    """深度优先遍历，但**不进嵌套函数/Lambda**（同名量在闭包内外互不相干）。"""
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield n
        stack.extend(ast.iter_child_nodes(n))


def _collect(node):
    """返回 (reads, mutations)：reads 是**真正消费**旧值的读；mutations 是增量修改。"""
    reads, muts = set(), set()
    for n in _walk_no_funcs(node):
        if isinstance(n, ast.AugAssign):
            # `x += 1` 是读改写：旧值被用了，但**累积结果**照样可能被下一步抹掉 ⇒ 记为增量
            for t in ast.walk(n.target):
                if isinstance(t, ast.Name):
                    muts.add(t.id)
        elif isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load) \
                and n.attr in MUTATORS and isinstance(n.value, ast.Name):
            muts.add(n.value.id)
        elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store):
            for t in ast.walk(n.value):
                if isinstance(t, ast.Name):
                    muts.add(t.id)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            reads.add(n.id)
    return reads - muts, muts


def _bindings(st):
    """本层语句**整体赋值**（建基准）的名字；下标赋值不算绑定。"""
    out = set()
    targets = []
    if isinstance(st, ast.Assign):
        targets = list(st.targets)
    elif isinstance(st, ast.AnnAssign):
        targets = [st.target]
    elif isinstance(st, ast.For):
        targets = [st.target]
    elif isinstance(st, (ast.With, ast.AsyncWith)):
        targets = [i.optional_vars for i in st.items if i.optional_vars is not None]
    for t in targets:
        for n in ast.walk(t):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                out.add(n.id)
    return out


def _scan_block(stmts, bound, mutated, reads_seen, out, path):
    for st in stmts:
        reads, muts = _collect(st)
        for name in _bindings(st):
            first = bound.get(name)
            if first is not None and name in mutated and name not in reads_seen \
                    and name not in reads:
                out.append((str(path), name, first, st.lineno))
            bound[name] = st.lineno
            mutated.discard(name)          # 重新建基准 ⇒ 增量状态归零
        mutated |= muts
        reads_seen |= reads


class Scanner(unittest.TestCase):
    """闸自身的牙齿与"不误杀"。"""

    def _scan_source(self, src):
        tree = ast.parse(src)
        out = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for field in ("body",):
                    _scan_block(getattr(node, field), {}, set(), set(), out, "sample.py")
        return out

    def test_flags_the_exact_regression_shape(self):
        src = (
            "def f():\n"
            "    ids = set()\n"
            "    for row in rows:\n"
            "        ids.add(row)\n"
            "    ids = collect()\n"
            "    return len(ids)\n")
        hits = self._scan_source(src)
        self.assertEqual([(h[1], h[2], h[3]) for h in hits], [("ids", 2, 5)],
                         f"正是第二百二十刀那种「建基准 → 增量 → 整体赋值」：{hits}")

    def test_does_not_flag_a_legitimate_read_rewrite(self):
        """`rows = sorted(rows)` 读了旧值 ⇒ 正常写法，不许误杀。"""
        src = (
            "def f():\n"
            "    rows = []\n"
            "    for x in xs:\n"
            "        rows.append(x)\n"
            "    rows = sorted(rows)\n"
            "    return rows\n")
        self.assertEqual(self._scan_source(src), [])

    def test_does_not_flag_a_plain_reassignment(self):
        src = ("def f():\n"
               "    x = 1\n"
               "    print(x)\n"
               "    x = 2\n"
               "    return x\n")
        self.assertEqual(self._scan_source(src), [], "读过旧值 ⇒ 正常重绑")

    def test_does_not_flag_independent_names(self):
        src = ("def f():\n"
               "    total = 0\n"
               "    for x in xs:\n"
               "        total += x\n"
               "    other = compute()\n"
               "    return total + other\n")
        self.assertEqual(self._scan_source(src), [])

    def test_flags_augassign_accumulator_being_discarded(self):
        src = ("def f():\n"
               "    n = 0\n"
               "    for x in xs:\n"
               "        n += 1\n"
               "    n = compute()\n"
               "    return n\n")
        hits = self._scan_source(src)
        self.assertEqual([h[1] for h in hits], ["n"], "累加器被整体赋值抹掉，同样要报")

    def test_nested_function_bodies_are_independent(self):
        src = ("def f():\n"
               "    ids = set()\n"
               "    def g():\n"
               "        ids = set()\n"
               "        return ids\n"
               "    return g\n")
        self.assertEqual(self._scan_source(src), [], "嵌套函数内的同名量互不相干")


class RepoScan(unittest.TestCase):
    def test_repo_is_clean_or_explicitly_allowlisted(self):
        files = []
        for root in ROOTS:
            files.extend(p for p in (ROOT / root).rglob("*.py")
                         if "__pycache__" not in p.parts)
        self.assertGreaterEqual(len(files), MIN_FILES,
                                f"扫描面太小({len(files)}) ⇒ 闸本身可能是空的")
        hits = []
        for path in files:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:      # pragma: no cover - 语法错的文件轮不到本闸
                continue
            rel = path.relative_to(ROOT)
            out = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _scan_block(node.body, {}, set(), set(), out, rel)
            for path_s, name, first, second in out:
                if (path_s, name, first) in ALLOW:
                    continue
                hits.append(f"{path_s}:{second} 「{name}」在 {first} 行建立后被增量修改，"
                            f"又在 {second} 行被整体赋值（增量被抹掉）")
        self.assertEqual(hits, [], "疑似死写/覆盖（确属刻意请登记 ALLOW 并写明理由）：\n"
                                   + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
