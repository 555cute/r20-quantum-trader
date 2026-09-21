r"""order_submit 抽取对拍门（结构优化阶段 4·B3 第八十八刀）。

`submit_protected_limit_order`（198 行，唯一真正**落单**的函数）从
`scripts/ai_factor_trader.py` **纯搬家**到 `scripts/trader/order_submit.py`。

本门除常规三件外，加一条**端到端行为例**：经门面壳提交一张穿价限价单，
必须在**入场价穿价幻觉闸**被拒（既证明 11 项注入活在调用期解析，
又证明审计④ 的价格理智闸随搬家一字未损）。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRE = "b6dbb7a"  # 本刀动工前最后提交（第八十七刀收口）
FN = "submit_protected_limit_order"
# 文档化差异：抽取之后**有意**改动的片段（旧文本 → 新文本）。
# 与 `tests/extraction/test_trader_cycle_stages_extraction.py` 同一手法：
# 两侧都经 `ast.parse` ⇒ 仍受结构约束，只是允许**逐条可核对**的文本替换。
# ⚠️ 每条的旧文本必须在基线里**唯一**、且必须写明**为什么**（改动理由），上限 5 条。
BODY_DELTAS: list = [
    # ---- 第二百二十九刀：静默吞异常 ⇒ 留痕（行为不变：仍按当前值提交、仍不阻断）----
    # 原实现 `except Exception: pass` 会让"沙盒报价重算出 bug"这件事**毫无痕迹**地过去，
    # 与本仓"失败必须留痕"的纪律不符。修它必须动这段被逐字冻结的主路径，故在此登记。
    # 锚点唯一性由本门断言（当前 1 次）。
    ("except Exception:\n        pass",
     "except Exception as _rsc_exc:\n"
     "        print(f'[demo rescale] warn {inst_id} 沙盒报价重算失败，按当前值提交: {_rsc_exc}')"),
]

INJ = ("confirm_signal_reservation", "record_open_intent", "release_signal_reservation",
       "route_and_reserve_signal", "MAX_LEVERAGE", "MIN_LEVERAGE", "canonical_base",
       "current_environment", "fetch_ticker", "okx_rest", "venue_registry")


def _base_text() -> str:
    r = subprocess.run(["git", "show", f"{PRE}:scripts/ai_factor_trader.py"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"基线取不到：{r.stderr[:200]}"
    return r.stdout


def _get_func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} 不在顶层")


def _body_dump(fn: ast.FunctionDef) -> str:
    return ast.dump(ast.Module(body=fn.body, type_ignores=[]), include_attributes=False)


def _body_src(fn: ast.FunctionDef) -> str:
    """段体的源码形态（`ast.unparse`）：只有源码形态才做得了文档化差异的文本替换。

    两侧都来自 `ast.parse` ⇒ 仍受结构约束，不受空白/换行影响。
    """
    return ast.unparse(ast.Module(body=fn.body, type_ignores=[]))


class OrderSubmitVerbatimTest(unittest.TestCase):
    def test_moved_body_matches_pre_extraction_verbatim(self):
        o = _get_func(ast.parse(_base_text()), FN)
        n = _get_func(ast.parse(
            (ROOT / "scripts/trader/order_submit.py").read_text(encoding="utf-8")), FN)
        self.assertEqual([a.arg for a in o.args.args], [a.arg for a in n.args.args])
        self.assertEqual([a.arg for a in n.args.kwonlyargs], list(INJ))
        base_src, got_src = _body_src(o), _body_src(n)
        for _old, _new in BODY_DELTAS:
            self.assertEqual(base_src.count(_old), 1,
                             f"文档化差异的锚点在基线里必须**唯一**（当前 "
                             f"{base_src.count(_old)} 次）—— 否则替换范围不可核对")
            base_src = base_src.replace(_old, _new)
        self.assertLessEqual(len(BODY_DELTAS), 5,
                             "文档化差异过多 ⇒ 这已经不是'搬家'了，请重新评估抽取边界")
        self.assertEqual(base_src, got_src,
                         "下单主路径与抽取前**不再是同一实现**（超出文档化差异）")

    def test_delta_mechanism_is_surgical(self):
        """自检：① 差异表能把"被批准的改动"放过去；② **未登记**的改动照样红。"""
        o = _get_func(ast.parse("def f():\n    try:\n        x = 1\n    except Exception:\n        pass\n"), "f")
        approved = _get_func(ast.parse("def f():\n    try:\n        x = 1\n    except Exception as e:\n        print(e)\n"), "f")
        sneaky = _get_func(ast.parse("def f():\n    try:\n        x = 2\n    except Exception as e:\n        print(e)\n"), "f")
        base_src = _body_src(o)
        deltas = [("except Exception:\n    pass", "except Exception as e:\n    print(e)")]
        for old, new in deltas:
            self.assertEqual(base_src.count(old), 1, "自检用锚点应当唯一")
            base_src = base_src.replace(old, new)
        self.assertEqual(base_src, _body_src(approved), "差异表应当放过已批准的改动")
        self.assertNotEqual(base_src, _body_src(sneaky), "未登记的改动必须照样红")

    def test_shell_signature_and_injections(self):
        o = _get_func(ast.parse(_base_text()), FN)
        tree = ast.parse((ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8"))
        n = _get_func(tree, FN)
        self.assertEqual([a.arg for a in n.args.args], [a.arg for a in o.args.args],
                         "壳签名与基线不一致（手写事故）")
        self.assertEqual(len(n.args.defaults), len(o.args.defaults))
        self.assertFalse(n.args.kwonlyargs, "壳不应有 kw-only 注入")
        self.assertIn("_order_submit_protected", ast.unparse(n))
        facade = set(dir(__import__("scripts.ai_factor_trader", fromlist=["x"])))
        for g in INJ:
            self.assertIn(g, facade, f"{g} 不是门面全局 ⇒ 壳传参必 NameError")

    def _stub_env(self):
        return types.SimpleNamespace(mode="demo", simulated=False)

    def test_crossed_limit_is_rejected_through_facade(self):
        """穿价幻觉闸必须活着：BUY 105 挂在现价 100 → 拒单且原因含「穿价幻觉」。"""
        import scripts.ai_factor_trader as aft
        bad_registry = types.SimpleNamespace(
            native_symbol_pure=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no meta")))
        with patch.dict(os.environ, {"R20_MAX_PRICE_CROSS_PCT": "0.005"}, clear=False), \
             patch.object(aft, "current_environment", self._stub_env), \
             patch.object(aft, "fetch_ticker", lambda inst: {"last": 100.0}), \
             patch.object(aft, "venue_registry", bad_registry):
            ok, msg = aft.submit_protected_limit_order(
                # 几何合法（R:R=(125-105)/(105-100)=4.0 过闸）但穿价 5% > 0.5%
                "BTC-USDT-SWAP", "buy", "long", 1.0, 105.0, 125.0, 100.0)
        self.assertFalse(ok, f"穿价单必须被拒，实际: {ok} / {msg}")
        self.assertIn("穿价幻觉", msg)

    def test_uncrossed_limit_is_not_rejected_by_the_cross_guard(self):
        """反向控制：不穿价的单**不得**带穿价拒因（防闸门过宽）。"""
        import scripts.ai_factor_trader as aft
        bad_registry = types.SimpleNamespace(
            native_symbol_pure=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no meta")))
        okx = types.SimpleNamespace(
            place_order=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("not placed")),
            pending_orders=lambda *a, **k: [], cancel_order=lambda *a, **k: None)
        with patch.dict(os.environ, {"R20_MAX_PRICE_CROSS_PCT": "0.005"}, clear=False), \
             patch.object(aft, "current_environment", self._stub_env), \
             patch.object(aft, "fetch_ticker", lambda inst: {"last": 100.0}), \
             patch.object(aft, "venue_registry", bad_registry), \
             patch.object(aft, "okx_rest", okx):
            ok, msg = aft.submit_protected_limit_order(
                # 几何合法且不穿价（0.2% < 0.5%）：不得带穿价拒因
                "BTC-USDT-SWAP", "buy", "long", 1.0, 100.2, 120.0, 95.0)
        self.assertNotIn("穿价幻觉", msg, "不穿价的单被误判穿价")

    def test_judgment_actually_notices_a_change(self):
        base = "def f():\n    x = 1\n    return x\n"
        tampered = "def f():\n    x = 1\n    return x + 1\n"
        o = _body_dump(_get_func(ast.parse(base), "f"))
        self.assertNotEqual(o, _body_dump(_get_func(ast.parse(tampered), "f")),
                            "自检：看不见改动")
        self.assertEqual(o, _body_dump(_get_func(ast.parse(base), "f")), "自检：同文误报")


if __name__ == "__main__":
    unittest.main()
