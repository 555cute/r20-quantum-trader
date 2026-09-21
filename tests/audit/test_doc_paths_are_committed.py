"""文档引用的路径必须**已被 git 跟踪**（第一百五十五刀）。

## 为什么（这是本会话一次真实事故的防复发门）

`.gitignore` 里曾有一行裸 `core`（本意是忽略崩溃转储），git 的"路径组件"匹配规则让它
连带忽略了**任何**名为 core 的目录 —— 包括 `tests/core/`。后果不是报错，而是：

- 本会话新增的 5 个门文件（`test_live_artifact_shape.py`、`test_day_key_uses_beijing_tz.py`、
  `test_numeric_coercion_single_source.py`、`test_timestamp_unit_convention.py`、
  `test_atomic_write_invariant.py`）**一直没被提交**；
- 而**本地全量测试照常全绿** —— 因为 pytest 跑的是**工作树**，那 5 个文件在工作树里确实存在；
- 甚至有一个提交（`e47ebc4`）**只改了文档**，而提交信息宣称新增了那个门：
  文档锚点指向一个"磁盘上有、仓库里没有"的文件。

也就是说：**"文档里写了、门也绿了"不能证明"仓库里真有这个东西"**。
本门把"引用即提交"变成机器检查：文档锚点表里出现的路径（代码位置列与门列），
必须①存在 ②**被 git 跟踪**。

（它与 `test_failure_semantics_doc.py` 互补：那边查"存在且可加载"，这边查"**在仓库里**"。）
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "FAILURE_SEMANTICS.md"
PATH_TOKEN = re.compile(r"`([^`]+)`")


def _git_tracked(paths: "list[str]") -> "set[str]":
    """返回**未**被 git 跟踪的路径。git 不可用时返回空集（不误报）。"""
    untracked: "set[str]" = set()
    for path in paths:
        try:
            done = subprocess.run(  # noqa: S603
                ["git", "ls-files", "--error-unmatch", path],  # noqa: S607
                cwd=str(ROOT), capture_output=True, text=True, timeout=10)
        except Exception:
            return set()
        if done.returncode != 0:
            untracked.add(path)
    return untracked


def referenced_paths(doc_text: str) -> "list[str]":
    """从锚点表里抽出被引用的仓库相对路径（去 `::Case` 后缀）。"""
    paths: "list[str]" = []
    for line in doc_text.splitlines():
        if not line.strip().startswith("|"):
            continue
        for token in PATH_TOKEN.findall(line):
            candidate = token.split("::", 1)[0].strip()
            if "/" in candidate and candidate.endswith(".py"):
                paths.append(candidate)
    return sorted(set(paths))


class DocPathsAreCommittedTest(unittest.TestCase):
    def test_the_doc_references_paths_at_all(self):
        """非空自检：锚点表若一个路径都抽不到，本门就是装饰。"""
        self.assertGreaterEqual(len(referenced_paths(DOC.read_text(encoding="utf-8"))), 10,
                                "锚点表里抽不到足够路径 ⇒ 抽取逻辑失效")

    def test_every_referenced_path_exists_and_is_tracked(self):
        paths = referenced_paths(DOC.read_text(encoding="utf-8"))
        missing = [p for p in paths if not (ROOT / p).exists()]
        self.assertEqual(missing, [], f"文档引用了不存在的路径：{missing}")
        untracked = _git_tracked(paths)
        self.assertEqual(untracked, set(),
                         "文档引用了**未被 git 跟踪**的文件（磁盘上可能有，但仓库里没有）："
                         f"{sorted(untracked)}\n"
                         "常见原因：被 .gitignore 的过宽规则命中（本仓 2026-09 就发生过 "
                         "`core` 命中 tests/core/ 的事故）")

    def test_gate_has_teeth(self):
        """有牙齿自检：造一个真实存在但未跟踪的文件，必须被判为未跟踪。"""
        import tempfile
        with tempfile.TemporaryDirectory(dir=str(ROOT), prefix=".untracked-probe-") as td:
            probe = Path(td) / "probe_gate.py"
            probe.write_text("# 存在但未跟踪\n", encoding="utf-8")
            rel = str(probe.relative_to(ROOT))
            self.assertIn(rel, _git_tracked([rel]),
                          "未跟踪文件没被判出来 ⇒ 本门没有牙齿")
            # 对照：一个确实被跟踪的文件不得误报
            tracked_rel = "docs/FAILURE_SEMANTICS.md"
            self.assertNotIn(tracked_rel, _git_tracked([tracked_rel]))


#: 全仓文档扫描范围（含仓库根的两份说明）
ALL_DOCS = (ROOT / "docs", ROOT / "README.md", ROOT / "AGENTS.md")
#: **源码树**前缀：这些目录下的文件"磁盘上存在"就必须"仓库里有"。
#: 运行态/本地目录（`data/`、`plan_local/`、`promo_local/`、`.archive/`）**不在范围内** ——
#: 它们被有意忽略（审计确认：文档引用的 6 个未跟踪路径全属此类），
#: 把它们算进来只会制造噪音，反而掩盖真正的信号。
SOURCE_ROOTS = ("tests/", "scripts/", "r20_backend/", "r20_gateway/", "plugins/", "frontend/src/", "docs/")


def broad_source_references(doc_text: str) -> "list[str]":
    """从任意文档里抽取**源码树**路径引用（去 `::Case` 后缀、去行号）。"""
    out: "list[str]" = []
    for token in PATH_TOKEN.findall(doc_text):
        candidate = token.split("::", 1)[0].strip()
        if "/" in candidate and candidate.startswith(SOURCE_ROOTS):
            out.append(candidate)
    return sorted(set(out))


class BroadDocReferencesTest(unittest.TestCase):
    """**所有**文档：源码树里"存在即应被跟踪"（第一百五十六刀）。

    为什么扩到全仓文档：上一刀的根因（`.gitignore` 裸 `core` 吞掉 `tests/core/`）不只威胁
    失败语义手册——任何文档引用的源码路径都可能"磁盘上有、仓库里没有"。范围限定在源码树，
    是因为运行态数据（`data/*.json`）被文档引用但**本就该**忽略（审计已确认）。
    """

    def _all_refs(self):
        refs: "dict[str, list[str]]" = {}
        for doc in ALL_DOCS:
            if doc.is_dir():
                for f in sorted(doc.rglob("*.md")):
                    for r in broad_source_references(f.read_text(encoding="utf-8")):
                        refs.setdefault(r, []).append(str(f.relative_to(ROOT)))
            elif doc.exists():
                for r in broad_source_references(doc.read_text(encoding="utf-8")):
                    refs.setdefault(r, []).append(doc.name)
        return refs

    def test_scan_is_not_vacuous(self):
        refs = self._all_refs()
        self.assertGreaterEqual(len(refs), 20,
                                f"全仓文档只抽到 {len(refs)} 个源码路径引用 ⇒ 抽取逻辑失效")

    def test_existing_source_paths_are_tracked(self):
        refs = self._all_refs()
        on_disk = [r for r in refs if (ROOT / r).exists()]
        self.assertGreaterEqual(len(on_disk), 10, "磁盘上存在的源码引用太少 ⇒ 检查无意义")
        untracked = _git_tracked(on_disk)
        self.assertEqual(
            untracked, set(),
            "以下源码路径**磁盘上存在但未被 git 跟踪**（文档引用它们，仓库里却没有）：\n"
            + "\n".join(f"  {p}   ← {refs[p][:2]}" for p in sorted(untracked))
            + "\n常见原因：.gitignore 过宽（如裸 `core` 命中 `tests/core/`）")

    def test_broad_check_has_teeth(self):
        """牙齿：源码树里造一个未跟踪文件，宽扫描必须报出来。"""
        import tempfile
        with tempfile.TemporaryDirectory(dir=str(ROOT / "tests"), prefix=".untracked-wide-") as td:
            probe = Path(td) / "probe.py"
            probe.write_text("# 存在但未跟踪\n", encoding="utf-8")
            rel = str(probe.relative_to(ROOT))
            self.assertIn(rel, _git_tracked([rel]), "宽扫描没有牙齿")

if __name__ == "__main__":
    unittest.main(verbosity=2)