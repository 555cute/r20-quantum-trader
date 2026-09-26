"""提示词模板编译簇抽离（结构优化阶段 4·B3 第五十三刀）。

## 背景

`scripts/prompt_library.py`（1035 行）里有两块职责：

| 簇 | 内容 |
|---|---|
| **模板编译** | 文本↔模块互转、模块标签继承、管线布局套用与视图 |
| 配置库 CRUD | `load_library` / `save_library` / profile 增删改查 / 导入导出 / 校验 |

实测（传递纯度扫描）：**CRUD 簇全部经 `BASELINE_FILE` / `LOCAL_FILE` / `MAX_PROFILE_CHARS`
被"污染"，而模板编译簇是纯的**。故抽出模板簇到
`scripts/prompt_templates.py`，门面 `prompt_library.py` **1035 → 1009 行**，
保留同名薄壳。

## ⚠️ 三项注入 + 一项随簇搬走的取舍

| 模块级名 | 本簇读点 | 处理 |
|---|---|---|
| `MAX_TEMPLATE_CHARS` | `_module` 的截断 | **注入**（配置面口径，留在门面以便 patch） |
| `_BASE_TEMPLATE_SOURCES` | `base_template_text` | **注入**（"管线→基座来源"注册表） |
| `_BASE_TEMPLATE_CACHE` | `base_template_text` | **注入**（**可变**缓存） |
| `_SECTION_RE` | `text_to_modules` | **随簇搬走**（只服务模板分节解析） |
| `BJ_TZ` | 本簇零读点 | 不搬 |

⚠️ `_BASE_TEMPLATE_CACHE` 是**可变** dict，且门面的
`register_base_template()` 会往它里面写。子模块若 import 期绑一份，
门面登记的基座就**永远读不到**（两处状态分叉）—— 故必须由门面
在调用时传入**同一个对象**。`CacheIsSharedTest` 专门钉住这条。

## ⚠️ 本刀我在测试/实现里犯的错（如实记录）

1. **docstring 写反了**：第一版我写"`_SECTION_RE` 在本簇里没有被读到（实测）"——
   **是错的**，`text_to_modules` 明确用了它。我读错了自己工具的扫描输出。
2. **手写导入清单漏项**：第一版导入是我手写的，漏了 `hashlib` → **21 例
   `NameError`**。改用 **AST 求自由名** 才发现还漏了 `importlib` / `sys` / `copy`，
   以及那两个缓存需要注入。
3. **手写签名全错**：我以为 `base_template_text(profile, key)` 之类，
   实际签名逐个不同；改用 **AST 取真实签名** 生成薄壳。
4. **忘了对外部调用者保持签名**：`pipeline_view` 等在
   `r20_backend/routers/strategy/prompts.py` 有调用点，故新形参一律
   **keyword-only 且由门面补齐**，公开签名对外不变。
5. **漏了 `align_pipeline_sources` 也调 `base_template_text`** →
   最后改用 `base_text_resolver` 回调统一注入，而不是把
   `sources`/`cache` 一层层往下穿。
6. **双模导入又漏了**：`from prompt_templates import …` 在
   `scripts.prompt_library` 布局下 `ModuleNotFoundError` —— 与第四十八刀
   `local_lock` 完全同一个坑，被 `test_dashboard_bills_extraction` 抓住。
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

FACADE = ROOT / "scripts" / "prompt_library.py"
SHARED = ROOT / "scripts" / "prompt_templates.py"
PRE_EXTRACTION_COMMIT = "b2855da"

MOVED = ["stable_base_module_id", "_module", "text_to_modules", "compile_modules",
         "base_template_modules", "base_template_text", "align_pipeline_sources",
         "_inherit_module_tags", "pipeline_view", "append_layer"]

import prompt_library as pl  # noqa: E402
import prompt_templates as pt  # noqa: E402


class FacadeSurfaceTest(unittest.TestCase):
    def test_facade_still_exposes_every_moved_name(self):
        for name in MOVED:
            self.assertTrue(callable(getattr(pl, name, None)), f"门面缺少 {name}")

    def test_facade_keeps_untouched_api(self):
        for name in ("load_library", "save_library", "create_profile", "update_profile",
                     "delete_profile", "activate_profile", "validate_profile",
                     "import_profile", "export_profile", "render_variables",
                     "apply_module_layout", "active_profile", "all_profiles",
                     "register_base_template", "scan_forbidden",
                     "MAX_TEMPLATE_CHARS", "MAX_PROFILE_CHARS", "ALLOWED_VARIABLES",
                     "_BASE_TEMPLATE_CACHE", "_BASE_TEMPLATE_SOURCES"):
            self.assertTrue(hasattr(pl, name), f"门面缺少 {name}")

    def test_shells_are_definitions_not_aliases(self):
        """⚠️ 薄壳必须是**定义**：别名会让 `patch.object(pl, …)` 之类接缝失效。"""
        for name in MOVED:
            self.assertIsNot(getattr(pl, name), getattr(pt, name), f"{name} 是别名")

    def test_every_shell_is_a_single_delegation(self):
        tree = ast.parse(FACADE.read_text(encoding="utf-8"))
        by_name = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        for name in MOVED:
            n = by_name.get(name)
            self.assertIsNotNone(n, f"门面未定义 {name}")
            body = [s for s in n.body
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            self.assertEqual(len(body), 1, f"{name} 壳不止一条语句")
            self.assertIn("_tpl_", ast.unparse(body[0]), f"{name} 未转调共享实现")

    def test_public_signatures_are_unchanged(self):
        """⚠️ 外部调用者（`routers/strategy/prompts.py`）用位置参数调用，故门面签名
        **不得**新增必需参数。"""
        for name, expect in (
            ("text_to_modules", ["text", "source", "locked"]),
            ("base_template_modules", ["text", "pipeline"]),
            ("align_pipeline_sources", ["modules", "pipeline"]),
            ("pipeline_view", ["base", "profile", "pipeline"]),
            ("base_template_text", ["pipeline"]),
            ("compile_modules", ["modules"]),
        ):
            import inspect
            params = list(inspect.signature(getattr(pl, name)).parameters)
            self.assertEqual(params, expect, f"{name} 门面签名被改动")


class SharedModuleTest(unittest.TestCase):
    def test_shared_module_reads_no_path_constants(self):
        tree = ast.parse(SHARED.read_text(encoding="utf-8"))
        assigned = {t.id for n in tree.body if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        # 2026-09 起方案库是双文件（`BASELINE_FILE` 读 / `LOCAL_FILE` 写），
        # 两个新名一并列入"共享模块不得定义"清单 —— 否则这次改名会把这门静默架空。
        for banned in ("LIBRARY_FILE", "BASELINE_FILE", "LOCAL_FILE",
                       "ROOT", "MAX_PROFILE_CHARS", "ROOT_DIR"):
            self.assertNotIn(banned, assigned, f"prompt_templates 不该定义 {banned}")

    def test_shared_module_does_not_import_the_facade(self):
        """反向依赖会成环。"""
        tree = ast.parse(SHARED.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.assertNotEqual(a.name, "prompt_library")
            elif isinstance(n, ast.ImportFrom):
                self.assertNotEqual(n.module, "prompt_library")

    def test_shared_module_has_no_free_names(self):
        """⚠️ 本刀我因为**手写导入清单**漏了 `hashlib` 导致 21 例 NameError。

        这条用 AST 求"自由名"来防同类问题：搬过来的函数体里不得引用
        本模块既未定义、也未导入的名字。
        """
        import builtins
        tree = ast.parse(SHARED.read_text(encoding="utf-8"))
        defined = set(dir(builtins))
        for n in tree.body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    defined.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, ast.FunctionDef):
                defined.add(n.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
        free = {}
        for n in tree.body:
            if not isinstance(n, ast.FunctionDef):
                continue
            local = {a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs}
            local |= {x.id for x in ast.walk(n)
                      if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
            for x in ast.walk(n):
                if (isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                        and x.id not in local and x.id not in defined):
                    free.setdefault(x.id, set()).add(n.name)
        self.assertEqual(free, {}, f"存在未定义的自由名: {free}")


class CacheIsSharedTest(unittest.TestCase):
    """⚠️ `register_base_template()` 写的缓存必须被 `base_template_text` 读到。"""

    def test_registered_base_template_is_visible(self):
        marker = "【回归哨兵】\nbase-template-cache-shared-proof"
        pl.register_base_template("trading_system", marker)
        try:
            self.assertEqual(pl.base_template_text("trading_system"), marker)
        finally:
            pl._BASE_TEMPLATE_CACHE.pop("trading_system", None)

    def test_cache_object_is_the_facade_one(self):
        """子模块不得持有自己的缓存副本（否则门面登记读不到）。"""
        marker = "【哨兵2】\nshared-cache-identity"
        pl.register_base_template("trading_user", marker)
        try:
            self.assertEqual(pl._BASE_TEMPLATE_CACHE.get("trading_user"), marker)
            self.assertEqual(pl.base_template_text("trading_user"), marker)
        finally:
            pl._BASE_TEMPLATE_CACHE.pop("trading_user", None)

    def test_max_template_chars_is_read_from_the_facade_at_call_time(self):
        """⚠️ `MAX_TEMPLATE_CHARS` 留在门面，故 patch 必须生效。"""
        from unittest.mock import patch
        modules = [{"title": "T", "content": "x" * 100, "source": "legacy"}]
        with patch.object(pl, "MAX_TEMPLATE_CHARS", 5):
            out = pl.text_to_modules("【T】\n" + "x" * 100, "legacy")
        self.assertTrue(out, "截断过小后仍应有模块")
        self.assertLessEqual(len(out[0]["content"]), 5,
                             "patch 门面 MAX_TEMPLATE_CHARS 未生效 —— 说明被 import 期烘焙了")


class BehaviourPreservedTest(unittest.TestCase):
    """行为等价：文本 ↔ 模块 ↔ 布局 的既有契约。"""

    def test_text_to_modules_splits_on_section_headers(self):
        text = "【甲】\nA\n\n【乙】\nB"
        mods = pl.text_to_modules(text, "legacy")
        self.assertEqual([m["title"] for m in mods], ["甲", "乙"])

    def test_text_to_modules_falls_back_to_single_module(self):
        mods = pl.text_to_modules("没有分节标题的纯文本", "legacy")
        self.assertEqual(len(mods), 1)

    def test_text_to_modules_empty_input(self):
        self.assertEqual(pl.text_to_modules("", "legacy"), [])

    def test_compile_modules_round_trip(self):
        text = "【甲】\nA"
        mods = pl.text_to_modules(text, "legacy")
        self.assertIn("A", pl.compile_modules(mods))

    def test_stable_base_module_id_is_deterministic(self):
        a = pl.stable_base_module_id("基准风控")
        b = pl.stable_base_module_id("基准风控")
        self.assertEqual(a, b)
        self.assertNotEqual(a, pl.stable_base_module_id("另一个"))

    def test_append_layer_keeps_both(self):
        out = pl.append_layer("base", "layer", "label")
        self.assertIn("base", out)
        self.assertIn("layer", out)


class VerbatimCopyTest(unittest.TestCase):
    """⚠️ 搬移只允许**复制**（第五十刀的教训）。"""

    def _body(self, src, name):
        tree = ast.parse(src)
        n = next(x for x in tree.body
                 if isinstance(x, ast.FunctionDef) and x.name == name)
        return ast.unparse(ast.Module(
            body=[s for s in n.body
                  if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))],
            type_ignores=[]))

    @staticmethod
    def _strip_injections(node: ast.FunctionDef) -> ast.FunctionDef:
        """在 **AST 层**剥掉本刀有意注入的东西，再比对函数体。

        ⚠️ 我在本条用例上栽了**三次**，都是因为拿字符串做替换：
        ① 用源码里的单引号写替换串，而 `ast.unparse` 统一输出双引号；
        ② 列了 8 条引号变体仍然漏；
        ③ 改成"先换名再剥整对"→ 留下悬空的 `=`；正则清空参数位又漏 `,)`。

        **结论：这种比对必须在 AST 层做，字符串正则是错工具。**
        剥法：删掉注入的 keyword-only 形参、删掉注入的 kwargs，
        并把 resolver 调用还原成原来的函数调用。
        """
        import copy
        n = copy.deepcopy(node)

        # ① 删掉注入的 keyword-only 形参
        INJECTED = {"max_template_chars", "base_text_resolver"}
        n.args.kwonlyargs = [a for a in n.args.kwonlyargs if a.arg not in INJECTED]
        n.args.kw_defaults = [d for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults)
                              if a.arg not in INJECTED]

        # ② 删掉注入的 kwargs；③ 还原 resolver 调用；④ 还原形参名引用
        RENAME = {"max_template_chars": "MAX_TEMPLATE_CHARS",
                  "sources": "_BASE_TEMPLATE_SOURCES",
                  "cache": "_BASE_TEMPLATE_CACHE"}
        for x in ast.walk(n):
            if isinstance(x, ast.Call):
                x.keywords = [k for k in x.keywords
                              if not (k.arg in INJECTED or k.arg in ("sources", "cache"))]
                if isinstance(x.func, ast.Name) and x.func.id == "base_text_resolver":
                    x.func = ast.Name(id="base_template_text", ctx=ast.Load())
                    x.keywords = []
            elif isinstance(x, ast.Name) and x.id in RENAME:
                x.id = RENAME[x.id]
        return n

    @staticmethod
    def _skeleton(node: ast.FunctionDef) -> str:
        """函数体的可执行骨架（剥 docstring），在 AST 层生成。"""
        body = [s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        return ast.dump(ast.Module(body=body, type_ignores=[]))

    def test_moved_bodies_match_pre_extraction_except_injected_names(self):
        import subprocess
        old_src = subprocess.run(
            ["git", "show", f"{PRE_EXTRACTION_COMMIT}:scripts/prompt_library.py"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(old_src.returncode, 0, old_src.stderr)
        new_src = SHARED.read_text(encoding="utf-8")

        old_tree = ast.parse(old_src.stdout)
        new_tree = ast.parse(new_src)

        for name in MOVED:
            o = next(x for x in old_tree.body
                     if isinstance(x, ast.FunctionDef) and x.name == name)
            nnode = next(x for x in new_tree.body
                         if isinstance(x, ast.FunctionDef) and x.name == name)
            self.assertEqual(self._skeleton(o),
                             self._skeleton(self._strip_injections(nnode)),
                             f"{name} 的函数体在搬移中被改写了（超出预期的注入替换）")


class DualImportTest(unittest.TestCase):
    """⚠️ 本刀又一次踩了双模导入的坑（与第四十八刀 `local_lock` 相同）。"""

    def test_imports_under_both_path_layouts(self):
        # 第七十八刀：以 spawn 为被测行为，离线守护下如实 skip（守卫在 spawn 前）。
        from tests.config_sandbox import skip_if_offline_suite
        skip_if_offline_suite(self)
        import subprocess
        cases = {
            "bare": ("import sys\nsys.path.insert(0, %r)\n"
                     "import prompt_library, prompt_templates\nprint('OK')\n"
                     % str(ROOT / "scripts")),
            "pkg": ("import sys\nsys.path.insert(0, %r)\n"
                    "import scripts.prompt_library, scripts.prompt_templates\nprint('OK')\n"
                    % str(ROOT)),
        }
        for label, code in cases.items():
            r = subprocess.run([sys.executable, "-c", code],
                               capture_output=True, text=True, timeout=60, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, f"{label}:\n{r.stderr[-900:]}")
            self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
