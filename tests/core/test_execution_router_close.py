"""统一执行路由·平仓侧：**先核验归零、再撤可证明属于本系统的腿**（第二百八十一刀，开新面 execution_router.py 平仓链）。

配套 `tests/core/test_execution_router.py`（开仓侧）。本文件覆盖归零核验、平仓前事实抓取、
可证明归属的撤腿，以及 `close_position` 的收尾三铁律。

| 语义 | 口径 |
|---|---|
| ★ **整体归零而非本方向归零** | 双向持仓下平掉空头时，多头的保护腿仍在保护它 ⇒ 按合约撤会误伤；未确认归零**绝不撤腿**（撤早了会撤掉还在保护中的腿）|
| ★ **读失败一律当未归零** | 读不到持仓 ⇒ 宁可留着腿也不误撤；`remaining` 必须**先初始化**（否则全读失败时 `return False, remaining` 会 `UnboundLocalError`）|
| ★ **量读不出按 1.0 保守处理** | 解析失败不当作 0（那会误判归零并撤腿）|
| ★ **平仓前抓事实** | 归属判定要用平仓前的量/方向 —— 平完再读只剩空仓，`matched` 判不出来就一张腿都撤不掉 |
| ★ **只撤可证明属于本系统的腿** | 由 `select_legs_to_cancel_after_close` 判定；无 id 的项跳过；撤单失败**只记账不改判平仓结论**；`not_touched` 数量如实报出 |
| ★ **腿基名走既有访问器** | 第 189 刀的教训：原来只读扁平 `symbol`，而 Gate 的合约在 `initial.contract` ⇒ 所有 Gate 腿被静默排除，"平仓后撤腿"这条链**从未覆盖 Gate** |
| ★ **`closed:False` 绝不换算成功** | 审计 B1：适配器明示未平（无持仓/双向歧义/非整数张数）时旧实现仍报 `ok=True` |
| ★ **平仓异常语义分层** | `ExchangeCapabilityError` **原样冒泡**；其它异常 ⇒ `stage=close`；收尾异常**绝不影响平仓结论**（谎报失败会让上层重试平仓）|
| 收尾只在非 OKX 且开关打开时做 | `CANCEL_STALE_PROTECTION_ON_CLOSE and v != "okx"` |
"""

import inspect
import types
import unittest
from unittest import mock

from r20_backend import execution_router as ER
from r20_backend.execution import own_records
from scripts.trader import venue_protection


class _CloseAdapter:
    """平仓适配器替身；`fast_close_position` 的签名由工厂决定（用于验 inspect 分支）。"""

    def __init__(self, venue="gate", environment="sandbox", rows=None,
                 closed=None, raises=None, accept_pos_side=False):
        self.capabilities = types.SimpleNamespace(venue=venue)
        self.environment = environment
        self.rows = list(rows or [])
        self.closed = {"closed": True, "reason": ""} if closed is None else closed
        self.raises = raises
        self.accept_pos_side = accept_pos_side
        self.calls = []
        self.closed_kwargs = None
        self.cancel_price_calls = []
        self.cancel_algo_calls = []
        self.protective_legs = []

    def positions(self):
        self.calls.append("positions")
        return list(self.rows)

    def list_protective_orders(self, symbol):
        return list(self.protective_legs)

    def cancel_price_order(self, order_id):
        self.cancel_price_calls.append(order_id)

    def cancel_algo_order(self, *, algo_id):
        self.cancel_algo_calls.append(algo_id)


