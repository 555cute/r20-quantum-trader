"""熔断引擎（`r20_backend/execution/circuit_breaker.py`）的残余分支收口 —— 第 335 刀。

本模块 316 行，是**钱路的风控闸门**：黑天鹅哨兵 + 状态文件 + 台账跨所同步旁车 +
单日回撤限额。修正后的全量基线里它是**钱路最大单块**（46 行真运行时缺口 / 71.4%）。

> 注意既有 `tests/trading/test_circuit_breaker_active_fail_closed.py` 覆盖的是
> **trader 孪生版**（`scripts/trader/circuit_guard.py`），不是本模块。

## 本刀立住的一条总纪律：**不可判定 = 不放松**

本模块每一条失败路径都必须返回 `True`（熔断）。这不是"保守"，是**唯一正确**的方向：
风控闸门在信息不足时放行，等于把"不知道"当成"安全"。

| 不可判定的情形 | 必须熔断 |
|---|---|
| 统一行情通道**抛异常** | ✓ |
| 行情**无有效数据**（双域+备源皆断） | ✓ |
| 行情**格式异常** | ✓ |
| 新闻情绪缓存**损坏** | ✓ |
| 熔断状态文件**损坏** | ✓ |
| 台账同步旁车**损坏/过旧** | ✓ |
| 旁车检查**本身抛异常** | ✓ |
| 台账**读取失败** | ✓ |
| 台账行**环境不可判** | 保守**全计**（宁停不漏） |
"""
from __future__ import annotations

import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import r20_backend.execution.circuit_breaker as cb  # noqa: E402


