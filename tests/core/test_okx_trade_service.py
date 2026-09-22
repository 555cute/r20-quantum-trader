"""OKX 直连交易服务：**令牌契约与"HTTP 200 ≠ 受保护"的逐腿核验**（第二百七十一刀，开新面 okx_trade_service.py）。

先打印整个文件（281 行）再动笔。两块：

**A. 平仓令牌（与第 270 刀的 `close_intent` 同构的 OKX 侧）**
| 语义 | 口径 |
|---|---|
| ★ **未配置即拒（fail-closed）** | `account_snapshot` / `fast_close_confirmed` 在 `env.configured` 为假时抛 `OKXNotConfigured`，文案明确指向"后台配置 V5 API Key" |
| ★ **快照内联签发令牌** | 每条非零仓位都带 `close_token`/`close_confirmation`/`close_token_expires_in`；尘量（`|pos|<=1e-12`）仓位不进快照 |
| ★ **令牌一次性 + 环境绑定** | `_consume_intent` 先 pop 再判过期；`env.identity` 与登记值不符 ⇒ 拒（环境/凭证切换后旧令牌作废）|
| ★ **缺 `posSide` 默认 `net`** | `_position_match` 用 `"net"` 兜底（第 187 刀修的正是这里曾用 `""` 导致净持仓匹配不到）|
| ★ **同仓位委托必须先撤干净** | 撤单失败 ⇒ `RuntimeError`（绝不带着冲突委托去平）；algo 撤单失败只告警不拦（`pending_algo_orders` 扫描失败同样只告警）|
| ★ **归零核验** | 最多回读 10 次 × 0.7s；仍未归零 ⇒ `RuntimeError` + "禁止重复点击" |

**B. attachAlgoOrds 保护核验（纯函数，无 sleep 无轮询）**
| 语义 | 口径 |
|---|---|
| ★ **主单直接终态 canceled ⇒ 未保护** | 附带算法单永不会被提交 ⇒ 三态直接 `UNPROTECTED`（不等待 live）|
| ★ **回执 failCode 非空 ⇒ 该腿失败** | 支持按 `attachAlgoClOrdId` 或（数量+触发值+方向）匹配；`"0"` 不算失败 |
| ★ **受保护必须逐字段回读命中** | 合约 + 方向 + 触发值 + 数量**精确等**才算 protected |
| ★ **结构性短缺 ≠ 还没回读到** | side/触发匹配但数量 < 期望（含多行累加）⇒ `coverage_shortfall` → `UNPROTECTED`，让下游能补挂 |
| ★ **信息不齐时不武断** | 出现不可量化/超量行 ⇒ 不判短缺，留在 `pending_readback`；多行合计已达标但非单行精确等也留 pending |
| 空腿集 | `expected_legs=[]` 且主单已 filled ⇒ UNPROTECTED（没有任何保护腿）；否则 pending |
"""

import threading
import types
import unittest
from decimal import Decimal
from unittest import mock

from r20_backend import okx_trade_service as OT


def _env(mode="demo", identity="demo-id", configured=True):
    return types.SimpleNamespace(mode=mode, identity=identity, configured=configured)


def _pos(inst_id="BTC-USDT-SWAP", pos="2", pos_side="long", pos_id="p1", mgn_mode="cross"):
    return {"instId": inst_id, "pos": pos, "posSide": pos_side, "posId": pos_id,
            "mgnMode": mgn_mode}


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.now = 50_000.0
        OT._INTENTS.clear()
        self.addCleanup(OT._INTENTS.clear)
        self._start(mock.patch.object(OT.time, "time", side_effect=lambda: self.now))
        self._start(mock.patch.object(OT.time, "sleep"))