def _adapter(accept_pos_side=False, **kwargs):
    if accept_pos_side:
        def _close(self, symbol, pos_side=None):
            self.closed_kwargs = {"symbol": symbol, "pos_side": pos_side}
            if self.raises:
                raise self.raises
            return self.closed
    else:
        def _close(self, symbol):
            self.closed_kwargs = {"symbol": symbol}
            if self.raises:
                raise self.raises
            return self.closed
    cls = type("CloseAdapter", (_CloseAdapter,), {"fast_close_position": _close})
    return cls(**kwargs)


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.sleep = self._start(mock.patch.object(ER.time, "sleep"))
        self.adapter = _adapter()
        self._start(mock.patch.object(ER, "get_adapter",
                                      return_value=self.adapter))
        self.require = self._start(mock.patch.object(ER, "require_execution"))
        self._start(mock.patch.object(ER, "is_sandbox_environment",
                                      side_effect=lambda e: str(e) in ("demo", "sandbox")))
        self._start(mock.patch.object(
            ER, "canonical_base",
            side_effect=lambda s: str(s or "").upper().split("-")[0]))


class VerifyFlatTests(_Base):
    def _flat(self, rows, *, tries=None):
        self.adapter.rows = rows
        self.pos_mock = mock.Mock(side_effect=lambda: list(self.adapter.rows))
        self.adapter.positions = self.pos_mock
        if tries is not None:
            self._start(mock.patch.object(ER, "CLOSE_FLAT_POLL_TRIES", tries))
        return ER._verify_symbol_flat(self.adapter, "BTC")

    def test_no_positions_at_all_is_flat(self):
        self.assertEqual(self._flat([]), (True, 0.0))

    def test_other_contracts_do_not_block_flatness(self):
        self.assertEqual(self._flat([{"base": "ETH", "size_signed": 5.0}]), (True, 0.0))

    def test_a_matching_position_is_not_flat(self):
        flat, remaining = self._flat([{"base": "BTC", "size_signed": -3.5}])
        self.assertIs(flat, False)
        self.assertEqual(remaining, 3.5)

    def test_dust_counts_as_flat(self):
        self.assertEqual(self._flat([{"base": "BTC", "size_signed": 1e-12}]), (True, 0.0))

    def test_the_largest_matching_position_wins(self):
        flat, remaining = self._flat([{"base": "BTC", "size_signed": 2.0},
                                      {"base": "BTC", "size_signed": -7.5}])
        self.assertIs(flat, False)
        self.assertEqual(remaining, 7.5)

    def test_both_directions_are_considered(self):
        """整体归零 ⇒ 平掉空头时若多头还在，就不算归零（否则会误撤多头的保护腿）。"""
        flat, remaining = self._flat([{"base": "BTC", "size_signed": 4.0}])
        self.assertIs(flat, False)
        self.assertEqual(remaining, 4.0)

    def test_non_dict_rows_are_skipped(self):
        self.assertEqual(self._flat(["junk", None, {"base": "BTC", "size_signed": 0}]),
                         (True, 0.0))

    def test_unparseable_size_counts_as_still_open(self):
        flat, remaining = self._flat([{"base": "BTC", "size_signed": "很多"}])
        self.assertIs(flat, False)
        self.assertEqual(remaining, 1.0, "读不出量按 1.0 保守处理，绝不当作 0")

    def test_missing_size_field_is_flat(self):
        self.assertEqual(self._flat([{"base": "BTC"}]), (True, 0.0))

    def test_read_failure_counts_as_not_flat_without_unbound_local(self):
        """本刀实测被专测抓出过的 `UnboundLocalError`：`remaining` 必须先初始化。"""
        self.adapter.rows = []
        self.adapter.positions = mock.Mock(side_effect=RuntimeError("读不到"))
        flat, remaining = ER._verify_symbol_flat(self.adapter, "BTC")
        self.assertIs(flat, False)
        self.assertEqual(remaining, 0.0)
        self.assertEqual(self.adapter.positions.call_count, ER.CLOSE_FLAT_POLL_TRIES)

    def test_read_failure_then_recovery_is_flat(self):
        calls = {"n": 0}

        def _positions():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("第一次读不到")
            return []

        self.adapter.positions = _positions
        self.assertEqual(ER._verify_symbol_flat(self.adapter, "BTC"), (True, 0.0))

    def test_polling_sleeps_between_attempts(self):
        self._flat([{"base": "BTC", "size_signed": 1.0}], tries=3)
        self.assertEqual(self.sleep.call_count, 3)

    def test_a_single_attempt_when_configured_to_one(self):
        flat, _ = self._flat([{"base": "BTC", "size_signed": 1.0}], tries=1)
        self.assertIs(flat, False)
        self.assertEqual(self.sleep.call_count, 1)
        self.assertEqual(self.pos_mock.call_count, 1)

    def test_zero_tries_still_polls_once(self):
        flat, _ = self._flat([], tries=0)
        self.assertIs(flat, True)
        self.assertEqual(self.pos_mock.call_count, 1)

    def test_case_insensitive_contract_match(self):
        flat, _ = self._flat([{"base": "btc", "size_signed": 1.0}])
        self.assertIs(flat, False)


