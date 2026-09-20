r"""cloud_protection 抽取对拍门（结构优化阶段 4·B3 第八十五刀）。

`amend_venue_stop_loss`(60) / `_live_oco_coverage`(18) /
`ensure_cloud_position_protection`(31) / `sync_cloud_algo_stop`(24) 从
`scripts/ai_factor_trader.py` **纯搬家**到 `scripts/trader/cloud_protection.py`。

本门除常规三件（逐字 / 壳形状 / ±自检）外，钉一条**跨函数注入**：
`ensure_cloud_position_protection` 用的覆盖率函数由门面注入 ⇒
`patch.object(aft, "_live_oco_coverage", …)` 必须能改变它的行为
（若子包偷用自己模块内的实现，这条 patch 面会静默失效）。
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

PRE = "447ad19"  # 本刀动工前最后提交（第八十四刀收口）
FNS = ("amend_venue_stop_loss", "_live_oco_coverage",
       "ensure_cloud_position_protection", "sync_cloud_algo_stop")
INJ = {"amend_venue_stop_loss": (),
       "_live_oco_coverage": ("_float_or_zero",),
       "ensure_cloud_position_protection": ("okx_rest", "_live_oco_coverage"),
       "sync_cloud_algo_stop": ("okx_rest",)}
MOVED = ROOT / "scripts" / "trader" / "cloud_protection.py"

# ---------------------------------------------------------------------------
# 搬走之后的**文档化差异**（aa6d4e0「优化分批止盈执行与多所挂单展示」）
#
# `amend_venue_stop_loss` 枚举现存 SL 触发单时，旧实现只读 `row["order"]["text"]`
# / `row["text"]` / `row["type"]`。Gate 的 `price_orders` 把标签放在
# `row["initial"]["text"]`（Gate 原生结构），于是 `t-r20sl*` 标签**扫不到** ⇒
# 旧 SL 单不被识别、棘轮时既不 amend 也不撤 ⇒ 云端止损单逐轮堆积
# （宽松旧单可能先于新单触发，正是本函数注释里点名要防的那个场景）。
# 修复 = 多读一层 `initial.text`。
#
# 按本仓既有先例（`test_brain_package_extraction.py` 第 137 刀白名单）放行，
# 范围极窄：**一条块替换**（净增 1 行 + 原单行赋值展开为 3 行），
# 断言"恰好出现一次"；任何其他差异仍会红。
# 另有 `test_initial_text_layer_is_actually_read` 正向钉住这一层真的被读到。
# ---------------------------------------------------------------------------
_DELTA_BLOCKS = (
    (
        '            _init = row.get("initial")\n'
        '            text = (str(_order.get("text") or "") if isinstance(_order, dict) else "") \\\n'
        '                + (str(_init.get("text") or "") if isinstance(_init, dict) else "") \\\n'
        '                + str(row.get("text") or "") + str(row.get("type") or "")\n',
        '            text = (str(_order.get("text") or "") if isinstance(_order, dict) else "") \\\n'
        '                + str(row.get("text") or "") + str(row.get("type") or "")\n',
    ),
)


def _normalised_moved_tree() -> ast.Module:
    """把 aa6d4e0 的文档化差异**还原**成搬运时的样子，再逐字比对。"""
    src = MOVED.read_text(encoding="utf-8")
    for new_block, old_block in _DELTA_BLOCKS:
        assert src.count(new_block) == 1, f"放行块没找到或重复（白名单过期）：{new_block[:60]!r}"
        src = src.replace(new_block, old_block)
    return ast.parse(src)


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


class CloudProtectionVerbatimTest(unittest.TestCase):
    def test_moved_bodies_match_pre_extraction_verbatim(self):
        old = _old_tree()
        new = _normalised_moved_tree()
        for fn in FNS:
            with self.subTest(fn=fn):
                o, n = _get_func(old, fn), _get_func(new, fn)
                self.assertEqual([a.arg for a in o.args.args],
                                 [a.arg for a in n.args.args])
                self.assertEqual([a.arg for a in n.args.kwonlyargs], list(INJ[fn]),
                                 f"{fn} 注入项不是声明的 kw-only 集合")
                self.assertEqual(_body_dump(o), _body_dump(n),
                                 f"{fn} 与抽取前**不再是同一实现**")

    def test_initial_text_layer_is_actually_read(self):
        """正向断言：Gate 的 `initial.text` 层必须被读到（aa6d4e0）。

        这一层是「`t-r20sl*` 标签能不能被扫到」的唯一通路；一旦被删掉，
        旧 SL 单不被识别 ⇒ 棘轮既不 amend 也不撤 ⇒ 云端止损单逐轮堆积。
        """
        moved = MOVED.read_text(encoding="utf-8")
        self.assertIn('_init = row.get("initial")', moved,
                      "Gate initial 层读取丢失 ⇒ 白名单块在掩盖删除")
        self.assertIn('if isinstance(_init, dict)', moved,
                      "initial 层的类型守卫丢失（非 dict 时会 AttributeError）")

        import scripts.trader.cloud_protection as cp
        listed = []
        cancelled = []
        ad = types.SimpleNamespace(
            # Gate 形状：标签只在 initial.text 里，order/text/type 都为空
            list_protective_orders=lambda sym: [
                {"id": "sl-old", "initial": {"text": "t-r20sl-btc"}}],
            cancel_price_order=lambda oid: cancelled.append(oid),
            attach_protective_orders=lambda *a, **k: {"sl": "sl-new"})
        ok, note = cp.amend_venue_stop_loss(ad, "BTC_USDT", "long", 78000.0, 3.0)
        self.assertTrue(ok, note)
        self.assertIn("sl-old", cancelled,
                      "initial.text 里的 t-r20sl 标签没被认出 ⇒ 旧 SL 单不会被清理")

    def test_delta_whitelist_actually_notices_undocumented_edits(self):
        """自检：白名单之外的一行改动必须被 `_body_dump` 看见。"""
        base = 'def f():\n    text = str(row.get("text") or "")\n    return text\n'
        tampered = base.replace("return text", "return text or 'x'")
        o = _body_dump(_get_func(ast.parse(base), "f"))
        self.assertNotEqual(o, _body_dump(_get_func(ast.parse(tampered), "f")),
                            "自检：未登记的行改动看不见")
        self.assertEqual(o, _body_dump(_get_func(ast.parse(base), "f")),
                         "自检：同文误报")

    def test_shells_are_def_with_lazy_same_name_injection(self):
        tree = ast.parse((ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8"))
        facade = set(dir(__import__("scripts.ai_factor_trader", fromlist=["x"])))
        for fn in FNS:
            with self.subTest(fn=fn):
                dumped = ast.unparse(_get_func(tree, fn))
                self.assertIn("_cloud_protection_", dumped, "壳没转调子包")
                for g in INJ[fn]:
                    self.assertIn(f"{g}={g}", dumped, f"壳缺同名注入 {g}")
                    self.assertIn(g, facade, f"{g} 不是门面全局 ⇒ 壳传参必 NameError")

    def test_sync_uses_patched_facade_okx_rest(self):
        import scripts.ai_factor_trader as aft
        amended = []
        okx = types.SimpleNamespace(
            pending_algo_orders=lambda inst: [
                {"state": "live", "posSide": "long", "slTriggerPx": "2400", "algoId": "a1"}],
            amend_algo_sl=lambda algo_id, px, **kw: amended.append((algo_id, px)))
        with patch.object(aft, "okx_rest", okx):
            self.assertTrue(aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2500))
        self.assertEqual(amended, [("a1", 2500)],
                         "patch 门面 okx_rest 没传到子包 ⇒ 注入断了")

    def test_ensure_uses_patched_facade_coverage_hook(self):
        """跨函数注入：门面 `_live_oco_coverage` 被打 patch 后必须影响 ensure。"""
        import scripts.ai_factor_trader as aft
        placed = []
        okx = types.SimpleNamespace(
            pending_algo_orders=lambda inst: [{"dummy": 1}],
            place_algo_oco=lambda *a, **k: placed.append((a, k)))
        with patch.object(aft, "okx_rest", okx), \
             patch.object(aft, "_live_oco_coverage", lambda rows, side: 5.0):
            ok, msg = aft.ensure_cloud_position_protection(
                "SOL-USDT-SWAP", "long", 5, 106, 101)
        self.assertTrue(ok, msg)
        self.assertIn("verified", msg)
        self.assertEqual(placed, [],
                         "已满覆盖却仍补单 ⇒ ensure 没用门面注入的覆盖率函数")

    def test_judgment_actually_notices_a_change(self):
        base = "def f():\n    x = 1\n    return x\n"
        tampered = "def f():\n    x = 1\n    return x + 1\n"
        o = _body_dump(_get_func(ast.parse(base), "f"))
        self.assertNotEqual(o, _body_dump(_get_func(ast.parse(tampered), "f")),
                            "自检：看不见改动")
        self.assertEqual(o, _body_dump(_get_func(ast.parse(base), "f")), "自检：同文误报")


if __name__ == "__main__":
    unittest.main()