def _bj_today():
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _closed(pnl, *, day=None, env="demo", **extra):
    row = {"status": "closed", "close_time": f"{day or _bj_today()} 12:00:00",
           "pnl": pnl, "environment": env}
    row.update(extra)
    return row


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20-cb-tails-")
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        for name, value in (
            ("DATA_DIR", self.data),
            ("NEWS_SENTIMENT_FILE", self.data / "news_sentiment.json"),
            ("CIRCUIT_BREAKER_FILE", self.data / "circuit_breaker.json"),
            ("LEDGER_JSON_FILE", self.data / "trading_ledger.json"),
            ("STOP_COOLDOWN_FILE", self.data / "stop_cooldown.json"),
        ):
            p = patch.object(cb, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _candles(self, *, open_=100.0, close=100.0, low=100.0):
        return [[0, str(open_), "h", str(low), str(close), "v"]]

    def _sentinel(self, candles=None, *, fetch_exc=None, news=None, **kw):
        if news is not None:
            (self.data / "news_sentiment.json").write_text(json.dumps(news),
                                                           encoding="utf-8")

        def _fetch(*a, **k):
            if fetch_exc is not None:
                raise fetch_exc
            # ⚠️ 默认必须 >=2 根：哨兵在**任何新闻检查之前**就要求 `len(candles) >= 2`，
            #    只给 1 根会让所有新闻用例都提前熔断在"无有效数据"上（本刀自伤一次）。
            return candles if candles is not None else self._candles() * 3
        return cb.check_black_swan_sentinel(fetch_candles_fn=_fetch)

    def _active(self, *, usdt=1000.0, cap=100.0, mode="demo", ledger="missing",
                cb_file="missing"):
        if ledger != "missing":
            (self.data / "trading_ledger.json").write_text(
                ledger if isinstance(ledger, str) else json.dumps(ledger),
                encoding="utf-8")
        if cb_file != "missing":
            (self.data / "circuit_breaker.json").write_text(
                cb_file if isinstance(cb_file, str) else json.dumps(cb_file),
                encoding="utf-8")
        env = (lambda: type("E", (), {"mode": mode})()) if mode is not None else None
        patches = [patch.object(cb, "check_black_swan_sentinel",
                                lambda **k: (False, "")),
                   patch.object(cb, "effective_daily_loss_limit", lambda _u: cap)]
        if env is None:
            patches.append(patch.dict(sys.modules, {}))
        for p in patches[:2]:
            p.start()
            self.addCleanup(p.stop)
        if env is None:
            # 让 `from scripts.okx_runtime import current_environment` 之后那句抛
            patches_env = patch("scripts.okx_runtime.current_environment",
                                side_effect=RuntimeError("env unknown"))
            patches_env.start()
            self.addCleanup(patches_env.stop)
        else:
            patches_env = patch("scripts.okx_runtime.current_environment", env)
            patches_env.start()
            self.addCleanup(patches_env.stop)
        return cb.is_circuit_breaker_active(usdt)


# ───────────────────── 黑天鹅哨兵 ─────────────────────
class BlackSwanTests(_Sandbox, unittest.TestCase):
    def test_a_calm_market_is_not_tripped(self):
        # 第 261 行
        self.assertEqual(self._sentinel(), (False, ""))

    def test_a_missing_fetch_fn_falls_back_to_the_real_market_channel(self):
        # 第 228–230 行 —— 延迟导入统一行情通道
        with patch("scripts.market_data_service.fetch_candles",
                   lambda *a, **k: self._candles() * 3):
            self.assertEqual(cb.check_black_swan_sentinel(), (False, ""))

    def test_a_fetch_exception_trips_as_undecidable(self):
        # 第 232–235 行
        tripped, why = self._sentinel(fetch_exc=OSError("both channels down"))
        self.assertTrue(tripped)
        self.assertIn("OSError", why, "异常类型必须出现在理由里（便于取证）")
        self.assertIn("不可判定=不放松", why)

    def test_no_candles_trips_as_undecidable(self):
        # 第 236/237 行
        tripped, why = self._sentinel(candles=[])
        self.assertTrue(tripped)
        self.assertIn("无有效数据", why)

    def test_a_single_candle_is_not_enough(self):
        tripped, why = self._sentinel(candles=self._candles())
        self.assertTrue(tripped)
        self.assertIn("无有效数据", why)

    def test_a_plunge_in_the_body_trips(self):
        # 第 243–245 行
        tripped, why = self._sentinel(candles=self._candles(open_=100.0, close=96.0,
                                                            low=96.0)
                                      + self._candles())
        self.assertTrue(tripped)
        self.assertIn("断崖式暴跌插针", why)
        self.assertIn("-4.00%", why)

    def test_a_long_lower_wick_trips_even_when_the_close_recovers(self):
        # 插针：`low` 相对 `open` 跌超 4%，但收盘只跌 1%
        tripped, why = self._sentinel(candles=self._candles(open_=100.0, close=99.0,
                                                            low=95.0)
                                      + self._candles())
        self.assertTrue(tripped)
        self.assertIn("断崖式暴跌插针", why)

    def test_a_drop_just_under_the_threshold_does_not_trip(self):
        # 边界：恰好 -3.00% 触限（`<=`），-2.99% 不触
        tripped, _ = self._sentinel(candles=self._candles(open_=100.0, close=97.01,
                                                          low=97.01)
                                    + self._candles())
        self.assertFalse(tripped)

    def test_a_malformed_candle_trips_as_undecidable(self):
        # 第 246/247 行
        tripped, why = self._sentinel(candles=[[0, "not-a-number", "h", "l", "c"],
                                               [0, "1", "h", "l", "c"]])
        self.assertTrue(tripped)
        self.assertIn("格式异常不可判定", why)

    def test_a_short_candle_row_trips_as_undecidable(self):
        tripped, why = self._sentinel(candles=[[0, "100"], [0, "100"]])
        self.assertTrue(tripped)
        self.assertIn("格式异常不可判定", why)

    # — 新闻情绪 —
    def test_severe_negative_news_trips(self):
        # 第 249–257 行
        tripped, why = self._sentinel(news={"overall_score": 12.0})
        self.assertTrue(tripped)
        self.assertIn("极度恶性利空舆情", why)
        self.assertIn("12.0", why)

    def test_news_just_above_the_threshold_does_not_trip(self):
        tripped, _ = self._sentinel(news={"overall_score": 20.1})
        self.assertFalse(tripped)

    def test_news_exactly_at_the_threshold_trips(self):
        tripped, why = self._sentinel(news={"overall_score": 20.0})
        self.assertTrue(tripped)
        self.assertIn("20.0", why)

    def test_a_missing_score_field_does_not_trip(self):
        tripped, _ = self._sentinel(news={"other": 1})
        self.assertFalse(tripped)

    def test_a_null_score_does_not_trip(self):
        tripped, _ = self._sentinel(news={"overall_score": None})
        self.assertFalse(tripped)

    def test_a_non_numeric_score_trips_as_undecidable(self):
        # `float("junk")` 抛 ⇒ 缓存视为损坏
        tripped, why = self._sentinel(news={"overall_score": "junk"})
        self.assertTrue(tripped)
        self.assertIn("新闻情绪缓存损坏", why)

    def test_a_corrupt_news_file_trips_as_undecidable(self):
        # 第 258/259 行
        (self.data / "news_sentiment.json").write_text("{ broken", encoding="utf-8")
        tripped, why = cb.check_black_swan_sentinel(
            fetch_candles_fn=lambda *a, **k: self._candles() * 3)
        self.assertTrue(tripped)
        self.assertIn("新闻情绪缓存损坏", why)

    def test_a_missing_news_file_is_not_a_trip(self):
        self.assertFalse((self.data / "news_sentiment.json").exists())
        self.assertEqual(self._sentinel()[0], False)


# ───────────────────── 台账求和 ─────────────────────
class LedgerPnlTests(unittest.TestCase):
    def test_only_closed_rows_of_today_count(self):
        rows = [_closed(10.0), {"status": "holding", "pnl": 999.0},
                _closed(10.0, day="2020-01-01")]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "demo", _bj_today()), 10.0)

    def test_a_foreign_environment_row_is_excluded(self):
        rows = [_closed(10.0, env="live"), _closed(-3.0, env="demo")]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "demo", _bj_today()), -3.0)

    def test_a_row_without_an_environment_tag_is_counted_conservatively(self):
        # 「行缺环境标签（历史旧行）⇒ 保守计入（宁停不漏）」
        rows = [_closed(10.0, env="")]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "demo", _bj_today()), 10.0)

    def test_an_unknown_current_environment_counts_everything(self):
        # want="" ⇒ 不做环境过滤，保守全计
        rows = [_closed(10.0, env="live"), _closed(5.0, env="demo")]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "", _bj_today()), 15.0)

    def test_a_bad_pnl_value_is_skipped_not_crashed(self):
        # 第 122/123 行
        rows = [_closed("not-a-number"), _closed(7.0)]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "demo", _bj_today()), 7.0)

    def test_a_non_mapping_row_escapes_the_risk_path(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 122 行只捕获 `(TypeError, ValueError)`，
        #   而 `"junk".get(...)` 抛的是 **AttributeError** ⇒ 一个非映射行会让
        #   这个**风控求和函数**整体抛出。
        #   调用方 `is_circuit_breaker_active` 的外层 `except Exception` 会兜住并
        #   fail-closed（安全方向），但 `ledger_today_stats`（前台 KPI）**没有**外层兜底。
        with self.assertRaises(AttributeError):
            cb.ledger_daily_closed_pnl(["junk", _closed(7.0)], "demo", _bj_today())

    def test_a_well_formed_row_after_a_bad_one_is_still_summed_when_parse_fails(self):
        rows = [_closed("not-a-number"), _closed(7.0)]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "demo", _bj_today()), 7.0)

    def test_a_null_pnl_counts_as_zero(self):
        rows = [_closed(None)]
        self.assertEqual(cb.ledger_daily_closed_pnl(rows, "demo", _bj_today()), 0.0)


