"""HTTP-boundary regressions derived from public OKX V5 request tables.

Sources (official excerpts also recorded in the absolute mission audit report):
https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-cancel-algo-order
https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-amend-algo-order
https://www.okx.com/docs-v5/en/#order-book-trading-algo-trading-post-place-algo-order
https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-place-order
https://www.okx.com/docs-v5/en/#order-book-trading-trade-post-amend-order
https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-bills-details-last-7-days

No exchange account, credentials, or trading network is accessed by these tests.
"""
import base64
import hashlib
import hmac
import json
import unittest
from decimal import Decimal, localcontext
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

from scripts import okx_rest as r
from scripts.okx_runtime import OKXEnvironment

INST = "BTC-USDT-SWAP"
ENV = OKXEnvironment("demo", "FAKE_AK", "FAKE_SK", "FAKE_PP")
EMPTY = OKXEnvironment("demo", "", "", "")


class OfficialContractTests(unittest.TestCase):
    def setUp(self):
        # Pin BOTH credential resolution and network boundary; never read .env.
        self.network = self.enterContext(patch.object(r, "urlopen"))
        self.enterContext(patch.object(r, "current_environment", return_value=ENV))
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        self.response.read.return_value = b'{"code":"0","data":[]}'
        self.network.return_value = self.response

    def captured(self, path):
        req = self.network.call_args.args[0]
        self.assertEqual(urlsplit(req.full_url).path, path)
        headers = dict((k.lower(), v) for k, v in req.header_items())
        body = req.data.decode() if req.data else ""
        prehash = headers["ok-access-timestamp"] + req.method + req.full_url.replace(ENV.base_url, "") + body
        sign = base64.b64encode(hmac.new(ENV.secret_key.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(headers["ok-access-sign"], sign)
        self.assertEqual(headers["x-simulated-trading"], "1")
        return json.loads(body) if body else parse_qs(urlsplit(req.full_url).query)

    def test_cancel_official_array_instid_and_ten_order_limit(self):
        # Official: maximum 10; each row requires instId and algoId/algoClOrdId.
        for count in (1, 10):
            with self.subTest(count=count):
                self.network.reset_mock()
                r.cancel_algo_orders([str(i) for i in range(count)], inst_id=INST)
                self.assertEqual(self.captured("/api/v5/trade/cancel-algos"),
                                 [{"instId": INST, "algoId": str(i)} for i in range(count)])
                self.network.assert_called_once()
        r.cancel_algo_orders([{"instId": INST, "algoClOrdId": "client1"},
                              {"instId": "ETH-USDT-SWAP", "algoId": "2"}])
        self.assertEqual(self.captured("/api/v5/trade/cancel-algos")[0],
                         {"instId": INST, "algoClOrdId": "client1"})
        for ids, kwargs in (([], {}), (["1"], {}), (["1"] * 11, {"inst_id": INST}),
                            ([{"instType": "SWAP", "algoId": "1"}], {}),
                            ([{"instId": INST}], {}), ([""], {"inst_id": INST})):
            self.network.reset_mock()
            with self.assertRaises(ValueError):
                r.cancel_algo_orders(ids, **kwargs)
            self.network.assert_not_called()

    def test_amend_algo_single_object_with_instid_and_boolean(self):
        r.amend_algo_sl("11", Decimal("110000.500"), inst_id=INST,
                        req_id="amend1", cxl_on_fail=False)
        body = self.captured("/api/v5/trade/amend-algos")
        self.assertEqual(body, {"instId": INST, "algoId": "11", "newSlTriggerPx": "110000.5",
                                "newSlOrdPx": "-1", "reqId": "amend1", "cxlOnFail": False})
        self.assertIs(type(body["cxlOnFail"]), bool)
        self.network.reset_mock()
        with self.assertRaises(ValueError):
            r.amend_algo_sl("11", 100)
        self.network.assert_not_called()
        # Official: 0 deletes a leg, and must not be dropped as an empty scalar.
        r.amend_algo_sl("11", 0, inst_id=INST, new_sl_ord_px=0)
        self.assertEqual(self.captured("/api/v5/trade/amend-algos")["newSlTriggerPx"], "0")

    def test_regular_attach_and_amend_leg_ids_are_not_standalone_algo_ids(self):
        r.place_order(INST, "buy", 1, px="100", attach_tp=120, attach_sl=90)
        body = self.captured("/api/v5/trade/order")
        self.assertEqual(body["attachAlgoOrds"], [{"tpTriggerPx": "120", "tpOrdPx": "-1",
                                                   "slTriggerPx": "90", "slOrdPx": "-1"}])
        self.assertNotIn("tdMode", body["attachAlgoOrds"][0])
        leg = {"attachAlgoId": "attached1", "newSlTriggerPx": "91", "newSlOrdPx": "-1",
               "newSlTriggerPxType": "mark"}
        r.amend_order(INST, "parent1", req_id="r1", attach_algo_ords=[leg], cxl_on_fail=True)
        body = self.captured("/api/v5/trade/amend-order")
        self.assertEqual(body["reqId"], "r1")
        self.assertNotIn("reqTxId", body)
        self.assertEqual(body["attachAlgoOrds"], [leg])
        self.assertIs(body["cxlOnFail"], True)
        r.amend_order(INST, "parent1", req_tx_id="legacy-python-name", new_px=100)
        body = self.captured("/api/v5/trade/amend-order")
        self.assertEqual(body["reqId"], "legacy-python-name")
        self.assertNotIn("reqTxId", body)
        # Official amend supports removal using zero in either trigger or order price.
        r.amend_order(INST, "parent1", attach_algo_ords=[{"attachAlgoId": "attached1", "newSlOrdPx": 0}])
        self.assertEqual(self.captured("/api/v5/trade/amend-order")["attachAlgoOrds"][0]["newSlOrdPx"], "0")

    def test_json_boolean_vs_query_boolean(self):
        r.place_algo_oco(INST, "sell", 2, pos_side="long", tp_trigger_px=110, sl_trigger_px=90)
        body = self.captured("/api/v5/trade/order-algo")
        self.assertIs(body["reduceOnly"], True)
        self.assertIs(body["cxlOnClosePos"], True)
        r.close_position(INST, auto_cxl=False)
        self.assertIs(self.captured("/api/v5/trade/close-position")["autoCxl"], False)
        r.request("GET", "/api/v5/account/positions", {"testFlag": False})
        self.assertEqual(self.captured("/api/v5/account/positions")["testFlag"], ["false"])
        self.network.reset_mock()
        with self.assertRaises(ValueError):
            r.place_order(INST, "buy", 1, px=100, reduce_only="true")
        self.network.assert_not_called()

    def test_decimal_no_ambient_context_rounding_and_nonfinite_rejected(self):
        value = Decimal("123456789012345678901234567890.12345678901234567890")
        with localcontext() as ctx:
            ctx.prec = 6
            r.amend_order(INST, "1", new_px=value)
        self.assertEqual(self.captured("/api/v5/trade/amend-order")["newPx"],
                         "123456789012345678901234567890.1234567890123456789")
        for value in (float("nan"), float("inf"), -float("inf"), Decimal("NaN"),
                      Decimal("sNaN"), "Infinity", "-Infinity", "NaN", True, 0, -3):
            with self.subTest(value=str(value)):
                self.network.reset_mock()
                with self.assertRaises(ValueError):
                    r.place_order(INST, "buy", 1, px=value)
                self.network.assert_not_called()

    def test_bad_attachments_and_incompatible_oco_flags_fail_locally(self):
        for leg in ({"tdMode": "cross", "slTriggerPx": 90, "slOrdPx": -1},
                    {"slTriggerPx": 90}, {"slOrdPx": "NaN"}):
            self.network.reset_mock()
            with self.assertRaises(ValueError):
                r.place_order(INST, "buy", 1, px=100, attach_algo_ords=[leg])
            self.network.assert_not_called()
        with self.assertRaises(ValueError):
            r.place_algo_oco(INST, "sell", 1, pos_side="long", tp_trigger_px=110,
                             sl_trigger_px=90, reduce_only=False, cxl_on_close_pos=True)

    def test_explicit_immutable_env_remains_fixed_after_global_switch(self):
        # Caller retains one frozen dataclass snapshot throughout a close sequence.
        other = OKXEnvironment("live", "OTHER_AK", "OTHER_SK", "OTHER_PP")
        with patch.object(r, "current_environment", return_value=other) as current:
            r.pending_algo_orders(INST, env=ENV)
            r.cancel_algo_orders(["1"], inst_id=INST, env=ENV)
            r.amend_algo_sl("1", 90, inst_id=INST, env=ENV)
            r.close_position(INST, env=ENV)
            current.assert_not_called()
        for call in self.network.call_args_list:
            headers = {k.lower(): v for k, v in call.args[0].header_items()}
            self.assertEqual(headers["ok-access-key"], "FAKE_AK")
            self.assertEqual(headers["x-simulated-trading"], "1")
        self.network.reset_mock()
        for call in (lambda: r.cancel_algo_orders(["1"], inst_id=INST, env=EMPTY),
                     lambda: r.amend_algo_sl("1", 90, inst_id=INST, env=EMPTY),
                     lambda: r.place_order(INST, "buy", 1, px=100, env=EMPTY)):
            with self.assertRaises(r.OKXNotConfigured):
                call()
        self.network.assert_not_called()

    def test_billid_and_algoid_cursors_and_limits(self):
        r.bills(after="bill-old", before="bill-new", begin=123, end=456, limit=100)
        query = self.captured("/api/v5/account/bills")
        self.assertEqual(query["after"], ["bill-old"])
        self.assertEqual(query["before"], ["bill-new"])
        self.assertEqual(query["begin"], ["123"])
        r.pending_algo_orders(INST, ord_type="conditional,oco", after="algo-old", before="algo-new")
        query = self.captured("/api/v5/trade/orders-algo-pending")
        self.assertEqual(query["after"], ["algo-old"])
        self.assertEqual(query["before"], ["algo-new"])
        self.assertEqual(query["ordType"], ["conditional,oco"])
        for fn in (r.bills, r.pending_algo_orders):
            for limit in (0, 101):
                self.network.reset_mock()
                with self.assertRaises(ValueError):
                    fn(limit=limit)
                self.network.assert_not_called()

    def test_malformed_json_and_business_errors(self):
        for data in (b'invalid-json', b'[]', b'null',
                     b'{"code":"50011","msg":"rate limit"}',
                     b'{"code":"0","data":[{"sCode":"51000","sMsg":"bad"}]}'):
            self.response.read.return_value = data
            with self.assertRaises(RuntimeError):
                r.balances(env=ENV)
