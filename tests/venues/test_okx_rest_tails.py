"""OKX V5 REST 私有客户端（`scripts/okx_rest.py`）的残余分支收口 —— 第 329 刀。

本模块 565 行，是**钱路最底层**：所有签名私有请求（下单 / 撤单 / 改单 / 设杠杆 /
平仓 / 查询 / 云端 OCO 棘轮）都经 `request()` 出网。既有
`tests/venues/test_okx_rest.py`（365 行）覆盖了主干，本刀补 19 行缺口 ——
**几乎全是"签名前必须拦住"的参数校验**。

## 本刀立住的三条纪律

1. **签名前校验，不是签名后**：`_validate_params` 在任何出网动作之前拒掉非法字段，
   因为一次"格式错但被交易所接受"的请求会留下**无法回滚**的持仓状态。
2. **`Decimal.normalize()` 会用环境精度四舍五入长价格** ⇒ 必须用定点格式化再摘尾零
   （`_fmt` 的 docstring 明说此事）。
3. **"接受"≠"最终确认"**：`amend_order` 的 docstring 提醒 `sCode=0` 只代表被受理。
"""
from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.okx_rest as okx  # noqa: E402


def _env(*, configured=True, simulated=True):
    return SimpleNamespace(configured=configured, simulated=simulated, mode="demo",
                           api_key="AK", secret_key="SK", passphrase="PP",
                           base_url="https://www.okx.com")


def _resp(payload, *, status=200, read_exc=None):
    stream = MagicMock()
    stream.read.side_effect = read_exc if read_exc else None
    if read_exc is None:
        stream.read.return_value = json.dumps(payload).encode("utf-8")
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: False
    return stream


class _CaptureRequest(unittest.TestCase):
    """把 `request` 换成记录器 —— 只测包裹层的参数构造。"""

    def setUp(self):
        self.calls: list = []

        def _fake(method, path, params=None, **kw):
            self.calls.append({"method": method, "path": path, "params": params, **kw})
            return [{"sCode": "0"}]
        p = patch.object(okx, "request", _fake)
        p.start()
        self.addCleanup(p.stop)

    @property
    def sent(self):
        assert len(self.calls) == 1, self.calls
        return self.calls[0]


# ───────────────────── _fmt ─────────────────────
class FormatTests(unittest.TestCase):
    def test_booleans_are_preserved_as_json_booleans(self):
        self.assertIs(okx._fmt(True), True)
        self.assertIs(okx._fmt(False), False)

    def test_ints_become_strings(self):
        self.assertEqual(okx._fmt(7), "7")

    def test_floats_are_rendered_without_rounding(self):
        # `Decimal.normalize()` 会用环境精度四舍五入长价格 ⇒ 必须定点格式化
        self.assertEqual(okx._fmt(1.23456789012345678), "1.2345678901234567")

    def test_trailing_fractional_zeros_are_removed(self):
        self.assertEqual(okx._fmt(1.50), "1.5")
        self.assertEqual(okx._fmt(2.0), "2")
        self.assertEqual(okx._fmt(0.100), "0.1")

    def test_a_decimal_is_rendered_losslessly(self):
        self.assertEqual(okx._fmt(Decimal("0.000000000000000001")),
                         "0.000000000000000001")

    def test_an_infinite_float_is_refused(self):
        # ★ 第 71/72 行
        for bad in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    okx._fmt(bad)
                self.assertIn("must be finite", str(ctx.exception))

    def test_an_infinite_decimal_is_refused(self):
        with self.assertRaises(ValueError):
            okx._fmt(Decimal("Infinity"))

    def test_the_string_spellings_of_infinity_and_nan_are_refused(self):
        # ★ 第 77–81 行 —— 全部已知拼法
        for bad in ("nan", "NaN", "snan", "inf", "INF", "infinity", "+inf",
                    "-inf", "+nan", "-nan", "+infinity", "-infinity", " inf "):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    okx._fmt(bad)
                self.assertIn("must be finite", str(ctx.exception))

    def test_an_ordinary_string_is_passed_through_untouched(self):
        self.assertEqual(okx._fmt("BTC-USDT-SWAP"), "BTC-USDT-SWAP")
        self.assertEqual(okx._fmt(""), "")

    def test_a_long_price_string_keeps_every_digit(self):
        self.assertEqual(okx._fmt("61234.12345678901234567890"),
                         "61234.12345678901234567890")


