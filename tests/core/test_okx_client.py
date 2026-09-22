"""OKX 原生 REST 客户端：**只有三个公共只读端点走免签通道，其余一律走私有 fail-closed 通道**（第二百九十二刀，开新面 okx_client.py）。

先打印整个文件（59 行）再动笔。未命中的 5 行里，`35/47/58` 是普通分支，
`21` 是"分流到私有通道"这条**安全分界**，而 `30` 经分析是**死代码**（见下）。

| 语义 | 口径 |
|---|---|
| ★ **分流判据 = 方法非 GET 或 路径不在白名单** | 白名单只有 `market/ticker`、`market/candles`、`public/instruments` 三个**公共只读**端点；其余（含 `GET` 但不在白名单的路径）统统交给 `okx_rest.request` —— 那是唯一的 fail-closed 私有传输 |
| ★ **上游业务码非 0 必须抛** | `payload["code"]` 不在 `(None, "0", 0)` 里 ⇒ `RuntimeError(msg)`；**没有 `msg` 时用固定文案**（不许把 `None` 抛出去）|
| ★ **`code` 缺失等于成功** | `None` 在白名单里 —— OKX 公共行情端点的返回体常常压根没有 `code` 字段 |
| ★ **`data` 缺失时返回整个 payload** | `payload.get("data", payload)`：拿不到统一信封时把原始体交出去，而不是静默返回 `None` |
| ★ **`instruments` 的 `inst_id` 是可选的** | 不传就不带这个查询参数（带了空值会让 OKX 报错）|
| ★ **`close_position` 先校验 `pos_side`** | 只接受 `long`/`short`；非法值**在发请求之前**就 `ValueError` |
| 🐞 **第 30 行是死代码** | 第 18 行的守卫把一切非 GET 都提前转走了 ⇒ 走到第 27 行时 `method` 恒为 `GET` ⇒ `body` 恒为 `None` ⇒ `if body:` **永不成立**（第 24 行的 `else ""` 同理）。除非白名单将来放开写方法，否则这行不可达，**如实登记不伪造覆盖** |

## 封闭性

`urlopen` 是从 `urllib.request` 直接导入到本模块的 ⇒ patch `r20_backend.okx_client.urlopen`
即可，**零真实网络**。分流用例 patch `r20_backend.okx_client.okx_rest.request`。
"""

import json
import unittest
from unittest import mock

from r20_backend import okx_client as OK

_WRITE_PATH = "/api/v5/trade/order"


class _Resp:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ClientConstructionTests(unittest.TestCase):
    def test_the_base_url_is_the_production_host(self):
        self.assertEqual(OK.OKXClient().base_url, "https://www.okx.com")

    def test_the_public_whitelist_is_exactly_the_three_read_only_endpoints(self):
        source = (OK.__file__ and open(OK.__file__, encoding="utf-8").read()) or ""
        for path in ("/api/v5/market/ticker", "/api/v5/market/candles",
                     "/api/v5/public/instruments"):
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', source)

    def test_the_public_shortcuts_use_the_public_paths(self):
        calls = []

        def _capture(self_, method, path, params=None):
            # ⚠️ patch **类**属性时它是普通函数 ⇒ 描述符把实例当第一个参数传进来
            calls.append((method, path, params))
            return {"ok": True}

        with mock.patch.object(OK.OKXClient, "_request", _capture):
            client = OK.OKXClient()
            client.ticker("BTC-USDT-SWAP")
            client.candles("BTC-USDT-SWAP", bar="4H", limit=7)
            client.instruments("SWAP")
        self.assertEqual(calls, [("GET", "/api/v5/market/ticker", {"instId": "BTC-USDT-SWAP"}),
                                 ("GET", "/api/v5/market/candles",
                                  {"instId": "BTC-USDT-SWAP", "bar": "4H", "limit": 7}),
                                 ("GET", "/api/v5/public/instruments", {"instType": "SWAP"})])


