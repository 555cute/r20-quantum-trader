"""平仓收尾：核验归零后撤掉**可证明属于本系统**的保护腿（第一百一十三刀）。

## 治本：遗留腿的产生源头

实测（2026-09-20）：Binance 13 张保护腿里只有 2 张对得上唯一活动仓，其余是历史遗留；
根因是**平仓路径从不撤腿** —— `close_position` 只提交市价全平，
`cancel_protective_orders` 全仓仅 `scale_out` 用过。不补这一环，清理赶不上产生。

## 三条铁律（顺序不可变，本门逐条钉住）

1. **先核验归零**：未确认归零绝不撤腿（撤早了，还在保护中的仓就裸了）；
2. **该合约必须整体归零**（任何方向都没仓）：Gate 账户实测 `position_mode=dual`，
   双向持仓下平掉空头时多头的保护腿仍在保护**多头**，按合约撤会误伤；
3. **只撤可证明属于本系统的腿**（`matched` / Gate `t-r20` 标签）：
   归属不可判定的腿可能是**用户手单**，撤错不可逆。
"""
from __future__ import annotations

import sys
import io
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.trader.venue_protection import select_legs_to_cancel_after_close  # noqa: E402


def _leg(symbol, order_type, side, qty, trig, algo_id):
    return {"id": algo_id, "algo_id": algo_id, "symbol": symbol, "side": side,
            "trigger_price": trig, "type": "CONDITIONAL",
            "raw": {"symbol": symbol, "orderType": order_type, "side": side.upper(),
                    "quantity": str(qty), "triggerPrice": str(trig), "algoStatus": "NEW"}}


def _gate_leg(contract, text, auto_size, trig, algo_id, direction="long"):
    return {"id": algo_id, "status": "open", "direction": direction,
            "trigger": {"price": str(trig), "rule": 1, "expiration": 604800},
            "initial": {"contract": contract, "size": 0, "text": text,
                        "is_reduce_only": True, "auto_size": auto_size, "amount": "0"},
            "raw": None}


CLOSED_POS = {"base": "UNI", "side": "short", "size_signed": -82.0}
LEGS = [
    _leg("UNIUSDT", "STOP_MARKET", "buy", 82.0, 9.025, "uni-sl"),
    _leg("UNIUSDT", "TAKE_PROFIT_MARKET", "buy", 82.0, 8.365, "uni-tp"),
    _leg("UNIUSDT", "TAKE_PROFIT_MARKET", "sell", 51.0, 10.3, "uni-old-long"),
    _leg("ETHUSDT", "STOP_MARKET", "buy", 0.537, 2628.0, "eth-undecidable"),
]


class SelectorTest(unittest.TestCase):
    def test_matched_legs_of_the_closed_position_are_selected(self):
        r = select_legs_to_cancel_after_close(CLOSED_POS, LEGS, [])
        self.assertEqual(sorted(r["ids"]), ["uni-sl", "uni-tp"])
        self.assertTrue(all(x["reason"] == "matched" for x in r["to_cancel"]))

    def test_other_symbols_and_other_positions_are_never_selected(self):
        r = select_legs_to_cancel_after_close(CLOSED_POS, LEGS, [])
        self.assertNotIn("eth-undecidable", r["ids"], "别合约的腿与本次平仓无关")
        self.assertNotIn("uni-old-long",
                         r["ids"], "同合约上**别的仓**（平多）的腿不因平掉空头而撤")

    def test_tagged_orphan_is_selectable_but_untagged_is_not(self):
        tagged = _gate_leg("BTC_USDT", "t-r20sl75064327", "close_short", 81320.0, "g-sl")
        untagged = _leg("BTCUSDT", "STOP_MARKET", "buy", 1.0, 50000.0, "btc-no-tag")
        r = select_legs_to_cancel_after_close({"base": "BTC"}, [tagged, untagged], [])
        self.assertIn("g-sl", r["ids"], "Gate 带 t-r20 标签 ⇒ 可证明是我们的")
        self.assertNotIn("btc-no-tag", r["ids"], "无标签且无台账 ⇒ 归属不可判定，绝不撤")

    def test_not_touched_are_reported_not_silently_dropped(self):
        r = select_legs_to_cancel_after_close(CLOSED_POS, LEGS, [])
        self.assertEqual(len(r["not_touched"]["side_mismatch"]), 1)
        self.assertEqual(len(r["not_touched"]["orphan_unattributed"]), 1)
        self.assertEqual(r["counts"]["not_touched"], 2)

    def test_no_position_facts_means_conservative_no_cancel(self):
        """退化情形（抓不到平仓前事实）：Binance 无标签腿归因不出 ⇒ 不撤。"""
        r = select_legs_to_cancel_after_close({"base": "UNI"}, LEGS, [])
        self.assertEqual(r["ids"], [], "判不出归属就不撤（宁留勿误撤）")

    def test_gate_full_close_leg_matches_without_size(self):
        """Gate 整仓平腿 size=0，但覆盖全部 ⇒ 平掉该仓后应可撤。"""
        pos = {"base": "BTC", "side": "short", "size_signed": -5.0}
        leg = _gate_leg("BTC_USDT", "t-r20sl1", "close_short", 81320.0, "g1")
        r = select_legs_to_cancel_after_close(pos, [leg], [])
        self.assertEqual(r["ids"], ["g1"])