# ───────────────────── _validate_params ─────────────────────
class ValidateParamsTests(unittest.TestCase):
    def test_a_clean_mapping_passes(self):
        # 校验器是 void 的 ⇒ 断言"跑完且无返回值"（比单纯"不抛"更强）
        self.assertIsNone(okx._validate_params(
            {"instId": "BTC-USDT-SWAP", "sz": "1", "reduceOnly": True}))

    def test_a_boolean_field_must_be_a_real_bool(self):
        with self.assertRaises(ValueError) as ctx:
            okx._validate_params({"reduceOnly": "true"})
        self.assertIn("JSON Boolean", str(ctx.exception))

    def test_the_trigger_price_type_is_whitelisted(self):
        for good in ("last", "index", "mark"):
            with self.subTest(good=good):
                okx._validate_params({"tpTriggerPxType": good})
        with self.assertRaises(ValueError):
            okx._validate_params({"tpTriggerPxType": "mid"})

    def test_a_boolean_in_a_price_field_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            okx._validate_params({"px": True})
        self.assertIn("finite decimal", str(ctx.exception))

    def test_an_unparsable_price_is_refused(self):
        # ★ 第 113–116 行
        with self.assertRaises(ValueError) as ctx:
            okx._validate_params({"px": object()})
        self.assertIn("finite decimal", str(ctx.exception))

    def test_an_infinite_price_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            okx._validate_params({"px": "Infinity"})
        self.assertIn("finite", str(ctx.exception))

    def test_a_negative_price_is_outside_the_v5_range(self):
        with self.assertRaises(ValueError) as ctx:
            okx._validate_params({"px": "-1"})
        self.assertIn("outside its V5 price/size range", str(ctx.exception))

    def test_minus_one_is_valid_as_a_market_execution_price(self):
        self.assertIsNone(okx._validate_params({"tpOrdPx": "-1"}))
        self.assertIsNone(okx._validate_params({"slOrdPx": "-1"}))

    def test_zero_deletes_a_new_leg_but_is_refused_elsewhere(self):
        # `deletion = key.startswith("new") and key not in {"newPx", "newSz"}`
        okx._validate_params({"newTpTriggerPx": "0"})
        with self.assertRaises(ValueError):
            okx._validate_params({"sz": "0"})

    def test_a_zero_new_px_is_still_out_of_range(self):
        # `newPx` / `newSz` 在白名单外 ⇒ 0 不许
        with self.assertRaises(ValueError):
            okx._validate_params({"newPx": "0"})

    def test_nested_lists_are_validated_recursively(self):
        with self.assertRaises(ValueError):
            okx._validate_params([{"px": "-1"}])

    def test_empty_and_none_values_are_skipped(self):
        self.assertIsNone(okx._validate_params({"px": None, "sz": "", "instId": "X"}))

    def test_cxl_on_close_pos_requires_reduce_only(self):
        # 第 124/125 行
        with self.assertRaises(ValueError) as ctx:
            okx._validate_params({"cxlOnClosePos": True})
        self.assertIn("requires reduceOnly=true", str(ctx.exception))
        okx._validate_params({"cxlOnClosePos": True, "reduceOnly": True})

    def test_a_non_mapping_non_sequence_is_ignored(self):
        for ignored in ("junk", 42, None):
            with self.subTest(ignored=ignored):
                self.assertIsNone(okx._validate_params(ignored))


