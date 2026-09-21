"""OKX 公共取数的多主机故障转移与标量解析（第一百八十一刀）。

| 语义 | 纪律 |
|---|---|
| `_get` | 依次试 `OKX_HOSTS`：**业务错（`code != "0"`）也要换下一个主机**（不只是网络异常）；全失败 ⇒ `None`（不抛、不伪装数据）|
| 标量解析 | `fundingRate` 取 `rows[0]`、大户比取 `rows[-1][1]`；空/缺字段/非数值 ⇒ `None` |
"""

import json
import unittest
from unittest.mock import patch

from r20_backend.exchanges.okx import OKX_HOSTS, OKXPublicAdapter


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = OKXPublicAdapter.__new__(OKXPublicAdapter)
        self.urls = []

    def _open(self, behavior):
        """`behavior(host)` 返回 dict（回包）或异常实例。"""
        state = {"n": 0}

        def _f(req, timeout=None):
            self.urls.append(req.full_url)
            state["n"] += 1
            out = behavior(req.full_url) if callable(behavior) else behavior
            if isinstance(out, Exception):
                raise out
            return _Resp(out)
        p = patch("r20_backend.exchanges.okx.urlopen", side_effect=_f)
        p.start()
        self.addCleanup(p.stop)
        return self.ad


class GetFailoverTest(_Base):
    def test_first_host_success_returns_data_without_trying_others(self):
        self._open({"code": "0", "data": [{"x": 1}]})
        self.assertEqual(self.ad._get("/api/v5/public/time", {"a": "1"}), [{"x": 1}])
        self.assertEqual(len(self.urls), 1, "成功即停，不做多余请求")
        self.assertIn("?a=1", self.urls[0], "params 编码进 query")

    def test_business_error_on_one_host_falls_through_to_the_next(self):
        """★ `code != 0`（业务错）**也要换主机** —— 多主机存在的意义就是互为备份。"""
        host_list = list(OKX_HOSTS)

        def _behavior(url):
            return {"code": "0", "data": ["ok"]} if url.startswith(host_list[-1]) \
                else {"code": "51001", "msg": "bad"}
        self._open(_behavior)
        self.assertEqual(self.ad._get("/api/v5/x"), ["ok"])
        self.assertEqual(len(self.urls), len(host_list), "逐个试到最后一个")

    def test_all_hosts_failing_returns_none(self):
        """全部主机失败 ⇒ `None`（**不抛异常、不伪装数据**）。"""
        self._open(RuntimeError("connection refused"))
        self.assertIsNone(self.ad._get("/api/v5/x", {"q": "1"}))
        self.assertEqual(len(self.urls), len(list(OKX_HOSTS)), "每个主机都试过")

    def test_missing_code_field_is_treated_as_success(self):
        self._open({"data": ["no-code"]})
        self.assertEqual(self.ad._get("/api/v5/x"), ["no-code"],
                         "回包无 code 字段 ⇒ 按成功处理（默认 0）")


class ScalarParseTest(_Base):
    def test_funding_rate_paths(self):
        for payload, want in (([{"fundingRate": "0.0001"}], 0.0001),
                              ([], None), ({}, None), ([{"fundingRate": "abc"}], None)):
            with self.subTest(payload=payload):
                self.ad._get = lambda path, params=None, **k: payload
                self.assertEqual(self.ad.fetch_funding_rate("BTC"), want)

    def test_non_dict_rows_crash_because_there_is_no_type_guard(self):
        """⚠️ **实测边界（列待议）**：`fetch_funding_rate` 只判 `if rows:` 就取 `rows[0].get(...)`，
        **没有**元素类型守卫 ⇒ 回包是**非空字符串**（如网关 HTML）时抛 `AttributeError`，
        而不是像本仓别处那样「坏数据 ⇒ `None`」。

        与同批记录的 Gate `positions()` 缺守卫是**同一形态**（单条坏负载毁掉整次读取）。
        改它属失败语义变更（吞掉 vs 上抛）⇒ 只钉现状、列为待议。
        """
        self.ad._get = lambda path, params=None, **k: "<html>gateway</html>"
        with self.assertRaises(AttributeError):
            self.ad.fetch_funding_rate("BTC")

    def test_top_trader_ratio_uses_the_last_row_second_column(self):
        for payload, want in (([["t1", "1.2"], ["t2", "1.9"]], 1.9),
                              ([], None), ([[1]], None), ("nope", None)):
            with self.subTest(payload=payload):
                self.ad._get = lambda path, params=None, **k: payload
                self.assertEqual(self.ad.fetch_top_trader_ratio("BTC"), want)