class RequestFacadeTests(_Base):
    def test_explicit_env_is_used(self):
        current = self._start(mock.patch.object(OT, "current_environment"))
        inner = self._start(mock.patch.object(OT.okx_rest, "request",
                                              return_value=[{"a": 1}]))
        env = _env()
        self.assertEqual(OT._request("GET", "/x", {"p": 1}, env, timeout=5),
                         [{"a": 1}])
        current.assert_not_called()
        inner.assert_called_once_with("GET", "/x", {"p": 1}, env=env, timeout=5)

    def test_current_environment_is_the_default(self):
        current = self._start(mock.patch.object(OT, "current_environment",
                                                return_value="CUR"))
        inner = self._start(mock.patch.object(OT.okx_rest, "request",
                                              return_value=[]))
        OT._request("GET", "/x")
        self.assertEqual(inner.call_args[1]["env"], "CUR")
        self.assertEqual(inner.call_args[1]["timeout"], 20)


class IntentLifecycleTests(_Base):
    def test_create_intent_shape_and_confirmation(self):
        env = _env(mode="live", identity="live-id")
        token, confirmation = OT._create_intent(env, _pos(pos="2.5", pos_side="SHORT"))
        self.assertEqual(confirmation, "CLOSE LIVE BTC-USDT-SWAP SHORT 2.5")
        record = OT._INTENTS[token]
        self.assertEqual(record["environment_id"], "live-id")
        self.assertEqual(record["posSide"], "short")
        self.assertEqual(record["expected_size"], 2.5)
        self.assertEqual(record["posId"], "p1")
        self.assertEqual(record["expires_at"], self.now + OT.INTENT_TTL_SECONDS)

    def test_missing_pos_and_pos_side_get_defaults(self):
        token, confirmation = OT._create_intent(_env(), {"instId": "X"})
        record = OT._INTENTS[token]
        self.assertEqual(record["expected_size"], 0.0)
        self.assertEqual(record["posSide"], "net")
        self.assertIn("NET", confirmation)
        self.assertEqual(record["posId"], "")

    def test_stale_intents_are_pruned_on_create(self):
        first, _ = OT._create_intent(_env(), _pos())
        self.now += OT.INTENT_TTL_SECONDS + 1
        second, _ = OT._create_intent(_env(), _pos())
        self.assertNotIn(first, OT._INTENTS)
        self.assertIn(second, OT._INTENTS)

    def test_consume_is_one_shot_and_expiry_aware(self):
        token, _ = OT._create_intent(_env(), _pos())
        self.assertEqual(OT._consume_intent(token)["instId"], "BTC-USDT-SWAP")
        with self.assertRaises(ValueError) as ctx:
            OT._consume_intent(token)
        self.assertIn("无效或已使用", str(ctx.exception))

        expiring, _ = OT._create_intent(_env(), _pos())
        self.now += OT.INTENT_TTL_SECONDS + 1
        with self.assertRaises(ValueError) as ctx:
            OT._consume_intent(expiring)
        self.assertIn("已过期", str(ctx.exception))
        self.assertNotIn(expiring, OT._INTENTS, "过期令牌先 pop ⇒ 已被消费")


class PositionMatchTests(unittest.TestCase):
    def test_missing_pos_side_defaults_to_net_on_both_sides(self):
        intent = {"instId": "X", "posSide": "net", "posId": ""}
        self.assertIsNotNone(OT._position_match([{"instId": "X"}], intent),
                             "缺 posSide 的仓位行必须能配上 net 意图")

    def test_side_match_is_case_insensitive(self):
        intent = {"instId": "X", "posSide": "long", "posId": ""}
        self.assertIsNotNone(OT._position_match([{"instId": "X", "posSide": "LONG"}],
                                                intent))

    def test_inst_and_side_must_both_match(self):
        intent = {"instId": "X", "posSide": "long", "posId": ""}
        self.assertIsNone(OT._position_match([{"instId": "Y", "posSide": "long"}], intent))
        self.assertIsNone(OT._position_match([{"instId": "X", "posSide": "short"}], intent))

    def test_pos_id_narrows_the_match_when_present(self):
        intent = {"instId": "X", "posSide": "long", "posId": "p2"}
        rows = [{"instId": "X", "posSide": "long", "posId": "p1"},
                {"instId": "X", "posSide": "long", "posId": "p2"}]
        self.assertEqual(OT._position_match(rows, intent)["posId"], "p2")
        self.assertIsNone(OT._position_match(rows[:1], intent))

    def test_first_candidate_wins(self):
        intent = {"instId": "X", "posSide": "long", "posId": ""}
        rows = [{"instId": "X", "posSide": "long", "tag": "first"},
                {"instId": "X", "posSide": "long", "tag": "second"}]
        self.assertEqual(OT._position_match(rows, intent)["tag"], "first")