# ───────────────────── _required / _validate_attachments ─────────────────────
class RequiredTests(unittest.TestCase):
    def test_a_real_string_passes(self):
        self.assertEqual(okx._required("X", "instId"), "X")

    def test_blank_and_non_strings_are_refused(self):
        for bad in (None, "", "   ", 42, [], {}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    okx._required(bad, "instId")
                self.assertIn("instId is required", str(ctx.exception))


class ValidateAttachmentsTests(unittest.TestCase):
    def test_none_is_a_valid_no_op(self):
        self.assertIsNone(okx._validate_attachments(None))

    def test_an_empty_list_is_a_valid_no_op(self):
        self.assertIsNone(okx._validate_attachments([]))

    def test_a_non_sequence_is_refused(self):
        # ★ 第 137/138 行
        for bad in ({"tpTriggerPx": "1"}, "junk", 42):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    okx._validate_attachments(bad)
                self.assertIn("array of objects", str(ctx.exception))

    def test_an_empty_leg_object_is_refused(self):
        # ★ 第 140/141 行
        with self.assertRaises(ValueError) as ctx:
            okx._validate_attachments([{}])
        self.assertIn("nonempty object", str(ctx.exception))

    def test_a_non_mapping_leg_is_refused(self):
        with self.assertRaises(ValueError):
            okx._validate_attachments(["junk"])

    def test_td_mode_inside_a_leg_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            okx._validate_attachments([{"tdMode": "cross", "tpTriggerPx": "1"}])
        self.assertIn("belongs to the parent order", str(ctx.exception))

    def test_a_trigger_without_its_execution_price_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            okx._validate_attachments([{"tpTriggerPx": "100"}])
        self.assertIn("requires tpOrdPx", str(ctx.exception))

    def test_the_amend_prefix_uses_capitalised_keys(self):
        # amend=True 时键名是 `newTpTriggerPx` / `newTpOrdPx`
        self.assertIsNone(okx._validate_attachments(
            [{"newTpTriggerPx": "100", "newTpOrdPx": "-1"}], amend=True))

    def test_amend_mode_does_not_apply_the_trigger_requires_price_rule(self):
        # `if not amend and ...` ⇒ 改单模式下该规则不生效（对照：非 amend 会抛）
        self.assertIsNone(okx._validate_attachments([{"newTpTriggerPx": "100"}],
                                                    amend=True))
        with self.assertRaises(ValueError):
            okx._validate_attachments([{"tpTriggerPx": "100"}])

    def test_a_leg_still_faces_the_full_param_validation(self):
        with self.assertRaises(ValueError):
            okx._validate_attachments([{"tpTriggerPx": "100", "tpOrdPx": "-1",
                                        "tpTriggerPxType": "mid"}])


# ───────────────────── _clean ─────────────────────
class CleanTests(unittest.TestCase):
    def test_none_returns_none(self):
        # ★ 第 155/156 行
        self.assertIsNone(okx._clean(None))

    def test_empty_scalars_are_dropped(self):
        self.assertEqual(okx._clean({"a": None, "b": "", "c": "keep"}), {"c": "keep"})

    def test_numbers_are_formatted(self):
        self.assertEqual(okx._clean({"px": 1.50, "sz": 2}), {"px": "1.5", "sz": "2"})

    def test_nested_mappings_are_cleaned_recursively(self):
        self.assertEqual(okx._clean({"outer": {"inner": None, "keep": 1}}),
                         {"outer": {"keep": "1"}})

    def test_a_nested_mapping_that_cleans_to_empty_is_dropped(self):
        self.assertEqual(okx._clean({"outer": {"inner": None}}), {})

    def test_a_list_of_mappings_is_cleaned_and_filtered(self):
        self.assertEqual(okx._clean([{"a": 1}, {"b": None}, {}]), [{"a": "1"}])

    def test_a_scalar_passes_through_unchanged(self):
        # ★ 第 169 行
        self.assertEqual(okx._clean("plain"), "plain")
        self.assertEqual(okx._clean(7), 7)

    def test_a_bare_list_of_scalars_drops_only_none_and_containers(self):
        # 判据是 `row not in (None, {}, [])` ⇒ **空串 `""` 会被保留**
        # （空串的剔除只发生在 Mapping 分支）
        self.assertEqual(okx._clean(["a", "", None, "b"]), ["a", "", "b"])
        self.assertNotIn(None, okx._clean(["a", None, "b"]))

    def test_a_top_level_scalar_sequence_is_not_formatted(self):
        # 顶层 list/tuple 走的是"逐项 _clean 再过滤"，不套 `_fmt`
        self.assertEqual(okx._clean([float("nan") and "x"]), ["x"])


# ───────────────────── request ─────────────────────
class RequestTests(unittest.TestCase):
    def _call(self, payload, **kw):
        kw.setdefault("params", {"instId": "BTC-USDT-SWAP"})
        with patch.object(okx, "urlopen", return_value=_resp(payload)):
            return okx.request(kw.pop("method", "GET"), "/api/v5/x",
                               env=_env(), **kw)

    def test_unconfigured_keys_raise_before_any_network_call(self):
        with patch.object(okx, "urlopen") as u:
            with self.assertRaises(okx.OKXNotConfigured):
                okx.request("GET", "/api/v5/x", env=_env(configured=False))
        u.assert_not_called()

    def test_the_current_environment_is_used_when_none_is_given(self):
        with patch.object(okx, "current_environment", return_value=_env(configured=False)):
            with self.assertRaises(okx.OKXNotConfigured):
                okx.request("GET", "/api/v5/x")

    def test_a_good_response_returns_the_data_rows(self):
        rows = self._call({"code": "0", "data": [{"instId": "BTC"}]})
        self.assertEqual(rows, [{"instId": "BTC"}])

    def test_a_non_list_data_body_is_wrapped(self):
        # ★ 第 229/230 行
        rows = self._call({"code": "0", "data": {"instId": "BTC"}})
        self.assertEqual(rows, [{"instId": "BTC"}])

    def test_non_dict_rows_are_filtered_out(self):
        rows = self._call({"code": "0", "data": [{"a": 1}, "junk", 42]})
        self.assertEqual(rows, [{"a": 1}])

    def test_an_empty_data_body_yields_no_rows(self):
        self.assertEqual(self._call({"code": "0", "data": []}), [])
        self.assertEqual(self._call({"code": "0"}), [])

    def test_a_row_level_failure_code_raises_first(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"code": "0", "data": [{"sCode": "51008", "sMsg": "余额不足"}]})
        self.assertIn("51008", str(ctx.exception))
        self.assertIn("余额不足", str(ctx.exception))

    def test_a_row_failure_without_a_message_uses_the_fallback_text(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"code": "0", "data": [{"sCode": "1"}]})
        self.assertIn("业务请求失败", str(ctx.exception))

    def test_a_top_level_failure_code_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"code": "50111", "msg": "Invalid API Key"})
        self.assertIn("50111", str(ctx.exception))

    def test_a_top_level_failure_without_a_message_uses_the_fallback(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"code": "50000"})
        self.assertIn("请求失败", str(ctx.exception))

    def test_a_non_dict_envelope_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call(["not", "a", "dict"])
        self.assertIn("invalid response envelope", str(ctx.exception))

    def test_a_network_failure_is_wrapped_in_a_runtime_error(self):
        with patch.object(okx, "urlopen", side_effect=OSError("no net")):
            with self.assertRaises(RuntimeError) as ctx:
                okx.request("GET", "/api/v5/x", env=_env())
        self.assertIn("网络请求失败", str(ctx.exception))
        self.assertIn("OSError", str(ctx.exception))

    def test_an_empty_body_parses_as_an_empty_object(self):
        stream = _resp({})
        stream.read.return_value = b""
        with patch.object(okx, "urlopen", return_value=stream):
            self.assertEqual(okx.request("GET", "/api/v5/x", env=_env()), [])

    def test_a_simulated_environment_sends_the_paper_trading_header(self):
        captured = {}

        def _capture(req, timeout=None):
            captured["headers"] = req.headers
            return _resp({"code": "0", "data": []})
        with patch.object(okx, "urlopen", side_effect=_capture):
            okx.request("GET", "/api/v5/x", env=_env(simulated=True))
        # `urllib` 会把 header 名首字母大写（`X-simulated-trading`）⇒ 大小写不敏感比对
        keys = {k.lower() for k in captured["headers"]}
        self.assertIn("x-simulated-trading", keys)

    def test_the_live_environment_omits_the_paper_trading_header(self):
        captured = {}

        def _capture(req, timeout=None):
            captured["headers"] = req.headers
            return _resp({"code": "0", "data": []})
        with patch.object(okx, "urlopen", side_effect=_capture):
            okx.request("GET", "/api/v5/x", env=_env(simulated=False))
        keys = {k.lower() for k in captured["headers"]}
        self.assertNotIn("x-simulated-trading", keys)

    def test_get_params_go_into_the_query_string(self):
        captured = {}

        def _capture(req, timeout=None):
            captured["selector"] = req.selector
            return _resp({"code": "0", "data": []})
        with patch.object(okx, "urlopen", side_effect=_capture):
            okx.request("GET", "/api/v5/x", {"limit": 100, "reduceOnly": True},
                        env=_env())
        self.assertIn("limit=100", captured["selector"])
        # ⚠️ 字段名大小写**原样上线**（OKX 大小写敏感）—— 不要对整串调 `.lower()` 再断言，
        #    本刀在此自伤过一次。
        self.assertIn("reduceOnly=true", captured["selector"])

    def test_post_params_go_into_the_body(self):
        captured = {}

        def _capture(req, timeout=None):
            captured["body"] = req.data
            return _resp({"code": "0", "data": []})
        with patch.object(okx, "urlopen", side_effect=_capture):
            okx.request("POST", "/api/v5/x", {"sz": 1.5}, env=_env())
        self.assertEqual(json.loads(captured["body"]), {"sz": "1.5"})


