r"""order_lifecycle 抽取对拍门（结构优化阶段 4·B3 第八十四刀）。

`clean_stale_open_orders`(132) / `reconcile_pending_orders`(83) 从
`scripts/ai_factor_trader.py` **纯搬家**到 `scripts/trader/order_lifecycle.py`。

本域两函数各带**嵌套闭包**（`_intent_covers` / `_cancel_orphan`）——本门钉：
① 闭包随整体搬迁、函数体（含嵌套 def）AST **零例外全等**；
② 壳同名注入形状；
③ **`_BROKEN_VENUES` 引用语义**（既有测试只断言 ok/日志，未断言集合被改到
   —— 若注入的是副本，凭证坏所的"本轮摘除执行资格"会静默失效）；
④ fail-closed 与 reason 文案经门面常量注入后仍生效；
⑤ 判据自检（±双向）。
"""
from __future__ import annotations

import ast
import io
import subprocess
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRE = "9dccec0"  # 本刀动工前最后提交（第八十三刀收口）
FNS = ("clean_stale_open_orders", "reconcile_pending_orders")
MOVED = ROOT / "scripts" / "trader" / "order_lifecycle.py"

# ---------------------------------------------------------------------------
# 搬走之后的**文档化差异**（aa6d4e0「修复负数张数泄漏」）
#
# 「搬走时逐字」这条性质在抽取那一刻成立（本门当时全绿）。此后 aa6d4e0 对
# `clean_stale_open_orders` 做了**一处真实的行为增强**：外所（Gate 带符号张数、
# 字段名 `size`/`amount`）不返回 `side` 时按符号推断方向，否则该行会被下方
# `if not _b or _side not in ('buy', 'sell'): continue` 直接丢弃 —— 表现为
# **外所孤儿挂单永不回收**（占保证金、深夜反抽时无保护成交）。
#
# 按本仓既有先例（`test_brain_package_extraction.py` 的第 137 刀白名单）放行
# 这批差异，范围刻意收得极窄：**两个净增代码块**整块剔除 + **一条改写行**还原，
# 且每处替换都断言"恰好出现一次"（白名单写错=立刻红，不会静默放水）。
# 另有 `test_signed_size_side_inference_lands_on_the_moved_impl` 正向钉住这段
# 新行为真的生效 —— 白名单不能被用来掩盖真正的搬运错误。
# ---------------------------------------------------------------------------
_DELTA_BLOCKS = (
    # ① 带符号张数 → 方向推断（净增 9 行）
    '            if not _side:\n'
    '                _sz_raw = o.get("size") if o.get("size") is not None else _raw.get("size")\n'
    '                if _sz_raw is None:\n'
    '                    _sz_raw = o.get("amount") if o.get("amount") is not None else _raw.get("amount")\n'
    '                try:\n'
    '                    if _sz_raw is not None and float(_sz_raw) != 0:\n'
    '                        _side = "buy" if float(_sz_raw) > 0 else "sell"\n'
    '                except (TypeError, ValueError):\n'
    '                    pass\n',
    # ② `is_reduce_only` 别名兜底（净增 2 行）
    '            if _ro is None:\n'
    '                _ro = o.get("is_reduce_only") if o.get("is_reduce_only") is not None else _raw.get("is_reduce_only")\n',
)
_DELTA_REWRITES = (
    # ③ base 归一里剥掉 `_USDT` 后缀（Gate 合约名形如 `BTC_USDT`）
    ('            _b = str(o.get("base") or "").upper() or inst_disp.replace("_USDT", "").replace("USDT", "").split("-")[0].upper()\n',
     '            _b = str(o.get("base") or "").upper() or inst_disp.split("_")[0].split("-")[0].upper()\n'),
)