class AccountSnapshotTests(_Base):
    def test_unconfigured_raises_with_the_mode_in_the_message(self):
        self._start(mock.patch.object(OT, "current_environment",
                                      return_value=_env(mode="live", configured=False)))
        with self.assertRaises(OT.OKXNotConfigured) as ctx:
            OT.account_snapshot()
        self.assertIn("LIVE", str(ctx.exception))
        self.assertIn("禁止交易", str(ctx.exception))

    def test_dust_positions_are_filtered_and_tokens_are_inlined(self):
        self._start(mock.patch.object(OT, "current_environment",
                                      return_value=_env(mode="demo")))
        calls = []

        def _req(method, path, params=None, env=None, timeout=20):
            calls.append((method, path, params))
            if path.endswith("/account/positions"):
                return [_pos(pos="2"), _pos(inst_id="ETH-USDT-SWAP", pos="1e-15")]
            return [{"ordId": "o1"}]

        self._start(mock.patch.object(OT, "_request", side_effect=_req))
        snap = OT.account_snapshot()
        self.assertEqual(snap["environment"], "demo")
        self.assertEqual(snap["environment_id"], "demo-id")
        self.assertEqual(snap["credential_source"], "static-v5-key")
        self.assertEqual(len(snap["positions"]), 1, "尘量仓位不进快照")
        self.assertEqual(snap["orders"], [{"ordId": "o1"}])
        self.assertEqual(snap["captured_at_ms"], int(self.now * 1000))
        entry = snap["positions"][0]
        self.assertIn(entry["close_token"], OT._INTENTS)
        self.assertEqual(entry["close_confirmation"],
                         "CLOSE DEMO BTC-USDT-SWAP LONG 2")
        self.assertEqual(entry["close_token_expires_in"], OT.INTENT_TTL_SECONDS)

    def test_empty_account_is_fine(self):
        self._start(mock.patch.object(OT, "current_environment", return_value=_env()))
        self._start(mock.patch.object(OT, "_request", return_value=[]))
        snap = OT.account_snapshot()
        self.assertEqual(snap["positions"], [])
        self.assertEqual(snap["orders"], [])