# ───────────────────── 交易包裹层 ─────────────────────
class PlaceOrderTests(_CaptureRequest):
    def test_a_minimal_order_is_built(self):
        okx.place_order("BTC-USDT-SWAP", "buy", 1)
        self.assertEqual(self.sent["path"], "/api/v5/trade/order")
        self.assertEqual(self.sent["params"]["ordType"], "limit")

    def test_a_client_order_id_is_forwarded(self):
        # ★ 第 283/284 行
        okx.place_order("BTC-USDT-SWAP", "buy", 1, cl_ord_id="R20-1")
        self.assertEqual(self.sent["params"]["clOrdId"], "R20-1")

    def test_a_falsy_client_order_id_is_omitted(self):
        okx.place_order("BTC-USDT-SWAP", "buy", 1, cl_ord_id="")
        self.assertNotIn("clOrdId", self.sent["params"])

    def test_target_adj_is_forwarded(self):
        # ★ 第 287/288 行
        okx.place_order("BTC-USDT-SWAP", "buy", 1, target_adj="0")
        self.assertEqual(self.sent["params"]["targetAdj"], "0")

    def test_the_attached_leg_carries_the_explicit_trigger_price_type(self):
        # 第一百六十六刀：`mark` 显式发送，不依赖交易所默认
        okx.place_order("BTC-USDT-SWAP", "buy", 1, attach_tp="110", attach_sl="95")
        leg = self.sent["params"]["attachAlgoOrds"][0]
        self.assertEqual(leg["tpTriggerPxType"], "mark")
        self.assertEqual(leg["slTriggerPxType"], "mark")
        self.assertEqual(leg["tpOrdPx"], "-1", "默认市价止盈")

    def test_extra_fields_are_merged_last(self):
        okx.place_order("BTC-USDT-SWAP", "buy", 1, extra={"tdMode": "isolated"})
        self.assertEqual(self.sent["params"]["tdMode"], "isolated")

    def test_a_missing_inst_id_is_refused(self):
        with self.assertRaises(ValueError):
            okx.place_order("", "buy", 1)