class EarlyReturnsTest(_Base):
    """各 `fetch_*` 的**早退**：没有数据 ⇒ `None`（不伪装成 0 或空结构）。"""

    def _stub(self, payload):
        self.ad._get = lambda path, params=None, **k: payload
        return self.ad

    def test_ticker_without_rows_is_none(self):
        for payload in (None, [], {}):
            with self.subTest(payload=payload):
                self.assertIsNone(self._stub(payload).fetch_ticker("BTC"))

    def test_candles_without_rows_is_none(self):
        for payload in (None, [], {}):
            with self.subTest(payload=payload):
                self.assertIsNone(self._stub(payload).fetch_candles("BTC"))

    def test_orderbook_without_data_is_none(self):
        for payload in (None, [], {}):
            with self.subTest(payload=payload):
                self.assertIsNone(self._stub(payload).fetch_orderbook("BTC"))

    def test_load_spec_without_match_is_none(self):
        for payload in (None, []):
            with self.subTest(payload=payload):
                self.assertIsNone(self._stub(payload)._load_spec("BTC-USDT-SWAP"),
                                  "查不到规格 ⇒ None（不编造）")

    def test_load_spec_with_a_dict_payload_crashes(self):
        """⚠️ **实测边界（列待议）**：`_load_spec` 对非列表回包直接下标取用 ⇒ 传 dict 时抛
        `KeyError: 0`（而非「结构不对 ⇒ `None`」）。

        这是本轮第三处**同类缺口**（Gate `positions()`、OKX `fetch_funding_rate`、此处）——
        形态一致：**缺元素/结构守卫 ⇒ 单条坏负载把整次读取打成异常**。
        本仓别处（Binance 各取数方法）都有守卫 ⇒ 记为待议、未擅自改。
        """
        with self.assertRaises(KeyError):
            self._stub({"instId": "BTC-USDT-SWAP"})._load_spec("BTC-USDT-SWAP")



    def test_candles_all_rows_unusable_is_none(self):
        """全行不可用（缺字段/非数值）⇒ `None`（**不是空列表**：空列表会被当成「读到但没有」）。"""
        for payload in ([[{}], ["x"]], [["1", "2"]]):
            with self.subTest(payload=payload):
                self.assertIsNone(self._stub(payload).fetch_candles("BTC"))



    def test_ticker_with_unparsable_numbers_is_none(self):
        """★ 数值字段解析失败 ⇒ **整个 ticker 记 `None`**（不是「部分字段缺失但照发」）。

        半截的 ticker 比没有 ticker 更危险：价格/量能里缺一块，下游却按完整行情推理。
        """
        for payload in ([{"instId": "BTC-USDT-SWAP", "last": "abc"}],
                        [{"instId": "BTC-USDT-SWAP", "last": "100", "volCcy24h": "x"}]):
            with self.subTest(payload=payload):
                self.assertIsNone(self._stub(payload).fetch_ticker("BTC"))