class FastCloseTests(_Base):
    def _wire(self, positions_seq, orders=None, close_result=None, algo=None,
              algo_error=None, cancel_error=None):
        seq = list(positions_seq)

        def _req(method, path, params=None, env=None, timeout=20):
            if path.endswith("/account/positions"):
                return seq.pop(0) if seq else []
            if path.endswith("/trade/orders-pending"):
                return list(orders or [])
            if path.endswith("/trade/cancel-order"):
                if cancel_error:
                    raise cancel_error
                return [{"sCode": "0"}]
            if path.endswith("/trade/close-position"):
                return list(close_result or [{"sCode": "0"}])
            return []

        self.request = self._start(mock.patch.object(OT, "_request", side_effect=_req))
        self.algo = self._start(mock.patch.object(
            OT, "pending_algo_orders",
            side_effect=algo_error if algo_error else None,
            return_value=None if algo_error else list(algo or [])))
        self.cancel_algo = self._start(mock.patch.object(OT, "cancel_algo_orders"))
        return self.request

    def _intent(self, env=None, pos=None, pos_side="long"):
        env = env or _env()
        self._start(mock.patch.object(OT, "current_environment", return_value=env))
        token, confirmation = OT._create_intent(env, pos or _pos(pos="2", pos_side=pos_side))
        return token, confirmation

    def test_unconfigured_is_refused(self):
        self._start(mock.patch.object(OT, "current_environment",
                                      return_value=_env(configured=False)))
        with self.assertRaises(OT.OKXNotConfigured) as ctx:
            OT.fast_close_confirmed("t", "c")
        self.assertIn("禁止应急平仓", str(ctx.exception))

    def test_invalid_token_and_identity_drift_are_refused(self):
        self._wire([[_pos()]])
        self._intent()
        with self.assertRaises(ValueError) as ctx:
            OT.fast_close_confirmed("nope", "c")
        self.assertIn("无效或已使用", str(ctx.exception))

        token, confirmation = self._intent()
        self._start(mock.patch.object(OT, "current_environment",
                                      return_value=_env(identity="other-id")))
        with self.assertRaises(ValueError) as ctx:
            OT.fast_close_confirmed(token, confirmation)
        self.assertIn("环境或凭证已变化", str(ctx.exception))

    def test_phrase_mismatch_reports_the_required_phrase(self):
        self._wire([[_pos()]])
        token, confirmation = self._intent()
        with self.assertRaises(ValueError) as ctx:
            OT.fast_close_confirmed(token, "CLOSE WRONG")
        self.assertIn(confirmation, str(ctx.exception))

    def test_vanished_position_is_refused(self):
        self._wire([[]])
        token, confirmation = self._intent()
        with self.assertRaises(ValueError) as ctx:
            OT.fast_close_confirmed(token, confirmation)
        self.assertIn("目标仓位已不存在", str(ctx.exception))

    def test_size_drift_is_refused(self):
        self._wire([[_pos(pos="3")]])
        token, confirmation = self._intent(pos=_pos(pos="2"))
        with self.assertRaises(ValueError) as ctx:
            OT.fast_close_confirmed(token, confirmation)
        self.assertIn("请刷新", str(ctx.exception))

    def test_pending_orders_on_other_side_are_left_alone(self):
        self._wire([[_pos()], []], orders=[
            {"ordId": "keep", "posSide": "short"},
            {"ordId": "kill", "posSide": "long"},
            {"ordId": "", "posSide": "long"}])
        token, confirmation = self._intent()
        out = OT.fast_close_confirmed(token, confirmation)
        self.assertEqual(out["canceled_entry_orders"], ["kill"],
                         "空 ordId 跳过；反向委托不动")

    def test_un_cancellable_order_blocks_the_close(self):
        self._wire([[_pos()], []], orders=[{"ordId": "bad", "posSide": "long"}],
                   cancel_error=RuntimeError("撤单被拒"))
        token, confirmation = self._intent()
        with self.assertRaises(RuntimeError) as ctx:
            OT.fast_close_confirmed(token, confirmation)
        self.assertIn("无法撤销", str(ctx.exception))
        self.assertIn("bad", str(ctx.exception))

    def test_algo_orders_are_cancelled_and_failures_only_warn(self):
        self._wire([[_pos()], []], algo=[{"algoId": "a1", "posSide": "long"},
                                         {"algoId": "a2", "posSide": "short"},
                                         {"algoId": "", "posSide": "long"}])
        token, confirmation = self._intent()
        out = OT.fast_close_confirmed(token, confirmation)
        self.assertEqual(out["canceled_entry_orders"], ["algo:a1"])

    def test_algo_scan_failure_is_swallowed(self):
        self._wire([[_pos()], []], algo_error=RuntimeError("扫描失败"))
        token, confirmation = self._intent()
        out = OT.fast_close_confirmed(token, confirmation)
        self.assertEqual(out["status"], "confirmed_closed")

    def test_close_payload_uses_the_target_margin_mode(self):
        request = self._wire([[_pos(mgn_mode="isolated")], []])
        token, confirmation = self._intent()
        OT.fast_close_confirmed(token, confirmation)
        close_calls = [c for c in request.call_args_list
                       if c[0][1].endswith("/trade/close-position")]
        payload = close_calls[0][0][2]
        self.assertEqual(payload["instId"], "BTC-USDT-SWAP")
        self.assertEqual(payload["mgnMode"], "isolated")
        self.assertEqual(payload["posSide"], "long")
        self.assertTrue(payload["autoCxl"])
        self.assertTrue(payload["clOrdId"].startswith("r20close"))

    def test_net_intent_closes_with_net_side(self):
        env = _env()
        self._start(mock.patch.object(OT, "current_environment", return_value=env))
        request = self._wire([[_pos(pos_side="net", pos="2")], []])
        token, confirmation = OT._create_intent(env, _pos(pos="2", pos_side="net"))
        OT.fast_close_confirmed(token, confirmation)
        close_calls = [c for c in request.call_args_list
                       if c[0][1].endswith("/trade/close-position")]
        self.assertEqual(close_calls[0][0][2]["posSide"], "net")

    def test_side_is_inferred_from_the_sign_when_the_intent_is_net_but_row_is_signed(self):
        env = _env()
        self._start(mock.patch.object(OT, "current_environment", return_value=env))
        request = self._wire([[{"instId": "BTC-USDT-SWAP", "pos": "-2",
                                "posSide": "net", "posId": "p1",
                                "mgnMode": "cross"}], []])
        token, confirmation = OT._create_intent(env, {"instId": "BTC-USDT-SWAP",
                                                      "pos": "-2", "posSide": "net",
                                                      "posId": "p1"})
        OT.fast_close_confirmed(token, confirmation)
        close_calls = [c for c in request.call_args_list
                       if c[0][1].endswith("/trade/close-position")]
        self.assertEqual(close_calls[0][0][2]["posSide"], "net",
                         "net 档位下不下有方向的 posSide")

    def test_position_not_zeroed_after_ten_polls_raises(self):
        self._wire([[_pos()]] + [[_pos()] for _ in range(12)])
        token, confirmation = self._intent()
        with self.assertRaises(RuntimeError) as ctx:
            OT.fast_close_confirmed(token, confirmation)
        self.assertIn("未确认归零", str(ctx.exception))
        self.assertIn("禁止重复点击", str(ctx.exception))
        self.assertEqual(OT.time.sleep.call_count, 10)
        self.assertEqual(OT.time.sleep.call_args[0][0], 0.7)

    def test_early_zero_stops_the_polling(self):
        self._wire([[_pos()], []])
        token, confirmation = self._intent()
        out = OT.fast_close_confirmed(token, confirmation)
        self.assertEqual(out["status"], "confirmed_closed")
        self.assertEqual(out["closed_size"], 2.0)
        self.assertEqual(OT.time.sleep.call_count, 1)

    def test_missing_position_during_polling_counts_as_flat(self):
        self._wire([[_pos()], [], []])
        token, confirmation = self._intent()
        self.assertEqual(OT.fast_close_confirmed(token, confirmation)["status"],
                         "confirmed_closed")