class SetLeverageTests(_CaptureRequest):
    def test_the_leverage_is_stringified(self):
        # ★ 第 328 行
        okx.set_leverage("BTC-USDT-SWAP", 5)
        self.assertEqual(self.sent["path"], "/api/v5/account/set-leverage")
        self.assertEqual(self.sent["params"]["lever"], "5")

    def test_the_margin_mode_is_forwarded(self):
        okx.set_leverage("BTC-USDT-SWAP", 5, mgn_mode="isolated")
        self.assertEqual(self.sent["params"]["mgnMode"], "isolated")

    def test_a_position_side_is_forwarded_when_given(self):
        # ★ 第 329/330 行
        okx.set_leverage("BTC-USDT-SWAP", 5, pos_side="long")
        self.assertEqual(self.sent["params"]["posSide"], "long")

    def test_an_omitted_position_side_leaves_the_key_absent(self):
        # 官方语义：省略 posSide ⇒ 该 instId 全模式生效
        okx.set_leverage("BTC-USDT-SWAP", 5)
        self.assertNotIn("posSide", self.sent["params"])


class AmendOrderTests(_CaptureRequest):
    def test_a_plain_amend_is_built(self):
        okx.amend_order("BTC-USDT-SWAP", "OID", new_px="101")
        self.assertEqual(self.sent["path"], "/api/v5/trade/amend-order")
        self.assertEqual(self.sent["params"]["newPx"], "101")

    def test_conflicting_req_id_aliases_are_refused(self):
        # ★ 第 347/348 行 —— `reqTxId` 只是 Python 侧别名，从不上线
        with self.assertRaises(ValueError) as ctx:
            okx.amend_order("BTC-USDT-SWAP", "OID", req_id="A", req_tx_id="B")
        self.assertIn("conflicting reqId aliases", str(ctx.exception))

    def test_matching_aliases_are_accepted(self):
        okx.amend_order("BTC-USDT-SWAP", "OID", req_id="A", req_tx_id="A")
        self.assertEqual(self.sent["params"]["reqId"], "A")

    def test_the_compat_alias_is_used_when_req_id_is_absent(self):
        okx.amend_order("BTC-USDT-SWAP", "OID", req_tx_id="A")
        self.assertEqual(self.sent["params"]["reqId"], "A")

    def test_a_missing_order_id_is_refused(self):
        with self.assertRaises(ValueError):
            okx.amend_order("BTC-USDT-SWAP", "")


