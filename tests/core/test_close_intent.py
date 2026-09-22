"""平仓意图（Binance/Gate）：**令牌一次性、口令逐字、指纹漂移即拒、不平则明确报错**（第二百七十刀，开新面 close_intent.py）。

先打印整个文件（151 行）再动笔。它与 OKX 平仓契约同构：服务端随机令牌登记 · 90 秒时效 ·
环境绑定 · 口令短语全等 · 消费前仓位数量二次校验 · 平仓后回读归零核验。

| 语义 | 口径 |
|---|---|
| ★ **口令错了不许烧令牌** | `venue_fast_close` 先 `peek`（只读）做环境/口令**预检**，全部通过才 `consume` ⇒ 输错短语后令牌仍可用（否则误触一次就得刷新持仓）|
| ★ **令牌一次性** | `consume` 先 `pop` 再判过期 ⇒ 过期令牌**也被消费掉**（不能反复重试同一个令牌）；重复使用报"无效或已使用" |
| ★ **环境绑定** | 快照签发时的 `environment` 与本次不一致 ⇒ 拒（防止 demo 令牌打到 live）|
| ★ **凭证指纹钉死** | 签发时有指纹、而此刻实际 api_key 指纹已漂移（凭证轮换）⇒ 拒，避免旧令牌打到新账户；未钉指纹（空）则跳过 |
| ★ **消费前数量二次校验** | 仓位已不存在 ⇒ "请刷新"；数量变化超出 `max(1e-12, 当前×1e-6)` 容差 ⇒ 拒（绝不下错量）|
| ★ **不平则明确报错，绝不静默半平** | 适配器返回 `{"closed": false}` ⇒ `RuntimeError`；回读 6 次仍未归零 ⇒ `RuntimeError` 并提示"禁止重复点击" |
| 匹配规则 | 按 `base`（无则取 symbol 的 `-` 前缀）匹配标的、按 `size_signed` 正负匹配方向、忽略近零腿 |
| 兼容 | 适配器 `fast_close_position` 签名里有 `pos_side` 才传该参数（`inspect.signature` 探测）|
| 档位轴 | `binance`: demo→demo / live→live；`gate`: demo→**sandbox** / live→live；未知值原样透传 |
"""

import threading
import unittest
from unittest import mock

from r20_backend import close_intent as CI


class _AdapterBase:
    def __init__(self, rows, result=None, stays_open=False):
        self.rows = list(rows)
        self.result = {"closed": True} if result is None else result
        self.stays_open = stays_open
        self.close_calls = []

    def positions(self):
        return list(self.rows)

    def _close(self, symbol, **kwargs):
        self.close_calls.append((symbol, kwargs))
        if not self.stays_open:
            self.rows = []
        return self.result


class _AdapterWithSide(_AdapterBase):
    def fast_close_position(self, symbol, pos_side=None):
        return self._close(symbol, pos_side=pos_side)


class _AdapterNoSide(_AdapterBase):
    def fast_close_position(self, symbol):
        return self._close(symbol)


def _row(base="BTC", size_signed=2.0, symbol="BTC-USDT-SWAP", with_base=True):
    row = {"symbol": symbol, "size_signed": size_signed}
    if with_base:
        row["base"] = base
    return row