class DecimalHelperTests(unittest.TestCase):
    def test_dec_eq_rejects_empty_and_handles_precision(self):
        self.assertFalse(OT._dec_eq(None, "4"))
        self.assertFalse(OT._dec_eq("4", ""))
        self.assertTrue(OT._dec_eq("4.000000", "4"))
        self.assertFalse(OT._dec_eq("4.1", "4"))

    def test_dec_eq_falls_back_to_string_compare(self):
        self.assertTrue(OT._dec_eq("abc", "abc"))
        self.assertFalse(OT._dec_eq("abc", "abd"))

    def test_dec_num_returns_none_when_unquantifiable(self):
        self.assertEqual(OT._dec_num("4.0"), Decimal("4.0"))
        self.assertEqual(OT._dec_num(0), Decimal("0"))
        self.assertIsNone(OT._dec_num(None))
        self.assertIsNone(OT._dec_num(""))
        self.assertIsNone(OT._dec_num("abc"))

    def test_dec_text_avoids_float_and_scientific_noise(self):
        self.assertEqual(OT._dec_text(Decimal("4.000")), "4")
        self.assertEqual(OT._dec_text(Decimal("4.5")), "4.5")
        self.assertEqual(OT._dec_text(Decimal("1E+3")), "1000",
                         "科学计数法要落成人话整数")