class ClosePositionTests(_CaptureRequest):
    def test_the_default_is_auto_cancel(self):
        okx.close_position("BTC-USDT-SWAP")
        self.assertIs(self.sent["params"]["autoCxl"], True)
        self.assertEqual(self.sent["params"]["posSide"], "net")

    def test_an_explicit_position_side_is_forwarded(self):
        okx.close_position("BTC-USDT-SWAP", pos_side="short")
        self.assertEqual(self.sent["params"]["posSide"], "short")


class PlaceAlgoOcoTests(_CaptureRequest):
    def _oco(self, **over):
        kw = {"inst_id": "BTC-USDT-SWAP", "side": "sell", "size": 1,
              "pos_side": "long", "tp_trigger_px": "110", "sl_trigger_px": "95"}
        kw.update(over)
        return okx.place_algo_oco(**kw)

    def test_an_oco_pair_is_built(self):
        self._oco()
        params = self.sent["params"]
        self.assertEqual(params["ordType"], "oco")
        self.assertIs(params["reduceOnly"], True)
        self.assertEqual(params["tpTriggerPxType"], "mark")
        self.assertEqual(params["slTriggerPxType"], "mark")

    def test_an_extra_that_overrides_the_order_type_is_refused(self):
        # ★ 第 494/495 行 —— `extra` 能覆盖 `ordType`，所以必须复核
        with self.assertRaises(ValueError) as ctx:
            self._oco(extra={"ordType": "conditional"})
        self.assertIn("requires ordType=oco", str(ctx.exception))

    def test_a_missing_inst_id_is_refused(self):
        with self.assertRaises(ValueError):
            self._oco(inst_id="")


class CancelAlgoOrdersTests(_CaptureRequest):
    def test_a_single_bare_id_becomes_a_one_row_body(self):
        okx.cancel_algo_orders("A1", inst_id="BTC-USDT-SWAP")
        self.assertEqual(self.sent["params"], [{"instId": "BTC-USDT-SWAP", "algoId": "A1"}])

    def test_a_batch_of_ids_is_accepted(self):
        okx.cancel_algo_orders(["A1", "A2"], inst_id="BTC-USDT-SWAP")
        self.assertEqual(len(self.sent["params"]), 2)

    def test_more_than_ten_ids_are_refused(self):
        with self.assertRaises(ValueError):
            okx.cancel_algo_orders([f"A{i}" for i in range(11)], inst_id="X")

    def test_an_empty_batch_is_refused(self):
        with self.assertRaises(ValueError):
            okx.cancel_algo_orders([], inst_id="X")

    def test_a_mapping_row_keeps_only_the_allowed_keys(self):
        okx.cancel_algo_orders([{"instId": "BTC-USDT-SWAP", "algoId": "A1",
                                 "junk": "x"}])
        self.assertEqual(self.sent["params"][0], {"instId": "BTC-USDT-SWAP",
                                                  "algoId": "A1"})

    def test_a_conflicting_inst_id_in_a_row_is_refused(self):
        # ★ 第 515/516 行
        with self.assertRaises(ValueError) as ctx:
            okx.cancel_algo_orders([{"instId": "ETH-USDT-SWAP", "algoId": "A1"}],
                                   inst_id="BTC-USDT-SWAP")
        self.assertIn("conflicting instId", str(ctx.exception))

    def test_a_matching_inst_id_is_accepted(self):
        okx.cancel_algo_orders([{"instId": "BTC-USDT-SWAP", "algoId": "A1"}],
                               inst_id="BTC-USDT-SWAP")
        self.assertEqual(self.sent["params"][0]["instId"], "BTC-USDT-SWAP")

    def test_a_row_without_a_bare_id_needs_the_module_level_inst_id(self):
        with self.assertRaises(ValueError):
            okx.cancel_algo_orders([{"algoClOrdId": "C1"}])


