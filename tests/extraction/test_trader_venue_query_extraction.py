r"""venue_query 抽取对拍门（结构优化阶段 4·B3 第八十六刀）。

`query_positions` / `venue_execution_ready` / `fetch_other_venue_positions` /
`_venue_health_stamp` / `close_position_confirmed`（163 行）从
`scripts/ai_factor_trader.py` **纯搬家**到 `scripts/trader/venue_query.py`。

本门除常规三件外，钉两条**跨函数注入行为**：
`close_position_confirmed` 用的 `query_positions`（OKX 分支）与
`fetch_other_venue_positions`（外所分支）都由门面注入 ——
`patch.object(aft, …)` 必须真的改变它的行为。
再钉 `_BROKEN_VENUES` **读侧引用语义**（§99.2：写侧在 cut 84 已钉）。
"""
from __future__ import annotations

import ast
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRE = "0e16447"  # 本刀动工前最后提交（第八十五刀收口）
FNS = ("query_positions", "venue_execution_ready", "fetch_other_venue_positions",
       "_venue_health_stamp", "close_position_confirmed")
INJ = {
    "query_positions": ("okx_rest",),
    "venue_execution_ready": ("venue_registry", "current_environment", "_BROKEN_VENUES"),
    "fetch_other_venue_positions": ("venue_registry", "venue_execution_ready"),
    "_venue_health_stamp": ("VENUE_HEALTH_FILE",),
    "close_position_confirmed": ("okx_rest", "current_environment", "query_positions",
                                 "fetch_other_venue_positions"),
}


def _old_tree() -> ast.Module:
    r = subprocess.run(["git", "show", f"{PRE}:scripts/ai_factor_trader.py"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"基线取不到：{r.stderr[:200]}"
    return ast.parse(r.stdout)


def _get_func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} 不在顶层")


def _body_dump(fn: ast.FunctionDef) -> str:
    return ast.dump(ast.Module(body=fn.body, type_ignores=[]), include_attributes=False)


class VenueQueryVerbatimTest(unittest.TestCase):
    def test_moved_bodies_match_pre_extraction_verbatim(self):
        old = _old_tree()
        new = ast.parse((ROOT / "scripts/trader/venue_query.py").read_text(encoding="utf-8"))
        for fn in FNS:
            with self.subTest(fn=fn):
                o, n = _get_func(old, fn), _get_func(new, fn)
                self.assertEqual([a.arg for a in o.args.args],
                                 [a.arg for a in n.args.args])
                self.assertEqual([a.arg for a in n.args.kwonlyargs], list(INJ[fn]),
                                 f"{fn} 注入项不是声明的 kw-only 集合")
                if fn == "close_position_confirmed":
                    self.assertIn("wire_pos_side", ast.unparse(n),
                                  "close_position_confirmed 应包含 position-mode wire 修复")
                else:
                    self.assertEqual(_body_dump(o), _body_dump(n),
                                     f"{fn} 与抽取前**不再是同一实现**")

    def test_shells_are_def_with_lazy_same_name_injection(self):
        tree = ast.parse((ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8"))
        facade = set(dir(__import__("scripts.ai_factor_trader", fromlist=["x"])))
        for fn in FNS:
            with self.subTest(fn=fn):
                dumped = ast.unparse(_get_func(tree, fn))
                self.assertIn("_venue_query_", dumped, "壳没转调子包")
                for g in INJ[fn]:
                    self.assertIn(f"{g}={g}", dumped, f"壳缺同名注入 {g}")
                    self.assertIn(g, facade, f"{g} 不是门面全局 ⇒ 壳传参必 NameError")

    def test_close_confirm_uses_patched_facade_query(self):
        """OKX 分支：patch 门面 query_positions 必须改变经壳行为。"""
        import scripts.ai_factor_trader as aft
        okx = types.SimpleNamespace(pending_orders=lambda inst: [],
                                    cancel_order=lambda *a: None,
                                    close_position=lambda *a, **k: None)
        with patch.object(aft, "okx_rest", okx), \
             patch.object(aft, "query_positions", lambda: (True, [], "")), \
             patch("scripts.okx_pos_mode.wire_pos_side", lambda *a, **k: "long"):
            ok, msg = aft.close_position_confirmed("BTC-USDT-SWAP", "long", 1.0)
        self.assertTrue(ok, msg)
        self.assertIn("closed", msg)
        # 门面注入断了会走真 query_positions → 无凭证 → fail-closed
        with patch.object(aft, "okx_rest", okx), \
             patch.object(aft, "query_positions", lambda: (False, [], "no creds")), \
             patch("scripts.okx_pos_mode.wire_pos_side", lambda *a, **k: "long"):
            ok2, msg2 = aft.close_position_confirmed("BTC-USDT-SWAP", "long", 1.0)
        self.assertFalse(ok2, "query_positions 注入断了（读不到凭证却判成功）")

    def test_close_confirm_uses_patched_facade_other_venues(self):
        """外所分支：patch 门面 fetch_other_venue_positions 必须改变经壳行为。"""
        import r20_backend.execution_router as router
        import scripts.ai_factor_trader as aft
        with patch.object(router, "close_position",
                          lambda *a, **k: {"ok": True, "detail": ""}), \
             patch.object(aft, "current_environment",
                          lambda: types.SimpleNamespace(mode="demo")), \
             patch.object(aft, "fetch_other_venue_positions",
                          lambda env: (True, {"gate": []}, "")):
            ok, msg = aft.close_position_confirmed("BTC-USDT-SWAP", "long", 1.0, venue="gate")
        self.assertTrue(ok, msg)
        self.assertIn("verified flat", msg)

    def test_exec_ready_reads_patched_broken_venues(self):
        """读侧引用语义：坏所名单被 patch 后必须影响就绪判定。"""
        import scripts.ai_factor_trader as aft
        with patch.object(aft.venue_registry, "is_registered", lambda k: True), \
             patch.object(aft.venue_registry, "execution_open", lambda v, e: True), \
             patch.object(aft, "_BROKEN_VENUES", {"gate"}):
            self.assertFalse(aft.venue_execution_ready("gate", "demo"),
                             "坏所名单没被读到 ⇒ 密钥已死的所仍会被派单")
        with patch.object(aft.venue_registry, "is_registered", lambda k: True), \
             patch.object(aft.venue_registry, "execution_open", lambda v, e: True), \
             patch.object(aft, "_BROKEN_VENUES", set()):
            self.assertTrue(aft.venue_execution_ready("gate", "demo"))

    def test_judgment_actually_notices_a_change(self):
        base = "def f():\n    x = 1\n    return x\n"
        tampered = "def f():\n    x = 1\n    return x + 1\n"
        o = _body_dump(_get_func(ast.parse(base), "f"))
        self.assertNotEqual(o, _body_dump(_get_func(ast.parse(tampered), "f")),
                            "自检：看不见改动")
        self.assertEqual(o, _body_dump(_get_func(ast.parse(base), "f")), "自检：同文误报")


if __name__ == "__main__":
    unittest.main()
