"""风控常量/交易池/本金/手动平仓路由：**未知 ≠ 无持仓、没确认就不许动、没平掉就不许报平**（第二百五十八刀，开新面 risk.py）。

先打印整个文件（367 行）再动笔。三个纯助手 + 8 条路由；本刀把失败语义按「破坏性操作
fail-closed」这条主线钉住：

| 关注点 | 口径 |
|---|---|
| 帮手 `_tracker_keys_for` | 真实 tracker 键形如 `BTC-USDT-SWAP_long` ⇒ 精确等值永远命中不了；按 instId/前缀/裸币匹配且大小写无关 |
| 帮手 `_live_holdings` | **只读** dashboard 缓存、零网络；模块不可用/快照缺失/快照过期一律返回**未知原因**（未知 ≠ 无持仓）|
| 帮手 `_holdings_report` | `held = 有实时持仓 或 有 tracker`；`holdings_unknown` 非空即代表**判不出来** |
| PUT 本金 / POST risk / reset | 确认短语（`UPDATE CAPITAL` / `RESET RISK`）逐字校验，且**先验短语后动手** |
| 极端值 | 命中 `high_risk_changes` 且短语不对 ⇒ **400 + 审计 `rejected_high_risk`**，一次 env 写都没发生 |
| 删除标的 | 删前四道闸：BTC ⇒ 403、池下限 ⇒ 409、不在池 ⇒ 404、有 tracker ⇒ 409、有实时持仓 ⇒ 409、**持仓未知 ⇒ 503**；任一道拦下都留审计、**不落 success** |
| 手动平仓 | 场所白名单 ⇒ 400；OKX 未配置 ⇒ 503；功能未启用 ⇒ 403；主循环持锁 ⇒ 409；密码错/legacy ⇒ 403；平仓异常按 `OKXNotConfigured`/`ValueError`/其它 ⇒ 503/409/502，且审计状态各不相同 |
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from r20_backend.routers import risk as A
from r20_backend.schemas import (
    InitialCapitalUpdate,
    InstrumentAddRequest,
    InstrumentDeleteRequest,
    ManualCloseRequest,
    RiskConfigUpdate,
    RiskResetRequest,
)


class _Base(unittest.TestCase):
    def setUp(self):
        self.audits = []

        def _patch(target, new=mock.DEFAULT, **kwargs):
            patcher = mock.patch.object(A, target, new, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

        def _start(patcher):
            patcher.start()
            self.addCleanup(patcher.stop)
            return patcher

        self._start = _start

        _patch("audit_record", lambda *a, **k: self.audits.append((a, k)))
        _patch("app_attr", side_effect=lambda name, default=None: default)
        _patch("refresh_settings")
        self.admin = mock.Mock()
        _patch("require_admin_header", self.admin)
        self.superadmin = mock.Mock(return_value={"id": 1, "username": "root",
                                                  "role": "superadmin"})
        _patch("require_superadmin", self.superadmin)
        _patch("MIN_POOL_SIZE", 1)
        _patch("MAX_POOL_SIZE", 20)
        self.risk_config = mock.MagicMock()
        self.risk_config.HIGH_RISK_PHRASE = "HIGH RISK"
        self.risk_config.SUITES = [{"id": "default"}]
        self.risk_config.schema.return_value = {"fields": []}
        self.risk_config.current_values.return_value = {"R20_MAX_LEVERAGE": 5.0}
        self.risk_config.process_values.return_value = {"R20_MAX_LEVERAGE": 5.0}
        self.risk_config.process_freshness.return_value = {"age": 1}
        self.risk_config.file_vs_process_diff.return_value = {}
        self.risk_config.effective_engine_values.return_value = {"R20_MAX_LEVERAGE": 5.0}
        self.risk_config.high_risk_changes.return_value = []
        self.risk_config.reset_keys.return_value = ["R20_MAX_LEVERAGE"]
        _patch("risk_config", self.risk_config)
        self.update_env = mock.Mock()
        _patch("update_env", self.update_env)
        self.remove_env = mock.Mock()
        _patch("remove_env", self.remove_env)

        _sync_patcher = mock.patch("scripts.instrument_pool.sync_pool_leverage_caps")
        self.sync = _sync_patcher.start()
        self.addCleanup(_sync_patcher.stop)

    def _rec(self, index=0):
        return self.audits[index][0]


class TrackerKeyTests(unittest.TestCase):
    def test_suffix_and_bare_coin_keys_are_matched_case_insensitively(self):
        trackers = {"BTC-USDT-SWAP_long": 1, "btc_short": 2, "btc": 3,
                    "BTC-USDT-SWAP": 4, "ETH-USDT-SWAP_long": 5}
        self.assertEqual(A._tracker_keys_for("BTC-USDT-SWAP", trackers),
                         ["BTC-USDT-SWAP", "BTC-USDT-SWAP_long", "btc", "btc_short"])

    def test_non_matching_and_empty_inputs_yield_nothing(self):
        self.assertEqual(A._tracker_keys_for("BTC-USDT-SWAP", {}), [])
        self.assertEqual(A._tracker_keys_for("BTC-USDT-SWAP", None), [])
        self.assertEqual(A._tracker_keys_for("BTC-USDT-SWAP",
                                             {"ETH-USDT-SWAP_long": 1, "": 2}), [])


class LiveHoldingsTests(unittest.TestCase):
    def _set_cache(self, value):
        import r20_backend.dashboard_cache as dash
        patcher = mock.patch.object(dash, "CACHE_DATA", value, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_module_unavailable_reports_unknown_not_no_holdings(self):
        with mock.patch.dict(sys.modules, {"r20_backend.dashboard_cache": None}):
            held, venues, unknown = A._live_holdings("BTC-USDT-SWAP")
        self.assertFalse(held)
        self.assertEqual(venues, [])
        self.assertIn("不可用", unknown)

    def test_non_dict_cache_and_missing_positions_are_both_unknown(self):
        self._set_cache([1, 2])
        self.assertEqual(A._live_holdings("BTC-USDT-SWAP")[2], "持仓快照不可用")
        self._set_cache({})
        self.assertEqual(A._live_holdings("BTC-USDT-SWAP")[2], "持仓快照缺失")

    def test_stale_snapshot_is_unknown_not_no_holdings(self):
        self._set_cache({"positions": [], "data_health": {"cache_age_seconds": 120}})
        held, venues, unknown = A._live_holdings("BTC-USDT-SWAP")
        self.assertFalse(held)
        self.assertEqual(venues, [])
        self.assertIn("已过期 120s", unknown)

    def test_matches_by_instid_prefix_or_bare_coin_and_defaults_the_venue(self):
        self._set_cache({"positions": [
            {"instId": "BTC-USDT-SWAP", "venue": "Binance"},
            {"instId": "btc-usdt", "venue": None},       # 裸币前缀 → 命中，venue 缺省 okx
            {"instId": "ETH-USDT-SWAP", "venue": "gate"},
            {"instId": "", "venue": "okx"},
            "not-a-dict",
        ]})
        held, venues, unknown = A._live_holdings("BTC-USDT-SWAP")
        self.assertTrue(held)
        self.assertEqual(venues, ["binance", "okx"])
        self.assertEqual(unknown, "")

    def test_fresh_snapshot_without_a_match_is_a_clean_no(self):
        self._set_cache({"positions": [{"instId": "ETH-USDT-SWAP", "venue": "okx"}],
                         "data_health": {"cache_age_seconds": 3}})
        self.assertEqual(A._live_holdings("BTC-USDT-SWAP"), (False, [], ""))

    def test_report_treats_a_tracker_as_held_even_without_live_positions(self):
        self._set_cache({"positions": [{"instId": "ETH-USDT-SWAP", "venue": "okx"}]})
        report = A._holdings_report("BTC-USDT-SWAP", {"BTC-USDT-SWAP_long": 1})
        self.assertEqual(report["tracker_keys"], ["BTC-USDT-SWAP_long"])
        self.assertTrue(report["has_tracker"])
        self.assertFalse(report["held_live"])
        self.assertTrue(report["held"])
        self.assertIsNone(report["holdings_unknown"])


class RiskReadTests(_Base):
    def test_risk_get_requires_admin_and_exposes_file_and_process_views(self):
        out = A.admin_risk_get(equity=1234.0, x_r20_session="t")
        self.risk_config.effective_engine_values.assert_called_once_with(1234.0)
        self.assertEqual(out["schema"], {"fields": []})
        self.assertEqual(out["suites"], [{"id": "default"}])
        self.assertEqual(out["process_values"], {"R20_MAX_LEVERAGE": 5.0})
        self.assertEqual(out["file_vs_process"], {})
        self.assertEqual(self.audits, [], "读操作不写审计")


class RiskUpdateTests(_Base):
    def test_empty_payload_is_rejected_before_anything_else(self):
        with self.assertRaises(HTTPException) as ctx:
            A.admin_risk_update(RiskConfigUpdate(), x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.update_env.assert_not_called()

    def test_unresolvable_suite_is_400_without_audit(self):
        self.risk_config.suite_values.side_effect = ValueError("未知套件")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_risk_update(RiskConfigUpdate(suite_id="nope"), x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])

    def test_high_risk_without_the_exact_phrase_is_rejected_and_audited(self):
        self.risk_config.high_risk_changes.return_value = [
            {"label": "单标的占比", "key": "R20_MAX_SINGLE_ASSET_RATIO", "value": 0.5,
             "threshold": 0.3},
            {"label": "杠杆", "key": "R20_MAX_LEVERAGE", "value": 12.0, "threshold": 10.0},
        ]
        with self.assertRaises(HTTPException) as ctx:
            A.admin_risk_update(RiskConfigUpdate(values={"R20_MAX_LEVERAGE": 12}),
                                x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("HIGH RISK", ctx.exception.detail)
        self.assertIn("50", ctx.exception.detail)   # RATIO 项按百分比展示
        self.assertEqual(self._rec()[1], "rejected_high_risk")
        self.assertEqual(len(self._rec()[2]["items"]), 2)
        self.update_env.assert_not_called()

    def test_high_risk_with_the_phrase_proceeds_and_records_confirmed_keys(self):
        self.risk_config.high_risk_changes.return_value = [
            {"label": "杠杆", "key": "R20_MAX_LEVERAGE", "value": 12.0, "threshold": 10.0}]
        self.risk_config.normalize.return_value = {"R20_MAX_LEVERAGE": 12.0}
        A.admin_risk_update(RiskConfigUpdate(values={"R20_MAX_LEVERAGE": 12},
                                             confirmation="high risk"),
                            x_r20_session="t")
        self.update_env.assert_called_once_with({"R20_MAX_LEVERAGE": 12.0})
        self.assertEqual(self._rec()[1], "success")
        self.assertEqual(self._rec()[2]["high_risk_confirmed"], ["R20_MAX_LEVERAGE"])

    def test_normalise_failure_is_400_and_audited_as_failed(self):
        self.risk_config.normalize.side_effect = ValueError("杠杆区间非法")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_risk_update(RiskConfigUpdate(values={"R20_MAX_LEVERAGE": 99}),
                                x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self._rec()[1], "failed")
        self.assertEqual(self._rec()[2]["reason"], "杠杆区间非法")
        self.update_env.assert_not_called()

    def test_success_writes_env_syncs_leverage_and_audits_before_after(self):
        self.risk_config.normalize.return_value = {"R20_MAX_LEVERAGE": 8.0}
        out = A.admin_risk_update(RiskConfigUpdate(values={"R20_MAX_LEVERAGE": 8}),
                                  x_r20_session="t")
        self.update_env.assert_called_once_with({"R20_MAX_LEVERAGE": 8.0})
        self.risk_config.reload_risk_constants.assert_called_once_with()
        # env_updates 缺 MIN ⇒ 从 current_values 退化，MAX 取写入值
        self.sync.assert_called_once_with(min_leverage=2.0, max_leverage=8.0)
        self.assertEqual(out["updated"], ["R20_MAX_LEVERAGE"])
        self.assertIsNone(out["applied_suite"])
        changed = self._rec()[2]["changed"]
        self.assertEqual(changed["R20_MAX_LEVERAGE"], {"before": 5.0, "after": 8.0})

    def test_suite_values_are_merged_before_an_empty_check(self):
        self.risk_config.suite_values.return_value = {"R20_MAX_LEVERAGE": 3.0}
        self.risk_config.normalize.return_value = {"R20_MAX_LEVERAGE": 3.0}
        out = A.admin_risk_update(RiskConfigUpdate(suite_id="conservative"),
                                  x_r20_session="t")
        self.risk_config.suite_values.assert_called_once_with("conservative")
        self.assertEqual(out["applied_suite"], "conservative")

    def test_leverage_sync_failure_is_swallowed_after_the_env_write(self):
        """杠杆帽同步失败只吞掉：env 已写、审计照记 success（不能因为同步失败谎报保存失败）。"""
        self.risk_config.normalize.return_value = {"R20_MAX_LEVERAGE": 8.0}
        self.sync.side_effect = RuntimeError("池文件写不动")
        out = A.admin_risk_update(RiskConfigUpdate(values={"R20_MAX_LEVERAGE": 8}),
                                  x_r20_session="t")
        self.update_env.assert_called_once_with({"R20_MAX_LEVERAGE": 8.0})
        self.assertEqual(out["updated"], ["R20_MAX_LEVERAGE"])
        self.assertEqual(self._rec()[1], "success")


class RiskResetTests(_Base):
    def test_reset_requires_the_exact_phrase(self):
        with self.assertRaises(HTTPException) as ctx:
            A.admin_risk_reset(RiskResetRequest(confirmation="reset"), x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.remove_env.assert_not_called()

    def test_reset_clears_overrides_and_audits(self):
        out = A.admin_risk_reset(RiskResetRequest(confirmation="  reset risk  "),
                                 x_r20_session="t")
        self.remove_env.assert_called_once_with({"R20_MAX_LEVERAGE"})
        self.risk_config.reload_risk_constants.assert_called_once_with()
        self.sync.assert_called_once_with(min_leverage=2.0, max_leverage=5.0)
        self.assertTrue(out["reset"])
        self.assertEqual(self._rec()[0], "risk.config.reset")

    def test_reset_survives_a_leverage_sync_failure(self):
        self.sync.side_effect = RuntimeError("池文件写不动")
        out = A.admin_risk_reset(RiskResetRequest(confirmation="RESET RISK"),
                                 x_r20_session="t")
        self.remove_env.assert_called_once_with({"R20_MAX_LEVERAGE"})
        self.assertTrue(out["reset"])


class AccountBaselineTests(_Base):
    def setUp(self):
        super().setUp()
        self.fn_update = mock.Mock(return_value={"previous_initial_capital": 5000.0,
                                                 "initial_capital": 10000.0,
                                                 "reset_time": "2026-01-01T00:00:00+08:00"})
        self._start(mock.patch.object(A, "update_initial_capital", self.fn_update))

    def test_phrase_is_checked_before_the_update_runs(self):
        with self.assertRaises(HTTPException) as ctx:
            A.admin_update_account_baseline(
                InitialCapitalUpdate(initial_capital=10000.0, confirmation="UPDATE"),
                x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.fn_update.assert_not_called()

    def test_value_error_is_400(self):
        self.fn_update.side_effect = ValueError("本金必须为正")
        with self.assertRaises(HTTPException) as ctx:
            A.admin_update_account_baseline(
                InitialCapitalUpdate(initial_capital=10000.0, confirmation="UPDATE CAPITAL"),
                x_r20_session="t")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.audits, [])

    def test_success_audits_previous_and_new_capital(self):
        out = A.admin_update_account_baseline(
            InitialCapitalUpdate(initial_capital=10000.0, confirmation="UPDATE CAPITAL"),
            x_r20_session="t")
        self.fn_update.assert_called_once_with(10000.0)
        payload = self._rec()[2]
        self.assertEqual(payload["previous_initial_capital"], 5000.0)
        self.assertEqual(payload["initial_capital"], 10000.0)
        self.assertTrue(out["updated"])
        self.assertEqual(out["reset_time"], "2026-01-01T00:00:00+08:00")


class InstrumentsListTests(_Base):
    def setUp(self):
        super().setUp()
        self._start(mock.patch.object(A, "read_json", return_value={}))
        self._start(mock.patch.object(
            A, "load_instruments",
            return_value=[{"instId": "BTC-USDT-SWAP"}, {"instId": "ETH-USDT-SWAP"},
                          {"instId": "SOL-USDT-SWAP"}]))

        def _report(inst_id, trackers):
            if inst_id == "BTC-USDT-SWAP":
                return {"tracker_keys": [], "has_tracker": False, "held_live": True,
                        "held_venues": ["okx"], "holdings_unknown": None, "held": True}
            if inst_id == "ETH-USDT-SWAP":
                return {"tracker_keys": [], "has_tracker": False, "held_live": False,
                        "held_venues": [], "holdings_unknown": "持仓快照缺失", "held": False}
            return {"tracker_keys": [], "has_tracker": False, "held_live": False,
                    "held_venues": [], "holdings_unknown": None, "held": False}

        self._start(mock.patch.object(A, "_holdings_report", side_effect=_report))

    def test_rows_flag_protected_tracker_live_and_unknown_states(self):
        out = A.admin_instruments(x_r20_admin_token="tok")
        rows = {r["instId"]: r for r in out["instruments"]}
        self.assertTrue(rows["BTC-USDT-SWAP"]["protected"])
        self.assertFalse(rows["BTC-USDT-SWAP"]["removable"], "BTC 恒不可删")
        self.assertFalse(rows["ETH-USDT-SWAP"]["removable"], "持仓未知 ⇒ 不可删")
        self.assertTrue(rows["SOL-USDT-SWAP"]["removable"])
        self.assertEqual(rows["BTC-USDT-SWAP"]["held_venues"], ["okx"])
        self.assertEqual(out["holdings_snapshot"]["unknown_count"], 1,
                         "BTC 属保底标的，不计入 unknown_count")
        self.assertTrue(out["limits"]["btc_required"])
        self.admin.assert_called_once_with("tok")

    def test_non_dict_tracker_file_is_coerced_to_empty(self):
        with mock.patch.object(A, "read_json", return_value=[1, 2]):
            out = A.admin_instruments(x_r20_admin_token="tok")
        self.assertEqual(len(out["instruments"]), 3)


class AddInstrumentTests(_Base):
    def setUp(self):
        super().setUp()
        self.okx = mock.Mock()
        self._start(mock.patch.object(A, "okx", self.okx))
        self._start(mock.patch.object(
            A, "from_okx_instrument",
            return_value={"instId": "ETH-USDT-SWAP", "name": "ETH"}))
        self.mutate = mock.Mock()
        self._start(mock.patch.object(A, "mutate_instruments", self.mutate))
        self.okx.instruments.return_value = [{"instId": "ETH-USDT-SWAP",
                                              "settleCcy": "USDT", "state": "live"}]

    def test_lookup_failure_is_502(self):
        self.okx.instruments.side_effect = RuntimeError("网络炸了")
        with self.assertRaises(HTTPException) as ctx:
            A.add_admin_instrument(InstrumentAddRequest(inst_id="ETH-USDT-SWAP"),
                                   x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 502)
        self.mutate.assert_not_called()

    def test_non_live_or_non_usdt_contract_is_400(self):
        for raw in ([], [{"instId": "ETH-USDT-SWAP", "settleCcy": "USDC", "state": "live"}],
                    [{"instId": "ETH-USDT-SWAP", "settleCcy": "USDT", "state": "suspend"}]):
            self.okx.instruments.return_value = raw
            with self.assertRaises(HTTPException) as ctx:
                A.add_admin_instrument(InstrumentAddRequest(inst_id="ETH-USDT-SWAP"),
                                       x_r20_admin_token="tok")
            self.assertEqual(ctx.exception.status_code, 400)
        self.mutate.assert_not_called()

    def test_duplicate_is_409_decided_inside_the_lock(self):
        self.mutate.side_effect = lambda fn: fn([{"instId": "ETH-USDT-SWAP"}])
        with self.assertRaises(HTTPException) as ctx:
            A.add_admin_instrument(InstrumentAddRequest(inst_id="ETH-USDT-SWAP"),
                                   x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("已在交易池", ctx.exception.detail)
        self.assertEqual(self.audits, [])

    def test_pool_full_is_409(self):
        self.mutate.side_effect = lambda fn: fn([{"instId": f"X{i}-USDT-SWAP"}
                                                 for i in range(20)])
        with self.assertRaises(HTTPException) as ctx:
            A.add_admin_instrument(InstrumentAddRequest(inst_id="ETH-USDT-SWAP"),
                                   x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self.audits, [])

    def test_success_appends_under_the_lock_and_audits(self):
        self.mutate.side_effect = lambda fn: fn([{"instId": "BTC-USDT-SWAP"}])
        out = A.add_admin_instrument(InstrumentAddRequest(inst_id="ETH-USDT-SWAP"),
                                     x_r20_admin_token="tok")
        self.assertEqual(out["added"]["instId"], "ETH-USDT-SWAP")
        self.assertEqual(out["count"], 2)
        self.assertEqual(self._rec()[0], "instrument.add")


class DeleteInstrumentTests(_Base):
    def setUp(self):
        super().setUp()
        self.pool = [{"instId": "BTC-USDT-SWAP"}, {"instId": "ETH-USDT-SWAP"},
                     {"instId": "SOL-USDT-SWAP"}]
        self._start(mock.patch.object(A, "read_json", return_value={}))
        self.load = mock.Mock(return_value=list(self.pool))
        self._start(mock.patch.object(A, "load_instruments", self.load))
        self.report = mock.Mock(return_value={"tracker_keys": [], "has_tracker": False,
                                              "held_live": False, "held_venues": [],
                                              "holdings_unknown": None, "held": False})
        self._start(mock.patch.object(A, "_holdings_report", self.report))
        self.mutate = mock.Mock(side_effect=lambda fn: fn(list(self.pool)))
        self._start(mock.patch.object(A, "mutate_instruments", self.mutate))

    def test_phrase_is_mandatory_and_rejection_is_audited(self):
        with self.assertRaises(HTTPException) as ctx:
            A.delete_admin_instrument("ETH-USDT-SWAP",
                                      InstrumentDeleteRequest(confirmation=""),
                                      x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self._rec()[1], "rejected_phrase")
        self.assertFalse(self._rec()[2]["provided"])
        self.mutate.assert_not_called()

    def test_confirm_phrase_is_accepted_from_the_query_parameter_too(self):
        out = A.delete_admin_instrument("ETH-USDT-SWAP", None,
                                        confirmation="remove eth-usdt-swap",
                                        x_r20_admin_token="tok")
        self.assertEqual(out["removed"], "ETH-USDT-SWAP")
        self.assertEqual(self.admin.call_args[0][0], "tok")

    def test_btc_guard_wins_over_the_pool_floor(self):
        self.load.return_value = [{"instId": "BTC-USDT-SWAP"}]
        with self.assertRaises(HTTPException) as ctx:
            A.delete_admin_instrument("BTC-USDT-SWAP",
                                      InstrumentDeleteRequest(confirmation="REMOVE BTC-USDT-SWAP"),
                                      x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_pool_floor_and_missing_symbol_are_409_and_404(self):
        self.load.return_value = [{"instId": "ETH-USDT-SWAP"}]
        with self.assertRaises(HTTPException) as ctx:
            A.delete_admin_instrument("ETH-USDT-SWAP",
                                      InstrumentDeleteRequest(confirmation="REMOVE ETH-USDT-SWAP"),
                                      x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 409)

        self.load.return_value = list(self.pool)
        with self.assertRaises(HTTPException) as ctx:
            A.delete_admin_instrument("DOGE-USDT-SWAP",
                                      InstrumentDeleteRequest(confirmation="REMOVE DOGE-USDT-SWAP"),
                                      x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_tracker_record_blocks_deletion(self):
        self.report.return_value = {"tracker_keys": ["ETH-USDT-SWAP_long"], "has_tracker": True,
                                    "held_live": False, "held_venues": [],
                                    "holdings_unknown": None, "held": True}
        with self.assertRaises(HTTPException) as ctx:
            A.delete_admin_instrument("ETH-USDT-SWAP",
                                      InstrumentDeleteRequest(confirmation="REMOVE ETH-USDT-SWAP"),
                                      x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self._rec()[1], "rejected_tracker")

    def test_live_holdings_block_deletion(self):
        self.report.return_value = {"tracker_keys": [], "has_tracker": False,
                                    "held_live": True, "held_venues": ["binance"],
                                    "holdings_unknown": None, "held": True}
        with self.assertRaises(HTTPException) as ctx:
            A.delete_admin_instrument("ETH-USDT-SWAP",
                                      InstrumentDeleteRequest(confirmation="REMOVE ETH-USDT-SWAP"),
                                      x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(self._rec()[1], "rejected_live_holdings")

    def test_unknown_holdings_fail_closed_with_503(self):
        self.report.return_value = {"tracker_keys": [], "has_tracker": False,
                                    "held_live": False, "held_venues": [],
                                    "holdings_unknown": "持仓快照已过期 200s", "held": False}
        with self.assertRaises(HTTPException) as ctx:
            A.delete_admin_instrument("ETH-USDT-SWAP",
                                      InstrumentDeleteRequest(confirmation="REMOVE ETH-USDT-SWAP"),
                                      x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(self._rec()[1], "rejected_unknown_holdings")
        self.mutate.assert_not_called()

    def test_success_rewrites_the_pool_under_the_lock_and_audits(self):
        out = A.delete_admin_instrument("ETH-USDT-SWAP",
                                        InstrumentDeleteRequest(confirmation="REMOVE ETH-USDT-SWAP"),
                                        x_r20_admin_token="tok")
        self.assertEqual(out["removed"], "ETH-USDT-SWAP")
        self.assertEqual(out["count"], 2)
        self.assertEqual(self._rec()[0], "instrument.remove")
        self.assertEqual(self._rec()[1], "success")

    def test_non_dict_tracker_file_is_coerced_to_empty_and_deletion_proceeds(self):
        with mock.patch.object(A, "read_json", return_value=[1, 2]):
            out = A.delete_admin_instrument(
                "ETH-USDT-SWAP",
                InstrumentDeleteRequest(confirmation="REMOVE ETH-USDT-SWAP"),
                x_r20_admin_token="tok")
        self.assertEqual(out["removed"], "ETH-USDT-SWAP")
        self.report.assert_called_once_with("ETH-USDT-SWAP", {})


class ManualCloseTests(_Base):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-risk-datalock-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._start(mock.patch.object(A, "DATA_DIR", self.tmp))
        self.settings = mock.Mock(manual_close_enabled=True)
        self._start(mock.patch.object(A, "settings", self.settings))
        self.env = mock.Mock(configured=True, mode="live")
        self._start(mock.patch("scripts.okx_runtime.current_environment",
                               return_value=self.env))
        self.store = mock.Mock()
        self.store.verify_password.return_value = True
        self._start(mock.patch("r20_backend.dependencies.get_auth_store",
                               return_value=self.store))
        self.fast_close = mock.Mock(return_value={"instId": "BTC-USDT-SWAP",
                                                  "posSide": "long", "closed_size": 1.0,
                                                  "environment": "live"})
        self._start(mock.patch.object(A, "fast_close_confirmed", self.fast_close))

    def _payload(self, **kw):
        base = {"close_token": "tok-" + "x" * 20, "admin_password": "pw",
                "confirmation": "CLOSE BTC-USDT-SWAP", "venue": "okx"}
        base.update(kw)
        return ManualCloseRequest(**base)

    def test_venue_allowlist_and_unconfigured_okx_are_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            A.manual_close_position(self._payload(venue="huobi"))
        self.assertEqual(ctx.exception.status_code, 400)

        self.env.configured = False
        with self.assertRaises(HTTPException) as ctx:
            A.manual_close_position(self._payload(venue="okx"))
        self.assertEqual(ctx.exception.status_code, 503)

    def test_feature_flag_off_is_403(self):
        self.settings.manual_close_enabled = False
        with self.assertRaises(HTTPException) as ctx:
            A.manual_close_position(self._payload())
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(self.audits, [])

    def test_trading_loop_lock_is_409(self):
        with mock.patch("fcntl.flock", side_effect=BlockingIOError()):
            with self.assertRaises(HTTPException) as ctx:
                A.manual_close_position(self._payload())
        self.assertEqual(ctx.exception.status_code, 409)
        self.fast_close.assert_not_called()

    def test_password_must_verify_and_legacy_is_refused(self):
        self.store.verify_password.return_value = False
        with self.assertRaises(HTTPException) as ctx:
            A.manual_close_position(self._payload())
        self.assertEqual(ctx.exception.status_code, 403)

        self.superadmin.return_value = {"id": 0, "username": "legacy-token",
                                        "role": "legacy"}
        with self.assertRaises(HTTPException) as ctx:
            A.manual_close_position(self._payload())
        self.assertEqual(ctx.exception.status_code, 403)
        self.fast_close.assert_not_called()

    def test_okx_success_audits_a_confirmed_close(self):
        out = A.manual_close_position(self._payload())
        self.assertEqual(out["instId"], "BTC-USDT-SWAP")
        self.assertEqual(self._rec()[0], "position.close")
        self.assertEqual(self._rec()[1], "confirmed_closed")
        self.assertEqual(self._rec()[2]["venue"], "okx")
        self.assertEqual(self._rec()[2]["actor"], "root")

    def test_unlock_failure_after_a_successful_close_is_swallowed(self):
        """解锁失败不能把已经确认平掉的结果变成异常（否则调用方以为没平）。"""
        with mock.patch("fcntl.flock", side_effect=[None, OSError("解锁失败")]):
            out = A.manual_close_position(self._payload())
        self.assertEqual(out["instId"], "BTC-USDT-SWAP")
        self.assertEqual(self._rec()[1], "confirmed_closed")

    def test_non_okx_venue_delegates_to_the_venue_router(self):
        delegated = mock.Mock(return_value={"instId": "ETH-USDT-SWAP", "venue": "gate",
                                            "environment": "live"})
        with mock.patch("r20_backend.close_intent.venue_fast_close", delegated):
            A.manual_close_position(self._payload(venue="gate"))
        delegated.assert_called_once_with("gate", self.env.mode, "tok-" + "x" * 20,
                                          "CLOSE BTC-USDT-SWAP")
        self.assertEqual(self._rec()[2]["venue"], "gate")

    def test_close_failures_map_to_distinct_statuses_and_audits(self):
        from scripts.okx_rest import OKXNotConfigured
        cases = [(OKXNotConfigured("未配置"), 503, "rejected_not_ready"),
                 (ValueError("令牌无效"), 409, "rejected"),
                 (RuntimeError("确认超时"), 502, "verification_failed")]
        for exc, status, audit_status in cases:
            self.audits.clear()
            self.fast_close.side_effect = exc
            with self.assertRaises(HTTPException) as ctx:
                A.manual_close_position(self._payload())
            self.assertEqual(ctx.exception.status_code, status)
            self.assertEqual(self._rec()[1], audit_status)


if __name__ == "__main__":
    unittest.main()