class AmendAlgoSlTests(_CaptureRequest):
    def test_a_plain_sl_move_is_built(self):
        okx.amend_algo_sl("A1", "100", inst_id="BTC-USDT-SWAP")
        self.assertEqual(self.sent["params"]["newSlTriggerPx"], "100")
        self.assertEqual(self.sent["params"]["newSlOrdPx"], "-1")

    def test_a_new_tp_trigger_without_an_execution_price_is_refused(self):
        # ★ 第 540/541 行
        with self.assertRaises(ValueError) as ctx:
            okx.amend_algo_sl("A1", "100", inst_id="BTC-USDT-SWAP",
                              new_tp_trigger_px="120")
        self.assertIn("newTpTriggerPx requires newTpOrdPx", str(ctx.exception))

    def test_a_matching_tp_pair_is_accepted(self):
        okx.amend_algo_sl("A1", "100", inst_id="BTC-USDT-SWAP",
                          new_tp_trigger_px="120", new_tp_ord_px="-1")
        self.assertEqual(self.sent["params"]["newTpTriggerPx"], "120")

    def test_the_trigger_price_types_are_optional(self):
        okx.amend_algo_sl("A1", "100", inst_id="BTC-USDT-SWAP",
                          new_sl_trigger_px_type="last")
        self.assertEqual(self.sent["params"]["newSlTriggerPxType"], "last")

    def test_a_missing_algo_id_is_refused(self):
        with self.assertRaises(ValueError):
            okx.amend_algo_sl("", "100", inst_id="BTC-USDT-SWAP")


class PendingAlgoOrdersTests(_CaptureRequest):
    def test_the_default_order_type_is_oco(self):
        okx.pending_algo_orders("BTC-USDT-SWAP")
        self.assertEqual(self.sent["params"]["ordType"], "oco")

    def test_the_conditional_oco_combination_is_allowed(self):
        # ★ 第 557/558 行
        okx.pending_algo_orders("BTC-USDT-SWAP", ord_type="conditional,oco")
        self.assertEqual(self.sent["params"]["ordType"], "conditional,oco")

    def test_any_other_combination_is_refused(self):
        for bad in ("conditional,trigger", "oco,trigger", "conditional,oco,trigger"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    okx.pending_algo_orders("BTC-USDT-SWAP", ord_type=bad)
                self.assertIn("only conditional,oco", str(ctx.exception))

    def test_an_out_of_range_limit_is_refused(self):
        for bad in (0, 101):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    okx.pending_algo_orders("BTC-USDT-SWAP", limit=bad)

    def test_the_result_is_filtered_locally_by_inst_id(self):
        with patch.object(okx, "request", return_value=[
                {"instId": "BTC-USDT-SWAP"}, {"instId": "ETH-USDT-SWAP"}]):
            rows = okx.pending_algo_orders("BTC-USDT-SWAP")
        self.assertEqual(rows, [{"instId": "BTC-USDT-SWAP"}])

    def test_no_local_filter_without_an_inst_id(self):
        with patch.object(okx, "request", return_value=[{"instId": "A"}, {"instId": "B"}]):
            self.assertEqual(len(okx.pending_algo_orders()), 2)


if __name__ == "__main__":
    unittest.main()
