"""US-001 封闭测试：scripts/okx_rest.py 统一 V5 直签客户端。

封闭三律对齐：
- 律①：一切交易所调用停在 HTTP 边界——patch `scripts.okx_rest.urlopen`（import 时
  绑定的别名，patch 位置即生效位置），零 subprocess、零真实网络。
- 律②：okx_rest 在 import 时绑定 `urlopen` 与 `current_environment`，测试 patch 的
  是 okx_rest 命名空间内的名字，而非 urllib.request 源头的同名函数。
- 律③：只钉住跨故事契约（方法名、异常、签名口径、头、端点路径、数组体），不钉历史
  CLI 实现。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import unittest
import urllib.parse
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.okx_rest as okx_rest
from scripts.okx_runtime import freeze_environment, unfreeze_environment

DEMO_ENV = {
    "R20_OKX_ENV": "demo",
    "OKX_DEMO_API_KEY": "DEMO_AK", "OKX_DEMO_SECRET_KEY": "DEMO_SK", "OKX_DEMO_PASSPHRASE": "DEMO_PP",
}
LIVE_ENV = {
    "R20_OKX_ENV": "live",
    "OKX_LIVE_API_KEY": "LIVE_AK", "OKX_LIVE_SECRET_KEY": "LIVE_SK", "OKX_LIVE_PASSPHRASE": "LIVE_PP",
}
UNCONFIGURED_ENV = {"R20_OKX_ENV": "demo"}  # 无任何键 → configured False


def _response(code="0", msg="", data=None):
    body = json.dumps({"code": code, "msg": msg, "data": data if data is not None else []}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _captured(mock):
    """Return (method, url, headers_lower, data_bytes) of the single urlopen call."""
    mock.assert_called_once()
    request = mock.call_args.args[0]
    headers = {str(key).lower(): value for key, value in request.header_items()}
    return request.get_method(), request.full_url, headers, request.data


def _expected_sign(secret: str, timestamp: str, method: str, path_with_query: str, body_text: str) -> str:
    prehash = timestamp + method + path_with_query + body_text
    return base64.b64encode(hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()


class OKXRestHttpBoundaryTests(unittest.TestCase):
    """Each case freezes the env (the real cycle mechanism) and intercepts urlopen."""

    def setUp(self):
        unfreeze_environment()
        patcher = patch.object(okx_rest, "urlopen")
        self.urlopen = patcher.start()
        self.addCleanup(unfreeze_environment)
        self.addCleanup(patcher.stop)

    # -- 签名口径 / 头 ------------------------------------------------------

    def test_signed_get_recomputable_prehash_and_demo_header(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"ordId": "1", "instId": "BTC-USDT-SWAP"}])
        rows = okx_rest.pending_orders("BTC-USDT-SWAP")
        self.assertEqual(rows[0]["ordId"], "1")
        method, url, headers, data = _captured(self.urlopen)
        self.assertEqual(method, "GET")
        self.assertIsNone(data)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/orders-pending?instType=SWAP&instId=BTC-USDT-SWAP")
        self.assertEqual(headers["ok-access-key"], "DEMO_AK")
        self.assertEqual(headers["ok-access-passphrase"], "DEMO_PP")
        self.assertEqual(headers["x-simulated-trading"], "1")
        self.assertEqual(
            headers["ok-access-sign"],
            _expected_sign("DEMO_SK", headers["ok-access-timestamp"], "GET",
                           url.replace("https://www.okx.com", ""), ""),
        )

    def test_live_mode_has_no_simulated_header_and_signs_with_body(self):
        freeze_environment(LIVE_ENV)
        self.urlopen.return_value = _response(data=[{"ordId": "42"}])
        okx_rest.cancel_order("ETH-USDT-SWAP", "42")
        method, url, headers, data = _captured(self.urlopen)
        self.assertEqual(method, "POST")
        self.assertNotIn("x-simulated-trading", headers)
        self.assertEqual(headers["ok-access-key"], "LIVE_AK")
        body_text = data.decode("utf-8")
        self.assertEqual(json.loads(body_text), {"instId": "ETH-USDT-SWAP", "ordId": "42"})
        self.assertEqual(
            headers["ok-access-sign"],
            _expected_sign("LIVE_SK", headers["ok-access-timestamp"], "POST",
                           "/api/v5/trade/cancel-order", body_text),
        )

    # -- fail-closed ---------------------------------------------------------

    def test_unconfigured_raises_without_any_request(self):
        freeze_environment(UNCONFIGURED_ENV)
        with self.assertRaises(okx_rest.OKXNotConfigured):
            okx_rest.balances()
        self.urlopen.assert_not_called()

    def test_okx_not_configured_is_runtime_error_subclass(self):
        self.assertTrue(issubclass(okx_rest.OKXNotConfigured, RuntimeError))

    # -- 错误语义 ------------------------------------------------------------

    def test_envelope_code_failure_raises_with_code_and_msg(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(code="50011", msg="invalid signature")
        with self.assertRaises(RuntimeError) as ctx:
            okx_rest.positions()
        self.assertIn("50011", str(ctx.exception))
        self.assertIn("invalid signature", str(ctx.exception))

    def test_row_scodes_failure_raises(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"ordId": "7", "sCode": "51001", "sMsg": "Order does not exist"}])
        with self.assertRaises(RuntimeError) as ctx:
            okx_rest.cancel_order("BTC-USDT-SWAP", "7")
        self.assertIn("51001", str(ctx.exception))
        self.assertIn("Order does not exist", str(ctx.exception))

    def test_envelope_code_1_all_operations_failed_unpacks_row_scode(self):
        """当 OKX 顶层返回 code=1 ('All operations failed') 时，解包 data 内部真实的业务级 sCode 与 sMsg。"""
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(
            code="1",
            msg="All operations failed",
            data=[{"ordId": "", "clOrdId": "c1", "sCode": "51121", "sMsg": "Order quantity must be a multiple of the lot size."}]
        )
        with self.assertRaises(RuntimeError) as ctx:
            okx_rest.place_order("SUI-USDT-SWAP", "buy", "1324.5", px="0.77")
        self.assertIn("51121", str(ctx.exception))
        self.assertIn("Order quantity must be a multiple of the lot size", str(ctx.exception))

    def test_success_rows_only(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"sCode": "0", "ordId": "9"}, "junk"])
        rows = okx_rest.pending_orders()
        self.assertEqual(rows, [{"sCode": "0", "ordId": "9"}])

    # -- place_order 与 attach TP/SL ----------------------------------------

    def test_place_order_attach_tp_sl_leg_and_numeric_formatting(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"sCode": "0", "ordId": "ord-1"}])
        rows = okx_rest.place_order(
            "BTC-USDT-SWAP", "buy", 0.02, pos_side="long", td_mode="cross",
            ord_type="limit", px=27181.5, attach_tp=28000.0, attach_sl=26500.0,
        )
        self.assertEqual(rows[0]["ordId"], "ord-1")
        _, url, _, data = _captured(self.urlopen)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/order")
        body = json.loads(data.decode("utf-8"))
        self.assertEqual(body["sz"], "0.02")
        self.assertEqual(body["px"], "27181.5")
        self.assertEqual(body["posSide"], "long")
        # 第一百六十六刀（用户拍板 mark）：附着腿**显式**带触发价类型，
        # 不再依赖交易所默认值（旧断言只有 4 个字段）
        self.assertEqual(body["attachAlgoOrds"], [{
            "tpTriggerPx": "28000", "tpOrdPx": "-1", "tpTriggerPxType": "mark",
            "slTriggerPx": "26500", "slOrdPx": "-1", "slTriggerPxType": "mark",
        }])

    # -- US-001 复审修复回归：_fmt 无损十进制、纯记法 --------------------------

    def test_float_price_sizes_lossless_plain_decimal_in_request_body(self):
        """致命 bug 回归：`:g` 曾把 px=110000.5 静默截断成 "110000"、把
        1250000.0 渲染成 "1.25e+06"。HTTP 边界捕获真实 body 钉死新契约。"""
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"sCode": "0", "ordId": "ord-fmt"}])
        okx_rest.place_order(
            "BTC-USDT-SWAP", "buy", 1250000.0, ord_type="limit",
            px=110000.5, attach_tp=0.02, attach_sl=0.0000012,
        )
        _, _, _, data = _captured(self.urlopen)
        body_text = data.decode("utf-8")
        body = json.loads(body_text)
        self.assertEqual(body["sz"], "1250000")        # 不再是 "1.25e+06"
        self.assertEqual(body["px"], "110000.5")       # 不再是截断的 "110000"
        leg = body["attachAlgoOrds"][0]
        self.assertEqual(leg["tpTriggerPx"], "0.02")   # 常规小数语义不变
        self.assertEqual(leg["slTriggerPx"], "0.0000012")  # 极小值全位展开
        lowered = body_text.lower()
        self.assertNotIn("e+", lowered)
        self.assertNotIn("e-", lowered)

    def test_any_float_never_emits_scientific_notation_in_body_or_querystring(self):
        """通用不变式：任意 float 入参，POST 请求体与 GET 查询串都必须是纯十进制
        且可无损读回原值（float(emit) == 原值）。"""
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[])
        nasty_floats = (1e-07, 1250000.0, 0.0000012, 110000.5, 0.02, 1e+21,
                        3.0000000000000004e-05, 0.1 + 0.2)
        for px in nasty_floats:
            with self.subTest(px=px):
                # POST 体（amend_order.newPx）
                self.urlopen.reset_mock()
                okx_rest.amend_order("BTC-USDT-SWAP", "1", new_px=px)
                _, _, _, data = _captured(self.urlopen)
                body_text = data.decode("utf-8").lower()
                self.assertNotIn("e+", body_text)
                self.assertNotIn("e-", body_text)
                emitted = json.loads(data.decode("utf-8"))["newPx"]
                self.assertIsInstance(emitted, str)
                self.assertEqual(float(emitted), px)
                # GET 查询串（orders_history.begin）
                self.urlopen.reset_mock()
                okx_rest.orders_history(begin=px)
                _, url, _, _ = _captured(self.urlopen)
                query = url.split("?", 1)[1].lower()
                self.assertNotIn("e+", query)
                self.assertNotIn("e-", query)
                emitted_q = urllib.parse.parse_qs(url.split("?", 1)[1])["begin"][0]
                self.assertEqual(float(emitted_q), px)

    def test_int_and_decimal_format_as_plain_decimal_strings(self):
        """int/Decimal 行为合理：一律输出无损纯十进制字符串（Decimal 曾直接
        透传导致 JSON 序列化崩溃，如今归一为 plain 记法）。"""
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"algoId": "a-fmt", "sCode": "0"}])
        okx_rest.place_algo_oco("BTC-USDT-SWAP", "sell", 2, pos_side="long",
                                tp_trigger_px=Decimal("110000.50"), sl_trigger_px=1250000)
        _, _, _, data = _captured(self.urlopen)
        body = json.loads(data.decode("utf-8"))
        self.assertEqual(body["sz"], "2")
        self.assertEqual(body["tpTriggerPx"], "110000.5")
        self.assertEqual(body["slTriggerPx"], "1250000")
        self.assertEqual(body["reduceOnly"], True)   # bool 语义不变

    # -- algo 面 -------------------------------------------------------------

    def test_place_algo_oco_body_maps_cli_flags(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"algoId": "a-1"}])
        okx_rest.place_algo_oco("BTC-USDT-SWAP", "sell", 2, pos_side="long",
                                td_mode="cross", tp_trigger_px=29000, sl_trigger_px=26000)
        method, url, headers, data = _captured(self.urlopen)
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/order-algo")
        body = json.loads(data.decode("utf-8"))
        self.assertEqual(body["ordType"], "oco")
        self.assertEqual(body["reduceOnly"], True)
        self.assertEqual(body["cxlOnClosePos"], True)
        self.assertEqual(body["slOrdPx"], "-1")
        self.assertEqual(body["tpOrdPx"], "-1")
        # 第一百六十六刀：云端棘轮腿与入场腿同口径（mark）
        self.assertEqual(body["tpTriggerPxType"], "mark")
        self.assertEqual(body["slTriggerPxType"], "mark")
        self.assertEqual(headers["content-type"], "application/json")

    def test_cancel_algo_orders_uses_array_body(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"algoId": "a-1", "sCode": "0"}])
        okx_rest.cancel_algo_orders(["a-1"], inst_id="BTC-USDT-SWAP")
        _, url, _, data = _captured(self.urlopen)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/cancel-algos")
        self.assertEqual(json.loads(data.decode("utf-8")), [{"algoId": "a-1", "instId": "BTC-USDT-SWAP"}])
        self.urlopen.reset_mock()
        self.urlopen.return_value = _response(data=[])
        with self.assertRaises(ValueError):
            okx_rest.cancel_algo_orders([])
        self.urlopen.assert_not_called()

    def test_amend_algo_sl_defaults_market_px(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"algoId": "a-9", "sCode": "0"}])
        okx_rest.amend_algo_sl("a-9", 26750.5, inst_id="BTC-USDT-SWAP")
        _, url, _, data = _captured(self.urlopen)
        self.assertEqual(url, "https://www.okx.com/api/v5/trade/amend-algos")
        rows = json.loads(data.decode("utf-8"))
        self.assertIsInstance(rows, dict)
        self.assertEqual(rows["instId"], "BTC-USDT-SWAP")
        self.assertEqual(rows["algoId"], "a-9")
        self.assertEqual(rows["newSlOrdPx"], "-1")
        self.assertEqual(rows["newSlTriggerPx"], "26750.5")

    def test_pending_algo_orders_filters_locally_by_inst(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[
            {"algoId": "a-1", "instId": "BTC-USDT-SWAP"},
            {"algoId": "a-2", "instId": "ETH-USDT-SWAP"},
        ])
        rows = okx_rest.pending_algo_orders("BTC-USDT-SWAP")
        self.assertEqual([row["algoId"] for row in rows], ["a-1"])
        _, url, _, _ = _captured(self.urlopen)
        self.assertTrue(url.startswith("https://www.okx.com/api/v5/trade/orders-algo-pending?"))

    # -- 端点映射（其余方法逐一钉住路径） ------------------------------------

    def test_endpoint_paths_and_query_defaults(self):
        freeze_environment(DEMO_ENV)
        cases = {
            "amend_order": (lambda: okx_rest.amend_order("BTC-USDT-SWAP", "5", new_px=100), "/api/v5/trade/amend-order"),
            "close_position": (lambda: okx_rest.close_position("BTC-USDT-SWAP", "long"), "/api/v5/trade/close-position"),
            "orders_history": (lambda: okx_rest.orders_history(limit=100), "/api/v5/trade/orders-history"),
            "fills": (lambda: okx_rest.fills(), "/api/v5/trade/fills"),
            "balances": (lambda: okx_rest.balances(), "/api/v5/account/balance"),
            "bills": (lambda: okx_rest.bills(limit=100), "/api/v5/account/bills"),
            "positions_history": (lambda: okx_rest.positions_history(limit=100), "/api/v5/account/positions-history"),
        }
        for name, (call, expected_url_part) in cases.items():
            with self.subTest(name=name):
                self.urlopen.reset_mock()
                self.urlopen.return_value = _response(data=[])
                call()
                _, url, _, data = _captured(self.urlopen)
                self.assertIn(expected_url_part, url.split("?")[0])
                if expected_url_part.endswith(("orders-history", "fills", "balance", "bills", "positions-history")):
                    self.assertIsNone(data, "queries must be GET")
                else:
                    self.assertIsNotNone(data, "mutations must POST a body")

    def test_positions_default_inst_type_swap_and_position_filters_zero(self):
        freeze_environment(DEMO_ENV)
        self.urlopen.return_value = _response(data=[{"instId": "BTC-USDT-SWAP", "pos": "0"}, {"instId": "ETH-USDT-SWAP", "pos": "-2"}])
        row = okx_rest.position("ETH-USDT-SWAP")
        self.assertEqual(row["pos"], "-2")
        _, url, _, _ = _captured(self.urlopen)
        self.assertIn("instType=SWAP", url)
        self.assertIn("instId=ETH-USDT-SWAP", url)

    # -- 溯源 tripwire：CLI 永不复活 ----------------------------------------

    def test_module_never_touches_cli_or_subprocess(self):
        source = inspect.getsource(okx_rest)
        for banned in ("subprocess", "okx --", ("replace_" + "cli_" + "prefix"), ("cli_" + "prefix"), "os.environ"):
            self.assertNotIn(banned, source, f"okx_rest must stay CLI-free: found {banned!r}")
        # 凭证唯一来源 = runtime 的 current_environment（含冻结优先）
        self.assertIn("current_environment", source)


class CurrentEnvironmentContractTests(unittest.TestCase):
    """okx_runtime.current_environment：冻结周期 env 优先，解冻回落实时选择。"""

    def tearDown(self):
        unfreeze_environment()

    def test_frozen_env_wins_then_live_selection(self):
        from scripts.okx_runtime import current_environment
        self.assertFalse(current_environment(UNCONFIGURED_ENV).configured)
        frozen = freeze_environment(DEMO_ENV)
        again = current_environment(UNCONFIGURED_ENV)  # 即使传入未配置 values，也必须命中冻结
        self.assertEqual(again.identity, frozen.identity)
        self.assertTrue(again.configured)
        self.assertTrue(again.simulated)
        unfreeze_environment()
        live = current_environment(LIVE_ENV)
        self.assertEqual(live.mode, "live")
        self.assertFalse(live.simulated)


if __name__ == "__main__":
    unittest.main()


class BrokerTagCoverageTest(unittest.TestCase):
    """**每一个会产单的端点**都必须带经纪商 tag（2026-09 补）。

    起因（实测）：`close_position` 此前不带 tag，而它是**每一个 OKX 市价全平**的
    唯一出口（`scripts/trader/venue_query.py::close_position_confirmed`，日志实测
    34 次整仓退出）—— 那些成交就不计经纪商归属。原先 tag 逻辑在两处各写一遍，
    新端点天然漏掉；现收敛到 `_with_broker_tag` 单一出口，并由本门兜住。

    为什么只盯这三个：`/trade/order`、`/trade/order-algo`、`/trade/close-position`
    是**会产生成交**的端点；撤单/改单/设杠杆不产生新成交，OKX 也不接受它们的 tag。
    """

    #: 会产生成交的端点（新增此类端点时必须一并加进来）
    ORDER_CREATING = ("/api/v5/trade/order", "/api/v5/trade/order-algo",
                      "/api/v5/trade/close-position")

    def test_tag_logic_has_a_single_exit(self):
        """tag 的挂载只能有**一处**实现（两处各写一遍正是漏掉 close_position 的成因）。"""
        import inspect
        src = inspect.getsource(okx_rest)
        self.assertEqual(src.count('params["tag"] = broker_tag'), 1,
                         "tag 挂载出现多份 ⇒ 新增端点必然会漏掉其中一个")

    def test_every_order_creating_endpoint_applies_the_tag(self):
        """AST 判据：凡调用产单端点的函数，其函数体必须经过 `_with_broker_tag`。

        行为用例只能覆盖**今天已知**的三个端点；这条从源码推导，将来有人新增
        `POST /api/v5/trade/xxx` 却忘了挂 tag，本门直接红。
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(okx_rest))
        checked = []
        for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
            paths = []
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "request"):
                    continue
                # 只认字面量端点，不 eval 任意表达式（也用不着 try/except —— 用例里
                # 吞异常会被 `test_tests_cannot_pass_vacuously` 判为"静默通过"）。
                if len(call.args) >= 2 and ast.literal_eval(call.args[0]) == "POST":
                    paths.append(ast.literal_eval(call.args[1]))
            hit = [p for p in paths if p in self.ORDER_CREATING]
            if not hit:
                continue
            checked.append(fn.name)
            self.assertIn("_with_broker_tag", ast.unparse(fn),
                          f"{fn.name} 调用产单端点 {hit} 却没挂经纪商 tag")
        self.assertEqual(sorted(checked), ["close_position", "place_algo_oco", "place_order"],
                         "产单端点的实现函数变了 —— 新端点必须一并纳入本门")

    def test_wire_params_carry_the_tag_on_all_three(self):
        """行为判据：拦截实际请求体，三个端点都必须出现 tag。"""
        captured = []

        def _fake(method, path, params=None, *, env=None, **kw):
            captured.append((path, dict(params or {})))
            return []

        with patch.object(okx_rest, "request", _fake):
            okx_rest.place_order("BTC-USDT-SWAP", "buy", 1, ord_type="limit", px="79000")
            okx_rest.place_algo_oco("BTC-USDT-SWAP", "buy", 1, pos_side="long",
                                    tp_trigger_px="80000", sl_trigger_px="78000")
            okx_rest.close_position("BTC-USDT-SWAP", "long")
        self.assertEqual([p for p, _ in captured],
                         ["/api/v5/trade/order", "/api/v5/trade/order-algo",
                          "/api/v5/trade/close-position"])
        for path, params in captured:
            self.assertEqual(params.get("tag"), okx_rest.DEFAULT_OKX_BROKER_TAG,
                             f"{path} 没有带上经纪商 tag")

    def test_explicit_and_env_override_precedence(self):
        """显式实参 > 环境变量 > 硬编码默认（与另外两个端点保持同一口径）。"""
        captured = []

        def _fake(method, path, params=None, *, env=None, **kw):
            captured.append(dict(params or {}))
            return []

        with patch.object(okx_rest, "request", _fake):
            okx_rest.close_position("BTC-USDT-SWAP", "long", tag="EXPLICIT")
            self.assertEqual(captured[-1]["tag"], "EXPLICIT")
            with patch.dict("os.environ", {"OKX_BROKER_TAG": "FROM-ENV"}):
                okx_rest.close_position("BTC-USDT-SWAP", "long")
            self.assertEqual(captured[-1]["tag"], "FROM-ENV")


