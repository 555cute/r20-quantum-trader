r"""全仓"未解析自由名"门（第一百一十九刀）。

## 为什么要有这条门

抽取子模块时如果**漏带模块级 import**，而该模块的取数/IO 又普遍包在
`except: pass` 里（本仓大量如此），失败会**完全静默**：函数照常返回，
值却是 0/空。真实事故：`scripts/brain/packages.py` 的 docstring 写着
"整段只读 `json` / `urllib` 两个标准库模块"，实际**只 import 了 typing** ⇒
`urllib.request.Request(...)` 抛 `NameError` 被静默吞掉 ⇒ 9 个标的现价全 0 ⇒
`data_quality: invalid` ⇒ 主脑 P0 数据有效性拦截、整轮禁止开仓。

这类缺陷**编译通过、导入通过、单测全绿**（该模块当时零测试覆盖），
只有"跑起来看值"才会暴露 ⇒ 必须由静态门兜底。

## 本门做什么

按**词法作用域**（模块级含嵌套块的绑定 + 函数参数 + 闭包外层）收集绑定名，
逐函数检查 `Load` 名是否都能解析；不能解析的即为疑似缺陷。

## 白名单（两处**刻意**的例外，逐名列出）

1. `scripts/brain/prompt.py`：`_resolve(name, lambda: NAME)` 是**有意**的接缝写法
   —— 惰性回退只在 `_g` 里没有该名时求值；`tests/llm/test_prompt_rendering_isolated.py`
   按 AST 抽取函数体隔离 exec，故必须容忍"名字只存在于门面"。
2. `r20_backend/app.py`：那些名字位于 `if False:` 块内，是
   `test_memory_routes_isolated` / `test_prompt_rendering_isolated` 的 **AST 锚点**，
   永不执行。

白名单按**精确名字集合**比对 ⇒ 这两个文件里**新增**别的未解析名仍会红。
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _free_names_scan import free_names  # noqa: E402

SCAN_ROOTS = ("scripts", "r20_backend", "r20_gateway", "plugins")   # 第 143 刀：dashboard/ 并入 r20_backend

ALLOWLIST = {
    "scripts/brain/prompt.py": {
        "AI_MEMORY_FILE", "AI_MEMORY_MD_FILE", "MAX_LEVERAGE", "MAX_MARGIN_EQUITY_RATIO",
        "MAX_SCALE_IN_COUNT", "MIN_LEVERAGE", "MIN_SCALE_IN_CONFIDENCE",
        "NEWS_SENTIMENT_FILE", "__version__", "_sl_atr_mult_for", "_xvenue_prompt_line",
    },
    "r20_backend/app.py": {"apply_module_layout", "compile_modules", "prof", "sys_mods", "test_market"},
}


class ModuleFreeNamesTest(unittest.TestCase):
    def test_no_unresolved_free_names(self):
        problems = []
        scanned = 0
        for root in SCAN_ROOTS:
            for path in sorted((ROOT / root).rglob("*.py")):
                if "__pycache__" in str(path):
                    continue
                scanned += 1
                rel = str(path.relative_to(ROOT))
                try:
                    missing = set(free_names(path))
                except SyntaxError as exc:      # 语法错另有门管，这里只报不炸
                    problems.append(f"{rel}: 语法错 {exc}")
                    continue
                allowed = ALLOWLIST.get(rel, set())
                unexpected = missing - allowed
                if unexpected:
                    problems.append(f"{rel}: 未解析自由名 {sorted(unexpected)}")
        self.assertGreater(scanned, 150, "扫描面异常偏小，检查 SCAN_ROOTS")
        self.assertEqual(problems, [], "存在未解析自由名（抽取时漏带 import，运行期会静默失败）")

    def test_allowlist_entries_still_needed(self):
        """白名单不许残留：文件里那些名字若已被修好/删除，就必须从白名单移除。"""
        stale = []
        for rel, names in ALLOWLIST.items():
            missing = set(free_names(ROOT / rel))
            if not (names & missing):
                stale.append(f"{rel}（白名单里的名字已不再缺失）")
        self.assertEqual(stale, [], "白名单已过期，请清理")

    def test_checker_actually_catches_the_real_defect(self):
        """自检：把修复前的 packages.py 片段喂给检查器，必须抓到 json / urllib。"""
        snippet = (
            "from typing import Any, Dict\n\n\n"
            "def fetch(item):\n"
            "    try:\n"
            "        req = urllib.request.Request('https://x/' + item)\n"
            "        return json.loads(req.full_url)\n"
            "    except Exception:\n"
            "        return 0\n"
        )
        tmp = ROOT / "tests" / "_tmp_freenames_snippet.py"
        tmp.write_text(snippet, encoding="utf-8")
        try:
            missing = set(free_names(tmp))
        finally:
            tmp.unlink(missing_ok=True)
        self.assertIn("urllib", missing)
        self.assertIn("json", missing)


if __name__ == "__main__":
    unittest.main()
