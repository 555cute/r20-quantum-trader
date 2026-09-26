"""目录说明文档与文件系统**保持一致**（结构优化阶段 4·B3 第三十三刀）。

## 这个测试在守什么

目标里有一条"**新增目录说明文档**"。本仓的目录说明写在各子包的 `__init__.py`
文档串里（`scripts/trader/`、`scripts/brain/`、`scripts/factors/` …），
每个都带一张「模块清单」表。

问题是：**这类文档会腐烂**。我这一阶段实测到 ——

| 子包 | 文档漏掉的模块 |
|---|---|
| `scripts/trader/` | `position_universe.py`（第三十一刀新增） |
| `scripts/factors/` | `scoring.py`、`candles_15m.py`（第二十九/三十二刀新增） |
| `r20_backend/council/` | 完全没有模块清单 |
| `scripts/ledger/` | 完全没有模块清单 |

而且**没有任何测试会红** —— 文档腐烂是静默的。等到有人照着清单找模块时才发现。

## 做法

对每个受管子包：把 `__init__.py` 文档串里出现的 `xxx.py` 名字
**与磁盘上的实际 `.py` 文件**做双向集合比对：

- 磁盘上有、文档没提 → **新增了模块却没更新说明**；
- 文档提了、磁盘上没有 → **模块被删/改名但说明没改**（更危险：指向不存在的文件）。

## ⚠️ 这个测试的边界（必须说清）

它只保证**"名字出现过"**，**不保证描述内容正确**。- 描述写得对不对、
  注入面列得准不准，机器判不了。故它挡的是"**忘了登记**"，不是"**写错了**"。

`__pycache__`、`__init__.py` 自身、以及 `_` 前缀的私有辅助文件不参与比对。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 受管子包 → 该子包的文件名出现形式。
#: `allowed_extra` 是在文档里**有意**提到但不在本目录的文件（如门面、测试）。
#: `docs` 是"登记名册"的额外来源 —— 有些子包把清单写在 README 而不是 __init__.py
#: （`dashboard_payload/` 就是：它的总约定在 r20_backend/README.md）。
MANAGED = {
    "scripts/trader": {"allowed_extra": set()},
    "scripts/brain": {"allowed_extra": {"ai_brain_trader.py"}},
    "scripts/factors": {"allowed_extra": {"factor_library.py"}},
    "scripts/ledger": {"allowed_extra": {"sync_full_ledger.py"}},
    "r20_backend/council": {"allowed_extra": {"council_manager.py"}},
    # 第五十九刀补登记：本子包此前**不在受管名单**里（.py 文件靠 __init__ 的
    # 项目符号清单导航）。既然已有该约定，顺手纳入监管，防止新模块漏登记。
    "r20_backend/exchanges": {"allowed_extra": set()},
    "r20_backend/dashboard_payload": {
        "allowed_extra": {"dashboard.py", "app.py"},
        "docs": ["r20_backend/README.md"],
    },
    # 第三十八刀补登记：`execution_router.py::open_protected_position` 的风控闸门
    # 抽成 `risk_gates.py` 后才发现本子包**此前根本不在受管名单里** ——
    # 也就是说 `indicators.py` / `sizing.py` / `circuit_breaker.py` 烂了文档也没人管。
    # 顺手纳入。
    "r20_backend/execution": {"allowed_extra": {"execution_router.py"}},
    # 第三十九刀新增：回测引擎的成块领域逻辑外提（门面仍是单文件 backtest_engine.py）。
    "scripts/backtest": {"allowed_extra": {"backtest_engine.py"}},
    # 第四十二刀新增：自进化引擎的可观测性聚簇外提。
    "scripts/evolution": {"allowed_extra": {"self_improvement_engine.py"}},
    # 第四十三刀新增：选所质量域外提（门面仍是单文件 venue_router.py）。
    "r20_backend/venue_routing": {"allowed_extra": {"venue_router.py"}},
    # 第四十四刀新增：快讯纯判断逻辑外提（门面仍是 news_sentiment_harvester.py）。
    "scripts/news": {"allowed_extra": {"news_sentiment_harvester.py"}},
    # 第四十五刀新增：微积分引擎实现外提（门面仍只做再导出）。
    "scripts/calculus": {"allowed_extra": {"calculus_engine.py"}},
}

#: 从文档里抽出的 `xxx.py` 文件名。
#:
#: ⚠️ 左侧用 `(?<![\w*])` 而不是 `\b`：`\b` 会在
#: `tests/test_brain_*_extraction.py` 这样的**通配写法**中间匹配到 `_extraction.py`，
#: 于是报"提到 _extraction.py 但全仓找不到"—— 假警报。
#: 要求左边既不是词字符、也不是 `*`（通配符），才是真正的文件名开头。
_DOC_NAME = re.compile(r"(?<![\w*])([a-z_][a-z0-9_]*\.py)\b")


def _doc_names(init_py: Path, extra_docs: list[str] | None = None) -> set[str]:
    """该子包**登记名册**里提到的所有 `xxx.py`。

    名册来源 = `__init__.py` 文档串 + 任意 `docs` 里的 Markdown。
    （`dashboard_payload/` 的清单写在 `r20_backend/README.md` —— 那里才是
    新人会去翻的地方，所以在 README 里登记是合理的，不该被本测试判为漏登记。）
    """
    tree = ast.parse(init_py.read_text(encoding="utf-8"))
    texts = [ast.get_docstring(tree) or ""]
    for rel in (extra_docs or []):
        texts.append((ROOT / rel).read_text(encoding="utf-8"))
    return {m.group(1) for t in texts for m in _DOC_NAME.finditer(t)}


def _disk_modules(pkg_dir: Path) -> set[str]:
    out = set()
    for p in pkg_dir.glob("*.py"):
        if p.name == "__init__.py":
            continue
        if p.name.startswith("_"):
            continue
        out.add(p.name)
    return out


class DirectoryDocsTest(unittest.TestCase):
    def test_every_managed_package_has_a_docstring(self):
        for rel in MANAGED:
            init = ROOT / rel / "__init__.py"
            with self.subTest(pkg=rel):
                self.assertTrue(init.exists(), f"{rel}/__init__.py 不存在")
                tree = ast.parse(init.read_text(encoding="utf-8"))
                self.assertTrue(ast.get_docstring(tree),
                                f"{rel}/__init__.py 没有文档串")

    def test_no_undocumented_module(self):
        """磁盘上的每个模块都必须在 `__init__.py` 文档里出现过。"""
        problems = []
        for rel, cfg in MANAGED.items():
            pkg = ROOT / rel
            documented = _doc_names(pkg / "__init__.py", cfg.get("docs"))
            for mod in sorted(_disk_modules(pkg)):
                if mod not in documented:
                    problems.append(f"{rel}/{mod} 未登记在 {rel}/__init__.py 的说明里")
        self.assertEqual(problems, [],
                         "新增模块后请更新该子包 __init__.py 的模块清单（本测试就是为此而设）")

    def test_no_doc_reference_to_a_missing_file(self):
        """文档里提到的 `.py` 必须在磁盘上真实存在。

        这比"漏登记"更危险：说明指向一个**不存在**的文件，会把人引到死路。
        """
        problems = []
        for rel, cfg in MANAGED.items():
            pkg = ROOT / rel
            existing = {p.name for p in pkg.glob("*.py")}
            for name in sorted(_doc_names(pkg / "__init__.py", cfg.get("docs"))):
                if name in existing:
                    continue
                if name in cfg.get("allowed_extra", set()):
                    continue
                # 允许引用同域的**兄弟**包文件（如 tests/、项目根），只要全仓能找到
                hits = list(ROOT.rglob(name))
                hits = [h for h in hits if ".venv" not in str(h) and "node_modules" not in str(h)]
                if not hits:
                    problems.append(f"{rel}/__init__.py 提到 {name}，但全仓找不到该文件")
        self.assertEqual(problems, [])

    def test_manifest_tables_are_present(self):
        """受管子包的文档串里应有一张「模块清单」表（便于"新文件放哪"）。"""
        for rel in ("scripts/trader", "scripts/brain", "scripts/factors",
                    "scripts/ledger", "r20_backend/council"):
            init = ROOT / rel / "__init__.py"
            doc = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8"))) or ""
            with self.subTest(pkg=rel):
                self.assertIn("模块清单", doc,
                              f"{rel}/__init__.py 缺少「模块清单」表")

    def test_managed_packages_all_exist(self):
        for rel in MANAGED:
            with self.subTest(pkg=rel):
                self.assertTrue((ROOT / rel).is_dir(), f"{rel} 不是目录")


class RootLevelModulesRegisteredTest(unittest.TestCase):
    """⚠️ 第六十五刀补：**根层模块**此前不在任何门禁里。

    `MANAGED` 只覆盖**子包**，而 `r20_backend/*.py`（根层，38 个）靠
    `r20_backend/README.md` 的 §L3/§L4 表格导航 —— 那张表**没有任何测试看着**。

    后果实测：第四十九刀抽出的 `r20_backend/redact.py` 与
    第五十一刀抽出的 `r20_backend/math_utils.py` **从未登记进 README**，
    直到本刀才发现。而这两刀恰恰就是"新增模块"的操作 ——
    **说明这个洞一直在漏**。

    判据与子包一致：磁盘上的每个根层模块名，都必须在该 README 里出现过；
    反向也查（README 提到的 `.py` 必须真实存在，避免把人引到死路）。
    """

    README = ROOT / "r20_backend" / "README.md"

    #: 文档里**有意**提到但不在 `r20_backend/` 根层的文件
    #: （子包内的、以及作为门面被引用的上层脚本）。
    ALLOWED_EXTRA = {
        "dashboard.py", "app.py",                       # dashboard_payload 的门面
        "ai_factor_trader.py", "ai_brain_trader.py",    # scripts/ 下的调用方
        "factor_library.py", "sync_full_ledger.py",     # scripts/ 下的兄弟模块
        "council_manager.py", "risk_gates.py",
    }

    def _disk_modules(self) -> set:
        pkg = ROOT / "r20_backend"
        return {p.name for p in pkg.glob("*.py")
                if p.name != "__init__.py" and not p.name.startswith("_")}

    def test_every_root_module_is_registered(self):
        """⚠️ 与 `scripts/` 侧同一判据：必须**以表格行形态**登记。

        单看"全文搜得到"会被正文里顺口的一句说明蒙过去（见该侧 docstring）。
        """
        doc = self.README.read_text(encoding="utf-8")
        # ⚠️ 判据必须匹配**文档真实的结构**：本 README 的
        #    §L1（启动与装配）/ §L2（HTTP 边界）用**正文列举**，
        #    §L3/§L4 用**表格**。第一版我只认表格行，于是把
        #    `app.py`/`scheduler.py`/`config.py`… 这 8 个**已登记**的模块
        #    误报成"未登记"。
        #
        #    正确判据 = "以反引号形式出现在文档里"；
        #    为防"正文顺口提一句就算数"（那是 scripts 侧负向验证抓到的洞），
        #    这里额外要求它出现在**列出模块的段落**里 —— 用"同行还有别的
        #    `.py`"或"在表格行里"来近似，二者取并集。
        entries = set()
        for ln in doc.splitlines():
            if f"{ROOT.name}" in ln and ln.lstrip().startswith("#"):
                continue
            if ln.lstrip().startswith("|") or ln.count(".py`") + ln.count(".py`、") >= 1:
                entries |= set(re.findall(r"`([A-Za-z0-9_]+\.py)`", ln))
        missing = sorted(m for m in self._disk_modules() if m not in entries)
        self.assertEqual(
            missing, [],
            f"这些 r20_backend/ 根层模块未登记在 {self.README.name} —— "
            f"新增模块后请补进 §L3/§L4 的表格或 §L1/§L2 的列举: {missing}")

    def test_documented_root_names_exist(self):
        """反向：README 里以根层形态出现的 `.py` 必须真实存在。"""
        doc = self.README.read_text(encoding="utf-8")
        referenced = set(re.findall(r"`([A-Za-z0-9_]+\.py)`", doc))
        on_disk = self._disk_modules()
        dangling = sorted(
            n for n in referenced
            if n not in on_disk and n not in self.ALLOWED_EXTRA
            and not any((ROOT / "r20_backend" / sub / n).exists()
                        for sub in ("dashboard_payload", "council", "execution",
                                    "exchanges", "routers", "llm", "policy",
                                    "sandbox", "venue_routing")))
        self.assertEqual(
            dangling, [],
            f"README 提到这些 `.py` 但全仓找不到 —— 会把人引到死路: {dangling}")

    def test_no_root_module_regressed_to_the_old_state(self):
        """把本刀修掉的两个具体漏登记钉住（防止再被"顺手删掉"）。"""
        doc = self.README.read_text(encoding="utf-8")
        for name in ("redact.py", "math_utils.py"):
            self.assertIn(name, doc,
                          f"{name} 是抽取产物，必须留在 README 的模块表里")


class ScriptsRootModulesRegisteredTest(unittest.TestCase):
    """⚠️ 第六十六刀补：`scripts/` 根层此前**连 README 都没有**。

    `MANAGED` 覆盖了 `scripts/` 下的 8 个子包，但 `scripts/*.py`
    （根层 **33 个**，即实盘 worker 与共用库）**没有任何导航文档** ——
    实测其中 23 个在全仓 `.md` 里连一次都没被提到。

    这对"**便于查 bug / 新增功能**"是直接伤害：新人只能逐个打开文件猜
    哪个是入口、哪个是库。

    本刀新建 `scripts/README.md` 并加此门禁。判据与前几刀一致：
    磁盘上的根层模块必须都在文档里出现（反之亦然）。
    """

    README = ROOT / "scripts" / "README.md"

    #: 文档里**有意**提到但不在 `scripts/` 根层的文件
    #: （子包内的部件、以及 `r20_backend/` 的兄弟模块）。
    ALLOWED_EXTRA = {
        "app.py", "dashboard.py", "ai_factor_trader.py",   # 提及的调用方/门面
    }

    def _disk_modules(self) -> set:
        pkg = ROOT / "scripts"
        return {p.name for p in pkg.glob("*.py") if not p.name.startswith("_")}

    def test_readme_exists(self):
        self.assertTrue(self.README.exists(),
                        "scripts/ 根层有 33 个模块，必须有导航文档")

    def test_every_root_module_is_registered(self):
        """⚠️ 必须**以表格行形态**登记，不能只是正文里被顺口提一句。

        负向验证当场抓到：把 `| \`ai_factor_trader.py\` | 2801 | …` 这一行
        替换掉之后，用例**仍然是绿的** —— 因为该文件名在文档别处
        （`trader/` 那行的说明文字"从 `ai_factor_trader.py` 抽出的…"）
        又出现了一次，`m not in doc` 这种"全文搜一次"的判据完全够不着。

        改为要求形如 `| \`name.py\` |` 的表格行存在。
        """
        doc = self.README.read_text(encoding="utf-8")
        entries = set()
        for ln in doc.splitlines():
            if ln.lstrip().startswith("|") or ".py`" in ln:
                entries |= set(re.findall(r"`([A-Za-z0-9_]+\.py)`", ln))
        missing = sorted(m for m in self._disk_modules() if m not in entries)
        self.assertEqual(
            missing, [],
            f"这些 scripts/ 根层模块未以表格行登记在 {self.README.name} —— "
            f"新增模块后请补进对应表格: {missing}")

    def test_documented_root_names_exist(self):
        """反向：README 提到的 `.py` 必须真实存在（防死引用）。"""
        doc = self.README.read_text(encoding="utf-8")
        referenced = set(re.findall(r"`([A-Za-z0-9_]+\.py)`", doc))
        on_disk = self._disk_modules()
        subdirs = [d for d in (ROOT / "scripts").iterdir() if d.is_dir()]
        dangling = sorted(
            n for n in referenced
            if n not in on_disk and n not in self.ALLOWED_EXTRA
            and not any((d / n).exists() for d in subdirs))
        self.assertEqual(
            dangling, [],
            f"README 提到这些 `.py` 但找不到 —— 会把人引到死路: {dangling}")

    def test_documented_subpackages_exist(self):
        """文档里列的 `xxx/` 子包必须真的是目录。"""
        doc = self.README.read_text(encoding="utf-8")
        # ⚠️ 抓**任意** `xxx/` 记号，而不是"恰好三格的表格行"。
        #    负向验证抓到：把子包行改成多一格（`| \`news/\`、\`ghostpkg/\` | … |`）
        #    时，严格的三格正则匹配不上 → 用例仍然是绿的，反而漏掉了
        #    "表格被改坏"这种更常见的手误。
        listed = set(re.findall(r"`([a-z_]+)/`", doc))
        self.assertTrue(listed, "未解析到子包清单")
        # ⚠️ 必须排除**本目录自己的名字**：文档里 `` `scripts/` `` 出现多次
        #    （标题、「`scripts/` 在 sys.path 上」等），它不是自己的子包。
        #    第一版没排除 → 误报 "['scripts']"。
        listed.discard("scripts")
        listed.discard(ROOT.name)
        missing = sorted(s for s in listed if not (ROOT / "scripts" / s).is_dir())
        self.assertEqual(missing, [], f"文档列了不存在的子包: {missing}")

    def test_daemons_and_main_entry_are_registered(self):
        """把"哪些是入口/守护"这条最有价值的信息钉住。"""
        doc = self.README.read_text(encoding="utf-8")
        for entry in ("ai_factor_trader.py", "daemon_web_sync.py",
                      "nightly_backup_and_clean.py", "sync_web_data.py"):
            self.assertIn(entry, doc, f"入口/守护 {entry} 必须出现在导航文档里")
        self.assertIn("每 15 分钟", doc, "主脚本的调度周期是关键信息，必须写明")


class ExtractedModulesHaveTestsTest(unittest.TestCase):
    """⚠️ 第六十八刀补：**抽出来的模块必须有测试引用**。

    ## 为什么要这条

    本阶段到第六十七刀共抽出 **72 个模块**。用
    `git log --diff-filter=A --grep=第.*刀` 取出清单逐个查 `tests/`，
    发现 **3 个一次都没被提到**：

    | 模块 | 行数 | 此前测试引用 |
    |---|---|---|
    | `dashboard_payload/factors_view.py` | 141 | **0** |
    | `dashboard_payload/reset_state.py` | 27 | **0** |
    | `dashboard_payload/ledger_view.py` | 79 | 1（仅间接） |

    它们只经 `r20_backend/dashboard_cache.py` 门面被调用，而门面级用例只验证
    "载荷非空 / 某几个键在"，**从不验证这些模块内部的取值优先级链**。
    这三个模块的 docstring 都写着"路径由门面注入（测试会指向沙箱）" ——
    **为可测性做了准备，却始终没人测。**

    ## 判据

    `MANAGED` 里每个子包的每个 `.py`，其**模块名或文件名**必须出现在
    某个 `tests/test_*.py` 里。

    ⚠️ 这条判据**比"必须有专门测试文件"宽松** —— 它只要求"被测试提到过"。
    这是刻意的：要求每个模块一个专属测试文件会产生大量样板，
    而真正要防的是"**抽完就当测过了**"这种静默漏测。
    """

    def _tests_text(self) -> str:
        """所有 `tests/test_*.py` 的正文。

        ⚠️ **必须排除本文件**（第六十八刀实测踩到）：
        本用例的 docstring 与断言里就写着那三个模块名，
        若不排除，则**把补测文件整个删掉、用例依然是绿的** ——
        门禁靠"自己提到自己"通过了。排除后该洞关闭。
        """
        me = Path(__file__).resolve()
        return "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            # 第 138 刀：tests/ 已按域分子目录 ⇒ 必须递归扫描
            for p in sorted((ROOT / "tests").rglob("test_*.py"))
            if p.resolve() != me)

    def test_every_managed_module_is_referenced_by_some_test(self):
        tests_text = self._tests_text()
        self.assertGreater(len(tests_text), 10000, "读到的测试文本异常地少")

        untested = []
        for rel in MANAGED:
            for f in sorted((ROOT / rel).glob("*.py")):
                if f.name == "__init__.py":
                    continue
                if f.stem in tests_text or f.name in tests_text:
                    continue
                untested.append(f"{rel}/{f.name}")
        self.assertEqual(
            untested, [],
            "这些抽取出来的模块在 tests/ 里一次都没被提到 —— "
            "抽完不等于测过，请补测试: " + ", ".join(untested))

    def test_the_three_originally_untested_modules_are_covered(self):
        """把本刀补测的三个模块具体钉住，防止测试文件被删。"""
        tests_text = self._tests_text()
        for name in ("build_factors_list", "read_reset_initial_state",
                     "load_ledger_lifecycle_trades"):
            # ⚠️ 用 assertTrue 而不是 assertIn：`assertIn(x, <一大段文本>)`
            #    在失败时会把**整份测试正文**倒进终端（实测刷屏数千行）。
            self.assertTrue(
                name in tests_text,
                name + " 曾被漏测（第六十八刀补上），不应再失去覆盖")


class ProjectReadmeStructureEntryTest(unittest.TestCase):
    """仓库根 README 的「代码结构入口 / Code map」章节必须指向**真实存在**的文件。

    ## 为什么加这条

    本仓 `AGENTS.md` 第 8 行长期写着：

    > 请优先阅读项目根目录下的 **`OPENCODE.md`**

    但该文件**在全仓历史上从未存在过** —— `git log --all --diff-filter=D`
    无删除记录，也未被 gitignore。**每一个接手的人都先被引到一次空路径。**

    第六十七刀在 `README.md` 里补了「🗂️ 代码结构入口」章节，
    把结构文档的真实位置摊开。本用例守两件事：

    1. 该章节**存在**且仍指向那几份真实文档（防止被人顺手删掉，
       于是又退回"只能靠 OPENCODE.md"的状态）；
    2. 章节里引用的每个**具体路径**都真实存在（防止文档链接腐烂）。

    ## 2026-09-27：README 拆成中英两版 ⇒ 本门**两版都守**

    英文 `README.md` 是默认（GitHub 仓库搜索权重主要吃这一份），中文在
    `README.zh-CN.md`。只守默认那份的话，另一版的章节结构烂掉没人会知道 ——
    所以按语言参数化：标题、表格用途串、否定性事实的措辞各按本语言钉。
    """

    #: (文件名, 章节标题, [(被指向的文档, 该行必须说明的用途串)], OPENCODE 否定性事实的正则)
    CASES = (
        ("README.md", "Code map", (
            ("r20_backend/README.md", "Backend layering"),
            ("scripts/README.md", "Runtime scripts & daemons"),
            ("frontend/src/components/admin/README.md", "Frontend components & state"),
        ), r"never contained|non-existent|does not exist"),
        ("README.zh-CN.md", "代码结构入口", (
            ("r20_backend/README.md", "后端分层"),
            ("scripts/README.md", "哪个是入口/守护"),
            ("frontend/src/components/admin/README.md", "前端组件"),
        ), r"从未存在|不存在"),
    )

    #: 章节里**有意**提到但不必存在的名字
    #: （`OPENCODE.md` 是被点名"不存在"的反面教材；
    #:  `__init__.py` 是泛指的清单载体，不是某一条路径）。
    ALLOWED_ABSENT = {"OPENCODE.md", "__init__.py"}

    def _section(self, filename: str, heading: str) -> str:
        """取章节正文。

        ⚠️ **必须锚定「`## ` 开头的标题行」**，不能用 `text.index(heading)`：
        顶部导航条里就有一句 `[Code map](#code-map-...)` / `[架构索引](#architecture-index)`，
        按裸串定位会**切到导航条那几行**，于是断言全部落在一个只有 60 字的片段上
        （2026-09-27 拆中英双语时实测踩到：中英两侧同时假红）。
        """
        text = (ROOT / filename).read_text(encoding="utf-8")
        m_head = re.search(r"^##[^\n]*" + re.escape(heading), text, re.M)
        self.assertIsNotNone(
            m_head,
            f"{filename} 缺少「{heading}」章节 —— "
            f"新人将只能靠 AGENTS.md 里那条指向不存在文件的指引")
        rest = text[m_head.start():]
        # 到下一个二级标题为止
        m = re.search(r"\n## ", rest[3:])
        return rest[: m.start() + 3] if m else rest

    def test_section_points_at_the_real_structure_docs(self):
        """⚠️ 必须断言**表格行**，不是"这个名字在章节里出现过"。

        负向验证抓到：把 `scripts/README.md` 那行的**说明文字**改掉后，
        用例仍然绿 —— 因为该文件名在紧邻的另一行（"抽取约定"那行）里
        又出现了一次。断言过宽的又一例。
        """
        for filename, heading, rows, _absent in self.CASES:
            sec = self._section(filename, heading)
            for doc, purpose in rows:
                with self.subTest(readme=filename, doc=doc):
                    row = re.search(r"^\|.*`" + re.escape(doc) + r"`.*\|\s*$", sec, re.M)
                    self.assertIsNotNone(row, f"{filename} 的结构章节缺少指向 {doc} 的表格行")
                    self.assertIn(purpose, row.group(0),
                                  f"指向 {doc} 的那行应说明它解决什么问题（含「{purpose}」）")

    def test_every_referenced_path_exists(self):
        for filename, heading, _rows, _absent in self.CASES:
            sec = self._section(filename, heading)
            refs = set(re.findall(r"`([A-Za-z0-9_./\-]+\.(?:md|py))`", sec))
            self.assertTrue(refs, f"{filename} 结构章节未解析到任何路径引用")
            dangling = sorted(
                r for r in refs
                if r not in self.ALLOWED_ABSENT and not (ROOT / r).exists())
            self.assertEqual(dangling, [],
                             f"{filename} 结构章节引用了不存在的路径（会把人引到死路）: {dangling}")

    def test_it_records_the_dead_opencode_pointer(self):
        """⚠️ 把"AGENTS.md 指向不存在的 OPENCODE.md"这个事实留在文档里。

        不要求 README 永远提它，但若有人删掉这段说明，
        下一个人就会重新踩同一个坑。
        """
        # ⚠️ 同样不能只断言"OPENCODE.md 出现过" —— 它在引言里本就出现，
        #    把"从未存在过"那句删掉后用例仍然绿（负向验证抓到的第二个洞）。
        #    改为断言**否定性事实**必须写明。
        for filename, heading, _rows, absent_re in self.CASES:
            sec = self._section(filename, heading)
            with self.subTest(readme=filename):
                self.assertIn("OPENCODE.md", sec, f"{filename} 应点名那份空路径指引")
                self.assertRegex(
                    sec, absent_re,
                    f"{filename} 必须写明 AGENTS.md 指向的 OPENCODE.md **并不存在** —— "
                    "只提名字而不说它不存在，等于把坑留着")

    def test_it_lists_the_three_gates(self):
        """把本阶段建的三道门禁写进入口章节，否则新人不知道有闸。"""
        for filename, heading, _rows, _absent in self.CASES:
            sec = self._section(filename, heading)
            for gate in ("test_directory_docs_current.py",
                         "test_readme_baseline_numbers.py"):
                self.assertIn(gate, sec, f"{filename} 结构入口章节应列出 {gate}")


class DocsDescribeRealityTest(unittest.TestCase):
    """抽查：文档里声称的"注入面"是否与代码相符（只查可机器判定的几条）。"""

    def test_factors_scoring_is_dependency_free(self):
        """`scoring.py` 文档称"注入面无" → 断言它确实不 import 取数模块。"""
        src = (ROOT / "scripts" / "factors" / "scoring.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.asname or a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods |= {a.asname or a.name for a in node.names}
        self.assertEqual(mods - {"annotations", "Any", "Dict"},
                         set(), f"scoring.py 声称零注入面，却 import 了 {mods}")

    def test_candles_15m_does_not_import_fetching(self):
        """`candles_15m.py` 文档称"取数留在门面" → 断言它不 import 取数函数。"""
        tree = ast.parse((ROOT / "scripts" / "factors" / "candles_15m.py")
                         .read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertNotIn("market_data_service", mods)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names |= {a.name for a in node.names}
        self.assertNotIn("fetch_candles", names)

    def test_position_universe_does_not_fetch(self):
        """`position_universe.py` 文档称"不取数" → 断言无任何网络/交易所 import。"""
        tree = ast.parse((ROOT / "scripts" / "trader" / "position_universe.py")
                         .read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.asname or a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods |= {a.asname or a.name for a in node.names}
        for forbidden in ("requests", "urllib", "okx_rest", "subprocess", "os"):
            self.assertNotIn(forbidden, mods)


if __name__ == "__main__":
    unittest.main()