class OkxPositionsTest(unittest.TestCase):
    """`positions()` 属 **OKXAdapter**（需要凭证字段与 canonical）—— 上一轮用错了夹具。"""

    def setUp(self):
        from r20_backend.exchanges.okx import OKXAdapter
        self.ad = OKXAdapter.__new__(OKXAdapter)
        self.ad.environment = "demo"
        self.ad.api_key, self.ad.secret_key, self.ad.passphrase = "K", "S", "P"
        self.ad.canonical = lambda s: str(s).split("-")[0]

    def test_zero_rows_are_skipped(self):
        with patch("scripts.okx_rest.positions",
                   return_value=[{"instId": "BTC-USDT-SWAP", "pos": "0"},
                                 {"instId": "BTC-USDT-SWAP", "pos": "2.5"}]):
            out = self.ad.positions()
        self.assertEqual(len(out), 1, "零仓行跳过")
        self.assertEqual(out[0]["size_signed"], 2.5)
        self.assertEqual(out[0]["base"], "BTC")

    def test_non_dict_row_crashes_because_there_is_no_type_guard(self):
        """⚠️ **实测边界（列待议）**：`positions()` 直接对每行 `.get()`，**没有**元素类型守卫
        ⇒ 非 dict 行抛 `AttributeError`，**整次持仓读取作废**。

        这是本仓记录的**第四处**同形态缺口（Gate `positions()`、OKX `fetch_funding_rate`、
        OKX `_load_spec`、此处）——族性问题，未擅自改。
        """
        with patch("scripts.okx_rest.positions", return_value=["not-a-dict"]):
            with self.assertRaises(AttributeError):
                self.ad.positions()



class OkxOpenOrdersTest(unittest.TestCase):
    def setUp(self):
        from r20_backend.exchanges.okx import OKXAdapter
        self.ad = OKXAdapter.__new__(OKXAdapter)
        self.ad.environment = "demo"
        self.ad.api_key, self.ad.secret_key, self.ad.passphrase = "K", "S", "P"
        self.canonical = None
        self.seen = []

    def _stub(self, payload):
        def _pend(inst_id=None, env=None):
            self.seen.append(inst_id)
            return payload
        import scripts.okx_rest as rest
        self._p = patch.object(rest, "pending_orders", side_effect=_pend)
        self._p.start()
        self.addCleanup(self._p.stop)
        return self.ad

    def test_rows_are_normalised_and_symbol_is_optional(self):
        self._stub([{"instId": "BTC-USDT-SWAP", "ordId": 1, "clOrdId": "c", "side": "BUY",
                     "posSide": "LONG", "px": "100.5", "sz": "2", "ordType": "LIMIT",
                     "state": "live", "cTime": 1700000000000}])
        out = self.ad.open_orders()
        self.assertEqual(self.seen, [None], "不传标的 ⇒ 不过滤")
        self.assertEqual(out[0]["order_id"], "1", "ID 一律字符串")
        self.assertEqual(out[0]["side"], "buy")
        self.assertEqual(out[0]["pos_side"], "long")
        self.assertEqual(out[0]["price"], 100.5)
        self.assertEqual(out[0]["state"], "live")
        self.assertEqual(out[0]["created_time_ms"], 1700000000000)
        self.ad.open_orders("BTC")
        self.assertEqual(self.seen[-1], "BTC-USDT-SWAP", "传标的 ⇒ 用交易所原生符号过滤")

    def test_missing_or_unusable_payload_is_empty(self):
        for payload in (None, []):
            with self.subTest(payload=payload):
                self._stub(payload)
                self.assertEqual(self.ad.open_orders(), [])

    def test_non_dict_row_crashes_fifth_instance_of_the_family(self):
        """⚠️ **实测边界（列待议）**：`open_orders` 也直接对每行 `.get()`，**无元素类型守卫**。

        这是本仓登记的**第五处**同形态缺口（Gate positions / OKX funding / OKX _load_spec /
        OKX positions / 此处）⇒ 族性问题确认，未擅自改。
        """
        self._stub(["not-a-dict"])
        with self.assertRaises(AttributeError):
            self.ad.open_orders()



