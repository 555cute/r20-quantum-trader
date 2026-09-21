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
#: **全部代码根**。⚠️ 第二百刀自查：第一版只扫 `scripts` + `r20_backend`，
#: 而本仓还有 `r20_gateway/`（网关：凭证库、发布器、任务存储）与 `plugins/` ——
#: 那两处的读点**完全隐形**，实测漏掉 `R20_GATEWAY_DB`、`R20_ALLOW_TEST_PUBLISH`、
#: `R20_JOB_RUNS_KEEP_DAYS` 三个键（模板里根本没有，门却是绿的 = **假绿**）。
#: 这条清单本身就是"判据范围"的一部分：新增代码根必须同步加进来。
SCAN_DIRS = ("scripts", "r20_backend", "r20_gateway", "plugins")

#: 允许"代码读、模板不提"的例外（附理由）。当前为空 —— 全部已补进模板。
ALLOWLIST: dict[str, str] = {}

#: 明显不是环境键的噪声（helper 自省、空串、纯数字等）
def _looks_like_env_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", key))


#: 形参/局部变量里"装着环境变量"的名字（本仓习惯把 env dict 传进路由与通知层）
ENV_DICT_RECEIVERS = {"env", "environment", "env_vars", "_env", "env_map"}


def _is_env_accessor(call: ast.Call) -> bool:
    """这次调用是不是在**读环境变量**。

    ⚠️ 第一百九十九刀自查出的**假阴性**：第一版只认 `os.getenv` / `os.environ.get` /
    `*environ*` / `_env_*` helper，而本仓大量代码把环境做成 dict 传参后写
    `env.get("R20_XXX")`（`notifications.py`、`routers/gateway/*`）—— 那些读点**完全隐形**。
    实测漏了 16 个键，其中 4 个（`OKX_BASE_URL`/`R20_TELEGRAM_API_BASE`/
    `R20_DINGTALK_SECRET`/`R20_FEISHU_SECRET`）**连模板都没有** ⇒ 本门当时是绿的，却是假绿。
    """
    name = ast.unparse(call.func)
    if name in ("os.getenv", "getenv", "os.environ.get", "environ.get"):
        return True
    if isinstance(call.func, ast.Attribute) and call.func.attr == "get":
        receiver = ast.unparse(call.func.value)
        if receiver.endswith("environ") or receiver.split(".")[-1] in ENV_DICT_RECEIVERS:
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
        self.assertGreaterEqual(len(keys), 85,
                                f"只扫到 {len(keys)} 个环境键 ⇒ 召回退化了（遗漏的读法会让门变假绿）")
        template = (ROOT / "env.example").read_text(encoding="utf-8")
        self.assertGreaterEqual(len(re.findall(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", template, re.M)), 60,
                                "模板赋值行太少 ⇒ 可能读错了文件")
        for must in ("R20_OKX_ENV", "R20_VENUE_PROTECTION_WATCHDOG",
                     "R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN", "R20_MAX_PRICE_CROSS_PCT"):
            self.assertIn(must, template, f"安全相关键 {must} 必须出现在模板里")

    def test_allowlist_entries_have_reasons(self):
        for key, reason in ALLOWLIST.items():
            self.assertTrue(str(reason).strip(), f"{key} 的放行理由不能为空")

    def test_scanner_covers_every_code_root(self):
        """牙齿（第二百刀）：代码根清单必须覆盖仓里**所有**含 .py 的一级目录。

        第一版只写了两根 ⇒ `r20_gateway/` 的读点隐形。这里让"漏根"变成判红：
        仓库里凡有 .py 的一级目录（除 tests/data 这类），都必须出现在 SCAN_DIRS 里。
        """
        skip = {"tests", "data", "backups", "logs", "deploy", "docs", "plan_local",
                "promo_local", "frontend", "node_modules", "dist", ".archive"}
        roots_with_py = set()
        for path in ROOT.iterdir():
            # 隐藏目录（`.venv`/`.archive`/`.git`）与构建产物一律不算代码根
            if not path.is_dir() or path.name.startswith(".") or path.name in skip:
                continue
            if any(path.rglob("*.py")):
                roots_with_py.add(path.name)
        missing = sorted(roots_with_py - set(SCAN_DIRS))
        self.assertEqual(missing, [], f"这些代码根没被扫描 ⇒ 它们的读点会隐形（假绿）：{missing}")

    def test_scanner_sees_env_dict_receivers(self):
        """牙齿（第一百九十九刀）：`env.get("R20_X")` 这种**dict 传参**的读法必须被看见。

        第一版看不见 ⇒ 4 个键在模板里缺席却门是绿的（假绿）。
        """
        for src in ('def f(env):\n    return env.get("R20_DICT_STYLE_KEY", "")\n',
                    'def f(environment):\n    return environment.get("R20_DICT_STYLE_KEY")\n'):
            self.assertEqual(consulted_env_keys(src), {"R20_DICT_STYLE_KEY"},
                             "dict 风格的环境读取没被看见 ⇒ 门会假绿")

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

# ---------------------------------------------------------------------------
# 反向：模板里声明的键必须**真的被消费**（第一百九十六刀）
#
# 方向一（上面）：代码会读 ⇒ 模板必须可发现。
# 方向二（这里）：模板声明 ⇒ 代码必须真的用它。否则操作者拧了一个**什么都不做**的旋钮
# （"配置假象"），比没有这个开关更坏。
#
# ⚠️ "被消费"的判据不能用"有没有 `os.environ.get("<字面量>")`"——本仓有三条真实通道：
#   1. 直接读：`os.environ[...]` / `environ.get` / `os.getenv` / `_env_*` helper（方向一用的判据）；
#   2. **键表**：`settings_store.MANAGED_KEYS` 这类表把键名当字符串存着，读写经由表
#      （凭证键全走这条：`OKX_LIVE_API_KEY` 等）；
#   3. **f-string 派生**：`env.get(f"R20_NOTIFY_{channel.upper()}_ENABLED")` 之类拼出键名
#      （通知开关全走这条）。
# 故判据 = 直接读 ∪ **非 docstring 的字面量出现** ∪ f-string 前后缀匹配。
# 排除 docstring 很关键：把键名写进文档字符串不算"代码会用它"。
# ---------------------------------------------------------------------------

TEMPLATE_ALLOWLIST: dict[str, str] = {}   # 当前为空：模板 90 键全部真被消费


def _literal_and_fstring_mechanisms() -> tuple:
    """返回 (字面量出现表, f-string 前后缀表) —— 逐文件 AST，**排除 docstring**。"""
    literal, fpatterns = {}, []
    for root in SCAN_DIRS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    ds = ast.get_docstring(node, clean=False)
                    if ds:
                        docstrings.add(ds)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and _looks_like_env_key(node.value) and node.value not in docstrings:
                    literal.setdefault(node.value, f"{rel}:{node.lineno}")
                elif isinstance(node, ast.JoinedStr):
                    parts = [v.value if isinstance(v, ast.Constant) else None for v in node.values]
                    if len(parts) >= 2 and isinstance(parts[0], str) and parts[0].startswith("R20_"):
                        suffix = parts[-1] if isinstance(parts[-1], str) else ""
                        fpatterns.append((parts[0], suffix, f"{rel}:{node.lineno}"))
    return literal, fpatterns


def template_keys() -> list:
    return re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=",
                      (ROOT / "env.example").read_text(encoding="utf-8"), re.M)


def consumed_by(key: str, literal: dict, fpatterns: list):
    if key in literal:
        return f"字面量 {literal[key]}"
    for prefix, suffix, where in fpatterns:
        if key.startswith(prefix) and key.endswith(suffix) and len(key) > len(prefix) + len(suffix) - 1:
            return f"f-string {prefix}…{suffix} @ {where}"
    return None


class TemplateKeysAreConsumedTest(unittest.TestCase):
    def test_every_template_key_is_actually_consumed(self):
        literal, fpatterns = _literal_and_fstring_mechanisms()
        dead = [k for k in template_keys()
                if not consumed_by(k, literal, fpatterns) and k not in TEMPLATE_ALLOWLIST]
        self.assertEqual(dead, [], "这些键在 env.example 里声明、但代码从不消费 ⇒ 操作者拧的是"
                                   "什么都不做的旋钮（配置假象）：\n  " + "\n  ".join(dead))

    def test_reverse_scan_is_not_vacuous(self):
        keys = template_keys()
        literal, fpatterns = _literal_and_fstring_mechanisms()
        self.assertGreaterEqual(len(keys), 80, f"模板只解析出 {len(keys)} 个键 ⇒ 门与实现脱节")
        self.assertGreaterEqual(len(fpatterns), 2,
                                "没扫到 f-string 派生键 ⇒ 判据少了一条真实通道（通知开关走它）")
        consumed = [k for k in keys if consumed_by(k, literal, fpatterns)]
        self.assertGreaterEqual(len(consumed), 80, f"只判定 {len(consumed)} 键被消费 ⇒ 扫描失效")

    def test_teeth_on_a_knob_nobody_reads(self):
        literal, fpatterns = {"SOMETHING_ELSE": "x:1"}, []
        self.assertIsNone(consumed_by("R20_NOBODY_READS_THIS", literal, fpatterns),
                          "没人消费的键必须判为未消费")
        # docstring 里出现不算消费（合成）：字面量表里不该有它
        src = 'def f():\n    """R20_DOC_ONLY_KNOB 只写在文档里"""\n    return 1\n'
        import ast as _ast
        ds = _ast.get_docstring(_ast.parse(src).body[0], clean=False)
        self.assertIn("R20_DOC_ONLY_KNOB", ds, "样本本身要成立")
        self.assertNotIn("R20_DOC_ONLY_KNOB", literal, "docstring 不得被当成消费点")
