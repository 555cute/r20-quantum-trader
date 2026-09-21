"""秒/毫秒分界与换算只能有**一处实现**（第一百八十八刀）。

## 为什么

本仓到处要把"可能是秒、也可能是毫秒"的时间戳归一。判据（`>= 1e11` ⇒ 毫秒）必须**只有一处**：

- ⚠️ 分界写错（比如 `1e9`）会把 epoch **秒**（~1.79e9）误判成毫秒再除以 1000 ⇒
  "还剩 7 天"算成"已过期"（本仓真实踩过，`venue_protection` 第一版）；
- 判据写**四遍**（本刀实测：`time_utils.parse_beijing`、`multi_venue`、
  `venue_protection` 的函数版与内联版）⇒ 改一处忘三处，迟早漂移。

本刀把唯一实现放进 `r20_backend/time_utils.py`（**无仓内依赖**的中性时间模块，
两侧都能 import），其余三处改为委派；并钉住"数值常量 `1e11` 不得出现在别处"。

## 本门

1. **唯一性**：AST 扫描 `scripts/` + `r20_backend/`，数值常量 `≈1e11` 只允许出现在
   `r20_backend/time_utils.py`（注释/文档字符串不算 —— 门扫的是**数值常量**）；
2. **委派**：三处原写法必须 import 唯一实现（防有人再内联回去）；
3. **行为**：秒输入原样、毫秒输入换算、`None` 原样（秒↔毫秒两个方向）；
4. 非空自检 + 牙齿（分界写错必须被行为用例抓到）。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("scripts", "r20_backend", "r20_gateway", "plugins")
CANONICAL = "r20_backend/time_utils.py"


def _iter_py():
    for d in SCAN_DIRS:
        for path in sorted((ROOT / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def find_ms_threshold_literals(source: str):
    """返回源码里数值常量 ≈1e11 的 (行号, 值) 列表（注释与字符串不算）。"""
    out = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            if abs(float(node.value) - 1e11) < 1.0:
                out.append((node.lineno, node.value))
    return out


class EpochUnitConverterTest(unittest.TestCase):
    def test_threshold_literal_lives_in_one_place(self):
        offenders = []
        for path in _iter_py():
            rel = str(path.relative_to(ROOT))
            if rel == CANONICAL:
                continue
            for line, value in find_ms_threshold_literals(path.read_text(encoding="utf-8")):
                offenders.append(f"{rel}:{line} 值={value!r}")
        self.assertEqual(offenders, [], "秒/毫秒分界常量出现在唯一实现之外（改一处忘三处）：\n"
                                        + "\n".join(offenders))

    def test_canonical_module_actually_defines_it(self):
        src = (ROOT / CANONICAL).read_text(encoding="utf-8")
        self.assertIn("EPOCH_MS_THRESHOLD = 1e11", src)
        self.assertEqual(len(find_ms_threshold_literals(src)), 1,
                         "唯一实现里应当**恰好**一个分界常量")

    def test_former_spellings_delegate(self):
        """两处**消费方**必须 import 唯一实现（唯一实现自己不 import 自己）。"""
        for rel in ("scripts/trader/venue_protection.py",
                    "r20_backend/dashboard_payload/multi_venue.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("time_utils", src, f"{rel} 没有委派给唯一实现（又写了一份分界）")
            self.assertIn("to_seconds" if "venue_protection" in rel else "to_millis", src,
                          f"{rel} 虽然出现 time_utils 字样，但没用换算函数")

    def test_conversion_behaviour_both_units(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from r20_backend.time_utils import to_millis, to_seconds
        seconds, millis = 1789953468, 1789953467788
        self.assertEqual(to_seconds(seconds), seconds, "秒输入被误除以 1000（会把 7 天算成已过期）")
        self.assertAlmostEqual(to_seconds(millis), millis / 1000.0, places=3)
        self.assertEqual(to_millis(seconds), seconds * 1000)
        self.assertEqual(to_millis(millis), millis, "毫秒输入被再乘 1000（双重放大）")
        self.assertIsNone(to_seconds(None))
        self.assertIsNone(to_millis(None))

    def test_teeth_on_wrong_threshold(self):
        """牙齿：分界改成 1e9（本仓真实事故写法）必须被判红。"""
        import sys
        sys.path.insert(0, str(ROOT))
        from r20_backend import time_utils
        original = time_utils.EPOCH_MS_THRESHOLD
        try:
            time_utils.EPOCH_MS_THRESHOLD = 1e9
            self.assertNotEqual(time_utils.to_seconds(1789953468), 1789953468,
                                "分界写成 1e9 时秒输入未被误算 ⇒ 本门的判据无效")
        finally:
            time_utils.EPOCH_MS_THRESHOLD = original

    def test_scan_is_not_vacuous(self):
        self.assertTrue(find_ms_threshold_literals("X = 1e11\n"), "扫描连样本都抓不到")
        total = sum(len(find_ms_threshold_literals(p.read_text(encoding="utf-8"))) for p in _iter_py())
        self.assertGreaterEqual(total, 1, "全仓一个分界常量都扫不到 ⇒ 门与实现脱节")


if __name__ == "__main__":
    unittest.main(verbosity=2)
