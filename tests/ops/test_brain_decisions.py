"""决策校验与缓存装配（brain/decisions.py）收口 —— 第 311 刀。

两个函数：`validate_and_filter_decision`（单条决策的置信度/RR 校验）与
`assemble_decision_cache`（装配落盘缓存，含**杠杆夹取**与策略快照绑定）。

本刀重点钉两处"最后一道防线"：

1. **拦截插件管线挂了 ⇒ 必须 fail-closed 降级成 `WAIT`**（第 51–62 行）。这是全链最靠后的
   一道闸：管线本身抛异常时，**绝不能**让模型原本的动作穿过去。降级路径还要自己把
   `rr` 算出来（供前台展示），但动作一律 `WAIT`、理由里带上原始异常。
2. **杠杆夹取必须与后台风控页联动**（2026-09-10 的事故）：旧版下限钉死 2 且提示词示例里
   `min(3, MAX)` 造成上限配 7 也一律 3x。模块 docstring 把这条列为"注入面"的理由 ——
   必须读到**补丁后**的 `max_leverage`/`min_leverage`，否则测试缝静默失效。
"""
from __future__ import annotations

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

from scripts.brain import decisions  # noqa: E402
from scripts.brain.decisions import (  # noqa: E402
    assemble_decision_cache,
    validate_and_filter_decision,
)


def _pkg(inst_id="BTC-USDT-SWAP", **over):
    pkg = {"instId": inst_id, "name": inst_id.split("-")[0], "price": 100.0,
           "bidPx": 99.5, "askPx": 100.5, "chg24h": 1.5, "vol24h": 10.0,
           "fundingRate": 0.01, "oiUsd": "1.0亿 U", "takerNetUsd": "2.0万 U",
           "lsRatio": 1.85, "data_quality": "valid"}
    pkg.update(over)
    return pkg


