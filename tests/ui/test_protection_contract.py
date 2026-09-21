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
| `protectionSlTriggerPxType` / `protectionTpTriggerPxType` | 保护腿**触发价类型**（第一百六十七刀）：按什么价触发该腿 | `mark`/`last`/`index`（原样透传交易所上报值）；**`"unknown"` ⟺ 读不到或该腿未上报**；**`None` ⟺ 该类腿不存在**（≠ unknown）|

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


def _xvenue_row(algos, *, size=100.0, ledger_rows=None):
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
        collect_cross_venue_positions(got, [], 0, 0, 0.0, source_errors=[],
                                      ledger_rows=ledger_rows)
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


class TriggerTypeDisclosureTest(unittest.TestCase):
    """触发价类型的三态披露（**读不到 ⇒ unknown，绝不填默认值**）。"""

    def test_okx_leg_type_is_disclosed_verbatim(self):
        row = _okx_row([_leg(slTriggerPxType="mark", tpTriggerPxType="mark")])
        self.assertEqual(row["protectionSlTriggerPxType"], "mark")
        self.assertEqual(row["protectionTpTriggerPxType"], "mark")
        # 修正后两腿类型不同也要如实分开报（合并成一个字段会说谎）
        row2 = _okx_row([_leg(slTriggerPxType="last", tpTriggerPxType="mark")])
        self.assertEqual(row2["protectionSlTriggerPxType"], "last")
        self.assertEqual(row2["protectionTpTriggerPxType"], "mark")

    def test_leg_present_but_type_unreported_is_unknown_not_mark(self):
        """腿在、类型未上报 ⇒ `unknown`。**不得**用"我们期望的 mark"顶替。"""
        row = _okx_row([_leg()])          # 夹具不带任何 *TriggerPxType
        self.assertEqual(row["protectionSlTriggerPxType"], "unknown")
        self.assertEqual(row["protectionTpTriggerPxType"], "unknown")
        xv = _xvenue_row([{"symbol": "ETHUSDT", "side": "buy", "type": "STOP_MARKET",
                           "raw": {"orderType": "STOP_MARKET", "triggerPrice": "105",
                                   "quantity": "100"}}])
        # 外所（Binance 等）没有这个概念 ⇒ 腿在但类型不可判定
        self.assertEqual(xv["protectionSlTriggerPxType"], "unknown")

    def test_missing_leg_is_none_not_unknown(self):
        """没有该类腿 ⇒ `None`（"没有这东西"不同于"读不到"）。"""
        row = _okx_row([_leg(tpTriggerPx=None)])
        self.assertIsNone(row["protectionTpTriggerPxType"])
        self.assertEqual(row["protectionSlTriggerPxType"], "unknown")
        empty = _okx_row([])
        self.assertIsNone(empty["protectionSlTriggerPxType"])
        self.assertIsNone(empty["protectionTpTriggerPxType"])

    def test_both_producers_emit_the_same_field_names(self):
        okx = _okx_row([_leg(slTriggerPxType="mark")])
        xv = _xvenue_row([{"symbol": "ETHUSDT", "side": "buy", "type": "STOP_MARKET",
                           "raw": {"orderType": "STOP_MARKET", "triggerPrice": "105",
                                   "quantity": "100"}}])
        for field in ("protectionSlTriggerPxType", "protectionTpTriggerPxType"):
            self.assertIn(field, okx, f"OKX 生产者缺字段 {field}")
            self.assertIn(field, xv, f"跨所生产者缺字段 {field}")

    def test_venue_reader_maps_okx_type_and_reports_missing_as_none(self):
        """单一取数点：`trigger_px_type` 认 OKX 两种键；读不到 ⇒ `None`（不是 "mark"）。"""
        from scripts.trader.venue_protection import trigger_px_type
        self.assertEqual(trigger_px_type({"slTriggerPxType": "mark"}), "mark")
        self.assertEqual(trigger_px_type({"raw": {"tpTriggerPxType": "LAST"}}), "last")
        self.assertIsNone(trigger_px_type({"slTriggerPx": "90"}))
        self.assertIsNone(trigger_px_type({"slTriggerPxType": "   "}))


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

