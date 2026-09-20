r"""circuit_guard 抽取对拍门（结构优化阶段 4·B3 第八十一刀）。

`check_black_swan_sentinel` / `is_circuit_breaker_active` 从
`scripts/ai_factor_trader.py` **纯搬家**到 `scripts/trader/circuit_guard.py`。
本门钉三件事：

1. **逐字相同**（AST 级，允许两类已声明的机械差异）：
   - 注入常量改名：`NEWS_SENTIMENT_FILE → news_sentiment_file` 等 3 个；
   - 门面内局部调用 `check_black_swan_sentinel()` 变成注入版调用
     （参数清空归一后对拍）。
2. **门面壳接线真实**：壳必须是 `def`（§58 计数锚惯例），且**调用期**
   解析全局 —— patch 门面模块的常量/函数必须能改变经壳调用的行为
   （既有专测 `test_black_swan_sentinel_revival` 的 patch 面保真证明）。
3. **基线可取**：抽取前 commit 的原文能用 `git show` 取到（对拍门通用前提）。
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: 抽取前的基线（第七十九～八十刀收口提交，本刀动工前的最后状态）
PRE = "aa5f555"
RENAMES = {
    "NEWS_SENTIMENT_FILE": "news_sentiment_file",
    "CIRCUIT_BREAKER_FILE": "circuit_breaker_file",
    "LEDGER_JSON_FILE": "ledger_json_file",
}


def _src(commit: str, rel: str) -> str:
    r = subprocess.run(["git", "show", f"{commit}:{rel}"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"基线取不到 {commit}:{rel}：{r.stderr[:200]}"
    return r.stdout


def _get_func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"函数 {name} 不在顶层")


def _normalize(node: ast.AST) -> str:
    """注入改名 + 清空 sentinel 局部调用的参数后 dump（对两边同规则）。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in RENAMES:
            sub.id = RENAMES[sub.id]
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                and sub.func.id in ("check_black_swan_sentinel", "sentinel_check")):
            # 基线版无参直调门面全局；新版经 `sentinel_check()` 注入
            # （或子包默认路径带注入 kw 直调）。两侧归一为同一形状。
            sub.args = []
            sub.keywords = []
            sub.func.id = "__sentinel__"
    return ast.dump(node, include_attributes=False)


#: ⚠️ **文档化差异**（第一百四十四刀新增本表）：本门要求搬运后函数体逐字，
#: 表外任何改动照旧翻红；表内差异在比较前先把"新文本"还原成"旧文本"。
#:
#: 本刀唯一一条：`is_circuit_breaker_active` 里把"台账同步旁车**不可判定**"
#: （旁车损坏/过旧）**如实披露**出来。旧 docstring 声称这类场景由 ledger 的
#: file_health STALE 通道兜底，但两个调用方都没有该检查（全仓 grep 只命中那句注释）
#: ⇒ 补偿不存在。用户拍板 **fail-closed**：不可判定 ⇒ 禁开仓（可见 + 有行为）。
DELTA_REWRITES = (
    ("""            from r20_backend.execution.circuit_breaker import (
                _ledger_sync_sidecar_state as _sidecar_state)
            _failed_venues, _sidecar_unknown = _sidecar_state()
            if _sidecar_unknown:
                # 与模块版同源（第一百四十四刀，用户拍板 fail-closed）：不可判定 ⇒ 禁开仓
                return True, (f"台账同步状态不可判定（{_sidecar_unknown}）⇒ "
                              "当日亏损求和不可判全，安全暂停开仓")
""",
     """            _failed_venues = _ledger_sync_failed_venues()
"""),
)