def _sf(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class FailClosedFallbackTests(unittest.TestCase):
    """★ 管线抛异常 ⇒ 一律 `WAIT`，但 `rr` 仍要尽力算出来给前台看。"""

    def _fallback(self, d_item, exc=None):
        import r20_backend.interceptor_manager as im
        boom = exc or RuntimeError("插件系统崩了")
        with patch.object(im, "run_interceptor_pipeline", side_effect=boom):
            return validate_and_filter_decision({}, d_item, set(), {}, safe_float=_sf)

    def test_falls_back_to_wait_with_the_original_exception_in_the_reason(self):
        action, reason, rr = self._fallback({"action": "BUY_LONG"})
        self.assertEqual(action, "WAIT", "管线挂了绝不许放行原动作")
        self.assertIn("插件系统崩了", reason)
        self.assertIn("安全降级为 WAIT", reason)
        self.assertEqual(rr, 0.0)

    def test_buy_long_risk_reward_is_computed_on_the_fallback(self):
        _, _, rr = self._fallback({"action": "BUY_LONG", "entry_price": "100",
                                   "stop_loss_price": "95", "take_profit_price": "115"})
        self.assertAlmostEqual(rr, 3.0)

    def test_sell_short_risk_reward_is_computed_on_the_fallback(self):
        _, _, rr = self._fallback({"action": "SELL_SHORT", "entry_price": "100",
                                   "stop_loss_price": "110", "take_profit_price": "80"})
        self.assertAlmostEqual(rr, 2.0)

    def test_buy_long_rr_is_zero_when_the_stop_is_not_below_the_entry(self):
        _, _, rr = self._fallback({"action": "BUY_LONG", "entry_price": "100",
                                   "stop_loss_price": "105", "take_profit_price": "115"})
        self.assertEqual(rr, 0.0)

    def test_buy_long_rr_is_zero_when_take_profit_is_not_above_entry(self):
        _, _, rr = self._fallback({"action": "BUY_LONG", "entry_price": "100",
                                   "stop_loss_price": "95", "take_profit_price": "99"})
        self.assertEqual(rr, 0.0)

    def test_sell_short_rr_requires_the_full_ordering(self):
        for tp, sl in (("105", "110"), ("80", "95")):
            with self.subTest(take_profit=tp, stop_loss=sl):
                _, _, rr = self._fallback({"action": "SELL_SHORT", "entry_price": "100",
                                           "stop_loss_price": sl, "take_profit_price": tp})
                self.assertEqual(rr, 0.0)

    def test_unknown_action_is_coerced_to_wait(self):
        for action in ("HOLD", "buy_long", "", None, "LONG"):
            with self.subTest(action=action):
                out_action, _, rr = self._fallback({"action": action})
                self.assertEqual(out_action, "WAIT")
                self.assertEqual(rr, 0.0)

    def test_falsy_decision_items_still_degrade_safely(self):
        for d_item in ({}, None, [], (), 0, "", 0.0):
            with self.subTest(d_item=d_item):
                action, reason, rr = self._fallback(d_item)
                self.assertEqual(action, "WAIT")
                self.assertEqual(rr, 0.0)
                self.assertIn("安全降级", reason)

    def test_truthy_non_dict_decision_item_crashes_the_fallback(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：兜底体写作 `(d_item or {}).get("action", ...)`
        #    —— `or {}` 只兜住**假值**。一个**真值非 dict**（`"junk"` / `42` / `["a"]`）
        #    会走进 `.get` 而抛 `AttributeError`，于是"最后一道防线"自己炸掉：
        #    调用方拿到的不是安全的 `WAIT`，而是一个异常。
        #    方向上它仍是 fail-closed（异常 ＞ 放行），但"降级成 WAIT"这个承诺在此不成立，
        #    所以必须显式钉住现状，避免后人以为这里"什么都兜得住"。
        for d_item in ("junk", 42, ["a"], {"a": 1}.keys):
            with self.subTest(d_item=d_item):
                with self.assertRaises(AttributeError):
                    self._fallback(d_item)

    def test_zero_stop_only_is_not_enough_for_rr(self):
        # `entry > stop_loss > 0` —— 三个条件都要；stop 为 0 时不算
        _, _, rr = self._fallback({"action": "BUY_LONG", "entry_price": "100",
                                   "stop_loss_price": "0", "take_profit_price": "115"})
        self.assertEqual(rr, 0.0)

    def test_normal_path_returns_the_pipeline_result_verbatim(self):
        import r20_backend.interceptor_manager as im
        with patch.object(im, "run_interceptor_pipeline",
                          return_value=("BUY_LONG", "通过", 2.5)) as pipeline:
            out = validate_and_filter_decision({"instId": "X"}, {"action": "BUY_LONG"},
                                               {"X"}, {"X": "long"}, safe_float=_sf)
        self.assertEqual(out, ("BUY_LONG", "通过", 2.5))
        pipeline.assert_called_once()

    def test_context_carries_active_ids_and_sides(self):
        import r20_backend.interceptor_manager as im
        with patch.object(im, "run_interceptor_pipeline",
                          return_value=("WAIT", "r", 0.0)) as pipeline:
            validate_and_filter_decision({}, {}, {"A"}, {"A": "long"}, safe_float=_sf)
        context = pipeline.call_args.args[2]
        self.assertEqual(context["active_inst_ids"], {"A"})
        self.assertEqual(context["active_position_sides"], {"A": "long"})

    def test_import_failure_also_degrades_rather_than_raising(self):
        with patch.dict(sys.modules, {"r20_backend.interceptor_manager": None}):
            action, reason, _ = validate_and_filter_decision({}, {"action": "BUY_LONG"},
                                                             set(), {}, safe_float=_sf)
        self.assertEqual(action, "WAIT")
        self.assertIn("安全降级", reason)


class _CacheBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = self.tmp.name
        self.validate_calls: list = []

    def _validate(self, *args):
        self.validate_calls.append(args)
        return ("BUY_LONG", "", 2.0)

    def _assemble(self, packages=None, decisions=None, **over):
        kw = {"packages": packages if packages is not None else [_pkg()],
              "decisions_dict": decisions if decisions is not None else {},
              "active_inst_ids": set(), "active_position_sides": {},
              "time_str": "2026-01-01 00:00:00", "macro_summary": "MACRO",
              "data_dir": self.data_dir, "max_leverage": 10.0, "min_leverage": 1.0,
              "safe_float": _sf, "get_system_version_tag": lambda: "v9",
              "validate": self._validate}
        kw.update(over)
        return assemble_decision_cache(**kw)

    def _write_multipliers(self, payload):
        Path(self.data_dir, "asset_multipliers.json").write_text(
            json.dumps(payload), encoding="utf-8")


class LeverageClampTests(_CacheBase, unittest.TestCase):
    """★ 2026-09-10 事故：上限配 7 却一律 3x。夹取必须与风控页区间联动。"""

    def _leverage(self, requested, *, max_lev, min_lev):
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"leverage": requested,
                                                          "margin_usdt": 100}},
                             max_leverage=max_lev, min_leverage=min_lev)
        return out["BTC-USDT-SWAP"]["decision"]["leverage"]

    def test_requested_value_inside_the_band_is_kept(self):
        self.assertEqual(self._leverage(5, max_lev=10.0, min_lev=2.0), 5)

    def test_lower_bound_lifts_a_too_small_request(self):
        # 换到最小可复现的整数对：请求 3、下限 5 ⇒ 抬到 5（事故原文的行为）
        self.assertEqual(self._leverage(3, max_lev=7.0, min_lev=5.0), 5)

    def test_upper_bound_caps_a_too_large_request(self):
        self.assertEqual(self._leverage(20, max_lev=7.0, min_lev=2.0), 7)

    def test_patched_bounds_are_actually_read_at_call_time(self):
        # ★ 这正是"注入面"存在的理由：读到的是**调用时**的值，不是 import 期快照
        self.assertEqual(self._leverage(3, max_lev=15.0, min_lev=1.0), 3)
        self.assertEqual(self._leverage(3, max_lev=15.0, min_lev=5.0), 5)

    def test_missing_leverage_falls_back_to_the_lower_bound(self):
        self.assertEqual(self._leverage(None, max_lev=10.0, min_lev=4.0), 4)

    def test_bounds_are_rounded_and_never_below_one(self):
        self.assertEqual(self._leverage(5, max_lev=10.7, min_lev=-3.0), 5)

    def test_lower_bound_is_capped_by_the_upper_bound(self):
        # `lev_lo = max(1, min(int(round(min_leverage)), lev_hi))` —— 区间倒挂时 lo = hi
        self.assertEqual(self._leverage(1, max_lev=4.0, min_lev=99.0), 4)

    def test_leverage_is_an_integer(self):
        self.assertIsInstance(self._leverage(5, max_lev=10.0, min_lev=1.0), int)


