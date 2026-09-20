# -*- coding: utf-8 -*-
"""保护判据的**跨生产者契约**（2026-09-20 第一百二十二刀）。

## 为什么需要这个门

本会话在"保护判定"这条链上连修四刀（第 18/20/21/22 刀），每次都是**同一个病**：
**同一个语义被两处各写一遍**，于是慢慢分叉，而分叉只在真机上以"面板/提示词说错话"
的形式暴露。已实测的分叉：

| 分叉 | 表现 |
|---|---|
| 覆盖口径 | 跨所路径认"整仓平腿"（`closePosition`/`close`），OKX 路径只累加 `sz` ⇒ OKX 报 0% 覆盖 |
| 覆盖率文案 | `partially_protected` + 100% 渲染成「部分保护（覆盖不足） 100%」 |
| `cloud_oco_verified` | **只有跨所路径写**该字段，OKX 不写；前端判据 `!== false` ⇒ **同一状态在 OKX 算"已保护"、在 binance 算"未保护"**，且 OKX 的 `unknown`（不可判定）也被算作已保护 |

本门把契约钉成**可执行断言**：两个生产者产出同一状态时，**给消费者的输入必须一致**
（含前端 KPI 判据的取值），从而这类分叉下次要么当场翻红、要么根本写不出来。

## 契约（生产 → 消费）

| 字段 | 语义 | 取值域 |
|---|---|---|
| `protectionStatus` | 保护判定 | `fully_protected` / `partially_protected` / `unprotected` / `unknown`（`factors.py` 另可加 `verification_stale`/`unknown_stale`） |
| `protectionCoveragePct` | 止损量覆盖百分比 | **`None` ⟺ `unknown`**；`0.0` ⟺ `unprotected`；`fully_protected` ⇒ `100.0` |
| `cloud_oco_verified` | 云端双腿**已验证**（前端 KPI 的否决位） | 必须存在，且 **⟺ `fully_protected`** |
| `protectionLegs` | 归属本仓的腿数（非"已覆盖"） | int ≥ 0 |
| `protectionExpiry` | 腿到期态（**OKX 算法单无此语义 ⇒ 可缺**） | `never`/`expiring`/`expired`/`unknown` |

消费者：面板 `KpiRibbon.vue` / `PositionsOrdersPanel.vue`（`cloud_oco_verified !== false
&& protectionStatus !== 'unprotected'`）、图表 `chartLiveLevels.ts`、提示词
`account_text._protection_text`。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from r20_backend.dashboard_payload.algo_protection import collect_algo_protection
from r20_backend.dashboard_payload.multi_venue import collect_cross_venue_positions

#: 前端 `KpiRibbon.vue:82` / `PositionsOrdersPanel.vue:79` 的判据（**逐字**抄写）。
#: ⚠️ 改这里必须同步改前端：本门的意义就是让"两处判据"不可能悄悄分叉。
def _frontend_counts_as_protected(row: dict) -> bool:
    return (row.get("cloud_oco_verified") is not False
            and row.get("protectionStatus") != "unprotected")


def _okx_row(algos, *, pos_sz=100.0):
    pos = {"instId": "ETH-USDT-SWAP", "posSide": "long", "pos_sz": pos_sz}
    collect_algo_protection([pos], [], lambda fn, *a, **k: (True, algos, ""),
                            lambda *a, **k: None, {})
    return pos


def _leg(**over):
    row = {"algoId": "a1", "state": "live", "posSide": "long", "reduceOnly": "true",
           "sz": "100", "slTriggerPx": "90", "tpTriggerPx": "110"}
    row.update(over)
    return row


def _xvenue_row(algos, *, size=100.0):
    got: list = []
    ad_rows = [{"base": "ETH", "symbol": "ETHUSDT", "size_signed": -abs(size),
                "side": "short", "entry_price": 100.0, "mark_price": 99.0, "leverage": 5}]

    class _Ad:
        def positions(self):
            return ad_rows

        def open_orders(self):
            return []

        def list_protective_orders(self, *a, **k):
            return algos

    empty = type("E", (), {"positions": lambda self: [], "open_orders": lambda self: [],
                           "list_protective_orders": lambda self, *a, **k: []})()
    with patch("r20_backend.exchanges.get_adapter",
               lambda v, *a, **k: _Ad() if v == "binance" else empty), \
         patch("r20_backend.dashboard_payload.multi_venue._global_env_axis", lambda: "demo"):
        collect_cross_venue_positions(got, [], 0, 0, 0.0, source_errors=[])
    assert got, "跨所夹具没产出持仓行"
    return got[0]


#: 同一语义场景在两边的夹具构造器。
def _scenarios():
    """(场景名, OKX 夹具, 跨所夹具, 期望状态)"""
    return [
        ("双腿满量", [_leg()],
         [{"symbol": "ETHUSDT", "side": "buy", "type": "STOP_MARKET",
           "raw": {"orderType": "STOP_MARKET", "triggerPrice": "105", "quantity": "100"}},
          {"symbol": "ETHUSDT", "side": "buy", "type": "TAKE_PROFIT_MARKET",
           "raw": {"orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "95", "quantity": "100"}}],
         "fully_protected"),
        ("半量止损", [_leg(sz="50", tpTriggerPx=None)],
         [{"symbol": "ETHUSDT", "side": "buy", "type": "STOP_MARKET",
           "raw": {"orderType": "STOP_MARKET", "triggerPrice": "105", "quantity": "50"}}],
         "partially_protected"),
        ("无腿", [], [], "unprotected"),
        ("量不可判定", [_leg(sz=None, tpTriggerPx=None)],
         [{"symbol": "ETHUSDT", "side": "buy", "type": "STOP_MARKET",
           "raw": {"orderType": "STOP_MARKET", "triggerPrice": "105"}}],
         "unknown"),
    ]


class ProtectionContractTest(unittest.TestCase):
    """两个生产者 × 同一语义场景 ⇒ 消费者拿到的输入必须一致。"""

    def test_both_producers_agree_on_status(self):
        for name, okx_algos, xv_algos, expected in _scenarios():
            with self.subTest(scenario=name):
                self.assertEqual(_okx_row(okx_algos)["protectionStatus"], expected,
                                 f"OKX 侧 {name} 判定与契约不符")
                self.assertEqual(_xvenue_row(xv_algos)["protectionStatus"], expected,
                                 f"跨所侧 {name} 判定与契约不符")

    def test_coverage_pct_domain_is_consistent(self):
        """`None ⟺ unknown`、`0.0 ⟺ unprotected`、`fully ⇒ 100.0` —— 两侧同规。"""
        for name, okx_algos, xv_algos, expected in _scenarios():
            for label, row in (("okx", _okx_row(okx_algos)),
                               ("xvenue", _xvenue_row(xv_algos))):
                with self.subTest(scenario=name, venue=label):
                    pct = row.get("protectionCoveragePct")
                    if expected == "unknown":
                        self.assertIsNone(pct, "不可判定不得给百分比（无证据的确定结论）")
                    elif expected == "unprotected":
                        self.assertEqual(pct, 0.0)
                    elif expected == "fully_protected":
                        self.assertEqual(pct, 100.0)
                    else:
                        self.assertIsInstance(pct, (int, float))

    def test_cloud_oco_verified_is_emitted_by_both_and_means_fully(self):
        """⭐ 本门的存在理由：该字段此前**只有跨所路径写**，前端 `!== false` 于是
        "缺字段"被当成已验证 ⇒ 同一状态在 OKX 算已保护、在 binance 算未保护。
        """
        for name, okx_algos, xv_algos, expected in _scenarios():
            for label, row in (("okx", _okx_row(okx_algos)),
                               ("xvenue", _xvenue_row(xv_algos))):
                with self.subTest(scenario=name, venue=label):
                    self.assertIn("cloud_oco_verified", row,
                                  "两个生产者都必须显式给这个字段（缺失=前端按已验证处理）")
                    self.assertEqual(row["cloud_oco_verified"],
                                     expected == "fully_protected")

    def test_frontend_predicate_agrees_across_venues(self):
        """同一状态在两侧必须得到**同一个**前端判据结果（本会话实测曾不一致）。"""
        for name, okx_algos, xv_algos, expected in _scenarios():
            with self.subTest(scenario=name):
                okx = _frontend_counts_as_protected(_okx_row(okx_algos))
                xv = _frontend_counts_as_protected(_xvenue_row(xv_algos))
                self.assertEqual(okx, xv,
                                 f"{name}：同一状态在 OKX({okx}) 与跨所({xv}) 判据不一致")
                self.assertEqual(okx, expected == "fully_protected",
                                 "只有 fully_protected 才算'已保护'（部分/不可判定都不算）")

    def test_protection_legs_present_on_both(self):
        for name, okx_algos, xv_algos, _expected in _scenarios():
            with self.subTest(scenario=name):
                for label, row in (("okx", _okx_row(okx_algos)),
                                   ("xvenue", _xvenue_row(xv_algos))):
                    self.assertIsInstance(row.get("protectionLegs"), int,
                                          f"{label} 缺 protectionLegs")

    def test_expiry_is_xvenue_specific_and_documented(self):
        """`protectionExpiry` 是跨所语义（Gate 腿 7 天到期）；OKX 算法单不适用 ⇒ 可缺。

        允许缺，但**不允许**在缺的时候被消费者读成"安全"。
        """
        row = _xvenue_row([])
        self.assertIn("protectionExpiry", row)
        self.assertNotIn("protectionExpiry", _okx_row([]))


if __name__ == "__main__":
    unittest.main()