class _IntentBase(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.now = 10_000.0
        CI._INTENTS.clear()
        self.addCleanup(CI._INTENTS.clear)
        self.clock = self._start(mock.patch.object(CI.time, "time",
                                                   side_effect=lambda: self.now))
        self.sleeper = self._start(mock.patch.object(CI.time, "sleep"))

    def _new(self, **over):
        params = {"venue": "binance", "environment": "demo",
                  "display_inst": "BTC-USDT-SWAP", "symbol": "BTC",
                  "pos_side": "long", "expected_size": 2.0}
        params.update(over)
        return CI.create(**params)


class AdapterEnvironmentTests(unittest.TestCase):
    def test_known_mappings(self):
        self.assertEqual(CI.adapter_environment("binance", "demo"), "demo")
        self.assertEqual(CI.adapter_environment("binance", "live"), "live")
        self.assertEqual(CI.adapter_environment("gate", "demo"), "sandbox",
                         "gate 的 demo 档在适配器里叫 sandbox")
        self.assertEqual(CI.adapter_environment("gate", "live"), "live")

    def test_case_and_unknown_values_pass_through(self):
        self.assertEqual(CI.adapter_environment("BINANCE", "DEMO"), "demo")
        self.assertEqual(CI.adapter_environment("okx", "weird"), "weird",
                         "未知轴值原样透传，交由适配器自校验")
        self.assertEqual(CI.adapter_environment("", "demo"), "demo")
        self.assertEqual(CI.adapter_environment(None, None), "")

    def test_single_source_of_truth_aliases(self):
        self.assertIs(CI._ADAPTER_ENV, CI.ADAPTER_ENV)

    def test_pos_side_is_decided_by_sign(self):
        self.assertEqual(CI._pos_side(1.0), "long")
        self.assertEqual(CI._pos_side(0.0), "short")
        self.assertEqual(CI._pos_side(-3.0), "short")


class CreateTests(_IntentBase):
    def test_confirmation_phrase_format(self):
        token, confirmation = self._new()
        self.assertEqual(confirmation, "CLOSE BINANCE DEMO BTC-USDT-SWAP LONG 2")
        self.assertTrue(token)
        self.assertNotEqual(token, "token-venue-fake")
        self.assertGreater(len(token), 20, "必须是服务端随机令牌")

    def test_fractional_size_is_rendered_compactly(self):
        _, confirmation = self._new(expected_size=0.5)
        self.assertIn("0.5", confirmation)
        self.assertNotIn("0.50000", confirmation)

    def test_token_is_unique_per_call(self):
        first, _ = self._new()
        second, _ = self._new()
        self.assertNotEqual(first, second)

    def test_expiry_is_ninety_seconds_out(self):
        token, _ = self._new()
        self.assertEqual(CI.peek(token)["expires_at"], self.now + CI.INTENT_TTL_SECONDS)
        self.assertEqual(CI.INTENT_TTL_SECONDS, 90)

    def test_stale_intents_are_pruned_on_the_next_create(self):
        old, _ = self._new()
        self.now += CI.INTENT_TTL_SECONDS + 1
        self._new()
        self.assertIsNone(CI.peek(old), "过期意图必须被顺手清掉")
        self.assertEqual(len(CI._INTENTS), 1)

    def test_credential_fingerprint_is_recorded(self):
        token, _ = self._new(credential_fingerprint="FP1")
        self.assertEqual(CI.peek(token)["credential_fingerprint"], "FP1")
        default, _ = self._new()
        self.assertEqual(CI.peek(default)["credential_fingerprint"], "")


class PeekAndConsumeTests(_IntentBase):
    def test_peek_is_read_only_and_returns_a_copy(self):
        token, _ = self._new()
        peeked = CI.peek(token)
        peeked["expected_size"] = 999
        self.assertEqual(CI.peek(token)["expected_size"], 2.0,
                         "peek 必须返回副本，改它不能污染登记表")
        self.assertIsNone(CI.peek("nope"))

    def test_consume_is_one_shot(self):
        token, _ = self._new()
        self.assertEqual(CI.consume(token)["symbol"], "BTC")
        with self.assertRaises(ValueError) as ctx:
            CI.consume(token)
        self.assertIn("无效或已使用", str(ctx.exception))

    def test_unknown_token_raises(self):
        with self.assertRaises(ValueError):
            CI.consume("nope")

    def test_expired_token_raises_and_is_still_consumed(self):
        token, _ = self._new()
        self.now += CI.INTENT_TTL_SECONDS + 1
        with self.assertRaises(ValueError) as ctx:
            CI.consume(token)
        self.assertIn("已过期", str(ctx.exception))
        self.assertNotIn(token, CI._INTENTS, "过期令牌先 pop 再判 ⇒ 已被消费，不能重试")

    def test_ttl_boundary_is_exclusive(self):
        token, _ = self._new()
        self.now += CI.INTENT_TTL_SECONDS - 0.001
        self.assertEqual(CI.consume(token)["symbol"], "BTC")


class CredentialFingerprintTests(_IntentBase):
    def test_current_fingerprint_uses_the_venue_credentials(self):
        vc = self._start(mock.patch("r20_backend.exchanges.venue_credentials",
                                    return_value=("APIKEY", "SECRET")))
        cf = self._start(mock.patch("r20_backend.exchanges.identity.credential_fingerprint",
                                    return_value="FP"))
        self.assertEqual(CI._current_credential_fp("binance", "demo"), "FP")
        vc.assert_called_once_with("binance", "demo")
        cf.assert_called_once_with("APIKEY")

    def test_credential_lookup_failure_falls_back_to_an_empty_key(self):
        self._start(mock.patch("r20_backend.exchanges.venue_credentials",
                               side_effect=RuntimeError("凭证库坏了")))
        cf = self._start(mock.patch("r20_backend.exchanges.identity.credential_fingerprint",
                                    return_value="EMPTY-FP"))
        self.assertEqual(CI._current_credential_fp("gate", "sandbox"), "EMPTY-FP")
        cf.assert_called_once_with("")


class FastCloseGuardTests(_IntentBase):
    """预检阶段：这些失败都必须发生在 `consume` **之前**。"""

    def _adapter(self, adapter):
        self._start(mock.patch("r20_backend.exchanges.get_adapter",
                               return_value=adapter))
        return adapter

    def test_unsupported_venue_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            CI.venue_fast_close("okx", "demo", "t", "x")
        self.assertIn("不支持的平仓场所", str(ctx.exception))

    def test_unknown_token_is_rejected_without_touching_state(self):
        self._adapter(_AdapterWithSide([_row()]))
        with self.assertRaises(ValueError) as ctx:
            CI.venue_fast_close("binance", "demo", "nope", "x")
        self.assertIn("无效或已使用", str(ctx.exception))
        self.assertEqual(CI._INTENTS, {})

    def test_environment_switch_is_rejected(self):
        token, _ = self._new()
        self._adapter(_AdapterWithSide([_row()]))
        with self.assertRaises(ValueError) as ctx:
            CI.venue_fast_close("binance", "live", token, "x")
        self.assertIn("环境已切换", str(ctx.exception))
        self.assertIsNotNone(CI.peek(token), "环境不符不得消费令牌")

    def test_wrong_phrase_does_not_burn_the_token(self):
        token, confirmation = self._new()
        self._adapter(_AdapterWithSide([_row()]))
        with self.assertRaises(ValueError) as ctx:
            CI.venue_fast_close("binance", "demo", token, "CLOSE WRONG")
        self.assertIn(confirmation, str(ctx.exception), "报错要告诉用户正确短语")
        self.assertIsNotNone(CI.peek(token), "口语预检失败后令牌必须还能用")

    def test_phrase_is_normalised_on_the_client_side(self):
        token, confirmation = self._new()
        adapter = self._adapter(_AdapterWithSide([_row()]))
        out = CI.venue_fast_close("binance", "demo", token,
                                  f"  {confirmation.lower()}  ")
        self.assertEqual(out["status"], "confirmed_closed")
        self.assertEqual(adapter.close_calls, [("BTC", {"pos_side": "long"})])

    def test_credential_rotation_is_refused(self):
        token, _ = self._new(credential_fingerprint="OLD")
        self._adapter(_AdapterWithSide([_row()]))
        self._start(mock.patch.object(CI, "_current_credential_fp",
                                      return_value="NEW"))
        with self.assertRaises(ValueError) as ctx:
            CI.venue_fast_close("binance", "demo", token,
                                CI.peek(token)["confirmation"])
        self.assertIn("已轮换", str(ctx.exception))
        self.assertIn("OLD", str(ctx.exception))
        self.assertIsNotNone(CI.peek(token), "指纹漂移不得消费令牌")

    def test_unpinned_fingerprint_skips_the_rotation_check(self):
        token, confirmation = self._new()
        self._adapter(_AdapterWithSide([_row()]))
        self._start(mock.patch.object(CI, "_current_credential_fp",
                                      return_value="ANY"))
        out = CI.venue_fast_close("binance", "demo", token, confirmation)
        self.assertEqual(out["status"], "confirmed_closed")

    def test_matching_fingerprint_passes(self):
        token, confirmation = self._new(credential_fingerprint="SAME")
        self._adapter(_AdapterWithSide([_row()]))
        self._start(mock.patch.object(CI, "_current_credential_fp",
                                      return_value="SAME"))
        self.assertEqual(CI.venue_fast_close("binance", "demo", token,
                                             confirmation)["status"],
                         "confirmed_closed")


class FastCloseSizeTests(_IntentBase):
    def _run(self, adapter, **intent_over):
        token, confirmation = self._new(**intent_over)
        self._start(mock.patch("r20_backend.exchanges.get_adapter",
                               return_value=adapter))
        return CI.venue_fast_close("binance", "demo", token, confirmation)

    def test_vanished_position_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._run(_AdapterWithSide([]))
        self.assertIn("已不存在", str(ctx.exception))

    def test_size_drift_beyond_tolerance_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self._run(_AdapterWithSide([_row(size_signed=3.0)]))
        self.assertIn("请刷新", str(ctx.exception))
        self.assertNotIn("closed", str(ctx.exception))

    def test_tiny_drift_within_tolerance_is_accepted(self):
        out = self._run(_AdapterWithSide([_row(size_signed=2.0 + 1e-9)]))
        self.assertEqual(out["status"], "confirmed_closed")

    def test_other_symbols_and_sides_are_ignored(self):
        rows = [_row(base="ETH", symbol="ETH-USDT-SWAP", size_signed=5.0),
                _row(base="BTC", symbol="BTC-USDT-SWAP", size_signed=-2.0)]
        with self.assertRaises(ValueError) as ctx:
            self._run(_AdapterWithSide(rows))
        self.assertIn("已不存在", str(ctx.exception),
                      "方向不符的仓位不算目标仓位")

    def test_base_falls_back_to_the_symbol_prefix(self):
        out = self._run(_AdapterWithSide([_row(with_base=False, size_signed=2.0)]))
        self.assertEqual(out["closed_size"], 2.0)

    def test_short_positions_are_supported(self):
        token, confirmation = self._new(pos_side="short")
        adapter = _AdapterWithSide([_row(size_signed=-2.0)])
        self._start(mock.patch("r20_backend.exchanges.get_adapter",
                               return_value=adapter))
        out = CI.venue_fast_close("binance", "demo", token, confirmation)
        self.assertEqual(out["posSide"], "short")
        self.assertEqual(adapter.close_calls[0][1], {"pos_side": "short"})

    def test_near_zero_legs_are_skipped(self):
        rows = [_row(size_signed=1e-18), _row(size_signed=2.0)]
        out = self._run(_AdapterWithSide(rows))
        self.assertEqual(out["closed_size"], 2.0)


class FastCloseExecutionTests(_IntentBase):
    def _run(self, adapter, **intent_over):
        token, confirmation = self._new(**intent_over)
        self._start(mock.patch("r20_backend.exchanges.get_adapter",
                               return_value=adapter))
        return CI.venue_fast_close("binance", "demo", token, confirmation)

    def test_success_payload_shape(self):
        adapter = _AdapterWithSide([_row()], result={"closed": True, "orderId": 7})
        out = self._run(adapter)
        self.assertEqual(out["status"], "confirmed_closed")
        self.assertEqual(out["venue"], "binance")
        self.assertEqual(out["environment"], "demo")
        self.assertEqual(out["instId"], "BTC-USDT-SWAP")
        self.assertEqual(out["posSide"], "long")
        self.assertEqual(out["closed_size"], 2.0)
        self.assertEqual(out["close_result"], {"closed": True, "orderId": 7})

    def test_side_kwarg_is_only_passed_when_the_adapter_accepts_it(self):
        adapter = _AdapterNoSide([_row()])
        self._run(adapter)
        self.assertEqual(adapter.close_calls, [("BTC", {})])

    def test_signature_probe_failure_is_tolerated(self):
        adapter = _AdapterWithSide([_row()])
        self._run(adapter)
        with mock.patch("inspect.signature", side_effect=ValueError("builtin")):
            token, confirmation = self._new()
            self._start(mock.patch("r20_backend.exchanges.get_adapter",
                                   return_value=_AdapterWithSide([_row()])))
            out = CI.venue_fast_close("binance", "demo", token, confirmation)
        self.assertEqual(out["status"], "confirmed_closed")

    def test_rejected_close_raises_with_the_reason(self):
        adapter = _AdapterWithSide([_row()],
                                   result={"closed": False, "reason": "风险限制"})
        with self.assertRaises(RuntimeError) as ctx:
            self._run(adapter)
        self.assertIn("平仓未受理", str(ctx.exception))
        self.assertIn("风险限制", str(ctx.exception))

    def test_position_not_zeroed_after_six_polls_raises(self):
        adapter = _AdapterWithSide([_row()], stays_open=True)
        with self.assertRaises(RuntimeError) as ctx:
            self._run(adapter)
        self.assertIn("未确认归零", str(ctx.exception))
        self.assertIn("禁止重复点击", str(ctx.exception))
        self.assertEqual(self.sleeper.call_count, 6, "最多回读 6 次")

    def test_polling_stops_as_soon_as_the_position_is_flat(self):
        adapter = _AdapterWithSide([_row()])          # 平仓后 rows 清空
        self._run(adapter)
        self.assertEqual(self.sleeper.call_count, 1,
                         "一次回读即归零 ⇒ 不该继续睡满 6 次")
        self.assertEqual(self.sleeper.call_args[0][0], 0.6)

    def test_token_is_consumed_only_after_all_prechecks_pass(self):
        adapter = _AdapterWithSide([_row()])
        token, confirmation = self._new()
        self._start(mock.patch("r20_backend.exchanges.get_adapter",
                               return_value=adapter))
        self.assertIsNotNone(CI.peek(token))
        CI.venue_fast_close("binance", "demo", token, confirmation)
        self.assertIsNone(CI.peek(token), "成功后令牌必须被消费")

    def test_gate_uses_the_sandbox_adapter_environment(self):
        adapter = _AdapterWithSide([_row()])
        token, confirmation = self._new(venue="gate", environment="demo")
        getter = self._start(mock.patch("r20_backend.exchanges.get_adapter",
                                        return_value=adapter))
        out = CI.venue_fast_close("gate", "demo", token, confirmation)
        self.assertEqual(out["status"], "confirmed_closed")
        self.assertEqual(getter.call_args[1]["environment"], "sandbox")


class ConcurrencyTests(_IntentBase):
    def test_concurrent_creates_do_not_lose_intents(self):
        tokens = []
        lock = threading.Lock()

        def worker(index):
            token, _ = self._new(display_inst=f"INST-{index}")
            with lock:
                tokens.append(token)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(tokens)), 24)
        self.assertEqual(len(CI._INTENTS), 24)


if __name__ == "__main__":
    unittest.main()