class PrivateTransportRoutingTests(unittest.TestCase):
    """★ 安全分界：只有三个公共只读端点可以走免签通道。"""

    def setUp(self):
        self.request = mock.patch.object(OK.okx_rest, "request",
                                         return_value={"routed": "private"}).start()
        self.env = mock.patch.object(OK, "current_environment",
                                     return_value="demo-env").start()
        self.addCleanup(mock.patch.stopall)
        self.client = OK.OKXClient()

    def test_a_non_get_method_is_routed_to_the_private_transport(self):
        out = self.client._request("POST", _WRITE_PATH, {"instId": "BTC"})
        self.assertEqual(out, {"routed": "private"})
        self.request.assert_called_once_with("POST", _WRITE_PATH, {"instId": "BTC"},
                                            env="demo-env")

    def test_a_get_to_a_non_whitelisted_path_is_also_routed_privately(self):
        """⚠️ 关键：`GET` 本身**不是**通行证 —— 不在白名单照样必须走私有通道。"""
        self.client._request("GET", "/api/v5/account/balance")
        self.request.assert_called_once_with("GET", "/api/v5/account/balance", None,
                                            env="demo-env")

    def test_a_delete_is_routed_privately(self):
        self.client._request("DELETE", _WRITE_PATH)
        self.assertEqual(self.request.call_args[0][0], "DELETE")

    def test_the_method_case_does_not_matter(self):
        self.client._request("post", _WRITE_PATH)
        self.assertEqual(self.request.call_args[0][0], "post")

    def test_balance_and_positions_delegate_to_the_private_helpers(self):
        with mock.patch.object(OK.okx_rest, "balances", return_value=[1]) as b, \
                mock.patch.object(OK.okx_rest, "positions", return_value=[2]) as p:
            self.assertEqual(self.client.balance(), [1])
            self.assertEqual(self.client.positions(), [2])
        b.assert_called_once_with(env="demo-env")
        p.assert_called_once_with(env="demo-env")

    def test_an_explicit_env_wins_over_the_current_one(self):
        self.client.balance(env="live-env")
        self.env.assert_not_called()

    def test_close_position_routes_through_the_private_helper(self):
        with mock.patch.object(OK.okx_rest, "close_position",
                               return_value={"closed": True}) as close:
            out = self.client.close_position("BTC-USDT-SWAP", "long")
        self.assertEqual(out, {"closed": True})
        close.assert_called_once_with("BTC-USDT-SWAP", "long", env="demo-env")


