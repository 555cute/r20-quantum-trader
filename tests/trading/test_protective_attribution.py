"""保护腿归属门：这条腿是给**当前哪个仓**挂的（第一百一十二刀）。

## 真实证据（2026-09-20，Binance DEMO 实盘只读实测）

账户上 **13 张**保护腿，唯一活动仓是 `UNI short 82`，而逐腿归属结果是：

| state | 数量 | 实测样本 |
|---|---|---|
| `matched`（就是给这个仓的） | 2 | UNI sl 9.025 / tp 8.365（qty 82） |
| `size_mismatch`（有仓但腿量不符 ⇒ 旧仓遗留） | 2 | UNI tp 10.3(qty 51) / tp 9.805(qty 55) |
| `orphan_attributed`（无仓，但台账有同向同量成交） | 3 | ARB 2416.7 / XRP 799.9 / ETH 0.527 |
| `orphan_unattributed`（**归属不可判定**） | 6 | ETH 0.537 / SOL 10.45 / SOL 10.27 … |

## 两条危害（本门钉住判据，不钉"要不要撤"）

1. **虚假安全感**：`scan_protective_orders` 只按币种+平仓方向+数量算覆盖 ⇒ 给新仓算覆盖时，
   旧仓遗留的腿会被算成"已有保护"；
2. **会减新仓**：这些腿是 `reduceOnly` + 有量的条件单 ⇒ 同币再开仓后，价格触及**旧触发价**
   时它会**真的减掉新仓**的一部分（实测 ETH/SOL 腿均如此）。

## 铁律

归属**不能**靠标签：实测 Binance 腿的 `clientAlgoId` 是交易所自生成的随机串，我们下单时
**没带** clientAlgoId ⇒ 无标签可依。故只能账实比对，且：
`orphan_unattributed` **不得**当作"我们的腿"去自动撤销（撤错用户手单不可逆）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.trader.venue_protection import attribute_protective_orders  # noqa: E402


def _leg(symbol, order_type, side, qty, trig, algo_id="a1"):
    """按**真实归一形状**造腿（`raw.orderType` 才是类型来源，`type` 恒 CONDITIONAL）。"""
    return {"id": algo_id, "algo_id": algo_id, "symbol": symbol, "side": side,
            "trigger_price": trig, "type": "CONDITIONAL",
            "raw": {"symbol": symbol, "orderType": order_type, "side": side.upper(),
                    "quantity": str(qty), "triggerPrice": str(trig),
                    "reduceOnly": True, "closePosition": False, "algoStatus": "NEW"}}


# ── 真实实测夹具（逐字对齐线上 13 张腿与真台账行）─────────────────────────
REAL_POSITIONS = [{"base": "UNI", "side": "short", "size_signed": -82.0}]
REAL_LEGS = [
    # 真实 side：平空=BUY、平多=SELL（实测 UNI 82 的两张是 BUY，51/55 的两张是 SELL）
    _leg("UNIUSDT", "STOP_MARKET", "buy", 82.0, 9.025, "uni-sl"),
    _leg("UNIUSDT", "TAKE_PROFIT_MARKET", "buy", 82.0, 8.365, "uni-tp"),
    _leg("UNIUSDT", "TAKE_PROFIT_MARKET", "sell", 51.0, 10.3, "uni-old-51"),
    _leg("UNIUSDT", "TAKE_PROFIT_MARKET", "sell", 55.0, 9.805, "uni-old-55"),
    _leg("ARBUSDT", "TAKE_PROFIT_MARKET", "buy", 2416.7, 0.1846, "arb-tp"),
    _leg("XRPUSDT", "TAKE_PROFIT_MARKET", "buy", 799.9, 1.496, "xrp-tp"),
    _leg("ETHUSDT", "TAKE_PROFIT_MARKET", "buy", 0.527, 2707.0, "eth-tp"),
    _leg("ETHUSDT", "STOP_MARKET", "buy", 0.537, 2628.0, "eth-sl"),
    _leg("ETHUSDT", "TAKE_PROFIT_MARKET", "buy", 0.537, 2505.6, "eth-tp2"),
    _leg("SOLUSDT", "STOP_MARKET", "buy", 10.45, 111.5, "sol-sl"),
    _leg("SOLUSDT", "TAKE_PROFIT_MARKET", "buy", 10.45, 104.1, "sol-tp"),
    _leg("SOLUSDT", "STOP_MARKET", "buy", 10.27, 112.9, "sol-sl2"),
    _leg("SOLUSDT", "TAKE_PROFIT_MARKET", "buy", 10.27, 107.2, "sol-tp2"),
]
REAL_LEDGER = [
    {"id": "binance_closed_66353266_1789875209000", "inst": "ARB", "venue": "binance",
     "side": "空", "sz": 2416.7, "status": "closed"},
    {"id": "binance_closed_163140646_1789872830000", "inst": "XRP", "venue": "binance",
     "side": "空", "sz": 799.9, "status": "closed"},
    {"id": "binance_closed_329240990_1789872994000", "inst": "ETH", "venue": "binance",
     "side": "空", "sz": 0.527, "status": "closed"},
]


class RealFixtureTest(unittest.TestCase):
    def setUp(self):
        self.r = attribute_protective_orders(REAL_POSITIONS, REAL_LEGS, REAL_LEDGER)

    def test_counts_match_the_live_measurement(self):
        self.assertEqual(self.r["legs_total"], 13)
        self.assertEqual(self.r["counts"], {
            "matched": 2, "size_mismatch": 0, "side_mismatch": 2,
            "orphan_attributed": 3, "orphan_unattributed": 6,
            "unparsed": 0, "foreign": 0})
        self.assertEqual(self.r["orphan_total"], 9)

    def test_live_position_is_matched_to_its_two_real_legs(self):
        matched = {(l["symbol"], l["kind"], l["size"]) for l in self.r["matched"]}
        self.assertEqual(matched, {("UNI", "sl", 82.0), ("UNI", "tp", 82.0)},
                         "活动仓 82 张的 SL/TP 必须被判为 matched")

    def test_old_side_legs_are_flagged_not_matched(self):
        """旧腿（SELL 平多、51/55 张）**不能**算作当前空仓的保护。

        实测这两张是给**更早的多仓**挂的（台账 `UNI/binance/多/51.0 closed`，05:18），
        现在持仓是空 ⇒ 判 `side_mismatch`（比单纯"量不符"更准确，也更有害：
        它会在同币再开多仓时以旧价减仓）。
        """
        mism = {(l["symbol"], l["size"], l["protects"], l["position_side"])
                for l in self.r["side_mismatch"]}
        self.assertEqual(mism, {("UNI", 51.0, "long", "short"), ("UNI", 55.0, "long", "short")})
        self.assertTrue(all(l["symbol"] != "UNI" or l["size"] == 82.0
                            for l in self.r["matched"]))

    def test_attributed_orphans_carry_ledger_evidence(self):
        ev = {l["symbol"]: l.get("evidence") for l in self.r["orphan_attributed"]}
        self.assertEqual(set(ev), {"ARB", "XRP", "ETH"})
        self.assertTrue(all(v for v in ev.values()), "可归因孤儿必须带台账证据 id")

    def test_unattributed_orphans_are_declared_undecidable(self):
        syms = {l["symbol"] for l in self.r["needs_human"]}
        self.assertEqual(syms, {"ETH", "SOL"}, "无台账记录的腿只能进'需要人看'")
        self.assertTrue(all(l["symbol"] not in {"ARB", "XRP"} for l in self.r["needs_human"]))

    def test_cleanup_candidates_exclude_undecidable(self):
        """可清理候选只含**可归因**项；归属不可判定的一律不进（撤错用户手单不可逆）。"""
        cand = {(l["symbol"], l["size"]) for l in self.r["cleanup_candidates"]}
        self.assertEqual(cand, {("ARB", 2416.7), ("XRP", 799.9), ("ETH", 0.527),
                               ("UNI", 51.0), ("UNI", 55.0)})
        undecidable = {(l["symbol"], l["size"]) for l in self.r["needs_human"]}
        self.assertEqual(cand & undecidable, set(), "两类必须互斥")


class SemanticsTest(unittest.TestCase):
    def test_close_side_maps_to_position_side_for_attribution(self):
        """腿是**平仓方向**：腿 BUY ⇒ 原仓 short。方向搞反会把多仓记录错配给空腿。"""
        legs = [_leg("ETHUSDT", "STOP_MARKET", "buy", 1.0, 100.0)]
        short_row = [{"inst": "ETH", "side": "空", "sz": 1.0, "status": "closed"}]
        long_row = [{"inst": "ETH", "side": "多", "sz": 1.0, "status": "closed"}]
        self.assertEqual(len(attribute_protective_orders([], legs, short_row)["orphan_attributed"]), 1)
        self.assertEqual(len(attribute_protective_orders([], legs, long_row)["orphan_unattributed"]), 1)

    def test_no_position_no_ledger_is_undecidable_never_ours(self):
        legs = [_leg("DOGEUSDT", "STOP_MARKET", "buy", 5.0, 0.1)]
        r = attribute_protective_orders([], legs, [])
        self.assertEqual(r["counts"]["orphan_unattributed"], 1)
        self.assertEqual(r["cleanup_candidates"], [],
                         "无任何账实证据时不得给出可清理候选")

    def test_missing_ledger_argument_does_not_crash(self):
        legs = [_leg("DOGEUSDT", "STOP_MARKET", "buy", 5.0, 0.1)]
        r = attribute_protective_orders([], legs, None)
        self.assertEqual(r["counts"]["orphan_unattributed"], 1)

    def test_foreign_legs_are_not_attributed(self):
        """没有本系统特征（标签/类型名都不匹配）的腿：既不是我们的，也不进孤儿。"""
        foreign = [{"id": "x", "symbol": "BTCUSDT", "side": "buy",
                    "raw": {"symbol": "BTCUSDT", "orderType": "LIMIT"}}]
        r = attribute_protective_orders([], foreign, [])
        self.assertEqual(r["counts"]["foreign"], 1)
        self.assertEqual(r["orphan_total"], 0)

    def test_tolerance_is_relative_not_absolute(self):
        """相对容差：大数量合约（SOL 10.45）的浮点尾差不该把它判成孤儿。"""
        legs = [_leg("SOLUSDT", "STOP_MARKET", "buy", 10.45, 100.0)]
        pos = [{"base": "SOL", "side": "short", "size_signed": -10.45000001}]
        r = attribute_protective_orders(pos, legs, [])
        self.assertEqual(r["counts"]["matched"], 1)

    def test_symbol_normalization_covers_venue_spellings(self):
        for sym in ("UNIUSDT", "UNI-USDT-SWAP", "UNI_USDT", "uni"):
            with self.subTest(sym=sym):
                legs = [_leg(sym, "STOP_MARKET", "buy", 82.0, 9.0)]
                r = attribute_protective_orders(REAL_POSITIONS, legs, [])
                self.assertEqual(r["counts"]["matched"], 1)

    def test_empty_inputs_are_safe(self):
        for args in ((None, None, None), ([], [], [])):
            r = attribute_protective_orders(*args)
            self.assertEqual(r["legs_total"], 0)
            self.assertEqual(r["orphan_total"], 0)


def _gate_leg(contract, text, auto_size, trig, rule=1, algo_id="g1", direction="long"):
    """Gate `price_orders` 的**真实归一形状**（本机实跑抓帧）。

    要点：顶层没有 `symbol`（合约在 `initial.contract`）；dual 模式用 `auto_size`
    整仓平 ⇒ `initial.size=0`（**不是**"不可判定"）；`direction` 是**平仓方向**。
    """
    return {"id": algo_id, "id_string": algo_id, "status": "open", "direction": direction,
            "trigger": {"strategy_type": 0, "price_type": 0, "price": str(trig),
                        "rule": rule, "expiration": 604800},
            "initial": {"contract": contract, "size": 0, "price": "0", "tif": "ioc",
                        "text": text, "is_reduce_only": True, "auto_size": auto_size,
                        "amount": "0"},
            "raw": None}


class GateShapeTest(unittest.TestCase):
    """Gate 腿：**带我们的标签**（`t-r20sl/t-r20tp`）⇒ 归属可**证明**，与币安不同。"""

    REAL_GATE_LEGS = [
        _gate_leg("BTC_USDT", "t-r20sl75064327", "close_short", 81320.0, rule=1, algo_id="g-sl"),
        _gate_leg("BTC_USDT", "t-r20tp75063933", "close_short", 78920.0, rule=2, algo_id="g-tp"),
        _gate_leg("BTC_USDT", "t-r20tp2166840", "close_long", 82560.0, rule=1,
                  algo_id="g-tp-old", direction="short"),
    ]

    def test_gate_orphans_are_attributed_by_tag(self):
        """Gate 三张腿在**无持仓**时全是 `orphan_attributed`，证据是 `tag`（可证明是我们的）。"""
        r = attribute_protective_orders([], self.REAL_GATE_LEGS, [])
        self.assertEqual(r["counts"]["orphan_attributed"], 3)
        self.assertEqual(r["counts"]["orphan_unattributed"], 0)
        self.assertTrue(all(l["evidence"] == "tag" for l in r["orphan_attributed"]))
        self.assertEqual({l["symbol"] for l in r["orphan_attributed"]}, {"BTC"})
        self.assertEqual({l["kind"] for l in r["orphan_attributed"]}, {"sl", "tp"})

    def test_gate_full_close_leg_matches_its_position(self):
        """`auto_size` 整仓平腿 size=0，但覆盖的是**全部** ⇒ 必须判 matched（不是不可判定）。"""
        pos = [{"base": "BTC", "side": "short", "size_signed": -5.0}]
        r = attribute_protective_orders(pos, self.REAL_GATE_LEGS[:2], [])
        self.assertEqual(r["counts"]["matched"], 2)
        self.assertTrue(all(l["full_close"] for l in r["matched"]))
        self.assertTrue(all(l["protects"] == "short" for l in r["matched"]))

    def test_gate_opposite_direction_leg_is_side_mismatch(self):
        """`close_long` 腿（平多）配在**空仓**上 ⇒ `side_mismatch`（旧仓遗留）。"""
        pos = [{"base": "BTC", "side": "short", "size_signed": -5.0}]
        r = attribute_protective_orders(pos, [self.REAL_GATE_LEGS[2]], [])
        self.assertEqual(r["counts"]["side_mismatch"], 1)


class UnparsedTest(unittest.TestCase):
    def test_tagged_leg_without_readable_symbol_goes_to_unparsed(self):
        """连币种都读不出的行单独登记，**不得**伪装成"不可判定孤儿"（那会误导清理决策）。"""
        broken = {"id": "x", "initial": {"text": "t-r20sl1", "size": 0}, "raw": None}
        r = attribute_protective_orders([], [broken], [])
        self.assertEqual(r["counts"]["unparsed"], 1)
        self.assertEqual(r["counts"]["orphan_unattributed"], 0)
        self.assertEqual(r["orphan_total"], 0, "读不出的行不算孤儿")
        self.assertTrue(any("币种" in str(x.get("reason")) for x in r["needs_human"]))


if __name__ == "__main__":
    unittest.main()