class LedgerStatsTests(unittest.TestCase):
    def test_the_stats_are_computed(self):
        out = cb.ledger_today_stats([_closed(10.0, gross_pnl=10.5, fee=0.5,
                                             funding_fee=-0.2)], "demo", _bj_today())
        self.assertEqual(out["net_realized"], 10.0)
        self.assertEqual(out["realized_gross"], 10.5)
        self.assertEqual(out["fees_paid"], 0.5)
        self.assertEqual(out["funding_paid"], -0.2)
        self.assertEqual(out["source"], "ledger")

    def test_wins_and_losses_are_counted(self):
        # 第 155–160 行
        out = cb.ledger_today_stats([_closed(10.0), _closed(-10.0)], "demo", _bj_today())
        self.assertEqual((out["win_trades"], out["loss_trades"]), (1, 1))
        self.assertEqual(out["win_rate"], 50.0)

    def test_friction_dust_is_excluded_from_the_counts(self):
        # `abs(net) < 0.01 and abs(gross) < 0.01` ⇒ 不算胜负（但仍进净额）
        out = cb.ledger_today_stats([_closed(0.001)], "demo", _bj_today())
        self.assertEqual((out["win_trades"], out["loss_trades"]), (0, 0))
        self.assertEqual(out["win_rate"], 0.0)

    def test_a_zero_pnl_row_counts_toward_neither(self):
        out = cb.ledger_today_stats([_closed(0.0)], "demo", _bj_today())
        self.assertEqual(out["win_rate"], 0.0)

    def test_a_bad_pnl_row_is_skipped(self):
        out = cb.ledger_today_stats([_closed("junk"), _closed(5.0)], "demo",
                                    _bj_today())
        self.assertEqual(out["net_realized"], 5.0)
        self.assertEqual(out["win_trades"], 1)

    def test_a_non_mapping_row_also_escapes_the_kpi_path(self):
        # 与 `ledger_daily_closed_pnl` 同源（同一处 `except (TypeError, ValueError)`）；
        # 区别是这个函数**没有**外层兜底 —— 异常会直接冒到前台。
        with self.assertRaises(AttributeError):
            cb.ledger_today_stats(["junk"], "demo", _bj_today())

    def test_the_environment_axis_is_applied(self):
        out = cb.ledger_today_stats([_closed(10.0, env="live")], "demo", _bj_today())
        self.assertEqual(out["net_realized"], 0.0)