class ReadSymbolPositionTests(_Base):
    def _read(self, rows, base="BTC", pos_side=None):
        self.adapter.rows = rows
        return ER._read_symbol_position(self.adapter, base, pos_side)

    def test_no_positions_degrades_to_base_only(self):
        self.assertEqual(self._read([]), {"base": "BTC"})

    def test_pos_side_is_seeded_when_given(self):
        self.assertEqual(self._read([], pos_side="Long"),
                         {"base": "BTC", "side": "long"})

    def test_matching_row_contributes_size_and_side(self):
        out = self._read([{"base": "BTC", "size_signed": -2.0, "side": "SHORT"}])
        self.assertEqual(out, {"base": "BTC", "side": "short", "size_signed": -2.0})

    def test_other_contracts_are_ignored(self):
        self.assertEqual(self._read([{"base": "ETH", "size_signed": 9.0}]), {"base": "BTC"})

    def test_non_dict_rows_are_skipped(self):
        self.assertEqual(self._read(["junk"]), {"base": "BTC"})

    def test_without_pos_side_the_first_match_breaks(self):
        out = self._read([{"base": "BTC", "size_signed": 1.0, "side": "long"},
                          {"base": "BTC", "size_signed": 2.0, "side": "short"}])
        self.assertEqual(out["size_signed"], 1.0)

    def test_with_pos_side_it_scans_until_the_side_matches(self):
        out = self._read([{"base": "BTC", "size_signed": 1.0, "side": "long"},
                          {"base": "BTC", "size_signed": 2.0, "side": "short"}],
                         pos_side="short")
        self.assertEqual(out["size_signed"], 2.0)
        self.assertEqual(out["side"], "short")

    def test_row_without_a_side_keeps_the_seeded_one(self):
        out = self._read([{"base": "BTC", "size_signed": 1.0}], pos_side="long")
        self.assertEqual(out["side"], "long")

    def test_read_failure_degrades_instead_of_raising(self):
        self.adapter.positions = mock.Mock(side_effect=RuntimeError("读不到"))
        self.assertEqual(ER._read_symbol_position(self.adapter, "BTC", "long"),
                         {"base": "BTC", "side": "long"})

    def test_base_is_upper_cased(self):
        self.assertEqual(self._read([], base="btc")["base"], "BTC")


