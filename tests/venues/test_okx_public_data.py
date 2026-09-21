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


if __name__ == "__main__":
    unittest.main()