class AssetMultiplierTests(_CacheBase, unittest.TestCase):
    """自我改进产出的 per-asset 倍率：夹在 [0.5, 1.5]，只影响保证金、不影响杠杆。"""

    def test_multiplier_scales_the_margin(self):
        self._write_multipliers({"multipliers": {"BTC": 1.2}})
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 120.0)

    def test_default_multiplier_is_one(self):
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 100.0)

    def test_multiplier_is_clamped_to_the_band(self):
        self._write_multipliers({"multipliers": {"BTC": 99.0, "ETH": 0.01}})
        out = self._assemble(packages=[_pkg("BTC-USDT-SWAP"), _pkg("ETH-USDT-SWAP")],
                             decisions={"BTC-USDT-SWAP": {"margin_usdt": 100},
                                        "ETH-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 150.0)
        self.assertEqual(out["ETH-USDT-SWAP"]["decision"]["margin_usdt"], 50.0)

    def test_zero_margin_stays_zero_regardless_of_multiplier(self):
        self._write_multipliers({"multipliers": {"BTC": 1.5}})
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 0}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 0.0)

    def test_negative_margin_stays_zero(self):
        self._write_multipliers({"multipliers": {"BTC": 1.5}})
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": -5}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 0.0)

    def test_full_symbol_key_is_also_accepted(self):
        self._write_multipliers({"multipliers": {"BTC-USDT-SWAP": 1.3}})
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 130.0)

    def test_short_symbol_key_wins_over_the_full_one(self):
        self._write_multipliers({"multipliers": {"BTC": 1.1, "BTC-USDT-SWAP": 1.4}})
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 110.0)

    def test_inst_id_without_a_dash_uses_itself_as_the_key(self):
        self._write_multipliers({"multipliers": {"WEIRD": 1.2}})
        out = self._assemble(packages=[_pkg("WEIRD")],
                             decisions={"WEIRD": {"margin_usdt": 100}})
        self.assertEqual(out["WEIRD"]["decision"]["margin_usdt"], 120.0)

    def test_corrupt_multiplier_file_is_swallowed_and_defaults_apply(self):
        # ★ 第 98 行 `except Exception: pass` —— 坏文件不许把决策装配整个打挂
        Path(self.data_dir, "asset_multipliers.json").write_text("{ not json",
                                                                 encoding="utf-8")
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 100.0)

    def test_multipliers_key_missing_uses_an_empty_mapping(self):
        self._write_multipliers({"other": 1})
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 100.0)

    def test_no_multiplier_file_at_all_is_fine(self):
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["margin_usdt"], 100.0)

    def test_multiplier_does_not_touch_leverage(self):
        self._write_multipliers({"multipliers": {"BTC": 1.5}})
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"leverage": 6, "margin_usdt": 100}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["leverage"], 6)