class OrphanCandidatesPayloadTest(unittest.TestCase):
    """第一百七十五刀：孤儿腿候选进面板载荷（**只报告不撤销**，且读不到要说读不到）。"""

    _ORPHAN_TAGGED = {"symbol": "XRPUSDT", "side": "buy", "type": "TAKE_PROFIT_MARKET",
                      "raw": {"orderType": "TAKE_PROFIT_MARKET", "clientAlgoId": "t-r20tp1",
                              "triggerPrice": "1.3255", "quantity": "826.5"}}

    def test_tagged_orphan_is_listed_as_a_candidate_with_evidence(self):
        row = _xvenue_row([self._ORPHAN_TAGGED])
        o = row["protectionOrphans"]
        self.assertTrue(o["readable"])
        self.assertEqual(len(o["attributed"]), 1)
        self.assertEqual(o["attributed"][0]["symbol"], "XRP")
        self.assertEqual(o["attributed"][0]["evidence"], "tag")
        self.assertEqual(o["attributed"][0]["kind"], "tp")

    #: 无标签腿（`clientAlgoId` 是交易所随机串，与真机一致）——只能靠台账取证
    #: ⚠️ `side: buy` = **平空**（故 `protects: short`）—— 与真机 XRP 826.5 那张一致；
    #: 写成 `sell` 会被判成"保护多仓"，与台账 `空` 记录对不上（我第一版就写错了，被本用例抓到）。
    _ORPHAN_UNTAGGED = {"symbol": "XRPUSDT", "side": "buy", "type": "TAKE_PROFIT_MARKET",
                        "raw": {"orderType": "TAKE_PROFIT_MARKET",
                                "clientAlgoId": "1vzDTiF4UXEHSSULlD9lug",
                                "triggerPrice": "1.3255", "quantity": "826.5"}}

    def test_ledger_evidence_promotes_and_unattributed_never_enters_candidates(self):
        no_ledger = _xvenue_row([self._ORPHAN_UNTAGGED])["protectionOrphans"]
        self.assertEqual(no_ledger["attributed"], [], "无标签又无台账 ⇒ 不得凭空归因")
        self.assertEqual(len(no_ledger["unattributed"]), 1)
        self.assertEqual(no_ledger["ledgerRows"], "unavailable", "取证依据不可用必须如实披露")
        with_ledger = _xvenue_row([self._ORPHAN_UNTAGGED],
                                 ledger_rows=[{"inst": "XRP", "side": "空", "sz": 826.5}])["protectionOrphans"]
        self.assertEqual(with_ledger["ledgerRows"], "ok")
        self.assertEqual(with_ledger["attributed"][0]["evidence"], "ledger")
        self.assertEqual(len(with_ledger["unattributed"]), 0)

    def test_client_algo_id_tag_is_now_scanned(self):
        """扫描器补了客户端订单号：带标签的 `clientAlgoId` 现在能被认出来。

        ⚠️ 真机现状（如实）：Binance 的 `clientAlgoId` 是交易所随机串（20/20 无 `r20`），
        所以这条**不会**让当下的 Binance 腿变得可归因 —— 它只是把"标签存在但看不见"的洞补上。
        """
        from scripts.trader.venue_protection import _row_text
        self.assertIn("r20sl", _row_text({"raw": {"clientAlgoId": "t-r20sl9"}}))
        self.assertNotIn("r20sl", _row_text({"raw": {"clientAlgoId": "1vzDTiF4UXEHSSULlD9lug"}}))

    def test_unreadable_legs_are_unknown_not_empty(self):
        """读腿失败 ⇒ `readable: False`（不是"没有孤儿腿"）。"""
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2] / "r20_backend" / "dashboard_payload"
               / "multi_venue.py").read_text(encoding="utf-8")
        body = src[src.index("def _venue_orphan_summary("):]
        body = body[:body.index("\ndef ")]
        self.assertIn('if not readable:', body)
        self.assertIn('"readable": False', body)

    def test_summary_does_not_cancel_anything(self):
        """本函数**只报告**：源码里不得出现任何撤销调用（撤销是显式运营动作）。"""
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2] / "r20_backend" / "dashboard_payload"
               / "multi_venue.py").read_text(encoding="utf-8")
        body = src[src.index("def _venue_orphan_summary("):]
        body = body[:body.index("\ndef ")]
        for forbidden in ("cancel_price_order", "cancel_protective_orders", "cancel_order"):
            self.assertNotIn(forbidden, body, f"面板载荷里出现了撤销调用 {forbidden}")

