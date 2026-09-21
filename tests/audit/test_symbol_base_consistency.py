"""跨模块"币种基名"必须**同输入同输出**（第一百八十五刀）。

## 背景

本仓有**四处**在回答"这串合约/持仓符号的币种是什么"：

| 实现 | 位置 | 用途 |
|---|---|---|
| `canonical_base` | `r20_backend/exchanges/base.py` | 面板/因子/符号归一（共用最多）|
| `_canonical_base_name`（= `canonical_base` 的别名）| `scripts/ai_brain_trader.py` | 池映射 |
| `_base_of` | `scripts/trader/venue_protection.py` | 保护腿覆盖判定 |
| `leg_base` | `scripts/trader/venue_protection.py` | 腿归属（第一百八十九刀由 `_leg_symbol` 更名）|

修复前它们在真机写法上**互相矛盾**（实测）：

| 输入 | `canonical_base` | `_base_of` | `leg_base` |
|---|---|---|---|
| `GATE:BTC_USDT` | **`GATE:BTC`** | `BTC` | **`GATE:BTC`** |
| `BTC_USDC` | **`BTCUSDC`** | `BTC` | `BTC` |
| `BTCUSDT` | `BTC` | **`BTCUSDT`** | `BTC` |

两个方向的后果：**按币种匹配静默失配**（合成 id 的币种被当成 `GATE:BTC`）、
**计价币被并进币种**（USDC/币本位写法）。`canonical_base` 的 docstring 本就承诺
"任意写法 → 裸币种" ⇒ 属实现违背契约。

本门钉三件事：
1. 三处提取函数在真机写法表上**一致**（委派给唯一实现）；
2. **显式期望值**（防止"三处一起退回去"也算通过）；
3. `canonical_position_inst_id` 的**刻意契约**不被池查找吞掉：日期合约/币本位**原样保留**
   （`canonical_base` 修好后，`BTC-USD-SWAP` 的币种是 `BTC`，若不加"标准形态"闸就会被映射到
   池内 `BTC-USDT-SWAP` —— **换了下单标的**）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 真机出现过的写法（含合成 id、无分隔符、非 USDT 计价、币本位、日期合约）
SPELLINGS = [
    "BTC", "btc", "BTC_USDT", "BTCUSDT", "BTC-USDT", "BTC-USDT-SWAP",
    "GATE:BTC_USDT", "BINANCE:BTCUSDT", "ETH_USDT", "XRPUSDT",
    "1000PEPE_USDT", "1000PEPEUSDT", "WBTC_USDT", "SOL_USDT", "UNI_USDT",
    "BTC_USDC", "BTC-USD-SWAP", "",
    ("BTC_USDT", "BTC"),
    ("BTCUSDT", "BTC"),
    ("BTC-USDT-SWAP", "BTC"),
    ("GATE:BTC_USDT", "BTC"),
    ("BINANCE:BTCUSDT", "BTC"),
    ("1000PEPEUSDT", "1000PEPE"),
    ("WBTC_USDT", "WBTC"),
    ("BTC_USDC", "BTC"),
    ("BTC-USD-SWAP", "BTC"),
]


def compare_extractors(extractors, spellings) -> list:
    """比对多个"币种基名提取器"在真机写法表上是否**一致**（判据抽成函数以便造牙齿）。

    `extractors` = [(名字, 函数)]；返回不一致的描述列表（空 = 全部一致）。
    """
    disagreements = []
    for raw in spellings:
        if isinstance(raw, tuple):        # 期望值行（由 test_documented_expectations 管）
            continue
        got = {}
        for name, fn in extractors:
            try:
                got[name] = fn(raw)
            except Exception as exc:      # 提取器抛错也算"不一致"，绝不静默跳过
                got[name] = f"<{type(exc).__name__}>"
        values = set(got.values())
        if len(values) > 1:
            disagreements.append(f"{raw!r}: " + " ".join(f"{k}={v!r}" for k, v in got.items()))
    return disagreements


class BaseNameConsistencyTest(unittest.TestCase):
    def test_three_extractors_agree_on_real_spellings(self):
        from r20_backend.exchanges.base import canonical_base
        from r20_backend.execution.own_records import canonical_inst
        from scripts.trader.venue_protection import _base_of, leg_base
        disagreements = compare_extractors(
            [("canonical_base", canonical_base),
             ("_base_of", _base_of),
             ("leg_base", lambda raw: leg_base({"symbol": raw, "inst_id": raw})),
             ("canonical_inst", canonical_inst)],
            SPELLINGS)
        self.assertEqual(disagreements, [], "同义异写（币种基名提取不一致）：\n"
                                            + "\n".join(disagreements))

    def test_teeth_catch_one_drifting_extractor(self):
        """牙齿（第二百零二刀）：四个提取器里**只要有一个**漂了，判据必须抓到。

        这条牙齿同时是"为什么要有本门"的活证据 —— 漂移的表现就是**不一致**，
        而"一致地错"由 `test_documented_expectations` 的显式期望值兜住。
        """
        def good(raw):
            return str(raw or "").split("-")[0].split("_")[0].upper()

        def drifting(raw):                      # 少切一刀：把 `BTC_USDT` 留成 `BTC_USDT`
            return str(raw or "").split("-")[0].upper()

        sample = ["BTC_USDT", "BTC-USDT"]
        self.assertEqual(compare_extractors([("good", good), ("drifting", drifting)], sample).__len__(),
                         1, "漂移必须被抓到（BTC_USDT 上两者不一致）")
        self.assertEqual(compare_extractors([("good", good), ("good2", good)], sample), [],
                         "两个一致的提取器不该被判不一致")

    def test_teeth_treat_extractor_exception_as_disagreement(self):
        """提取器抛错也要算"不一致"（读不到 ≠ 没有：不得静默跳过该写法）。"""
        def boom(_raw):
            raise ValueError("坏提取器")

        out = compare_extractors([("ok", lambda r: "BTC"), ("boom", boom)], ["BTC_USDT"])
        self.assertEqual(len(out), 1)
        self.assertIn("<ValueError>", out[0])

    def test_documented_expectations(self):
        """显式期望值：三处**一起**回退也会被抓（避免"一致地错"）。"""
        from r20_backend.exchanges.base import canonical_base
        from scripts.trader.venue_protection import _base_of
        for raw, want in [s for s in SPELLINGS if isinstance(s, tuple)]:
            self.assertEqual(canonical_base(raw), want, f"canonical_base({raw!r}) 应得 {want!r}")
            self.assertEqual(_base_of(raw), want, f"_base_of({raw!r}) 应得 {want!r}")

    def test_extractors_delegate_to_the_single_implementation(self):
        """源码级：这两处**不得**再自己拼一份归一（就是漂移的根源）。"""
        src = (ROOT / "scripts" / "trader" / "venue_protection.py").read_text(encoding="utf-8")
        for fn in ("_base_of", "leg_base"):
            body = src[src.index(f"def {fn}("):]
            body = body[:body.index("\ndef ", 10)]
            self.assertIn("canonical_base", body,
                          f"{fn} 没委派给唯一实现（又写了一份归一 ⇒ 迟早漂移）")

    def test_unknown_instrument_forms_stay_verbatim(self):
        """刻意契约：日期合约/币本位**原样保留**（修 `canonical_base` 时差点踩坏，用例救回）。"""
        import scripts.ai_brain_trader as abt
        for raw in ("SOL-USDT-250926", "BTC-USD-SWAP"):
            self.assertEqual(abt.canonical_position_inst_id(raw), raw,
                             f"{raw} 被判成'认识的形态' ⇒ 可能被换到池内 USDT 合约（换标的！）")

    def test_standard_forms_still_map_to_the_pool(self):
        import scripts.ai_brain_trader as abt
        for raw in ("BTC", "BTCUSDT", "BTC_USDT", "BTC-USDT-SWAP"):
            self.assertEqual(abt.canonical_position_inst_id(raw), "BTC-USDT-SWAP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
