"""失败语义手册的**防腐烂门**（第 44 刀）。

`docs/FAILURE_SEMANTICS.md` 的价值全在"绑定真实代码与门禁"——一旦锚点文件/用例改名，
文档就变成谎话。本门解析文档里的机器可读锚点块（`<!-- anchors:begin/end -->`），
逐行断言：

1. 代码锚点路径存在；
2. 门禁锚点**文件**存在，且其中**确实含有**该行声明的用例名（防改名后文档漂移）；
3. 三条方向纪律的标题仍在（防被静默删掉）。

⚠️ 本门自身也带失效自检：锚点行数不得少于下限，否则"解析不到 ⇒ 恒过"。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "FAILURE_SEMANTICS.md"
MARK_BEGIN = "<!-- anchors:begin -->"
MARK_END = "<!-- anchors:end -->"


def _anchor_rows():
    text = DOC.read_text(encoding="utf-8")
    assert MARK_BEGIN in text and MARK_END in text, "锚点块标记丢了"
    body = text.split(MARK_BEGIN, 1)[1].split(MARK_END, 1)[0]
    rows = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("|---") or "输入" in line and "读不到时" in line:
            continue
        # 去 markdown 代码跨度反引号（文档里路径写成 `...` 更好读）
        cells = [c.strip().strip("`").strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        rows.append(cells)
    return rows


class FailureSemanticsDocGateTest(unittest.TestCase):
    def test_doc_exists_and_keeps_the_three_doctrines(self):
        text = DOC.read_text(encoding="utf-8")
        for doctrine in ("读不到 ≠ 没有", "不可判定 ≠ 安全", "日志不说谎"):
            with self.subTest(doctrine=doctrine):
                self.assertIn(doctrine, text, f"方向纪律被删了：{doctrine}")

    @staticmethod
    def _assert_case_is_loadable(gate_path: str, case: str) -> None:
        """锚点必须指向**可加载的真实用例**（不是注释/字符串里的同名文本）。

        实现：把路径转成模块名 import 进来，再断言
        - `path::SomeCase` ⇒ 模块里有个 `unittest.TestCase` 子类叫这个名字；
        - `path::test_something` ⇒ 某个 TestCase 子类确实有这个可调用方法。

        为什么不用"最近验证提交"这类**时效列**：那类列会腐烂，且"多久没改动"
        不等于"多久没验证"；真正要保证的性质是"这条绑定指向的用例仍然真实存在且可执行"。
        """
        import importlib
        import unittest as _ut
        mod_name = gate_path[:-3].replace("/", ".") if gate_path.endswith(".py") else gate_path
        mod = importlib.import_module(mod_name)
        if case.startswith("test_"):
            owners = [v for v in vars(mod).values()
                      if isinstance(v, type) and issubclass(v, _ut.TestCase)
                      and callable(getattr(v, case, None))]
            assert owners, (f"{mod_name} 里没有任何 TestCase 定义 {case}() "
                            f"⇒ 锚点指向的用例不可执行：{gate_path}::{case}")
            return
        obj = getattr(mod, case, None)
        assert isinstance(obj, type) and issubclass(obj, _ut.TestCase), (
            f"{mod_name}.{case} 不是可加载的 unittest.TestCase "
            f"⇒ 锚点可能只写在注释/字符串里：{gate_path}::{case}")

    def test_every_row_states_a_sanctioned_direction(self):
        """每一行的「方向」必须出自**受认可词表**。

        防的是「悄悄把某行改成静默忽略」：本手册只认可五类处置 ——
        **保留**（不可逆动作宁可不做）、**不做**（fail-closed）、**披露**（继续但说清楚）、
        **吼**（可用性优先但必须告警）、**防**（从源头消灭坏状态）。
        """
        allowed = ("保留", "不做", "披露", "吼", "防")
        rows = _anchor_rows()
        self.assertGreaterEqual(len(rows), 10, "判据失效：锚点行没解析到")
        for cells in rows:
            direction = cells[2]
            with self.subTest(input=cells[0]):
                self.assertTrue(any(w in direction for w in allowed),
                                f"方向列不在受认可词表 {allowed} 内：{direction!r}")
                for banned in ("忽略", "静默", "照常"):
                    self.assertNotIn(banned, direction,
                                     f"方向列出现禁止措辞 {banned!r}：{direction!r}")

    def test_anchors_still_point_at_real_code_and_tests(self):
        rows = _anchor_rows()
        self.assertGreaterEqual(len(rows), 10,
                                f"判据失效：只解析到 {len(rows)} 行锚点（块被改坏了？）")
        for cells in rows:
            _input, _behavior, _direction, code, gate = cells[:5]
            gate_path, _, case = gate.partition("::")
            with self.subTest(input=_input):
                self.assertTrue((ROOT / code).exists(),
                                f"代码锚点不存在（文档漂移）：{code}")
                gp = ROOT / gate_path
                self.assertTrue(gp.exists(), f"门禁锚点不存在（文档漂移）：{gate_path}")
                self.assertTrue(case, f"锚点缺用例名（形如 path::Case）：{gate}")
                gp_text = gp.read_text(encoding="utf-8")
                # ① 先做**声明**判定（快速失败 + 报错更贴近文本）：
                #    ⚠️ 不能用 `assertIn`：首版就是这样，把 `X` 改名成 `XRened` 时
                #    仍然通过（子串命中），负例当场暴露了这个假阴性。
                self.assertRegex(
                    gp_text, rf"\b(?:class|def)\s+{re.escape(case)}\b",
                    f"门禁用例已改名/删除（锚点指向的声明不存在）：{gate}")
                # ② 再要求它是**真的能加载的用例**：注释里写 `# class X:` 也能骗过
                #    正则（文本匹配的天然弱点），但骗不过 import + 属性检查。
                self._assert_case_is_loadable(gate_path, case)


if __name__ == "__main__":
    unittest.main(verbosity=2)
