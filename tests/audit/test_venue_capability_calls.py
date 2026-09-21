"""按所分流的能力调用必须**带守卫**（第一百九十二刀）。

## 为什么（上一刀真机踩到）

三个适配器的能力**并不一致**（实测矩阵）：

| 方法 | okx | binance | gate |
|---|---|---|---|
| `cancel_price_order` | ✗ | ✗ | ✓ |
| `cancel_algo_order` | ✗ | ✓ | ✗ |
| `list_open_orders` | ✗ | ✗ | ✓ |
| `detect_position_mode` | ✗ | ✓ | ✓ |
| `signed_request` | ✗ | ✓ | ✓ |

`cancel_orphan_attributed_legs`（跨所、默认含 binance）此前直接 `ad.cancel_price_order(leg_id)`
⇒ 对 **Binance 恒 `AttributeError`** ⇒ 腿撤不掉（而 Binance 恰是孤儿腿最多的所）。
这类调用的失败往往是**被 except 吞掉**的（"读不到/做不到"静默变成"没有"），所以必须靠门钉住。

## 判据

在 `scripts/`、`r20_backend/` 里，对**按所分流的能力**（至少一所有、至少一所无）的调用，
必须能在其所在函数里找到下列**任一**守卫：

1. `hasattr(同一接收者, "方法")` / `getattr(同一接收者, "方法", …)` —— 能力探针；
2. **按所分流**：形如 `_v == "gate"` / `venue in ("gate", "binance")` 的字符串比较
   （结构化识别：比较双方是"场所名变量"与"场所字面量"）；
3. **接收者本身绑定到某个所**：`ad_bn` / `ad_gate` / `_venue_accounts_gate` 这类名字里含场所名；
4. `self.方法(...)` —— 适配器**自身内部**调用（只有它自己有这个方法）；
5. 显式允许清单（附理由）。

现状：**0 处无守卫**。另附非空自检与牙齿（合成的无守卫 `ad.cancel_price_order(x)` 必被抓）。
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SCAN_DIRS = ("scripts", "r20_backend", "r20_gateway", "plugins")
VENUES = ("okx", "binance", "gate")
VENUE_WORDS = set(VENUES)
#: 形如 `_v` / `venue` / `_gv` 的"场所名变量"（用于结构化识别按所分流）
VENUEISH_NAMES = {"v", "venue", "_v", "_gv", "target_venue", "venue_key", "name", "self_venue"}
#: 显式放行（附理由）。当前为空 —— 守卫都能结构化识别。
ALLOWLIST: dict[tuple[str, str, int], str] = {}


def adapter_classes():
    from r20_backend.exchanges import get_adapter
    return {v: type(get_adapter(v, environment="demo")) for v in VENUES}


def venue_specific_methods(classes) -> dict:
    """至少一所有、至少一所无的公共能力 ⇒ 调用它们就得带守卫。"""
    allm = set()
    for c in classes.values():
        allm |= {m for m in dir(c) if not m.startswith("_") and callable(getattr(c, m, None))}
    out = {}
    for m in sorted(allm):
        have = [v for v, c in classes.items() if hasattr(c, m)]
        if 0 < len(have) < len(VENUES):
            out[m] = have
    return out


def _enclosing(tree: ast.AST, lineno: int):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno >= best.lineno:
                    best = node
    return best


def venue_bound_by_binding(tree, fn, recv: str, method: str, specific: dict):
    """③ 接收者是否**由 `get_adapter("<venue>", …)` 绑定**（同函数或模块级），且该所有这个方法。

    真机形态：`ad_bn = get_adapter("binance", environment=…)`、`ad = get_adapter("gate", …)`
    以及模块级 `okx_rest`（名字里就含场所）。这类接收者天然只可能是某一个所 ⇒ 无需再探测。
    """
    scopes = [n for n in (fn, tree) if n is not None]
    for scope in scopes:
        for node in ast.walk(scope):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if recv not in targets:
                continue
            value = node.value
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == "get_adapter"):
                continue
            venue = None
            for arg in value.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    venue = arg.value.lower()
                    break
            if venue is None:                      # get_adapter(venue, …) 变量参数：看名字
                for kw in value.keywords + []:
                    if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        venue = kw.value.value.lower()
            if venue and method in specific and venue in specific[method]:
                return f"bound-to-{venue}"
    return None


def guard_kind(fn, recv: str, method: str, *, tree=None, specific=None):
    """返回守卫类型名，或 None（无守卫）。"""
    if recv == "self":
        return "self-internal"                                   # ④
    named = [w for w in VENUE_WORDS if w in recv.lower()]
    if named and specific is not None:
        # ③a 名字里带场所 ⇒ 只在该所**确实有这个方法**时才算守卫；
        # 若带的是"没有该方法的所"（如 `ad_gate.cancel_algo_order`）⇒ **不豁免**，这正是危险。
        if any(w in specific.get(method, []) for w in named):
            return f"venue-bound-name:{'/'.join(sorted(named))}"
    if tree is not None and specific is not None:
        bound = venue_bound_by_binding(tree, fn, recv, method, specific)   # ③b ad_bn = get_adapter("binance")
        if bound:
            return bound
    if fn is None:
        return None
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("hasattr", "getattr"):       # ①
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and node.args[1].value == method and ast.unparse(node.args[0]) == recv:
                return f"{node.func.id}-probe"
        if isinstance(node, ast.Compare):                         # ②
            ops = [ast.unparse(o) for o in [node.left] + list(node.comparators)]
            lits = {n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            if (lits & VENUE_WORDS) and any(o in VENUEISH_NAMES for o in ops):
                return "venue-branch"
    return None


def find_unguarded_calls(source: str, rel_path: str, specific: dict):
    out = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method not in specific:
            continue
        recv = ast.unparse(node.func.value)
        fn = _enclosing(tree, node.lineno)
        if guard_kind(fn, recv, method, tree=tree, specific=specific):
            continue
        if (rel_path, method, node.lineno) in ALLOWLIST:
            continue
        out.append((node.lineno, method, recv, fn.name if fn else "<module>", specific[method]))
    return out


def _iter_py():
    for root in SCAN_DIRS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


class VenueCapabilityCallsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specific = venue_specific_methods(adapter_classes())

    def test_every_venue_specific_call_has_a_guard(self):
        problems = []
        for path in _iter_py():
            rel = str(path.relative_to(ROOT))
            for line, method, recv, fname, have in find_unguarded_calls(
                    path.read_text(encoding="utf-8"), rel, self.specific):
                problems.append(f"{rel}:{line} ({fname}) {recv}.{method}(…) 仅 {have} 有该方法")
        self.assertEqual(problems, [], "按所分流的能力调用没有守卫（另一所会 AttributeError，"
                                       "且常被 except 吞掉）：\n" + "\n".join(problems))

    def test_matrix_is_not_vacuous(self):
        self.assertGreaterEqual(len(self.specific), 5,
                                f"按所分流的能力只有 {len(self.specific)} 个 ⇒ 门可能已与实现脱节")
        for must in ("cancel_price_order", "cancel_algo_order", "list_open_orders"):
            self.assertIn(must, self.specific)

    def test_call_sites_are_actually_scanned(self):
        seen = 0
        for path in _iter_py():
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in self.specific:
                    seen += 1
        self.assertGreaterEqual(seen, 15, f"只扫到 {seen} 个相关调用点 ⇒ 门与实现脱节")

    def test_teeth_on_unguarded_call(self):
        # ⚠️ 这张表必须覆盖下面每个用例用到的方法 —— 否则 `find_unguarded_calls` 根本不看它，
        # 断言 `== []` 就成了**空转**（本刀自己踩到过一次，见提交信息）。
        specific = {"cancel_price_order": ["gate"], "cancel_algo_order": ["binance"]}
        unguarded = ("def f(ad, oid):\n"
                     "    return ad.cancel_price_order(oid)\n")
        self.assertTrue(find_unguarded_calls(unguarded, "scripts/x.py", specific),
                        "无守卫的按所能力调用没被抓到 ⇒ 门没有牙齿")
        for guarded in (
            "def f(ad, oid):\n"
            "    if hasattr(ad, 'cancel_price_order'):\n"
            "        return ad.cancel_price_order(oid)\n",
            "def f(_v, ad, oid):\n"
            "    return ad.cancel_price_order(oid) if _v == 'gate' else None\n",
            "def f(ad_gate, oid):\n"
            "    return ad_gate.cancel_price_order(oid)\n",
            # ③b：接收者由 get_adapter("binance") 绑定 ⇒ 该所确有 cancel_algo_order ⇒ 合规
            "def f(oid):\n"
            "    ad_bn = get_adapter('binance', environment='demo')\n"
            "    return ad_bn.cancel_algo_order(algo_id=oid)\n",
            "class A:\n"
            "    def f(self, oid):\n"
            "        return self.cancel_price_order(oid)\n",
        ):
            self.assertEqual(find_unguarded_calls(guarded, "scripts/x.py", specific), [],
                             f"合规写法被误判：{guarded[:40]!r}")
        # 反例：**绑错所**必须被抓（gate 没有 cancel_algo_order）
        wrong_venue = ("def f(oid):\n"
                       "    ad_gate = get_adapter('gate', environment='demo')\n"
                       "    return ad_gate.cancel_algo_order(algo_id=oid)\n")
        self.assertTrue(find_unguarded_calls(wrong_venue, "scripts/x.py", specific),
                        "把某所专有方法调到**绑错所**的接收者上没被抓到 ⇒ 门漏掉真实危险")

    def test_allowlist_entries_have_reasons(self):
        for key, reason in ALLOWLIST.items():
            self.assertTrue(str(reason).strip(), f"{key} 的放行理由不能为空")


if __name__ == "__main__":
    unittest.main()
