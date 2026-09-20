r"""ledger_writer 抽取对拍门（结构优化阶段 4·B3 第八十三刀）。

`record_trade` / `record_open_intent` 从 `scripts/ai_factor_trader.py`
**纯搬家**到 `scripts/trader/ledger_writer.py`。同名注入 ⇒ **函数体零例外
逐字**（AST dump 必须全等，不需要任何归一规则）。壳调用期注入 + 行为接线
+ ±自检与前几刀门同构。
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRE = "0c521ea"  # 本刀动工前最后提交（第八十二刀收口）
FNS = ("record_trade", "record_open_intent")

#: ⚠️ **文档化差异**（第一百三十五刀新增本表）：本门默认要求搬运后**零例外逐字**；
#: 某函数若确需**有意的行为修复**，必须在此登记"新文本 → 旧文本"，于是
#: "新体还原差异 == 旧体"，表外任何改动照旧翻红。
#:
#: 本刀唯一一条：`record_open_intent` 的落盘从**非原子直写**
#: （`open(..., "w")`）改为**原子替换**（`_atomic_write_json`）。
#: 旧写法写崩/断电会留下 **0 字节或半截 JSON**，读取侧据此把"读不到"
#: 当成"没有意图"⇒ 逐笔按孤儿撤单（第一百三十四刀已让读取侧 fail-closed，
#: 本刀从源头消灭该状态：失败时旧文件原样保全）。
DELTA_REWRITES = {
    "record_open_intent": [
        ("_atomic_write_json(OPEN_INTENT_FILE, intents[-200:])",
         "with open(OPEN_INTENT_FILE, \"w\", encoding=\"utf-8\") as f:\n"
         "            json.dump(intents[-200:], f, ensure_ascii=False, indent=2)"),
    ],
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
    return ast.dump(ast.Module(body=fn.body, type_ignores=[]),
                    include_attributes=False)


class LedgerWriterVerbatimTest(unittest.TestCase):
    def test_moved_bodies_match_pre_extraction_verbatim(self):
        old = _old_tree()
        new = ast.parse((ROOT / "scripts/trader/ledger_writer.py").read_text(encoding="utf-8"))
        for fn in FNS:
            with self.subTest(fn=fn):
                o, n = _get_func(old, fn), _get_func(new, fn)
                self.assertEqual([a.arg for a in o.args.args],
                                 [a.arg for a in n.args.args])
                # 同名注入：kw-only 参数名必须就是门面里真实存在的全局名
                # （常量是大写；`_atomic_write_json`/`__version__` 等私有名
                #  同样要求同名 —— 判据是"在门面命名空间可解析"，不是大写）
                facade_globals = {x for x in dir(__import__('scripts.ai_factor_trader',
                                                            fromlist=['x']))}
                for a in n.args.kwonlyargs:
                    self.assertIn(a.arg, facade_globals,
                                  f"{fn} 注入名 {a.arg} 不是门面全局 ⇒ 壳传参必 NameError")
                # **零例外**逐字（表内差异先还原；锚点必须唯一，防白名单过期）
                new_src = (ROOT / "scripts/trader/ledger_writer.py").read_text(encoding="utf-8")
                for _new_tok, _old_tok in DELTA_REWRITES.get(fn, []):
                    self.assertEqual(new_src.count(_new_tok), 1,
                                     f"{fn} 的文档化差异锚点没找到或重复：{_new_tok[:60]!r}")
                    new_src = new_src.replace(_new_tok, _old_tok)
                n = _get_func(ast.parse(new_src), fn)
                self.assertEqual(_body_dump(o), _body_dump(n),
                                 f"{fn} 与抽取前**不再是同一实现**（超出文档化差异）")

    def test_shells_are_def_with_lazy_same_name_injection(self):
        tree = ast.parse((ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8"))
        want = {"record_trade": ("LEDGER_JSON_FILE", "_atomic_write_json",
                                 "record_trade_sqlite", "current_environment", "__version__"),
                "record_open_intent": ("OPEN_INTENT_FILE", "OPEN_INTENT_TTL_MS",
                                 "_atomic_write_json")}
        for fn, names in want.items():
            with self.subTest(fn=fn):
                dumped = ast.unparse(_get_func(tree, fn))
                self.assertIn("_ledger_writer_", dumped, "壳没转调子包")
                for g in names:
                    self.assertIn(f"{g}={g}", dumped, f"壳缺同名注入 {g}")

    def test_intent_shell_wiring_reacts_to_facade_patch(self):
        """行为：patch 门面 OPEN_INTENT_FILE，经壳写入必须落 patch 后的文件。"""
        import json
        import tempfile
        from unittest.mock import patch
        import scripts.ai_factor_trader as aft
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "intents.json"
            f.write_text(json.dumps([{"instId": "OLD", "side": "buy",
                                      "ts": 1},   # 远古 → TTL 清理掉
                                     ]), encoding="utf-8")
            with patch.object(aft, "OPEN_INTENT_FILE", str(f)), \
                 patch.object(aft, "OPEN_INTENT_TTL_MS", 21600000):
                aft.record_open_intent("BTC-USDT-SWAP", "buy")
            rows = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual([r["instId"] for r in rows], ["BTC-USDT-SWAP"],
                         "patch 没传到子包（壳在 import 期快照）或清理语义丢失")

    def test_intent_write_is_atomic_so_a_crash_cannot_truncate(self):
        """落盘是**原子替换**：写崩时旧文件原样保全（第一百三十五刀）。

        旧写法 `open(OPEN_INTENT_FILE, "w")` **先截断再写** ⇒ 中途失败会留下
        0 字节或半截 JSON；读取侧（挂单对账 / 存量挂单回收）据此把"读不到"当成
        "没有意图"⇒ **逐笔按孤儿撤单**（第一百三十四刀已让读取侧 fail-closed，
        本刀从源头消灭该状态）。本门把"写崩 = 旧文件原样"钉死。
        """
        import io
        import json
        import tempfile
        from contextlib import redirect_stdout
        from unittest.mock import patch

        import scripts.ai_factor_trader as aft
        original = [{"instId": "BTC-USDT-SWAP", "side": "buy", "ts": 4102444800000}]
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "intents.json"
            f.write_text(json.dumps(original), encoding="utf-8")
            before = f.read_text(encoding="utf-8")
            with patch.object(aft, "OPEN_INTENT_FILE", str(f)), \
                 patch.object(aft, "OPEN_INTENT_TTL_MS", 21600000), \
                 patch("os.replace", side_effect=OSError("模拟磁盘满/断电")), \
                 redirect_stdout(io.StringIO()):
                aft.record_open_intent("ETH-USDT-SWAP", "sell")
            after = f.read_text(encoding="utf-8")
            leftovers = sorted(p.name for p in Path(td).iterdir()
                               if p.name != "intents.json")
        self.assertEqual(after, before,
                         "写崩必须**保全旧文件**（非原子直写会先截断 ⇒ 0 字节/半截 JSON）")
        self.assertEqual(leftovers, [], "失败路径必须清掉临时文件（不残留 .tmp）")
        self.assertEqual(json.loads(after), original)

    def test_judgment_actually_notices_a_change(self):
        base = "def f():\n    x = LEDGER_JSON_FILE\n    return x\n"
        tampered = "def f():\n    x = LEDGER_JSON_FILE\n    return [x]\n"
        o = _body_dump(_get_func(ast.parse(base), "f"))
        n = _body_dump(_get_func(ast.parse(tampered), "f"))
        self.assertNotEqual(o, n, "自检：看不见改动")
        same = _body_dump(_get_func(ast.parse(base), "f"))
        self.assertEqual(o, same, "自检：同文误报")


if __name__ == "__main__":
    unittest.main()