class OkxOrderDelegationTest(unittest.TestCase):
    def setUp(self):
        from r20_backend.exchanges.okx import OKXAdapter
        self.ad = OKXAdapter.__new__(OKXAdapter)
        self.ad.environment = "demo"
        self.ad.api_key, self.ad.secret_key, self.ad.passphrase = "K", "S", "P"
        import scripts.okx_rest as rest
        self.rest = rest
        self.calls = []

    def _stub(self, name, ret):
        def _f(*a, **kw):          # 有的委托是位置参数（如 set_leverage），有的是关键字
            self.calls.append((name, {"args": a, "kwargs": kw}))
            return ret
        p = patch.object(self.rest, name, side_effect=_f)
        p.start()
        self.addCleanup(p.stop)
        return self.ad

    def test_set_leverage_passes_integer_leverage_and_mode(self):
        self._stub("set_leverage", {"ok": True})
        self.ad.set_leverage("BTC", 3.7, margin_mode="isolated", pos_side="long")
        name, call = self.calls[-1]
        self.assertEqual(name, "set_leverage")
        self.assertEqual(call["args"][0], "BTC-USDT-SWAP")
        self.assertEqual(call["args"][1], 3, "杠杆必须被取整成 int")
        self.assertEqual(call["kwargs"]["mgn_mode"], "isolated")
        self.assertEqual(call["kwargs"]["pos_side"], "long")

    def test_cancel_order_passes_both_id_kinds(self):
        self._stub("cancel_order", {"ordId": "1"})
        out = self.ad.cancel_order("BTC", "123", client_order_id="c-1")
        kw = self.calls[-1][1]["kwargs"]
        self.assertEqual(kw["ord_id"], "123")
        self.assertEqual(kw["cl_ord_id"], "c-1")
        self.assertEqual(out["venue"], "okx")
        self.assertEqual(out["symbol"], "BTC-USDT-SWAP")
        self.ad.cancel_order("BTC")
        self.assertEqual(self.calls[-1][1]["kwargs"]["ord_id"], "", "缺 order_id ⇒ 空串")

    def test_list_protective_orders_uses_the_real_api_name(self):
        """审计 D4：真实 API 名是 `pending_algo_orders`（`list_algo_orders` 并不存在）。"""
        self._stub("pending_algo_orders", [{"algoId": "a1"}])
        out = self.ad.list_protective_orders("BTC")
        self.assertEqual(out, [{"algoId": "a1"}])
        self.assertEqual(self.calls[-1][1]["kwargs"]["inst_id"], "BTC-USDT-SWAP")
        self.ad.list_protective_orders()
        self.assertIsNone(self.calls[-1][1]["kwargs"]["inst_id"], "不传标的 ⇒ 不过滤")

    def test_fast_close_raises_when_there_is_no_position(self):
        """★ **没找到持仓 ⇒ 抛 `ValueError`**，不是返回一个假的「已平」（受理≠平掉的同族）。"""
        self.ad.positions = lambda: []
        with self.assertRaises(ValueError) as ctx:
            self.ad.fast_close_position("BTC")
        self.assertIn("未找到", str(ctx.exception))

    def test_fast_close_uses_the_legs_pos_side(self):
        self.ad.positions = lambda: [{"instId": "BTC-USDT-SWAP", "posSide": "long",
                                      "pos": "2"}]
        self._stub("close_position", {"ok": True})
        out = self.ad.fast_close_position("BTC")
        kw = self.calls[-1][1]["kwargs"]
        self.assertEqual(kw["inst_id"], "BTC-USDT-SWAP")
        self.assertEqual(kw["pos_side"], "long", "钉住具体腿，不猜")
        self.assertEqual(out["venue"], "okx")

    def test_position_without_pos_side_key_crashes(self):
        """⚠️ **实测边界（列待议）**：`fast_close_position` 用 `target[posSide]` **直取键**，
        缺该键 ⇒ `KeyError`（而不是「读不到方向 ⇒ 拒绝下平仓单」）。

        这是本仓登记的**第六处**同形态缺口（缺键/类型守卫 ⇒ 单条坏负载把整次操作打成异常）。
        """
        self.ad.positions = lambda: [{"instId": "BTC-USDT-SWAP", "pos": "2"}]
        with self.assertRaises(KeyError):
            self.ad.fast_close_position("BTC")


if __name__ == "__main__":
    unittest.main()