class NoOrderEndpointBypassTest(unittest.TestCase):
    """除 `scripts/okx_rest.py` 外，**任何生产模块**都不得自己拼产单端点的请求体。

    起因（实测漏网）：`r20_backend/okx_trade_service.py::fast_close_confirmed`
    —— 后台「应急一键平仓」—— 自己拼了 `POST /api/v5/trade/close-position`，
    绕过了 `_with_broker_tag` ⇒ 那批成交不计经纪商归属。

    OKX 文档「经纪商指引/经纪商常用接口」把产单端点列得很明确：**下单 / 批量下单 /
    市价全平 / 策略委托下单** —— 请求参数带 `tag` 的都"务必录入专属 Broker code"。
    故这四个端点的字面量只允许出现在统一客户端里；旁路一出现本门就红。
    """

    #: 会产单的端点（带引号比较，避免 `/trade/order` 前缀命中 `/trade/order-algo`）
    ORDER_ENDPOINT_LITERALS = ('"/api/v5/trade/order"', "'/api/v5/trade/order'",
                               '"/api/v5/trade/batch-orders"', "'/api/v5/trade/batch-orders'",
                               '"/api/v5/trade/close-position"', "'/api/v5/trade/close-position'",
                               '"/api/v5/trade/order-algo"', "'/api/v5/trade/order-algo'")

    ALLOWED = {"scripts/okx_rest.py"}

    def test_only_the_shared_client_talks_to_order_endpoints(self):
        root = Path(okx_rest.__file__).resolve().parents[1]
        offenders = []
        for base in ("scripts", "r20_backend", "r20_gateway"):
            for path in (root / base).rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                rel = path.relative_to(root).as_posix()
                if rel in self.ALLOWED:
                    continue
                text = path.read_text(encoding="utf-8")
                for literal in self.ORDER_ENDPOINT_LITERALS:
                    if literal in text:
                        offenders.append(f"{rel} :: {literal.strip(chr(34))}")
        self.assertEqual(offenders, [],
                         "这些生产模块绕过了统一客户端自拼产单请求（会漏掉经纪商 tag）：\n  "
                         + "\n  ".join(offenders))
