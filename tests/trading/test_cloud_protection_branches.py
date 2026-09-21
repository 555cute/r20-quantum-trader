"""云端保护单的**过滤/跳过分支**（第二百一十四刀）。

全量探针在 `scripts/trader/cloud_protection.py` 点出 9 行未执行，**全是 `continue` 型的保守跳过**。
它们看着不起眼，但每一条都能翻转"有没有保护"的结论：

- 数覆盖时把这些单算进去 ⇒ 误判"已保护" ⇒ 裸奔；把它们漏掉 ⇒ 误判"没保护" ⇒ 触发安全退出。
- 棘轮清理旧 SL 时（`amend_venue_stop_loss`）：残余旧单必须撤掉（否则宽松旧单可能先触发），
  但**刚挂上的那张**绝不能撤（`_oid == new_id` 那一跳）。

| 行 | 分支 |
|---|---|
| 45 | 保护单列表里出现**非 dict** 行 ⇒ 跳过（真机可能混入 None/字符串）|
| 69-70 | 原生改单成功后，**残余旧 SL** 逐笔撤掉 |
| 83 | 回退路径里"旧单 id == 新挂上的 id"⇒ 不撤（否则刚挂的就被撤了）|
| 104 | 状态不是 live/effective ⇒ 不计入覆盖 |
| 108 | 方向不是平仓方向（多头要 sell）⇒ 不计入 |
| 110 | 缺 tp/sl 触发价 ⇒ 不计入（半条腿不算保护）|
| 113 | 不是 reduce-only ⇒ 不计入（可能反手开仓的单不是保护单）|
| 148 | 修复后**核验读失败** ⇒ 继续重试（不是"核验通过"）|
"""

import unittest
from unittest.mock import patch

from scripts.trader.cloud_protection import (amend_venue_stop_loss,
                                             ensure_cloud_position_protection,
                                             sync_cloud_algo_stop,
                                             _live_oco_coverage)


def _oco(**kw):
    base = dict(state="live", posSide="long", side="sell", tpTriggerPx="74000",
                slTriggerPx="68000", reduceOnly="true", sz=3.0)
    base.update(kw)
    return base


def _fz(v):
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


class LiveOcoCoverageFilterTest(unittest.TestCase):
    """覆盖核算的每一条"不算数"都必须真的不算数。"""

    def _cov(self, orders, pos_side="long"):
        return _live_oco_coverage(orders, pos_side, _float_or_zero=_fz)

    def test_valid_order_counts(self):
        self.assertEqual(self._cov([_oco()]), 3.0)

    def test_non_live_state_does_not_count(self):
        for state in ("canceled", "filled", "partially_filled"):
            with self.subTest(state=state):
                self.assertEqual(self._cov([_oco(state=state)]), 0.0,
                                 f"state={state} 不是活单 ⇒ 不许计入覆盖")

    def test_wrong_side_does_not_count(self):
        self.assertEqual(self._cov([_oco(side="buy")]), 0.0,
                         "平多头要用 sell 腿；buy 腿不是保护单")

    def test_missing_trigger_does_not_count(self):
        for field in ("tpTriggerPx", "slTriggerPx"):
            with self.subTest(field=field):
                self.assertEqual(self._cov([_oco(**{field: ""})]), 0.0,
                                 f"缺 {field} ⇒ 半条腿不算保护")

    def test_non_reduce_only_does_not_count(self):
        self.assertEqual(self._cov([_oco(reduceOnly="false")]), 0.0,
                         "非 reduce-only 的单可能反手开仓 ⇒ 不许当保护单")

    def test_net_and_matching_pos_side_count(self):
        self.assertEqual(self._cov([_oco(posSide="net")]), 3.0)
        self.assertEqual(self._cov([_oco()], pos_side="long"), 3.0)

    def test_short_side_expects_buy_leg(self):
        self.assertEqual(self._cov([_oco(posSide="short", side="buy")], pos_side="short"), 3.0)
        self.assertEqual(self._cov([_oco(posSide="short", side="sell")], pos_side="short"), 0.0)


class _Ad:
    def __init__(self, rows, *, amend=None, cancel_raises=False, attach=None):
        self._rows = rows
        self._amend = amend
        self._attach = attach
        self._cancel_raises = cancel_raises
        self.cancelled = []
        if amend is not None:
            self.amend_stop_loss = amend

    def list_protective_orders(self, symbol):
        return self._rows

    def cancel_price_order(self, oid):
        if self._cancel_raises:
            raise RuntimeError("cancel boom")
        self.cancelled.append(oid)

    def attach_protective_orders(self, symbol, pos_side, *, sl_px, contracts):
        return self._attach if self._attach is not None else {"sl": "new-sl"}


def _sl_row(oid, text="t-r20sl1"):
    return {"id": oid, "type": "STOP", "initial": {"text": text}}