# ───────────────────── 旁车 ─────────────────────
class SidecarTests(_Sandbox, unittest.TestCase):
    def _sidecar(self, payload, *, raw=None):
        p = self.data / "ledger_sync_status.json"
        p.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
        return cb._ledger_sync_sidecar_state()

    def test_a_missing_sidecar_is_not_unknown(self):
        self.assertEqual(cb._ledger_sync_sidecar_state(), ([], ""))

    def test_a_fresh_clean_sidecar_reports_nothing(self):
        self.assertEqual(self._sidecar({"venues": {}, "generated_at": ""}), ([], ""))

    def test_a_failed_venue_is_reported(self):
        failed, unknown = self._sidecar({"venues": {"gate": {"status": "failed",
                                                             "reason": "timeout"}}})
        self.assertEqual(failed, ["gate"])
        self.assertEqual(unknown, "")

    def test_a_stale_sidecar_is_unknown(self):
        # 第 61/62 行 —— 过旧 ⇒ 不可判定
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(seconds=99999)).isoformat()
        failed, unknown = self._sidecar({"venues": {}, "generated_at": old})
        self.assertEqual(failed, [])
        self.assertIn("旁车过旧", unknown)

    def test_a_fresh_sidecar_is_not_unknown(self):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.assertEqual(self._sidecar({"venues": {}, "generated_at": now})[1], "")

    def test_a_corrupt_sidecar_is_unknown(self):
        # 第 86/87 行
        _, unknown = self._sidecar(None, raw="{ broken")
        self.assertIn("旁车损坏/不可读", unknown)

    def test_an_unconfigured_venue_is_not_counted_as_failed(self):
        # 第 70–84 行：免密只读行情模式下的失败不算"台账不可判全"。
        # ⚠️ 必须把 `venue_credentials` 打桩 —— 否则它会读到**真实凭证**
        #    （实测：连 `venue_credentials("gate", "")` 都返回 32/64 字符的 live key），
        #    豁免分支永远不触发，用例变成"测生产环境"。
        with patch("r20_backend.exchanges.venue_credentials",
                   lambda v, e: ("", "")):
            for reason in ("-2015 invalid api-key", "未配置凭证", "missing credential",
                           "invalid api-key", "not configured", "未提供", "需显式设"):
                with self.subTest(reason=reason):
                    failed, _ = self._sidecar({"venues": {
                        "gate": {"status": "failed", "reason": reason}}})
                    self.assertEqual(failed, [], f"{reason!r} 不应熔断其他已配置场所")

    def test_the_exemption_tokens_are_matched_case_insensitively(self):
        with patch("r20_backend.exchanges.venue_credentials",
                   lambda v, e: ("", "")):
            failed, _ = self._sidecar({"venues": {
                "gate": {"status": "failed", "reason": "INVALID API-KEY expired"}}})
        self.assertEqual(failed, [])

    def test_an_unconfigured_venue_with_real_credentials_still_counts(self):
        with patch("r20_backend.exchanges.venue_credentials",
                   lambda v, e: ("AK", "SK")):
            failed, _ = self._sidecar({"venues": {
                "gate": {"status": "failed", "reason": "invalid api-key"}}})
        self.assertEqual(failed, ["gate"], "凭证其实在 ⇒ 仍视为真实失败")

    def test_a_non_credential_failure_reason_is_never_exempted(self):
        with patch("r20_backend.exchanges.venue_credentials",
                   lambda v, e: ("", "")):
            failed, _ = self._sidecar({"venues": {
                "gate": {"status": "failed", "reason": "timeout"}}})
        self.assertEqual(failed, ["gate"])

    def test_a_credential_lookup_failure_still_counts_the_venue(self):
        # 第 82/83 行 —— 查凭证本身抛 ⇒ 不豁免（宁可停）
        with patch("r20_backend.exchanges.venue_credentials",
                   side_effect=RuntimeError("boom")):
            failed, _ = self._sidecar({"venues": {
                "gate": {"status": "failed", "reason": "invalid api-key"}}})
        self.assertEqual(failed, ["gate"])

    def test_a_non_dict_venue_entry_is_ignored(self):
        failed, unknown = self._sidecar({"venues": {"gate": "junk"}})
        self.assertEqual((failed, unknown), ([], ""))

    def test_a_non_failed_status_is_ignored(self):
        failed, _ = self._sidecar({"venues": {"gate": {"status": "ok"}}})
        self.assertEqual(failed, [])

    def test_the_compat_shell_returns_only_the_list(self):
        # 第 96 行
        self._sidecar({"venues": {"gate": {"status": "failed", "reason": "x"}}})
        self.assertEqual(cb._ledger_sync_failed_venues(), ["gate"])


