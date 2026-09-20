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
                # ⚠️ 必须是**真实声明**（`class X` / `def X`），不能用 in 判定：
                # 本门首版用 `assertIn` ⇒ 把 `X` 改名成 `XRened` 时**仍然通过**（子串命中），
                # 负例当场暴露了这个假阴性。
                self.assertRegex(
                    gp_text, rf"\b(?:class|def)\s+{re.escape(case)}\b",
                    f"门禁用例已改名/删除（锚点指向的声明不存在）：{gate}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
