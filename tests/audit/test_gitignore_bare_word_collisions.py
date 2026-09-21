"""`.gitignore` 的**裸词**模式不得吞掉同名源码目录（第一百五十七刀）。

## 为什么（这是"5 个门长期未提交"事故的**根因**检查）

git 忽略规则按**路径组件**匹配。一行不带 `/`、不带 `.`、不带 `*` 的**裸词**（如 `core`）
会匹配**任意层级**上名字相同的文件或目录 —— 本仓就因此静默忽略了整个 `tests/core/`：
本会话 5 个新门长期未被提交，而本地全量测试照常全绿（pytest 读工作树）。

按后缀/路径写忽略规则不会造成这种事故（`*.pyc`、`data/*.json`、`/core` 都明确）。
故本门只做一件事：**若某个裸词模式与源码树里真实存在的目录同名 ⇒ 翻红**，
并提示改成锚定形态（`/core`）或写明路径。

（有意忽略的"agent 运行态"文件如 `AGENTS.md`、`SOUL.md` 含 `.` ⇒ 不是裸词，不在本门范围；
显式写成 `agent/`、`.agents/` 的目录模式是**有意**的约定，本门只报"裸词且命中真实源码目录"。）
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITIGNORE = ROOT / ".gitignore"
#: 源码树：这些目录下"磁盘上存在"的东西都属于项目资产，被静默忽略就是事故。
SOURCE_ROOTS = ("tests", "scripts", "r20_backend", "r20_gateway", "plugins", "docs", "frontend/src")
BARE_WORD = re.compile(r"^[A-Za-z0-9_-]+$")     # 无 /、无 .、无 *、无 ! ⇒ 裸词


def bare_word_patterns(text: str) -> "list[str]":
    """抽出 `.gitignore` 里的裸词模式（忽略注释与空行）。"""
    out: "list[str]" = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if BARE_WORD.match(line):
            out.append(line)
    return out


def colliding_source_dirs(text: str, root: Path = ROOT) -> "list[tuple[str, str]]":
    """返回 `(裸词模式, 撞上的源码目录相对路径)`。"""
    hits: "list[tuple[str, str]]" = []
    for pattern in bare_word_patterns(text):
        for base in SOURCE_ROOTS:
            base_dir = root / base
            if not base_dir.is_dir():
                continue
            for candidate in base_dir.rglob(pattern):
                if candidate.is_dir():
                    hits.append((pattern, str(candidate.relative_to(root))))
    return sorted(set(hits))


class GitignoreBareWordCollisionTest(unittest.TestCase):
    def test_pattern_extraction_is_not_vacuous(self):
        """抽取自检：裸词**天然很少**（多数规则用了 `/`、`.` 或后缀），故这里只要求 ≥1，
        真正的"抓得住"证据由 `test_gate_has_teeth` 用合成输入给出。

        ⚠️ 本仓 2026-09 修掉 `core` 后，剩下的裸词只有 `id_rsa`（有意忽略私钥文件）。
        若有朝一日裸词变多，本用例仍会通过 —— 它守的是"抽取器还认得裸词"，不是数量。
        """
        patterns = bare_word_patterns(GITIGNORE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(patterns), 1,
                                "一个裸词都抽不到 ⇒ 抽取逻辑失效（或 .gitignore 已无裸词，"
                                "那也应显式调整本用例）")

    def test_no_bare_word_swallows_a_source_directory(self):
        hits = colliding_source_dirs(GITIGNORE.read_text(encoding="utf-8"))
        self.assertEqual(
            hits, [],
            "`.gitignore` 的裸词模式正在静默忽略真实源码目录（git 按路径组件匹配）：\n"
            + "\n".join(f"  裸词 `{p}` 命中 {d}" for p, d in hits)
            + "\n修法：改成锚定形态（如 `/core`）或写明具体路径")

    def test_gate_has_teeth(self):
        """牙齿：合成一份含裸词 `core` + 真实 `tests/core` 的输入，必须报出。"""
        import tempfile
        with tempfile.TemporaryDirectory(dir=str(ROOT / "tests"), prefix=".collision-probe-") as td:
            probe_dir = Path(td) / "core"          # 造一个名为 core 的源码目录
            probe_dir.mkdir()
            (probe_dir / "dummy.py").write_text("# x\n", encoding="utf-8")
            rel_base = Path(td).relative_to(ROOT)
            hits = colliding_source_dirs("core\n*.pyc\n", root=ROOT)
            # 真实 tests/core 本来就存在 ⇒ 裸词 `core` 必须命中
            self.assertTrue(hits, "裸词 `core` 命中 tests/core 却未报出 ⇒ 本门没有牙齿")
            self.assertTrue(any(d.endswith("/core") for _, d in hits))
            # 反例：锚定形态不得误报
            self.assertEqual(colliding_source_dirs("/core\ncore.*\n"), [])
            # 清理探针（临时目录由 TemporaryDirectory 负责，这里只断言其存在过）
            self.assertTrue(rel_base.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