class CancelProvenOwnLegsTests(_Base):
    def setUp(self):
        super().setUp()
        self.select = self._start(mock.patch.object(
            venue_protection, "select_legs_to_cancel_after_close",
            return_value={"to_cancel": [], "counts": {"not_touched": 0}}))
        self._start(mock.patch.object(venue_protection, "leg_base",
                                      side_effect=lambda l: l.get("symbol")))
        self._start(mock.patch.object(own_records, "canonical_inst",
                                      side_effect=lambda s: f"{s}USDT"))
        self._start(mock.patch.object(own_records, "load_ledger", return_value={"rows": []}))
        self.ledger_rows = self._start(mock.patch.object(own_records, "read_ledger_rows",
                                                         return_value=[{"r": 1}]))

    def _cancel(self, legs, to_cancel, not_touched=0, before=None):
        self.adapter.protective_legs = legs
        self.select.return_value = {"to_cancel": list(to_cancel),
                                    "counts": {"not_touched": not_touched}}
        return ER._cancel_proven_own_legs(self.adapter, "BTC", before)

    def test_only_legs_of_this_contract_are_considered(self):
        self._cancel([{"symbol": "BTCUSDT", "id": "a"},
                      {"symbol": "ETHUSDT", "id": "b"}], [])
        sent = self.select.call_args[0][1]
        self.assertEqual([l["id"] for l in sent], ["a"])

    def test_selection_receives_the_pre_close_facts(self):
        before = {"base": "BTC", "size_signed": -2.0, "side": "short"}
        self._cancel([], [], before=before)
        self.assertEqual(self.select.call_args[0][0], before)

    def test_selection_falls_back_to_base_only(self):
        self._cancel([], [])
        self.assertEqual(self.select.call_args[0][0], {"base": "BTC"})

    def test_ledger_rows_are_passed_through(self):
        self._cancel([], [])
        self.assertEqual(self.select.call_args[0][2], [{"r": 1}])

    def test_ledger_failure_degrades_to_none(self):
        self.ledger_rows.side_effect = RuntimeError("台账坏了")
        self._cancel([], [])
        self.assertIsNone(self.select.call_args[0][2])

    def test_protective_listing_failure_means_no_legs(self):
        self.adapter.list_protective_orders = mock.Mock(side_effect=RuntimeError("读不到"))
        note = self._cancel([], [])
        self.assertEqual(self.select.call_args[0][1], [])
        self.assertIn("保护腿已撤 0 张", note)

    def test_price_order_cancel_is_preferred(self):
        note = self._cancel([], [{"id": "leg-1"}])
        self.assertEqual(self.adapter.cancel_price_calls, ["leg-1"])
        self.assertIn("保护腿已撤 1 张", note)

    def test_algo_cancel_is_the_fallback(self):
        class _AlgoOnly:
            def list_protective_orders(self, symbol):
                return []

            def cancel_algo_order(self, *, algo_id):
                self.seen = algo_id

        self.adapter = _AlgoOnly()
        note = self._cancel([], [{"id": "leg-2"}])
        self.assertEqual(self.adapter.seen, "leg-2")
        self.assertIn("保护腿已撤 1 张", note)

    def test_absent_cancel_methods_count_as_failed(self):
        class _NoCancel:
            def list_protective_orders(self, symbol):
                return []

        self.adapter = _NoCancel()
        note = self._cancel([], [{"id": "leg-3"}])
        self.assertIn("撤单失败 1 张", note)
        self.assertIn("需人工核对", note)

    def test_items_without_an_id_are_skipped(self):
        note = self._cancel([], [{"id": ""}, {}])
        self.assertIn("保护腿已撤 0 张", note)
        self.assertNotIn("失败", note)

    def test_per_leg_failure_is_counted_not_raised(self):
        self.adapter.cancel_price_order = mock.Mock(side_effect=RuntimeError("撤不动"))
        note = self._cancel([], [{"id": "leg-4"}])
        self.assertIn("撤单失败 1 张", note)

    def test_untouched_legs_are_reported(self):
        note = self._cancel([], [], not_touched=3)
        self.assertIn("另有 3 张**未撤**", note)
        self.assertIn("归属审计", note)

    def test_clean_note_has_no_extra_clauses(self):
        note = self._cancel([], [])
        self.assertEqual(note, "；保护腿已撤 0 张")

    def test_contract_match_requires_the_canonical_form(self):
        """⚠️ 这里**不**做大小写归一：过滤条件是 `leg_base(l) == canonical_inst(base)`。
        所以 `leg_base` 必须返回规范形态，否则该腿会被静默排除
        （第 189 刀踩过的正是这类"静默排除"）。"""
        self._cancel([{"symbol": "btcusdt", "id": "a"}], [])
        self.assertEqual(self.select.call_args[0][1], [])
        self._cancel([{"symbol": "BTCUSDT", "id": "a"}], [])
        self.assertEqual(len(self.select.call_args[0][1]), 1)