# ───────────────────── 冷却（薄壳） ─────────────────────
class CooldownShellTests(_Sandbox, unittest.TestCase):
    def test_reading_cooldown_state_uses_the_current_file_path(self):
        # 第 178 行 —— 路径必须在**调用时**解析
        self.assertEqual(cb._read_stop_cooldowns_state(), ({}, False))

    def test_a_corrupt_cooldown_file_reports_corrupt(self):
        (self.data / "stop_cooldown.json").write_text("{ broken", encoding="utf-8")
        data, corrupt = cb._read_stop_cooldowns_state()
        self.assertTrue(corrupt, "损坏≠缺失：必须能被区分出来（fail-closed 依据）")
        self.assertIsInstance(data, dict)

    def test_loading_cooldowns_returns_a_dict(self):
        self.assertEqual(cb.load_stop_cooldowns(), {})

    def test_a_cooldown_can_be_added_and_detected(self):
        cb.add_stop_cooldown("BTC-USDT-SWAP", "long")
        self.assertTrue(cb.is_in_stop_cooldown("BTC-USDT-SWAP", "long"))

    def test_a_different_side_is_not_in_cooldown(self):
        cb.add_stop_cooldown("BTC-USDT-SWAP", "long")
        self.assertFalse(cb.is_in_stop_cooldown("BTC-USDT-SWAP", "short"))