class CircuitGuardVerbatimTest(unittest.TestCase):
    def test_moved_bodies_match_pre_extraction_except_injections(self):
        old = ast.parse(_src(PRE, "scripts/ai_factor_trader.py"))
        mod_src = (ROOT / "scripts/trader/circuit_guard.py").read_text(encoding="utf-8")
        for _new_tok, _old_tok in DELTA_REWRITES:
            self.assertEqual(mod_src.count(_new_tok), 1,
                             f"文档化差异锚点没找到或重复：{_new_tok[:60]!r}")
            mod_src = mod_src.replace(_new_tok, _old_tok)
        new = ast.parse(mod_src)
        for fn in ("check_black_swan_sentinel", "is_circuit_breaker_active"):
            with self.subTest(fn=fn):
                # 原有**位置参数**必须原样（新函数只允许追加 kw-only 注入参数）
                o, n = _get_func(old, fn), _get_func(new, fn)
                self.assertEqual([a.arg for a in o.args.args],
                                 [a.arg for a in n.args.args],
                                 f"{fn} 原有位置参数被改动")
                # 函数体逐字（归一后）
                ob = ast.Module(body=o.body, type_ignores=[])
                nb = ast.Module(body=n.body, type_ignores=[])
                self.assertEqual(_normalize(ob), _normalize(nb),
                                 f"{fn} 与抽取前**不再是同一实现**")

    def test_facade_shells_are_def_with_lazy_injection(self):
        """门面壳：`def` 形状（计数锚惯例）+ 注入的全局名**全部仍是门面全局**。"""
        src = (ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for fn in ("check_black_swan_sentinel", "is_circuit_breaker_active"):
            with self.subTest(fn=fn):
                shell = _get_func(tree, fn)
                dumped = ast.unparse(shell)
                self.assertIn("_circuit_guard_", dumped, "壳没有转调子包实现")
                # 壳必须在调用期引用这些**门面全局**（import 期快照=红）
                if fn == "check_black_swan_sentinel":
                    want = ("fetch_candles_direct", "NEWS_SENTIMENT_FILE")
                else:
                    want = ("check_black_swan_sentinel",  # ← patch 面经由 sentinel 注入
                            "CIRCUIT_BREAKER_FILE", "LEDGER_JSON_FILE",
                            "current_environment", "effective_daily_loss_limit")
                for g in want:
                    self.assertIn(g, dumped, f"壳未调用期注入门面全局 {g}")

    def test_shell_wiring_reacts_to_facade_patch(self):
        """行为证明：patch **门面**模块的常量，经门面壳调用必须改变行为
        —— 若壳在 import 期快照了值（`name = impl` 式别名），本例会红。"""
        from unittest.mock import patch
        import scripts.ai_factor_trader as aft

        # 造一个"新闻极端利空"的临时情绪文件
        import json
        import tempfile
        # 平盘行情（[ts,o,h,l,c]，跌幅远小于 3%）+ 极端利空情绪文件：
        # 必须一路走到情绪分支并在那里触发 —— 证明 patch 传到了子包。
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "news.json"
            f.write_text(json.dumps({"overall_score": 3.3}), encoding="utf-8")
            with patch.object(aft, "NEWS_SENTIMENT_FILE", str(f)), \
                 patch.object(aft, "fetch_candles_direct",
                              lambda *a, **k: [["1", "100.0", "100.5", "99.8", "100.2"],
                                               ["0", "99.8", "100.1", "99.5", "100.0"]]):
                active, reason = aft.check_black_swan_sentinel()
        self.assertTrue(active, "patch 门面常量没影响经壳调用 ⇒ 壳在快照值，注入断了")
        self.assertIn("情绪指数", reason)

    def test_judgment_actually_notices_a_change(self):
        """⚠️ 自检：归一化对拍必须**能**发现真实改动，且**不误报**纯改名。"""
        base = "def f():\n    x = NEWS_SENTIMENT_FILE\n    return x\n"
        tampered = "def f():\n    x = news_sentiment_file\n    return x + 1\n"
        renamed = "def f():\n    x = news_sentiment_file\n    return x\n"

        def norm(text: str) -> str:
            tree = ast.parse(text)
            return _normalize(ast.Module(body=_get_func(tree, "f").body, type_ignores=[]))

        # 改名后 +1：必须被识别为**不同**
        self.assertNotEqual(norm(base), norm(tampered),
                            "自检失败：对拍判据看不见 +1 的改动")
        # 纯改名：必须视为**相同**（这正是 RENAMES 的用途）
        self.assertEqual(norm(base), norm(renamed),
                         "自检失败：纯改名被误报为行为变化")


if __name__ == "__main__":
    unittest.main()