class ClosePositionTests(_Base):
    def setUp(self):
        super().setUp()
        # 撤腿链在 `CloseCleanupTests`/`CancelProvenOwnLegsTests` 里被**逐缝**专测；
        # 这里只验平仓本身，闭掉它以免去读生产的 trading_ledger.json。
        # （需要真跑它的那一条用例会把原函数重新盖回去，见下。）
        self._real_cancel = ER._cancel_proven_own_legs
        self._start(mock.patch.object(ER, "_cancel_proven_own_legs",
                                      return_value="；保护腿已撤 0 张"))

    def _close(self, adapter=None, venue="gate", pass_adapter=False, **kwargs):
        ad = adapter or self.adapter
        self.adapter = ad
        self._start(mock.patch.object(ER, "get_adapter", return_value=ad))
        if pass_adapter:
            kwargs["adapter"] = ad
        return ER.close_position("BTC-USDT-SWAP", venue=venue, **kwargs)

    def test_success_shape(self):
        out = self._close()
        self.assertIs(out["ok"], True)
        self.assertEqual(out["stage"], "done")
        self.assertEqual(out["venue"], "gate")
        self.assertEqual(out["asset"], "BTC")
        self.assertIn("市价全平已提交", out["detail"])

    def test_venue_falls_back_to_gate(self):
        out = self._close(adapter=_adapter(venue=None), venue=None)
        self.assertEqual(out["venue"], "gate")

    def test_venue_comes_from_the_injected_adapter(self):
        """⚠️ 第 668 行读的是 `adapter` **形参**：只有调用方显式注入 adapter，
        `capabilities.venue` 才会被采纳（此时 `v` 与实际使用的 `ad` 才一致）。"""
        out = self._close(adapter=_adapter(venue="binance"), venue=None,
                          pass_adapter=True)
        self.assertEqual(out["venue"], "binance")

    def test_uninjected_adapter_keeps_the_label_and_the_adapter_in_step(self):
        """不注入 adapter 时 `v` 就是 `venue`（默认 gate），而 `ad` 由 get_adapter(v) 取
        ⇒ 标签与实际使用的适配器**始终一致**（这正是该表达式不会错配的原因）。"""
        out = self._close(venue="gate")
        self.assertEqual(out["venue"], "gate")
        self.assertEqual(ER.get_adapter.call_args[0][0], "gate")

    def test_sandbox_normalisation_applies_only_to_gate(self):
        gate = _adapter(venue="gate")
        self._close(adapter=gate, venue="gate", environment="demo")
        self.assertEqual(self.require.call_args[1]["environment"], "sandbox")

    def test_require_execution_receives_the_adapter_environment(self):
        self._close(environment="demo")
        self.assertEqual(self.require.call_args[0][0], "gate")
        self.assertEqual(self.require.call_args[1]["environment"], "sandbox")

    def test_refused_gate_propagates(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        self.require.side_effect = ExchangeCapabilityError("未开闸")
        with self.assertRaises(ExchangeCapabilityError):
            self._close()

    def test_close_failure_is_reported(self):
        out = self._close(adapter=_adapter(raises=RuntimeError("被拒")))
        self.assertEqual(out["stage"], "close")
        self.assertIn("平仓失败", out["detail"])

    def test_capability_error_from_the_adapter_propagates(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        with self.assertRaises(ExchangeCapabilityError):
            self._close(adapter=_adapter(raises=ExchangeCapabilityError("不支持")))

    def test_closed_false_is_never_reported_as_success(self):
        out = self._close(adapter=_adapter(closed={"closed": False,
                                                   "reason": "双向持仓歧义"}))
        self.assertEqual(out["stage"], "close")
        self.assertIn("平仓未受理", out["detail"])
        self.assertIn("双向持仓歧义", out["detail"])

    def test_closed_false_without_a_reason_dumps_the_payload(self):
        out = self._close(adapter=_adapter(closed={"closed": False}))
        self.assertIn("'closed': False", out["detail"])

    def test_non_dict_close_results_are_accepted(self):
        self.assertIs(self._close(adapter=_adapter(closed=[]))["ok"], True)

    def test_pos_side_is_forwarded_when_the_signature_supports_it(self):
        adapter = _adapter(accept_pos_side=True)
        self._close(adapter=adapter, pos_side="short")
        self.assertEqual(adapter.closed_kwargs["pos_side"], "short")

    def test_pos_side_is_dropped_when_unsupported(self):
        adapter = _adapter(accept_pos_side=False)
        out = self._close(adapter=adapter, pos_side="short")
        self.assertIs(out["ok"], True)
        self.assertEqual(adapter.closed_kwargs, {"symbol": "BTC"})

    def test_pos_side_is_not_sent_when_absent(self):
        adapter = _adapter(accept_pos_side=True)
        self._close(adapter=adapter)
        self.assertIsNone(adapter.closed_kwargs["pos_side"])

    def test_mock_like_callables_just_lack_the_parameter(self):
        """`inspect.signature(Mock())` 返回 `(*args, **kwargs)`（不抛）⇒ pos_side 不进参。"""
        adapter = _adapter()
        adapter.fast_close_position = mock.Mock(return_value={"closed": True})
        out = self._close(adapter=adapter, pos_side="short")
        self.assertIs(out["ok"], True)
        adapter.fast_close_position.assert_called_once_with("BTC")

    def test_value_error_from_signature_inspection_is_swallowed(self):
        adapter = _adapter(accept_pos_side=True)
        with mock.patch.object(inspect, "signature", side_effect=ValueError("无签名")):
            out = self._close(adapter=adapter, pos_side="short")
        self.assertIs(out["ok"], True)
        self.assertEqual(adapter.closed_kwargs["pos_side"], None,
                         "探测失败就当「不支持 pos_side」⇒ 不进参，绝不因为读不到签名而拒平")

    def test_type_error_from_signature_inspection_is_swallowed(self):
        adapter = _adapter(accept_pos_side=True)
        with mock.patch.object(inspect, "signature", side_effect=TypeError("无签名")):
            out = self._close(adapter=adapter, pos_side="short")
        self.assertIs(out["ok"], True)

    def test_pre_close_facts_are_captured_before_the_close(self):
        adapter = _adapter(rows=[{"base": "BTC", "size_signed": -2.0,
                                  "side": "short"}])
        real_select = self._start(mock.patch.object(
            venue_protection, "select_legs_to_cancel_after_close",
            return_value={"to_cancel": [], "counts": {"not_touched": 0}}))
        self._start(mock.patch.object(venue_protection, "leg_base",
                                      side_effect=lambda l: l.get("symbol")))
        self._start(mock.patch.object(own_records, "canonical_inst",
                                      side_effect=lambda s: f"{s}USDT"))
        self._start(mock.patch.object(own_records, "load_ledger", return_value=None))
        self._start(mock.patch.object(own_records, "read_ledger_rows",
                                      return_value=[]))
        self._start(mock.patch.object(ER, "_verify_symbol_flat",
                                      return_value=(True, 0.0)))
        # 把真函数盖回 Mock 之上（后 start 的 patch 在顶层）
        self._start(mock.patch.object(ER, "_cancel_proven_own_legs",
                                      self._real_cancel))
        self._close(adapter=adapter)
        snapshot = real_select.call_args[0][0]
        self.assertEqual(snapshot["size_signed"], -2.0,
                         "归属判定必须拿到**平仓前**的量（平完再读只剩空仓）")
        self.assertEqual(snapshot["side"], "short")


class CloseCleanupTests(_Base):
    def setUp(self):
        super().setUp()
        self.flat = self._start(mock.patch.object(ER, "_verify_symbol_flat",
                                                 return_value=(True, 0.0)))
        self.cancel = self._start(mock.patch.object(
            ER, "_cancel_proven_own_legs", return_value="；保护腿已撤 2 张"))

    def _close(self, venue="gate", **kwargs):
        return ER.close_position("BTC", venue=venue, adapter=self.adapter, **kwargs)

    def test_flat_contract_triggers_the_leg_cleanup(self):
        out = self._close()
        self.assertEqual(self.flat.call_count, 1)
        self.cancel.assert_called_once()
        self.assertIn("保护腿已撤 2 张", out["detail"])

    def test_cleanup_uses_the_pre_close_snapshot(self):
        self._close()
        self.assertEqual(self.cancel.call_args[0][1], "BTC")
        self.assertEqual(self.cancel.call_args[0][2], {"base": "BTC"})

    def test_not_flat_keeps_the_legs_with_a_remaining_note(self):
        self.flat.return_value = (False, 3.5)
        out = self._close()
        self.cancel.assert_not_called()
        self.assertIn("未核验归零（同合约仍有 3.5）", out["detail"])
        self.assertIn("保护腿保持不动", out["detail"])

    def test_not_flat_without_a_readable_size_says_so(self):
        self.flat.return_value = (False, 0.0)
        out = self._close()
        self.assertIn("未核验归零（持仓读不到）", out["detail"])

    def test_okx_skips_the_cleanup_entirely(self):
        out = self._close(venue="okx")
        self.assertEqual(self.flat.call_count, 0)
        self.cancel.assert_not_called()
        self.assertNotIn("保护腿", out["detail"])
        self.assertIs(out["ok"], True)

    def test_the_switch_turns_the_cleanup_off(self):
        self._start(mock.patch.object(ER, "CANCEL_STALE_PROTECTION_ON_CLOSE", False))
        out = self._close()
        self.assertEqual(self.flat.call_count, 0)
        self.assertIs(out["ok"], True)

    def test_cleanup_exception_never_changes_the_close_verdict(self):
        self.flat.side_effect = RuntimeError("核验炸了")
        out = self._close()
        self.assertIs(out["ok"], True, "收尾失败不得让上层以为平仓失败并重试")
        self.assertIn("保护腿收尾异常", out["detail"])
        self.assertIn("需人工核对", out["detail"])

    def test_cleanup_failure_does_not_leak_the_exception_text(self):
        self.flat.side_effect = RuntimeError("内部路径 /data/dsh/secret")
        out = self._close()
        self.assertIn("RuntimeError", out["detail"])

    def test_detail_keeps_the_adapter_payload_and_the_note(self):
        out = self._close()
        self.assertIn("市价全平已提交", out["detail"])
        self.assertIn("保护腿已撤 2 张", out["detail"])

    def test_truncation_of_the_adapter_payload(self):
        self.adapter2 = _adapter(closed={"closed": True, "blob": "x" * 500})
        out = ER.close_position("BTC", venue="gate", adapter=self.adapter2)
        self.assertLess(len(out["detail"]), 400)


class ImportFallbackTests(unittest.TestCase):
    """`risk_constants` 导入兜底：把模块**源码**在**全新命名空间**里编译执行。

    为什么不用 `importlib.reload`（`tests/trading/test_router_refusal_stages.py:308` 用的那招）：
    reload 是在**同一个模块命名空间**里重跑模块体，所以上一轮已经绑定的名字会**幸存**。
    实测差异：reload 路径下 `TOTAL_EXPOSURE_CAP` 仍是旧值（套件环境为 0.0）⇒ 断言"退化为 0"
    会**通过**；而全新命名空间里这个名字**根本没被绑定**。也就是说 reload 版把兜底的
    一个漏洞盖住了 —— 本类用全新命名空间把真实行为钉出来（同一 `__file__` ⇒ 覆盖率同文件计）。
    """

    def _exec_module(self, modules):
        import sys
        from pathlib import Path
        src = Path(ER.__file__).read_text(encoding="utf-8")
        ns = {"__name__": "r20_execution_router_fallback_probe",
              "__file__": ER.__file__, "__package__": "r20_backend"}
        with mock.patch.dict(sys.modules, modules):
            exec(compile(src, ER.__file__, "exec"), ns)
        return ns

    def test_missing_exposure_cap_alone_falls_back_to_zero(self):
        """内层兜底：模块在、只缺 `MAX_TOTAL_EXPOSURE_USDT` ⇒ 敞口帽退化 0.0，其余常量照用。"""
        fake = types.ModuleType("scripts.risk_constants")
        fake.MAX_LEVERAGE = 5.0
        fake.MIN_LEVERAGE = 2.0
        fake.MAX_MARGIN_EQUITY_RATIO = 0.25
        fake.MAX_SINGLE_ASSET_MARGIN = 1000.0
        ns = self._exec_module({"scripts.risk_constants": fake})
        self.assertEqual(ns["TOTAL_EXPOSURE_CAP"], 0.0, "缺敞口帽 ⇒ 0.0（闸门停用），不是无上限")
        self.assertEqual(ns["MAX_LEVERAGE"], 5.0, "这是内层兜底：其余常量照用")
        self.assertEqual(ns["MIN_LEVERAGE"], 2.0)

    def test_outer_fallback_leaves_the_exposure_cap_unbound(self):
        """⚠️ 实测缺陷：两级兜底都走到底时，`MAX_MARGIN_EQUITY_RATIO`/`MAX_LEVERAGE` 都赋了值，
        但 **`TOTAL_EXPOSURE_CAP` 从未被绑定** —— 这个部署里 `scripts.risk_constants` 一直可导入，
        所以是**潜伏**问题：一旦真的走到这条兜底，`open_protected_position` 第 254 行
        `total_exposure_cap=TOTAL_EXPOSURE_CAP` 会抛 `NameError`，而不是"退化为闸门停用"。
        现有 reload 版用例（`tests/trading/...::ImportFallbackConstantsTest`）因为旧值幸存而看不到这一点。"""
        ns = self._exec_module({"scripts.risk_constants": None,
                                "risk_constants": None})
        self.assertEqual(ns["MAX_MARGIN_EQUITY_RATIO"], 0.20)
        self.assertEqual(ns["MAX_SINGLE_ASSET_MARGIN"], 0.0)
        self.assertIsNone(ns["MAX_LEVERAGE"], "常量为空 ⇒ 上限必须是 None（夹取 no-op）")
        self.assertIsNone(ns["MIN_LEVERAGE"])
        self.assertNotIn("TOTAL_EXPOSURE_CAP", ns,
                         "兜底没有绑定它 ⇒ 真正走到这里会 NameError（reload 版被旧值掩盖）")

    def test_the_probe_namespace_never_touches_the_live_module(self):
        before = (ER.TOTAL_EXPOSURE_CAP, ER.MAX_LEVERAGE, ER.MIN_LEVERAGE,
                  ER.MAX_MARGIN_EQUITY_RATIO, ER.MAX_SINGLE_ASSET_MARGIN)
        self._exec_module({"scripts.risk_constants": None, "risk_constants": None})
        after = (ER.TOTAL_EXPOSURE_CAP, ER.MAX_LEVERAGE, ER.MIN_LEVERAGE,
                 ER.MAX_MARGIN_EQUITY_RATIO, ER.MAX_SINGLE_ASSET_MARGIN)
        self.assertEqual(after, before, "隔离命名空间执行不得改动线上模块对象")


class ConstantsTests(unittest.TestCase):
    def test_documented_constants(self):
        self.assertIs(ER.CANCEL_STALE_PROTECTION_ON_CLOSE, True)
        self.assertEqual(ER.CLOSE_FLAT_POLL_TRIES, 5)
        self.assertEqual(ER.CLOSE_FLAT_POLL_SLEEP, 0.6)


if __name__ == "__main__":
    unittest.main()