def _leg(kind="tp", side="sell", sz="4", x_price="100", cid=None):
    leg = {"kind": kind, "side": side, "sz": sz, "x_price": x_price}
    if cid:
        leg["attach_algo_cl_ord_id"] = cid
    return leg


def _pending(algo_id="a1", inst="BTC-USDT-SWAP", side="sell", sz="4", x_price="100"):
    return {"algoId": algo_id, "instId": inst, "side": side, "sz": sz, "xPrice": x_price}


class ProtectionTerminalStateTests(unittest.TestCase):
    def test_canceled_main_order_is_unprotected_without_waiting(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            pending_rows=[])
        self.assertEqual(out["status"], "PROTECTION_PENDING")

        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            pending_rows=[],
                                            main_order_state="CANCELLED")
        self.assertEqual(out["status"], "UNPROTECTED")
        self.assertEqual(out["legs"][0]["state"], "not_submitted")
        self.assertIn("终态", out["detail"])

    def test_empty_leg_set_with_filled_main_order_is_unprotected(self):
        out = OT.verify_attached_protection(inst_id="X", expected_legs=[],
                                            pending_rows=[], main_order_state="filled")
        self.assertEqual(out["status"], "UNPROTECTED")
        self.assertEqual(out["legs"], [])


class ProtectionReceiptTests(unittest.TestCase):
    def test_fail_code_marks_the_leg_failed(self):
        rows = [{"sz": "4", "xPrice": "100", "side": "sell",
                 "failCode": "51008", "failReason": "余额不足"}]
        out = OT.verify_attached_protection(inst_id="X", expected_legs=[_leg()],
                                            attach_rows=rows, pending_rows=[])
        self.assertEqual(out["status"], "UNPROTECTED")
        self.assertEqual(out["legs"][0]["state"], "failed")
        self.assertEqual(out["legs"][0]["failCode"], "51008")
        self.assertIn("余额不足", out["legs"][0]["failReason"])

    def test_zero_fail_code_is_not_a_failure(self):
        rows = [{"sz": "4", "xPrice": "100", "side": "sell", "failCode": "0"}]
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            attach_rows=rows,
                                            pending_rows=[_pending()])
        self.assertEqual(out["status"], "PROTECTED")

    def test_receipt_row_can_be_matched_by_attach_client_id(self):
        rows = [{"attachAlgoClOrdId": "cid-1", "failCode": "1",
                 "failReason": "拒绝"}]
        out = OT.verify_attached_protection(inst_id="X",
                                            expected_legs=[_leg(cid="cid-1")],
                                            attach_rows=rows, pending_rows=[])
        self.assertEqual(out["legs"][0]["state"], "failed")

    def test_unrelated_receipt_rows_are_ignored(self):
        rows = [{"sz": "999", "xPrice": "100", "side": "sell",
                 "failCode": "1", "failReason": "别的腿"}]
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            attach_rows=rows,
                                            pending_rows=[_pending()])
        self.assertEqual(out["status"], "PROTECTED", "只按本腿匹配，不能被别的腿牵连")

    def test_non_dict_receipt_rows_are_skipped(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            attach_rows=["junk", None],
                                            pending_rows=[_pending()])
        self.assertEqual(out["status"], "PROTECTED")

    def test_receipt_matching_by_trigger_alternate_field_names(self):
        rows = [{"sz": "4", "tpTriggerPx": "100", "side": "sell",
                 "failCode": "2", "failReason": "x"}]
        out = OT.verify_attached_protection(inst_id="X", expected_legs=[_leg()],
                                            attach_rows=rows, pending_rows=[])
        self.assertEqual(out["legs"][0]["state"], "failed")