class AtomicWriteTests(_Sandbox, unittest.TestCase):
    def test_the_payload_round_trips(self):
        target = self.data / "out.json"
        cb._atomic_write_json(target, {"a": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})

    def test_the_file_permissions_are_restricted(self):
        target = self.data / "out.json"
        cb._atomic_write_json(target, {"a": 1})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_a_write_failure_cleans_up_and_reraises(self):
        # 第 197–202 行
        target = self.data / "out.json"
        with patch.object(cb.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                cb._atomic_write_json(target, {"a": 1})
        self.assertEqual([p.name for p in self.data.glob(".out.json-*")], [])

    def test_a_cleanup_failure_still_reraises_the_original(self):
        # 第 200/201 行 —— 清理失败不能吞掉原异常
        target = self.data / "out.json"
        with patch.object(cb.os, "replace", side_effect=OSError("disk full")), \
             patch.object(cb.os, "unlink", side_effect=OSError("read-only")):
            with self.assertRaises(OSError) as ctx:
                cb._atomic_write_json(target, {"a": 1})
        self.assertIn("disk full", str(ctx.exception))


# ───────────────────── 统一熔断判定 ─────────────────────
class CircuitBreakerActiveTests(_Sandbox, unittest.TestCase):
    def test_a_clean_state_is_not_tripped(self):
        self.assertEqual(self._active(), (False, ""))

    def test_a_tripped_sentinel_short_circuits_with_its_reason(self):
        # 第 267/268 行
        with patch.object(cb, "check_black_swan_sentinel",
                          lambda **k: (True, "🚨 哨兵理由")):
            tripped, why = cb.is_circuit_breaker_active(1000.0)
        self.assertTrue(tripped)
        self.assertIn("哨兵理由", why, "哨兵理由必须原样上抛")

    def test_an_active_state_file_trips(self):
        # 第 275–277 行
        tripped, why = self._active(cb_file={"active": True, "reason": "极端行情"})
        self.assertTrue(tripped)
        self.assertIn("极端行情", why)

    def test_a_triggered_status_trips(self):
        tripped, _ = self._active(cb_file={"status": "triggered", "headline": "暴跌"})
        self.assertTrue(tripped)

    def test_an_expired_breaker_does_not_trip(self):
        tripped, _ = self._active(cb_file={"active": True,
                                           "expires_at_ts": 1.0})
        self.assertFalse(tripped)

    def test_a_future_expiry_trips(self):
        import time as _t
        tripped, _ = self._active(cb_file={"active": True,
                                           "expires_at_ts": _t.time() + 3600})
        self.assertTrue(tripped)

    def test_a_reasonless_activation_uses_a_generic_label(self):
        tripped, why = self._active(cb_file={"active": True})
        self.assertTrue(tripped)
        self.assertIn("熔断中", why)

    def test_a_corrupt_state_file_trips(self):
        # 第 278/279 行
        tripped, why = self._active(cb_file="{ broken")
        self.assertTrue(tripped)
        self.assertIn("熔断状态文件损坏", why)

    def test_a_corrupt_sidecar_trips(self):
        (self.data / "ledger_sync_status.json").write_text("{ broken", encoding="utf-8")
        tripped, why = self._active(ledger=[_closed(-1.0)])
        self.assertTrue(tripped)
        self.assertIn("台账同步状态不可判定", why)

    def test_a_stale_sidecar_trips(self):
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(seconds=99999)).isoformat()
        (self.data / "ledger_sync_status.json").write_text(
            json.dumps({"venues": {}, "generated_at": old}), encoding="utf-8")
        tripped, why = self._active(ledger=[_closed(-1.0)])
        self.assertTrue(tripped)
        self.assertIn("旁车过旧", why)

    def test_a_failed_venue_trips(self):
        # 第 293–295 行
        (self.data / "ledger_sync_status.json").write_text(
            json.dumps({"venues": {"gate": {"status": "failed",
                                            "reason": "timeout"}}}), encoding="utf-8")
        tripped, why = self._active(ledger=[_closed(-1.0)])
        self.assertTrue(tripped)
        self.assertIn("gate", why)

    def test_a_sidecar_exception_trips(self):
        # 第 296–298 行 —— 「理论上不会抛」不是契约
        with patch.object(cb, "_ledger_sync_sidecar_state",
                          side_effect=RuntimeError("boom")):
            tripped, why = self._active(ledger=[_closed(-1.0)])
        self.assertTrue(tripped)
        self.assertIn("旁车检查不可用", why)

    def test_daily_loss_beyond_the_cap_trips(self):
        # 第 311/312 行
        tripped, why = self._active(ledger=[_closed(-150.0)], cap=100.0)
        self.assertTrue(tripped)
        self.assertIn("-150.00U", why)
        self.assertIn("100.0U", why)

    def test_daily_loss_within_the_cap_does_not_trip(self):
        tripped, _ = self._active(ledger=[_closed(-50.0)], cap=100.0)
        self.assertFalse(tripped)

    def test_exactly_at_the_cap_does_not_trip(self):
        # `today_pnl < -_loss_cap` 是严格小于
        tripped, _ = self._active(ledger=[_closed(-100.0)], cap=100.0)
        self.assertFalse(tripped)

    def test_an_unavailable_environment_counts_every_row(self):
        # 第 307/308 行 —— 环境不可判 ⇒ 保守全计
        tripped, why = self._active(ledger=[_closed(-60.0, env="live"),
                                            _closed(-60.0, env="demo")],
                                    cap=100.0, mode=None)
        self.assertTrue(tripped, "环境不可判时两环境的亏损必须合并计算")
        self.assertIn("-120.00U", why)

    def test_a_corrupt_ledger_trips(self):
        # 第 313/314 行
        tripped, why = self._active(ledger="{ broken")
        self.assertTrue(tripped)
        self.assertIn("日亏损风控数据读取失败", why)

    def test_a_missing_ledger_skips_the_loss_check_entirely(self):
        tripped, _ = self._active(ledger="missing", cap=0.0)
        self.assertFalse(tripped, "台账不存在 ⇒ 尚未同步过，不应据此禁开仓")

    def test_a_non_list_ledger_trips(self):
        tripped, why = self._active(ledger={"not": "a list"})
        self.assertTrue(tripped)
        self.assertIn("日亏损风控数据读取失败", why)


if __name__ == "__main__":
    unittest.main()