class AmendStopLossCleanupTest(unittest.TestCase):
    def test_non_dict_rows_are_skipped(self):
        ad = _Ad([None, "junk", _sl_row("sl-1")])
        ok, detail = amend_venue_stop_loss(ad, "BTC_USDT", "long", 68000.0, 1.0)
        self.assertTrue(ok, f"非 dict 行不该让棘轮失败：{detail}")
        self.assertIn("sl-1", ad.cancelled, "合法旧单仍要被撤/改")

    def test_residual_old_legs_are_cancelled_after_native_amend(self):
        ad = _Ad([_sl_row("sl-1"), _sl_row("sl-2"), _sl_row("sl-3")],
                 amend=lambda symbol, pos_side, oid, new_sl: "sl-1")
        ok, detail = amend_venue_stop_loss(ad, "BTC_USDT", "long", 68000.0, 1.0)
        self.assertTrue(ok)
        self.assertEqual(sorted(ad.cancelled), ["sl-2", "sl-3"],
                         f"原生改单后残余旧单必须逐个撤掉（避免宽松旧单先触发）：{ad.cancelled}")

    def test_newly_placed_leg_is_never_cancelled_in_fallback(self):
        # 没有 amend_stop_loss ⇒ 回退"先挂新、再撤旧"；新单 id 与某张旧单相同 ⇒ 那张不能撤
        ad = _Ad([_sl_row("sl-1"), _sl_row("sl-2")], attach={"sl": "sl-1"})
        ok, detail = amend_venue_stop_loss(ad, "BTC_USDT", "long", 68000.0, 1.0)
        self.assertTrue(ok)
        self.assertEqual(ad.cancelled, ["sl-2"],
                         f"刚挂上的那张绝不能被撤（否则保护瞬间消失）：{ad.cancelled}")