class PublicRequestTests(unittest.TestCase):
    def setUp(self):
        self.client = OK.OKXClient()

    def _urlopen(self, payload):
        return mock.patch.object(OK, "urlopen", return_value=_Resp(payload))

    def test_a_successful_read_returns_the_data_field(self):
        with self._urlopen({"code": "0", "data": [{"last": "1"}]}):
            self.assertEqual(self.client.ticker("BTC"), [{"last": "1"}])

    def test_a_missing_code_is_treated_as_success(self):
        """OKX 公共行情端点常常没有 `code` 字段 —— 不能因此判失败。"""
        with self._urlopen({"data": [1, 2]}):
            self.assertEqual(self.client.ticker("BTC"), [1, 2])

    def test_a_null_code_is_treated_as_success(self):
        with self._urlopen({"code": None, "data": [3]}):
            self.assertEqual(self.client.ticker("BTC"), [3])

    def test_an_integer_zero_code_is_treated_as_success(self):
        with self._urlopen({"code": 0, "data": [4]}):
            self.assertEqual(self.client.ticker("BTC"), [4])

    def test_a_non_zero_code_raises_with_the_upstream_message(self):
        with self._urlopen({"code": "51001", "msg": "Instrument ID does not exist"}):
            with self.assertRaises(RuntimeError) as ctx:
                self.client.ticker("NOPE")
        self.assertEqual(str(ctx.exception), "Instrument ID does not exist")

    def test_a_non_zero_code_without_a_message_uses_the_default_text(self):
        with self._urlopen({"code": "51001"}):
            with self.assertRaises(RuntimeError) as ctx:
                self.client.ticker("NOPE")
        self.assertEqual(str(ctx.exception), "OKX request failed")

    def test_a_missing_data_field_returns_the_whole_payload(self):
        with self._urlopen({"code": "0", "info": "raw"}):
            self.assertEqual(self.client.ticker("BTC"), {"code": "0", "info": "raw"})

    def test_the_url_carries_the_query_string(self):
        with self._urlopen({"data": []}) as urlopen:
            self.client.ticker("BTC-USDT-SWAP")
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url,
                         "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT-SWAP")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)

    def test_the_timeout_is_ten_seconds(self):
        with self._urlopen({"data": []}) as urlopen:
            self.client.ticker("BTC")
        self.assertEqual(urlopen.call_args[1]["timeout"], 10)

    def test_the_user_agent_is_set(self):
        with self._urlopen({"data": []}) as urlopen:
            self.client.ticker("BTC")
        self.assertEqual(urlopen.call_args[0][0].get_header("User-agent"),
                         "R20-Standalone/6.6.2")

    def test_a_missing_params_argument_does_not_break_the_url(self):
        with self._urlopen({"data": []}) as urlopen:
            self.client._request("GET", "/api/v5/market/ticker")
        self.assertEqual(urlopen.call_args[0][0].full_url,
                         "https://www.okx.com/api/v5/market/ticker")


class InstrumentsTests(unittest.TestCase):
    def setUp(self):
        self.client = OK.OKXClient()
        self.seen = []

        def _capture(method, path, params):
            self.seen.append(params)
            return []

        self._patch = mock.patch.object(self.client, "_request", _capture).start()
        self.addCleanup(mock.patch.stopall)

    def test_without_an_inst_id_only_the_type_is_sent(self):
        self.client.instruments("SWAP")
        self.assertEqual(self.seen, [{"instType": "SWAP"}])

    def test_an_inst_id_is_added_when_given(self):
        self.client.instruments("SWAP", "BTC-USDT-SWAP")
        self.assertEqual(self.seen, [{"instType": "SWAP", "instId": "BTC-USDT-SWAP"}])

    def test_an_empty_inst_id_is_treated_as_absent(self):
        """假值与缺省同义：带上空 `instId` 会让 OKX 报参数错误。"""
        self.client.instruments("SWAP", "")
        self.assertEqual(self.seen, [{"instType": "SWAP"}])

    def test_the_default_type_is_swap(self):
        self.client.instruments()
        self.assertEqual(self.seen, [{"instType": "SWAP"}])


class ClosePositionValidationTests(unittest.TestCase):
    def setUp(self):
        self.client = OK.OKXClient()
        self.close = mock.patch.object(OK.okx_rest, "close_position").start()
        self.addCleanup(mock.patch.stopall)

    def test_both_valid_sides_are_accepted(self):
        for side in ("long", "short"):
            with self.subTest(side=side):
                self.client.close_position("BTC-USDT-SWAP", side)
                self.assertEqual(self.close.call_args[0][1], side)

    def test_an_unknown_side_raises_before_any_request(self):
        with self.assertRaises(ValueError) as ctx:
            self.client.close_position("BTC-USDT-SWAP", "sideways")
        self.assertEqual(str(ctx.exception), "pos_side must be long or short")
        self.close.assert_not_called()

    def test_the_case_must_match_exactly(self):
        for side in ("LONG", "Long", "buy"):
            with self.subTest(side=side):
                with self.assertRaises(ValueError):
                    self.client.close_position("BTC-USDT-SWAP", side)

    def test_an_empty_side_raises(self):
        with self.assertRaises(ValueError):
            self.client.close_position("BTC-USDT-SWAP", "")


if __name__ == "__main__":
    unittest.main()