class MismatchLegsDisclosureTest(unittest.TestCase):
    """第一百八十一刀：方向/量与任何持仓都对不上的腿，面板也要看得见（两种语义分开）。"""

    _MISM = {"symbol": "SOLUSDT", "side": "buy", "type": "TAKE_PROFIT_MARKET",
             "raw": {"orderType": "TAKE_PROFIT_MARKET", "quantity": "10.55",
                     "triggerPrice": "103.53"}}

    def test_mismatch_legs_are_listed(self):
        """⚠️ 要造出 `side_mismatch`，必须**同币有持仓**且腿方向相反 —— 只有别币持仓时
        那条腿属于 `orphan_*`（我第一版就搞错了，用例当场纠正）。这里直接调汇总函数。"""
        from r20_backend.dashboard_payload.multi_venue import _venue_orphan_summary
        positions = [{"base": "SOL", "symbol": "SOLUSDT", "side": "long", "size_signed": 10.0}]
        o = _venue_orphan_summary(positions, [self._MISM], None, readable=True)
        self.assertTrue(o["readable"])
        self.assertEqual(len(o["sideMismatch"]), 1)
        self.assertEqual(o["sideMismatch"][0]["symbol"], "SOL")
        self.assertEqual(o["sizeMismatch"], [])

    def test_mismatch_legs_are_not_counted_in_coverage(self):
        """方向不符的腿**不得**计入覆盖（本仓覆盖应视为 0，且要求修复）。"""
        row = _xvenue_row([self._MISM])
        self.assertEqual(row["protectionCoveragePct"], 0.0)
        self.assertIn(row["protectionStatus"], ("unprotected", "unknown"))

    def test_unreadable_legs_report_empty_mismatch_not_fake_zero(self):
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2] / "r20_backend" / "dashboard_payload"
               / "multi_venue.py").read_text(encoding="utf-8")
        body = src[src.index("def _venue_orphan_summary("):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn('"readable": False', body)
        self.assertIn('"sideMismatch": []', body, "读腿失败时不得给出'0 条'的假精确")

class UnclassifiedLegsDisclosureTest(unittest.TestCase):
    """第一百八十二刀：**读到了但认不出**的腿也要看得见（它们不计入覆盖 ⇒ 覆盖可能被低估）。"""

    _UNKNOWN = {"symbol": "XRPUSDT", "type": "CONDITIONAL", "raw": {"quantity": "100"}}

    def _summary(self, legs):
        from r20_backend.dashboard_payload.multi_venue import _venue_orphan_summary
        positions = [{"base": "XRP", "symbol": "XRPUSDT", "side": "long", "size_signed": 100.0}]
        return _venue_orphan_summary(positions, legs, None, readable=True)

    def test_unclassifiable_legs_are_counted_and_disclosed(self):
        o = self._summary([self._UNKNOWN])
        self.assertEqual(o["foreignCount"], 1, "认不出类型的腿必须给数（不计入覆盖 ⇒ 覆盖可能低估）")
        self.assertEqual(o["unparsedCount"], 0)

    def test_they_do_not_count_as_coverage(self):
        """行为侧：认不出的腿**不得**算覆盖（否则会静默把不明订单当成保护）。"""
        from scripts.trader.venue_protection import scan_protective_orders
        import time
        v = scan_protective_orders([self._UNKNOWN], symbol="XRPUSDT", pos_side="long",
                                   position_size=100.0, now_s=time.time())
        self.assertEqual(v["covered_size"], 0.0)
        self.assertEqual(v["foreign_count"], 1)

    def test_unreadable_legs_give_none_not_zero(self):
        from r20_backend.dashboard_payload.multi_venue import _venue_orphan_summary
        o = _venue_orphan_summary([], [], None, readable=False)
        self.assertIsNone(o["foreignCount"], "读腿失败时不得给出'0 条认不出'的假精确")