class DecisionEntryShapeTests(_CacheBase, unittest.TestCase):
    def test_non_dict_decision_entry_is_replaced_by_an_empty_dict(self):
        # ★ 第 104 行 —— 决策表里一项不是 dict（模型回包畸形/半写）不许 AttributeError
        out = self._assemble(decisions={"BTC-USDT-SWAP": ["junk"]})
        decision = out["BTC-USDT-SWAP"]["decision"]
        self.assertEqual(decision["entry_price"], 0.0)
        self.assertEqual(decision["leverage"], 1)
        self.assertEqual(decision["margin_usdt"], 0.0)

    def test_absent_symbol_yields_an_empty_decision(self):
        out = self._assemble(decisions={})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["confidence"], 0.0)

    def test_field_aliases_are_accepted(self):
        out = self._assemble(decisions={"BTC-USDT-SWAP": {
            "limit_price": "101", "take_profit": "120", "stop_loss": "95"}})
        decision = out["BTC-USDT-SWAP"]["decision"]
        self.assertEqual(decision["entry_price"], 101.0)
        self.assertEqual(decision["take_profit_price"], 120.0)
        self.assertEqual(decision["stop_loss_price"], 95.0)

    def test_confidence_is_clamped_to_zero_through_one_hundred(self):
        for raw, expected in (("150", 100.0), ("-20", 0.0), ("55.5", 55.5)):
            with self.subTest(raw=raw):
                out = self._assemble(decisions={"BTC-USDT-SWAP": {"confidence": raw}})
                self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["confidence"], expected)

    def test_normalized_keys_are_handed_to_validate(self):
        # ★ 下游拦截器依赖规范化后的键（模块注释：「Ensure normalized keys exist」）
        self._write_multipliers({"multipliers": {"BTC": 1.2}})
        self._assemble(decisions={"BTC-USDT-SWAP": {
            "limit_price": "101", "take_profit": "120", "stop_loss": "95",
            "leverage": 6, "margin_usdt": 100}})
        normalized = self.validate_calls[0][1]
        self.assertEqual(normalized["entry_price"], 101.0)
        self.assertEqual(normalized["take_profit_price"], 120.0)
        self.assertEqual(normalized["stop_loss_price"], 95.0)
        self.assertEqual(normalized["leverage"], 6)
        self.assertEqual(normalized["margin_usdt"], 120.0)

    def test_validate_receives_the_documented_four_positional_arguments(self):
        self._assemble(active_inst_ids={"X"}, active_position_sides={"X": "long"})
        args = self.validate_calls[0]
        self.assertEqual(len(args), 4)
        self.assertEqual(args[2], {"X"})
        self.assertEqual(args[3], {"X": "long"})


