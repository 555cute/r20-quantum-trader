"""指标名的键**只能有一种拼法**（第一百八十四刀）。

## 缺陷（同族：同一语义写多遍 ⇒ 漂移）

`scripts/market_data_service.py` 里原本有**三种**指标名规范化：

| 位置 | 写法 | 例子（输入 `EMA-20`）|
|---|---|---|
| MCP 批量分支 | `ind.upper().replace("-","")` | `EMA20` |
| REST 逐指标兜底 | `ind.upper()`（**保留横杠**）| `EMA-20` |
| 本地计算 / 单指标 | `ind.upper().replace("-","").replace("_","")` | `EMA20` |

后果有两个方向：

- **读者取不到**：生产者按一种拼法存、消费者按另一种取 ⇒ 静默"没有"（读不到≠没有）；
- **`missing` 判定失明**：它按去横杠比较，于是 REST 已存 `"EMA-20"` 时仍算缺失 ⇒
  每轮白算一遍本地指标（本门实测：修复前会多调一次 `_local_math_indicators`），
  且可能把同一指标写成两个键。

今天线上消费方用的名字（`adx`/`kdj`/`bbwidth`/`cmf`）都不带分隔符 ⇒ 属**潜在**缺陷，
本门把它钉死，避免新指标名（`EMA-20`、`BB_WIDTH` 之类）一上线就踩。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MDS = ROOT / "scripts" / "market_data_service.py"


def adhoc_normalization_offenders(src: str, helper_name: str = "_indicator_key") -> list:
    """源码里"第二处指标名规范化"的位置（第一百八十四刀的判据，抽成函数以便**造牙齿**）。

    规则：除了 `helper_name` 自身那几行，任何同时出现 `.upper()` 与 `.replace(` 的调用
    都是同义异写的第二份实现 ⇒ 迟早漂移。
    """
    import ast as _ast
    tree = _ast.parse(src)
    helper = next((n for n in _ast.walk(tree)
                   if isinstance(n, _ast.FunctionDef) and n.name == helper_name), None)
    if helper is None:
        return ["找不到 %s（门已过期）" % helper_name]
    helper_lines = set(range(helper.lineno, (helper.end_lineno or helper.lineno) + 1))
    offenders = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        text = _ast.unparse(node)
        if ".upper()" in text and ".replace(" in text and getattr(node, "lineno", 0) not in helper_lines:
            offenders.append("第 %d 行: %s" % (node.lineno, text[:80]))
    return offenders


class IndicatorKeySingleSpellingTest(unittest.TestCase):
    def setUp(self):
        from scripts import market_data_service as m
        self.m = m
        self._orig = (m._public_post, m._local_math_indicators, m.fetch_single_indicator)

    def tearDown(self):
        m = self.m
        m._public_post, m._local_math_indicators, m.fetch_single_indicator = self._orig

    def test_canonical_key_cases(self):
        k = self.m._indicator_key
        self.assertEqual(k("adx"), "ADX")
        self.assertEqual(k("EMA-20"), "EMA20")
        self.assertEqual(k("BB_WIDTH"), "BBWIDTH")
        self.assertEqual(k("ema20"), "EMA20")
        self.assertEqual(k(None), "")
        self.assertEqual(k("EMA-20"), k("ema_20"), "同义指标必须同键")

    def test_rest_fallback_stores_the_canonical_key_and_does_not_recompute(self):
        m = self.m
        local_calls = []
        m._public_post = lambda *a, **k: None                     # MCP 不可用
        m.fetch_single_indicator = lambda inst, ind, bar=None, timeout=None: {"value": 1}
        m._local_math_indicators = lambda inst, missing, bar: local_calls.append(list(missing)) or {}
        out = m.fetch_indicators_batch("BTC-USDT-SWAP", ["EMA-20"], bar="1H")
        self.assertEqual(list(out), ["EMA20"], "REST 兜底仍按原拼法存键 ⇒ 消费方可能取不到")
        self.assertEqual(local_calls, [],
                         "已取到的指标又被判为缺失 ⇒ 每轮白算本地指标（missing 判定失明）")

    def test_mcp_payload_key_is_normalized_too(self):
        m = self.m
        payload = {"data": [{"data": [{"timeframes": {"1H": {"indicators": {
            "EMA20": [{"values": {"ema20": "123.4"}}]}}}}]}]}
        m._public_post = lambda *a, **k: payload
        out = m.fetch_indicators_batch("BTC-USDT-SWAP", ["EMA-20"], bar="1H")
        self.assertEqual(list(out), ["EMA20"])
        self.assertEqual(out["EMA20"].get("ema20"), "123.4")

    def test_no_adhoc_normalization_outside_the_helper(self):
        """源码级：该模块里**只允许** `_indicator_key` 一处做规范化。"""
        offenders = adhoc_normalization_offenders(MDS.read_text(encoding="utf-8"))
        self.assertEqual(offenders, [], "出现第二处指标名规范化（同义异写会漂移）：\n"
                                        + "\n".join(offenders))

    def test_teeth_catch_a_second_normalization(self):
        """牙齿（第二百零二刀）：**造一份第二处实现**，判据必须抓到它。

        没有这一条，"判据能不能咬"就只能靠人读源码相信它 —— 而那正是本仓反复吃过的亏。
        """
        bad = (
            "def _indicator_key(name):\n"
            "    return str(name).upper().replace('-', '').replace('_', '')\n"
            "\n"
            "def other_place(ind):\n"
            "    return ind.upper().replace('-', '')   # 第二处实现 ⇒ 必须被抓\n"
        )
        offenders = adhoc_normalization_offenders(bad)
        self.assertEqual(len(offenders), 1, f"第二处规范化没被抓到：{offenders}")
        # 断言**具体行号**：`bad` 里那处调用在第 5 行（第 4 行才进 other_place）。
        # ⚠️ 第一版这里写的是 `assertIn("other_place", " ".join(offenders) + " other_place")`
        # —— 自己把要断言的关键字拼进了被检查的字符串，**恒真**（假断言，本刀自查抓出）。
        self.assertIn("第 5 行", offenders[0], f"抓到的不是那一行：{offenders[0]}")

        clean = (
            "def _indicator_key(name):\n"
            "    return str(name).upper().replace('-', '').replace('_', '')\n"
            "\n"
            "def other_place(ind):\n"
            "    return _indicator_key(ind)   # 委派给唯一实现 ⇒ 干净\n"
        )
        self.assertEqual(adhoc_normalization_offenders(clean), [], "干净源码被误报")


if __name__ == "__main__":
    unittest.main(verbosity=2)
