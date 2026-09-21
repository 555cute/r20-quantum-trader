"""代码会读的环境键必须在 `env.example` 里可发现（第一百九十五刀）。

## 这一刀查的是什么

`env.example` 是操作者唯一的配置清单。若某键**代码会读**、模板里却完全没有，那么：

- 操作者**无法发现**这个开关存在（只能吃代码里的兜底默认值）；
- 对**安全开关**尤其致命：本刀实测漏掉的两个里就有
  `R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN`（预演档：1 = 只报不做、0 = 真实写单）——
  模板不提它，等于让人以为"开了巡检就在安全档"。

实测（本刀）：真正的环境访问点涉及 71 键，模板只记录 66 个 ⇒ **24 个键完全缺席**
（含上面那个安全档、防抖窗口、价格理智闸 `R20_MAX_PRICE_CROSS_PCT`/`FAR_PCT`、
台账同步总闸 `R20_LEDGER_SYNC_DISABLED`、LLM 超时/重试、路径覆盖等）。

## 判据

AST 收集**真正的环境访问点**（`os.environ[...]` / `os.environ.get` / `os.getenv` /
仓内 `_env_*`/`env_*` helper 的首个字符串字面量参数），要求每个键在 `env.example` 里
**至少被提及一次**（模板里既可为 `KEY=` 赋值行、也可为注释示例 —— 判据是"可发现"），
否则必须进 `ALLOWLIST` 并写明理由。

⚠️ 反面教训（同刀）：本门**不**校验模板里的数值是否等于代码兜底值 —— 那件事本刀靠人核对，
而且我第一版就写错了两个数字（`600` 写成 `60`、`10000` 写成 `1000`）。模板里已加注：
"数值必须与代码 `os.environ.get(..., <兜底>)` 逐字一致"。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("scripts", "r20_backend")

#: 允许"代码读、模板不提"的例外（附理由）。当前为空 —— 全部已补进模板。
ALLOWLIST: dict[str, str] = {}

#: 明显不是环境键的噪声（helper 自省、空串、纯数字等）
def _looks_like_env_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", key))


def _is_env_accessor(call: ast.Call) -> bool:
    name = ast.unparse(call.func)
    if name in ("os.getenv", "getenv", "os.environ.get", "environ.get"):
        return True
    if isinstance(call.func, ast.Attribute) and call.func.attr == "get" \
            and ast.unparse(call.func.value).endswith("environ"):
        return True
    short = name.split(".")[-1]
    return short.startswith("_env") or short.startswith("env_") or short.endswith("_env")


def consulted_env_keys(source: str) -> set:
    """源码里真正被读的环境键（字符串字面量形式）。"""
    out = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_env_accessor(node):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and _looks_like_env_key(arg.value):
                    out.add(arg.value)
                    break
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("environ"):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str) \
                    and _looks_like_env_key(sl.value):
                out.add(sl.value)
    return out


def all_consulted_keys() -> dict:
    found = {}
    for root in SCAN_DIRS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            for key in consulted_env_keys(path.read_text(encoding="utf-8")):
                found.setdefault(key, rel)
    return found


class EnvKeysAreDocumentedTest(unittest.TestCase):
    def test_every_consulted_key_is_discoverable_in_the_template(self):
        template = (ROOT / "env.example").read_text(encoding="utf-8")
        missing = []
        for key, rel in sorted(all_consulted_keys().items()):
            if key in template or key in ALLOWLIST:
                continue
            missing.append(f"{key}（首个读点 {rel}）")
        self.assertEqual(missing, [], "这些键代码会读、但 env.example 里发现不了"
                                      "（操作者只能吃兜底默认值）：\n  " + "\n  ".join(missing))

    def test_scan_is_not_vacuous(self):
        keys = all_consulted_keys()
        self.assertGreaterEqual(len(keys), 40, f"只扫到 {len(keys)} 个环境键 ⇒ 门与实现脱节")
        template = (ROOT / "env.example").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(re.findall(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", template, re.M)), 60,
                                "模板赋值行太少 ⇒ 可能读错了文件")
        for must in ("R20_OKX_ENV", "R20_VENUE_PROTECTION_WATCHDOG",
                     "R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN", "R20_MAX_PRICE_CROSS_PCT"):
            self.assertIn(must, template, f"安全相关键 {must} 必须出现在模板里")

    def test_allowlist_entries_have_reasons(self):
        for key, reason in ALLOWLIST.items():
            self.assertTrue(str(reason).strip(), f"{key} 的放行理由不能为空")

    def test_teeth_on_an_undocumented_key(self):
        src = 'import os\nX = os.environ.get("R20_BRAND_NEW_KNOB", "0")\n'
        self.assertEqual(consulted_env_keys(src), {"R20_BRAND_NEW_KNOB"},
                         "连合成样本都扫不到 ⇒ 门没有牙齿")
        # 非字面量（变量键）不该被误当成键
        self.assertEqual(consulted_env_keys('import os\nK="X"\nY=os.environ.get(K)\n'), set())

    def test_safety_switches_document_their_safe_tier(self):
        """安全开关必须在模板里**讲清档位**（只说"有这个键"不够）。"""
        template = (ROOT / "env.example").read_text(encoding="utf-8")
        for needle in ("R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN",
                       "只报", "绝不下单"):
            self.assertIn(needle, template, f"模板缺少 {needle!r} ⇒ 档位语义没写清")


if __name__ == "__main__":
    unittest.main()