class CacheContractTests(_CacheBase, unittest.TestCase):
    def test_cache_key_is_the_inst_id(self):
        out = self._assemble(packages=[_pkg("BTC-USDT-SWAP"), _pkg("ETH-USDT-SWAP")],
                             decisions={})
        self.assertEqual(sorted(out), ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

    def test_risk_reward_is_formatted_or_dashed(self):
        self.assertEqual(self._assemble(decisions={})["BTC-USDT-SWAP"]
                         ["decision"]["risk_reward_ratio"], "2.00 : 1")
        self._validate = lambda *a: ("WAIT", "拒", 0.0)
        self.assertEqual(self._assemble(decisions={})["BTC-USDT-SWAP"]
                         ["decision"]["risk_reward_ratio"], "--")

    def test_rejection_reason_wins_over_the_model_summary(self):
        self._validate = lambda *a: ("WAIT", "拦截：仓位冲突", 0.0)
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"summary_reason": "模型自己的理由"}})
        self.assertEqual(out["BTC-USDT-SWAP"]["decision"]["summary_reason"], "拦截：仓位冲突")

    def test_model_summary_is_used_and_truncated_when_there_is_no_rejection(self):
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"summary_reason": "长" * 300}})
        self.assertEqual(len(out["BTC-USDT-SWAP"]["decision"]["summary_reason"]), 120)

    def test_default_summary_when_nothing_is_provided(self):
        out = self._assemble(decisions={})
        self.assertIn("全市场矩阵综合评估中",
                      out["BTC-USDT-SWAP"]["decision"]["summary_reason"])

    def test_policy_snapshot_is_echoed_into_the_cache(self):
        out = self._assemble(decisions={},
                             policy_snapshot={"policy_version": "v7.9.2",
                                              "policy_hash": "abc", "summary": "S"})
        entry = out["BTC-USDT-SWAP"]
        self.assertEqual(entry["policy_version"], "v7.9.2")
        self.assertEqual(entry["policy_hash"], "abc")
        self.assertEqual(entry["policy_snapshot"],
                         {"policy_version": "v7.9.2", "policy_hash": "abc", "summary": "S"})

    def test_absent_policy_snapshot_falls_back_to_the_version_tag(self):
        out = self._assemble(decisions={})
        entry = out["BTC-USDT-SWAP"]
        self.assertEqual(entry["policy_version"], "v9@unknown")
        self.assertEqual(entry["policy_hash"], "unknown")
        self.assertEqual(entry["policy_snapshot"]["summary"], "")

    def test_council_block_merges_status_with_the_adopted_role(self):
        out = self._assemble(decisions={"BTC-USDT-SWAP": {"adopted_role": "CIO"}},
                             council_status={"ran": True, "reason": "consensus"})
        self.assertEqual(out["BTC-USDT-SWAP"]["council"],
                         {"ran": True, "reason": "consensus", "adopted_role": "CIO"})

    def test_council_defaults_to_not_ran(self):
        self.assertEqual(self._assemble(decisions={})["BTC-USDT-SWAP"]["council"],
                         {"ran": False, "adopted_role": None})

    def test_thought_process_defaults_are_explicit_placeholders(self):
        thought = self._assemble(decisions={})["BTC-USDT-SWAP"]["thought_process"]
        self.assertIn("中性", thought["market_structure"])
        self.assertIn("模型未提供", thought["calculus_dynamics"])
        self.assertIn("模型未提供", thought["math_prob_rationale"])
        self.assertIn("以【本周期风险预算】为准", thought["risk_reward_evaluation"])

    def test_volume_and_oi_default_falls_back_to_the_package_values(self):
        thought = self._assemble(decisions={})["BTC-USDT-SWAP"]["thought_process"]
        self.assertIn("1.0亿 U", thought["volume_and_oi"])
        self.assertIn("2.0万 U", thought["volume_and_oi"])

    def test_raw_fields_fall_back_to_a_dash(self):
        out = self._assemble(packages=[_pkg(fundingRate=0, oiUsd=None,
                                            takerNetUsd=None, lsRatio=None)],
                             decisions={})
        entry = out["BTC-USDT-SWAP"]
        self.assertEqual(entry["raw_funding_rate"], "--")
        self.assertEqual(entry["raw_oi"], "--")
        self.assertEqual(entry["raw_taker_vol"], "--")
        self.assertEqual(entry["raw_ls_ratio"], "--")

    def test_ls_ratio_zero_is_rendered_not_dashed(self):
        # `is not None` 判据 ⇒ 0 要显示成 "0"，不是 "--"
        out = self._assemble(packages=[_pkg(lsRatio=0)], decisions={})
        self.assertEqual(out["BTC-USDT-SWAP"]["raw_ls_ratio"], "0")

    def test_smart_money_and_xvenue_default_to_empty_dicts(self):
        out = self._assemble(packages=[_pkg()], decisions={})
        # 包里有 smart_money 就透传；没有就给空 dict（不是 None）
        out2 = self._assemble(packages=[_pkg(smart_money={"available": False})], decisions={})
        self.assertEqual(out2["BTC-USDT-SWAP"]["smart_money"], {"available": False})
        self.assertEqual(out["BTC-USDT-SWAP"]["xvenue"], {})

    def test_absent_data_quality_defaults_to_invalid_not_valid(self):
        # ★ 默认值是 "invalid"（宁可说无效，也不默认有效）
        pkg = _pkg()
        del pkg["data_quality"]
        out = self._assemble(packages=[pkg], decisions={})
        self.assertEqual(out["BTC-USDT-SWAP"]["data_quality"], "invalid")

    def test_explicit_none_data_quality_passes_through_as_none(self):
        # ⚠️ `.get(key, default)` 的默认值只在**键不存在**时生效 ⇒ 显式 `None` 会原样透出。
        #    这是 `dict.get` 的既有语义，不是本函数的特殊处理 —— 钉住以免被当成 bug "修掉"
        out = self._assemble(packages=[_pkg(data_quality=None)], decisions={})
        self.assertIsNone(out["BTC-USDT-SWAP"]["data_quality"])

    def test_timestamp_is_a_unix_second_integer(self):
        entry = self._assemble(decisions={})["BTC-USDT-SWAP"]
        self.assertIsInstance(entry["timestamp"], int)
        self.assertEqual(entry["time_str"], "2026-01-01 00:00:00")
        self.assertEqual(entry["macro_assessment"], "MACRO")

    def test_empty_package_list_yields_an_empty_cache(self):
        self.assertEqual(self._assemble(packages=[], decisions={}), {})


if __name__ == "__main__":
    unittest.main()