class RouterCloseTest(unittest.TestCase):
    """接线层：桩适配器跑真实 `close_position` 流程。"""

    class _Ad:
        environment = "demo"

        def __init__(self, *, after=None, before=None, legs=None, raise_cancel=False,
                     positions_raises=False):
            self._after = after if after is not None else []
            self._before = before if before is not None else [
                {"base": "UNI", "side": "short", "size_signed": -82.0}]
            self._legs = legs if legs is not None else LEGS
            self._raise_cancel = raise_cancel
            self._positions_raises = positions_raises
            self._closed = False
            self.cancelled = []

        def native_symbol(self, s):
            return f"{str(s).upper()}USDT"

        def fast_close_position(self, asset, **kw):
            self._closed = True
            return {"status": "closed", "id": "c1"}

        def positions(self):
            if self._positions_raises:
                raise RuntimeError("positions down")
            return self._after if self._closed else self._before

        def list_protective_orders(self, symbol=None):
            return self._legs

        def cancel_algo_order(self, **kw):
            if self._raise_cancel:
                raise RuntimeError("cancel boom")
            self.cancelled.append(kw.get("algo_id"))

    def setUp(self):
        from r20_backend import execution_router
        self.er = execution_router
        self._p1 = patch.object(self.er, "require_execution", lambda *a, **k: None)
        self._p2 = patch.object(self.er, "CLOSE_FLAT_POLL_SLEEP", 0.0)
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop); self.addCleanup(self._p2.stop)

    def _close(self, ad, **kw):
        return self.er.close_position("UNI", venue=kw.pop("venue", "binance"), adapter=ad, **kw)

    def test_flat_verified_cancels_exactly_the_proven_legs(self):
        ad = self._Ad()
        r = self._close(ad)
        self.assertTrue(r["ok"])
        self.assertEqual(ad.cancelled, ["uni-sl", "uni-tp"])
        self.assertIn("保护腿已撤 2 张", r["detail"])
        self.assertIn("未撤", r["detail"], "未撤的腿必须如实报出，不许静默丢弃")

    def test_not_flat_cancels_nothing(self):
        """**最关键的不变量**：未核验归零 ⇒ 一张都不撤（否则正在保护的仓会裸）。"""
        ad = self._Ad(after=[{"base": "UNI", "side": "short", "size_signed": -82.0}])
        r = self._close(ad)
        self.assertTrue(r["ok"], "平仓本身已受理 ⇒ 仍算成功")
        self.assertEqual(ad.cancelled, [])
        self.assertIn("未核验归零", r["detail"])
        self.assertIn("保持不动", r["detail"])

    def test_read_failure_counts_as_not_flat(self):
        ad = self._Ad(positions_raises=True)
        r = self._close(ad)
        self.assertEqual(ad.cancelled, [], "读不到持仓 ⇒ 按未归零处理（宁可不撤）")
        self.assertIn("未核验归零", r["detail"])

    def test_cancel_failure_does_not_flip_the_close_result(self):
        ad = self._Ad(raise_cancel=True)
        r = self._close(ad)
        self.assertTrue(r["ok"], "平仓确实成功了；谎报失败会让上层重试平仓")
        self.assertIn("撤单失败", r["detail"])
        self.assertIn("需人工核对", r["detail"])

    def test_flag_off_disables_the_cleanup(self):
        with patch.object(self.er, "CANCEL_STALE_PROTECTION_ON_CLOSE", False):
            ad = self._Ad()
            r = self._close(ad)
        self.assertEqual(ad.cancelled, [])
        self.assertNotIn("保护腿已撤", r["detail"])

    def test_okx_path_is_untouched(self):
        """OKX 有自己的平仓/算法单链路，本收尾不得插手。"""
        ad = self._Ad()
        self.assertTrue(self._close(ad, venue="okx")["ok"])
        self.assertEqual(ad.cancelled, [])

    def test_pre_close_facts_are_captured_before_closing(self):
        """归属要用**平仓前**的量/方向：平完再读只剩空仓 ⇒ 判不出 matched。"""
        ad = self._Ad(after=[], before=[{"base": "UNI", "side": "short", "size_signed": -82.0}])
        self._close(ad)
        self.assertEqual(ad.cancelled, ["uni-sl", "uni-tp"])

    def test_without_pre_close_facts_nothing_is_cancelled(self):
        ad = self._Ad(after=[], before=[])
        self._close(ad)
        self.assertEqual(ad.cancelled, [], "抓不到事实 ⇒ 归因不出 ⇒ 保守不撤")

    def test_gate_legs_with_nested_contract_reach_the_selection(self):
        """第一百八十九刀：Gate 腿的合约在**嵌套** `initial.contract`（扁平 `symbol` 为空）。

        接线层此前只读扁平 `l.get("symbol")` ⇒ **所有 Gate 腿被静默排除** ⇒
        "平仓后撤掉可证明属于自己的腿"这条链从未覆盖 Gate（线上遗留腿与该结论一致）。
        本用例用带 `t-r20` 标签的 Gate 腿钉住它现在真的能进选择并被撤。
        """
        gate_leg = _gate_leg("UNI_USDT", "t-r20sl75064327", "close_short", 9.025, "gate-sl")
        ad = self._Ad(legs=[gate_leg])
        r = self._close(ad, venue="gate")
        self.assertTrue(r["ok"], r["detail"])
        self.assertEqual(ad.cancelled, ["gate-sl"],
                         "Gate 腿（嵌套 contract + 本系统标签）应当被撤；被排除说明接线层又只看扁平字段")

    def test_other_symbols_still_never_reach_the_selection(self):
        """放宽字段读取后**不得**把别的币的腿也拉进来（撤错不可逆）。"""
        mine = _gate_leg("UNI_USDT", "t-r20sl1", "close_short", 9.025, "gate-mine")
        other = _gate_leg("BTC_USDT", "t-r20sl2", "close_short", 81320.0, "gate-other")
        ad = self._Ad(legs=[mine, other])
        r = self._close(ad, venue="gate")
        self.assertTrue(r["ok"], r["detail"])
        self.assertEqual(ad.cancelled, ["gate-mine"], "别的币的腿绝不能被撤")