def _old_tree() -> ast.Module:
    r = subprocess.run(["git", "show", f"{PRE}:scripts/ai_factor_trader.py"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"基线取不到：{r.stderr[:200]}"
    return ast.parse(r.stdout)


def _normalised_moved_tree() -> ast.Module:
    """把 aa6d4e0 的文档化差异**还原**成搬运时的样子，再逐字比对。"""
    src = MOVED.read_text(encoding="utf-8")
    for block in _DELTA_BLOCKS:
        assert src.count(block) == 1, f"放行块没找到或重复（白名单过期）：{block[:60]!r}"
        src = src.replace(block, "")
    for new_line, old_line in _DELTA_REWRITES:
        assert src.count(new_line) == 1, f"放行改写没找到或重复（白名单过期）：{new_line[:60]!r}"
        src = src.replace(new_line, old_line)
    return ast.parse(src)


def _get_func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} 不在顶层")


def _body_dump(fn: ast.FunctionDef) -> str:
    return ast.dump(ast.Module(body=fn.body, type_ignores=[]), include_attributes=False)


class OrderLifecycleVerbatimTest(unittest.TestCase):
    def test_moved_bodies_match_pre_extraction_verbatim(self):
        """含嵌套闭包的函数体必须逐字（仅放行上面登记的 aa6d4e0 差异）。"""
        old = _old_tree()
        new = _normalised_moved_tree()
        for fn in FNS:
            with self.subTest(fn=fn):
                o, n = _get_func(old, fn), _get_func(new, fn)
                self.assertEqual([a.arg for a in o.args.args],
                                 [a.arg for a in n.args.args])
                self.assertEqual(_body_dump(o), _body_dump(n),
                                 f"{fn} 与抽取前**不再是同一实现**")
                # 嵌套闭包必须**还在**（整体随迁，不是被外提）
                nested = {x.name for x in ast.walk(n) if isinstance(x, ast.FunctionDef)}
                self.assertTrue(nested - {fn}, f"{fn} 的嵌套闭包没随迁")

    def test_signed_size_side_inference_lands_on_the_moved_impl(self):
        """正向断言：外所不返回 `side` 时按带符号张数推断方向（aa6d4e0）。

        没有这段推断时，Δ 白名单里的两个代码块就只剩"删掉也不红"，
        而外所孤儿挂单会重新变成永不回收 —— 所以必须行为级钉住。
        """
        import scripts.ai_factor_trader as aft
        cancelled = []
        gate = types.SimpleNamespace(
            # Gate 形状：带符号张数（-3 = 空），无 `side` 字段，字段名 size
            list_open_orders=lambda b: [{"id": "g1", "contract": "BTC_USDT",
                                        "create_time": 1, "size": -3}],
            cancel_order=lambda inst, oid: cancelled.append((inst, oid)))
        okx = types.SimpleNamespace(pending_orders=lambda: [], cancel_order=lambda *a: None)
        with patch.object(aft, "_BROKEN_VENUES", set()), \
             patch.object(aft, "okx_rest", okx), \
             patch.object(aft, "current_environment",
                          lambda: types.SimpleNamespace(mode="demo")), \
             patch.object(aft.venue_registry, "execution_open", lambda v, e: v == "gate"), \
             patch.object(aft.venue_registry, "get_adapter",
                          lambda v, environment=None: gate), \
             patch.object(aft, "load_instruments", lambda: [{"instId": "BTC-USDT-SWAP"}]), \
             patch.object(aft, "load_open_intents", lambda: []), \
             redirect_stdout(io.StringIO()):
            ok, msg = aft.clean_stale_open_orders()
        self.assertTrue(ok, msg)
        self.assertEqual(cancelled, [("BTC", "g1")],
                         "带符号张数没推断出方向 ⇒ 外所孤儿挂单不会被回收")

    def test_delta_whitelist_actually_notices_undocumented_edits(self):
        """自检：白名单之外的任何一行改动都必须被 `_body_dump` 看见。"""
        base = "def f():\n    _side = str(o.get('side') or '').lower()\n    return _side\n"
        tampered = base.replace("return _side", "return _side or 'x'")
        o = _body_dump(_get_func(ast.parse(base), "f"))
        self.assertNotEqual(o, _body_dump(_get_func(ast.parse(tampered), "f")),
                            "自检：未登记的行改动看不见")
        self.assertEqual(o, _body_dump(_get_func(ast.parse(base), "f")),
                         "自检：同文误报")


    def test_shells_are_def_with_lazy_same_name_injection(self):
        tree = ast.parse((ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8"))
        want = {
            "clean_stale_open_orders": ("load_open_intents", "OPEN_INTENT_TTL_MS",
                                        "_BROKEN_VENUES", "current_environment",
                                        "load_instruments", "okx_rest", "venue_registry"),
            "reconcile_pending_orders": ("_order_pos_side", "load_open_intents",
                                         "load_trackers", "OPEN_INTENT_TTL_MS",
                                         "RECONCILE_REASON_SIDE_MISMATCH",
                                         "RECONCILE_REASON_INTENT_STALE",
                                         "RECONCILE_REASON_ORPHAN", "okx_rest"),
        }
        facade = set(dir(__import__("scripts.ai_factor_trader", fromlist=["x"])))
        for fn, names in want.items():
            with self.subTest(fn=fn):
                dumped = ast.unparse(_get_func(tree, fn))
                self.assertIn("_order_lifecycle_", dumped, "壳没转调子包")
                for g in names:
                    self.assertIn(f"{g}={g}", dumped, f"壳缺同名注入 {g}")
                    self.assertIn(g, facade, f"{g} 不是门面全局 ⇒ 壳传参必 NameError")

    def test_broken_venues_reference_semantics_survive_injection(self):
        """**本刀最易静默失效的一条**：凭证坏所必须被记进**调用方那个**集合。

        既有 batch6 用例只断言"跳过而非拦轮"，没断言集合被改到 ——
        注入若传成副本，`venue_execution_ready` 就永远看不到坏所，
        本轮路由会继续往外所派单（真金白银的口子）。
        """
        import scripts.ai_factor_trader as aft
        gate_err = type("GateAPIError", (Exception,), {})("Gate INVALID_KEY: Invalid key provided")
        gate = type("G", (), {
            "list_open_orders": lambda self, b: (_ for _ in ()).throw(gate_err)})()
        okx = types.SimpleNamespace(pending_orders=lambda: [], cancel_order=lambda *a: None)
        broken = set()   # ← 我们自己持有引用
        with patch.object(aft, "_BROKEN_VENUES", broken), \
             patch.object(aft, "okx_rest", okx), \
             patch.object(aft, "current_environment",
                          lambda: types.SimpleNamespace(mode="demo")), \
             patch.object(aft.venue_registry, "execution_open", lambda v, e: v == "gate"), \
             patch.object(aft.venue_registry, "get_adapter",
                          lambda v, environment=None: gate), \
             patch.object(aft, "load_instruments", lambda: [{"instId": "BTC-USDT-SWAP"}]), \
             redirect_stdout(io.StringIO()):
            ok, msg = aft.clean_stale_open_orders()
        self.assertTrue(ok, f"凭证坏所应跳过而非拦轮: {msg}")
        self.assertIn("gate", broken,
                      "注入的是集合副本 ⇒ 坏所没被登记，路由仍会派单到该所")

    def test_reconcile_reacts_to_facade_patches_and_fails_closed(self):
        """孤儿单撤销 + reason 文案（门面常量注入）+ 撤销失败 fail-closed。"""
        import scripts.ai_factor_trader as aft
        calls = []
        okx = types.SimpleNamespace(
            pending_orders=lambda: [{"instId": "BTC-USDT-SWAP", "ordId": "o1",
                                     "side": "buy", "state": "live"}],
            cancel_order=lambda inst, oid: calls.append((inst, oid)))
        with patch.object(aft, "okx_rest", okx), \
             patch.object(aft, "load_trackers", lambda: {}), \
             patch.object(aft, "load_open_intents", lambda: []), \
             patch.object(aft, "RECONCILE_REASON_ORPHAN", "测试原因-孤儿"), \
             redirect_stdout(io.StringIO()) as buf:
            ok, kept = aft.reconcile_pending_orders()
        self.assertTrue(ok)
        self.assertEqual(calls, [("BTC-USDT-SWAP", "o1")], "孤儿单没被撤销")
        self.assertEqual(kept, set())
        self.assertIn("测试原因-孤儿", buf.getvalue(),
                      "reason 文案没走门面注入（patch 面断了）")

        boom = types.SimpleNamespace(
            pending_orders=lambda: [{"instId": "BTC-USDT-SWAP", "ordId": "o1",
                                     "side": "buy", "state": "live"}],
            cancel_order=lambda *a: (_ for _ in ()).throw(RuntimeError("net down")))
        with patch.object(aft, "okx_rest", boom), \
             patch.object(aft, "load_trackers", lambda: {}), \
             patch.object(aft, "load_open_intents", lambda: []), \
             redirect_stdout(io.StringIO()):
            ok2, _ = aft.reconcile_pending_orders()
        self.assertFalse(ok2, "撤销失败必须 fail-closed")

    def test_judgment_actually_notices_a_change(self):
        base = "def f():\n    def g():\n        return 1\n    return g()\n"
        tampered = "def f():\n    def g():\n        return 2\n    return g()\n"
        o = _body_dump(_get_func(ast.parse(base), "f"))
        self.assertNotEqual(o, _body_dump(_get_func(ast.parse(tampered), "f")),
                            "自检：嵌套闭包内的改动看不见")
        self.assertEqual(o, _body_dump(_get_func(ast.parse(base), "f")),
                         "自检：同文误报")


if __name__ == "__main__":
    unittest.main()