class ProtectionReadbackTests(unittest.TestCase):
    def test_exact_readback_is_protected(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            pending_rows=[_pending()])
        self.assertEqual(out["status"], "PROTECTED")
        self.assertEqual(out["legs"][0]["state"], "protected")
        self.assertEqual(out["legs"][0]["algoId"], "a1")
        self.assertIn("回读", out["detail"])
        self.assertIn("核验", out["detail"])

    def test_all_legs_must_be_protected(self):
        out = OT.verify_attached_protection(
            inst_id="BTC-USDT-SWAP",
            expected_legs=[_leg(kind="tp"), _leg(kind="sl", side="buy", x_price="90")],
            pending_rows=[_pending()])
        self.assertEqual(out["status"], "PROTECTION_PENDING")
        states = {l["kind"]: l["state"] for l in out["legs"]}
        self.assertEqual(states["tp"], "protected")
        self.assertEqual(states["sl"], "pending_readback")

    def test_filled_main_order_with_pending_leg_stays_pending(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            pending_rows=[],
                                            main_order_state="filled")
        self.assertEqual(out["status"], "PROTECTION_PENDING",
                         "回执无 failCode 但尚未回读到 ⇒ 不得宣称受保护，也不冤枉成短缺")

    def test_inst_id_mismatch_is_not_counted(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            pending_rows=[_pending(inst="ETH-USDT-SWAP")])
        self.assertEqual(out["legs"][0]["state"], "pending_readback")

    def test_side_mismatch_is_not_counted(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg(side="sell")],
                                            pending_rows=[_pending(side="buy")])
        self.assertEqual(out["legs"][0]["state"], "pending_readback")

    def test_trigger_mismatch_is_not_counted(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg(x_price="100")],
                                            pending_rows=[_pending(x_price="101")])
        self.assertEqual(out["legs"][0]["state"], "pending_readback")

    def test_trigger_check_is_skipped_when_the_leg_has_no_price(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg(x_price=None)],
                                            pending_rows=[_pending(x_price="101")])
        self.assertEqual(out["status"], "PROTECTED")

    def test_side_check_is_skipped_when_the_leg_has_no_side(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg(side=None)],
                                            pending_rows=[_pending(side="buy")])
        self.assertEqual(out["status"], "PROTECTED")

    def test_leg_without_size_matches_the_first_structurally_matching_row(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg(sz=None)],
                                            pending_rows=[_pending(sz="999")])
        self.assertEqual(out["status"], "PROTECTED")

    def test_non_dict_pending_rows_are_skipped(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            pending_rows=["junk", None, _pending()])
        self.assertEqual(out["status"], "PROTECTED")

    def test_alternate_trigger_field_names_are_accepted(self):
        row = {"algoId": "a9", "instId": "BTC-USDT-SWAP", "side": "sell",
               "sz": "4", "slTriggerPx": "100"}
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg()],
                                            pending_rows=[row])
        self.assertEqual(out["status"], "PROTECTED")