if __name__ == "__main__":
    unittest.main()


class CancelFailureBranchesTest(unittest.TestCase):
    """撤销链的**读失败 / 无撤单能力 / 缺 id** 分支（第二百零八刀，覆盖率探针发现从未执行）。

    三条都是「宁可留腿，不可裸奔」的具体落点：

    - 读腿失败 ⇒ 当作没有可撤的腿（**不**按合约全撤）；
    - 交易所适配器**没有**撤单能力 ⇒ 记 `failed`，不许假装撤过（Gate 曾有此坑）；
    - 腿**没有 id** ⇒ 逐腿撤无从下手 ⇒ 跳过（绝不退化成"按合约全撤"）。
    """

    def setUp(self):
        from r20_backend import execution_router
        self.er = execution_router
        self._p1 = patch.object(self.er, "require_execution", lambda *a, **k: None)
        self._p2 = patch.object(self.er, "CLOSE_FLAT_POLL_SLEEP", 0.0)
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop); self.addCleanup(self._p2.stop)

    class _NoCancel:
        """能平仓、能读腿、但**没有**任何撤单方法（模拟能力缺失的适配器）。

        ⚠️ 平仓**前**必须真有持仓：否则腿连「可撤」都算不上（归因不出 ⇒ 走「未撤」那条路），
        根本到不了「无撤单能力」这一分支 —— 第一版桩返回 `[]` 就是这个错。
        """

        def __init__(self, legs, *, before=None):
            self._legs = legs
            self._before = before if before is not None else [
                {"base": "UNI", "side": "short", "size_signed": -82.0}]
            self._closed = False
            self.cancelled = []

        def native_symbol(self, s):
            return f"{str(s).upper()}USDT"

        def fast_close_position(self, asset, **kw):
            self._closed = True
            return {"status": "closed", "id": "c1"}

        def positions(self):
            return [] if self._closed else self._before

        def list_protective_orders(self, symbol=None):
            return self._legs

    class _ListRaises(_NoCancel):
        """读腿就炸。"""

        def __init__(self, legs=None):
            super().__init__(legs if legs is not None else LEGS)

        def list_protective_orders(self, symbol=None):
            raise RuntimeError("legs down")

        def cancel_algo_order(self, **kw):
            self.cancelled.append(kw.get("algo_id"))

    def _close(self, ad):
        return self.er.close_position("UNI", venue="binance", adapter=ad)

    def test_read_legs_failure_cancels_nothing(self):
        ad = self._ListRaises()
        r = self._close(ad)
        self.assertTrue(r["ok"], "平仓本身已受理 ⇒ 仍算成功")
        self.assertEqual(ad.cancelled, [], "读不到腿 ⇒ 什么都不撤（不猜、不按合约全撤）")

    def test_adapter_without_cancel_capability_records_failure(self):
        ad = self._NoCancel(LEGS)
        r = self._close(ad)
        self.assertEqual(ad.cancelled, [])
        self.assertIn("撤单失败", r["detail"], "无撤单能力必须如实报失败，不许假装撤过")

    def test_leg_without_id_is_not_cancelled(self):
        legs = [dict(x, algo_id="", id="") for x in LEGS]
        ad = self._NoCancel(legs)
        ad.cancel_algo_order = lambda **kw: ad.cancelled.append(kw.get("algo_id"))
        r = self._close(ad)
        self.assertEqual(ad.cancelled, [], "缺 id 的腿不能撤（按合约全撤会误伤别人的腿）")

    def test_ledger_read_failure_still_cancels_matched_legs(self):
        """台账读不到 ⇒ 退化成「只有 tag/持仓证据」，但**不因此放弃**已证明属于自己的腿。"""
        ad = RouterCloseTest._Ad()
        with patch("r20_backend.execution.own_records.read_ledger_rows",
                   side_effect=RuntimeError("ledger boom")):
            r = self._close(ad)
        self.assertEqual(ad.cancelled, ["uni-sl", "uni-tp"],
                         "台账读失败不该让可撤的腿变成不可撤")

    def test_pool_read_failure_is_only_a_warning(self):
        """所池读取失败：按无限制继续（不制造新的阻塞点），但**必须真的告警**。"""
        with patch("r20_backend.exchanges.routing_policy.load_venue_pool",
                   side_effect=RuntimeError("pool boom")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                out = self.er._load_venue_pool_soft("binance")
        self.assertEqual(out, {})
        self.assertIn("池配置读取失败", buf.getvalue(),
                      "告警式降级也必须真的发声，不许静默（否则『读不到』就变成了『没有』）")