class _OkxRest:
    """按序列返回持仓；序列里放**异常实例**表示这一次调用直接抛（模拟读失败）。"""

    def __init__(self, rows_seq):
        self._seq = list(rows_seq)
        self.placed = []

    def pending_algo_orders(self, inst_id):
        item = self._seq.pop(0) if len(self._seq) > 1 else self._seq[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def place_algo_oco(self, *a, **k):
        self.placed.append((a, k))
        return {"id": "oco-1"}


class EnsureProtectionVerifyRetryTest(unittest.TestCase):
    def test_verify_read_failure_retries_instead_of_counting_as_verified(self):
        """修复后第一次核验**读失败** ⇒ 必须继续重试，绝不当成"核验通过"。"""
        empty, good = [], [_oco(sz=1.0)]
        # 第 2 次（核验轮询的第一跳）**读失败** ⇒ 必须 continue 重试，而不是当作核验通过
        rest = _OkxRest([empty, RuntimeError("verify read boom"), good])
        with patch("scripts.trader.cloud_protection.time.sleep", lambda *_: None):
            ok, detail = ensure_cloud_position_protection(
                "BTC-USDT-SWAP", "long", 1.0, 74000.0, 68000.0,
                okx_rest=rest, _live_oco_coverage=lambda rows, side: _live_oco_coverage(
                    rows, side, _float_or_zero=_fz))
        self.assertTrue(ok, f"读失败应重试到成功：{detail}")
        self.assertIn("repaired and verified", detail)

    def test_verify_never_succeeds_reports_failure(self):
        rest = _OkxRest([[]])
        with patch("scripts.trader.cloud_protection.time.sleep", lambda *_: None):
            ok, detail = ensure_cloud_position_protection(
                "BTC-USDT-SWAP", "long", 1.0, 74000.0, 68000.0,
                okx_rest=rest, _live_oco_coverage=lambda rows, side: 0.0)
        self.assertFalse(ok, "核验始终不通过 ⇒ 必须返回失败（不许当作已保护）")



    def test_pos_side_mismatch_does_not_count(self):
        """空头持仓时，多头的保护腿绝不能算进它的覆盖。"""
        self.assertEqual(
            _live_oco_coverage([_oco(posSide="long", side="buy")], "short", _float_or_zero=_fz),
            0.0)


class _BareAd:
    """既没有 cancel_price_order 也没有 cancel_algo_order，只有 cancel_order。"""

    def __init__(self, rows):
        self._rows = rows
        self.called = []

    def list_protective_orders(self, symbol):
        return self._rows

    def cancel_order(self, symbol, oid):
        self.called.append(oid)

    def attach_protective_orders(self, symbol, pos_side, *, sl_px, contracts):
        return {"sl": "new-sl"}


class AmendStopLossFailureToleranceTest(unittest.TestCase):
    def test_list_failure_falls_back_to_attaching(self):
        class _Boom:
            def list_protective_orders(self, symbol):
                raise RuntimeError("list boom")

            def attach_protective_orders(self, symbol, pos_side, *, sl_px, contracts):
                return {"sl": "new-sl"}

        ok, detail = amend_venue_stop_loss(_Boom(), "BTC_USDT", "long", 68000.0, 1.0)
        self.assertTrue(ok, f"枚举旧单失败不该让棘轮整体失败（先挂新才是关键）：{detail}")

    def test_cancel_falls_back_to_cancel_order(self):
        ad = _BareAd([_sl_row("sl-1"), _sl_row("sl-2")])
        ok, detail = amend_venue_stop_loss(ad, "BTC_USDT", "long", 68000.0, 1.0)
        self.assertTrue(ok)
        self.assertEqual(ad.called, ["sl-1", "sl-2"],
                         f"没有 cancel_price_order/cancel_algo_order 时必须退到 cancel_order：{ad.called}")

    def test_cancel_failure_only_warns_and_keeps_the_new_stop(self):
        ad = _Ad([_sl_row("sl-1")], cancel_raises=True)
        ok, detail = amend_venue_stop_loss(ad, "BTC_USDT", "long", 68000.0, 1.0)
        self.assertTrue(ok, f"撤旧单失败只该告警（新单已生效，宁可双不可裸）：{detail}")
        self.assertEqual(ad.cancelled, [], "撤单抛错 ⇒ 不会记录成功撤单")
        self.assertIn("撤旧 0/1", detail, f"detail 要如实说「没撤掉」：{detail}")


class EnsureProtectionFailureTest(unittest.TestCase):
    def test_initial_verify_read_failure_is_reported(self):
        rest = _OkxRest([RuntimeError("read boom")])
        ok, detail = ensure_cloud_position_protection(
            "BTC-USDT-SWAP", "long", 1.0, 74000.0, 68000.0,
            okx_rest=rest, _live_oco_coverage=lambda rows, side: 0.0)
        self.assertFalse(ok)
        self.assertIn("unable to verify", detail)

    def test_repair_failure_is_reported(self):
        class _BadRest(_OkxRest):
            def place_algo_oco(self, *a, **k):
                raise RuntimeError("place boom")

        rest = _BadRest([[]])
        ok, detail = ensure_cloud_position_protection(
            "BTC-USDT-SWAP", "long", 1.0, 74000.0, 68000.0,
            okx_rest=rest, _live_oco_coverage=lambda rows, side: 0.0)
        self.assertFalse(ok)
        self.assertIn("repair failed", detail)




class _SyncRest:
    def __init__(self, orders=None, *, raises=None):
        self._orders = orders or []
        self._raises = raises
        self.amended = []

    def pending_algo_orders(self, inst_id):
        if self._raises is not None:
            raise self._raises
        return self._orders

    def amend_algo_sl(self, algo_id, new_sl, *, inst_id, new_sl_ord_px):
        self.amended.append((algo_id, new_sl, inst_id, new_sl_ord_px))


class SyncCloudAlgoStopTest(unittest.TestCase):
    """棘轮上移后必须把云端止损**立刻**同步过去（不等 15 分钟 LLM 周期）。"""

    def _live(self, **kw):
        base = {"state": "live", "posSide": "long", "algoId": "algo-1", "slTriggerPx": "68000"}
        base.update(kw)
        return base

    def test_no_live_stop_returns_false(self):
        rest = _SyncRest([self._live(state="canceled")])
        self.assertFalse(sync_cloud_algo_stop("BTC-USDT-SWAP", "long", 69000.0, okx_rest=rest))
        self.assertEqual(rest.amended, [], "没有活止损单 ⇒ 不许凭空 amend")

    def test_matching_price_skips_amend(self):
        rest = _SyncRest([self._live(slTriggerPx="69000")])
        self.assertTrue(sync_cloud_algo_stop("BTC-USDT-SWAP", "long", 69000.0, okx_rest=rest),
                        "价格已一致 ⇒ 幂等返回 True")
        self.assertEqual(rest.amended, [], "价格一致时不该重复 amend（幂等跳过）")

    def test_price_change_amends_the_live_order(self):
        rest = _SyncRest([self._live()])
        self.assertTrue(sync_cloud_algo_stop("BTC-USDT-SWAP", "long", 69250.0, okx_rest=rest))
        self.assertEqual(rest.amended, [("algo-1", 69250.0, "BTC-USDT-SWAP", "-1")],
                         f"必须用该活单的 algoId 改单：{rest.amended}")

    def test_net_pos_side_is_tolerated(self):
        """净持仓账户（OKX one-way）的云端单 `posSide` 是 `net` —— 必须认，否则收紧静默失效。"""
        rest = _SyncRest([self._live(posSide="net")])
        self.assertTrue(sync_cloud_algo_stop("BTC-USDT-SWAP", "long", 70000.0, okx_rest=rest),
                        "net 容错：找不到活止损单会让「云端止损收紧」静默失效")
        self.assertEqual(len(rest.amended), 1)

    def test_read_failure_returns_false_without_raising(self):
        rest = _SyncRest(raises=RuntimeError("read boom"))
        self.assertFalse(sync_cloud_algo_stop("BTC-USDT-SWAP", "long", 69000.0, okx_rest=rest),
                         "读失败只返回 False（调用方靠本地棘轮兜底），不许抛出去炸主循环")


if __name__ == "__main__":
    unittest.main()