class ProtectionShortfallTests(unittest.TestCase):
    def test_single_short_row_is_a_structural_shortfall(self):
        out = OT.verify_attached_protection(inst_id="BTC-USDT-SWAP",
                                            expected_legs=[_leg(sz="4")],
                                            pending_rows=[_pending(sz="3")])
        leg = out["legs"][0]
        self.assertEqual(leg["state"], "coverage_shortfall")
        self.assertEqual(out["status"], "UNPROTECTED")
        self.assertIn("covered 3 < filled 4", leg["reason"])
        self.assertEqual(leg["algo_ids"], ["a1"])
        self.assertIn("短缺", out["detail"])

    def test_multiple_short_rows_are_summed(self):
        out = OT.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[_leg(sz="4")],
            pending_rows=[_pending(algo_id="a1", sz="1"),
                          _pending(algo_id="a2", sz="2")])
        leg = out["legs"][0]
        self.assertEqual(leg["state"], "coverage_shortfall")
        self.assertIn("covered 3 < filled 4", leg["reason"])
        self.assertEqual(leg["algo_ids"], ["a1", "a2"])

    def test_multiple_rows_meeting_the_target_are_only_pending(self):
        out = OT.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[_leg(sz="4")],
            pending_rows=[_pending(algo_id="a1", sz="3"),
                          _pending(algo_id="a2", sz="3")])
        self.assertEqual(out["legs"][0]["state"], "pending_readback",
                         "多行合计已达标但非单行精确等 ⇒ 保守留在 pending，不冤枉成短缺")
        self.assertEqual(out["status"], "PROTECTION_PENDING")

    def test_an_over_covered_row_suppresses_the_shortfall_verdict(self):
        out = OT.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[_leg(sz="4")],
            pending_rows=[_pending(algo_id="a1", sz="3"),
                          _pending(algo_id="a2", sz="99")])
        self.assertEqual(out["legs"][0]["state"], "pending_readback",
                         "出现超量行 ⇒ 信息不齐，不武断判短缺")

    def test_an_unquantifiable_row_also_suppresses_the_shortfall_verdict(self):
        out = OT.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[_leg(sz="4")],
            pending_rows=[_pending(algo_id="a1", sz="3"),
                          _pending(algo_id="a2", sz="abc")])
        self.assertEqual(out["legs"][0]["state"], "pending_readback")

    def test_an_exact_row_rescues_a_shortfall_candidate(self):
        out = OT.verify_attached_protection(
            inst_id="BTC-USDT-SWAP", expected_legs=[_leg(sz="4")],
            pending_rows=[_pending(algo_id="short", sz="1"),
                          _pending(algo_id="exact", sz="4")])
        self.assertEqual(out["status"], "PROTECTED")
        self.assertEqual(out["legs"][0]["algoId"], "exact")

    def test_shortfall_wins_over_pending_across_legs(self):
        out = OT.verify_attached_protection(
            inst_id="BTC-USDT-SWAP",
            expected_legs=[_leg(kind="tp", side="sell", sz="4"),
                           _leg(kind="sl", side="buy", sz="4", x_price="90")],
            pending_rows=[_pending(sz="3")])
        self.assertEqual(out["status"], "UNPROTECTED")


class ReadbackWrapperTests(unittest.TestCase):
    def test_wrapper_reads_pending_algo_orders_then_verifies(self):
        env = _env()
        reader = mock.Mock(return_value=[_pending()])
        with mock.patch.object(OT, "pending_algo_orders", reader), \
                mock.patch.object(OT, "current_environment", return_value=env):
            out = OT.readback_attached_protection("BTC-USDT-SWAP", [_leg()])
        reader.assert_called_once_with("BTC-USDT-SWAP", env=env)
        self.assertEqual(out["status"], "PROTECTED")

    def test_explicit_env_skips_the_current_environment_lookup(self):
        explicit = _env(mode="live", identity="live")
        reader = mock.Mock(return_value=[])
        with mock.patch.object(OT, "pending_algo_orders", reader), \
                mock.patch.object(OT, "current_environment") as current:
            OT.readback_attached_protection("X", [_leg()], env=explicit)
        current.assert_not_called()
        self.assertEqual(reader.call_args[1]["env"], explicit)

    def test_wrapper_is_read_only(self):
        """零写请求：只调 pending_algo_orders，不碰 cancel/close。"""
        with mock.patch.object(OT, "pending_algo_orders", return_value=[]), \
                mock.patch.object(OT, "_request") as request, \
                mock.patch.object(OT, "current_environment", return_value=_env()):
            OT.readback_attached_protection("X", [_leg()])
        request.assert_not_called()


class ConcurrencyTests(_Base):
    def test_concurrent_intent_creation_loses_nothing(self):
        tokens = []
        lock = threading.Lock()

        def worker():
            token, _ = OT._create_intent(_env(), _pos())
            with lock:
                tokens.append(token)

        threads = [threading.Thread(target=worker) for _ in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(tokens)), 24)
        self.assertEqual(len(OT._INTENTS), 24)


if __name__ == "__main__":
    unittest.main()
