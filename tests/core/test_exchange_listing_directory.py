"""合约目录拉取：**各所路径不同**、**演示档要带模拟头**（第二百三十刀）。

| 语义 | 口径 |
|---|---|
| ★ 各所路径 | okx `/api/v5/public/instruments?instType=SWAP`；binance `/fapi/v1/exchangeInfo`；gate `/api/v4/futures/usdt/contracts` |
| ★ 演示档模拟头 | OKX 若 `simulated_trading` ⇒ 带 `x-simulated-trading: 1`；**live 档不得带**（否则可能把实盘当模拟或反之）|
| 未知场所 | ⇒ `ExchangeCapabilityError`（**明确拒绝**，不是悄悄空目录）|
| 字段归一 | 各所字段名不同（okx `state` / binance `status` / gate `in_delisting`）⇒ 统一成「合约名(大写) ⇒ 字典」|
| ★ 不吞失败 | 网络/解析失败**抛出**（由调用方决定 fail-open）—— 「**降级不是错，隐瞒降级才是**」|
| 铁律（数据类原文） | `ok=False` 仅表**目录不可用**（fail-open 语义：不阻塞）；`listed_count` **未知 = None，绝不填 0 冒充** |
"""

import json
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

from r20_backend.exchanges import listing as L
from r20_backend.exchanges import ExchangeCapabilityError


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class NormTest(unittest.TestCase):
    def test_norm_uppercases_and_strips(self):
        self.assertEqual(L._norm(" btc-usdt-swap "), "BTC-USDT-SWAP")
        self.assertEqual(L._norm(None), "")
        self.assertEqual(L._norm(""), "")

    def test_snapshot_dataclass_shape_and_documented_iron_rules(self):
        """★ 数据类形状（frozen + 五个字段）与它**写在源码里的两条铁律**。

        ⚠️ 这是一条**文档断言**（不是行为断言）：两条铁律写在**字段行内注释**里
        （`ok=False 仅表目录不可用（fail-open）`、`listed_count 未知 = None（绝不填 0 冒充）`），
        行内注释**不进 `__doc__`** —— 我第一版正是在这里断言 `__doc__` 而红的。
        """
        fields = L.ListingSnapshot.__dataclass_fields__
        self.assertEqual(sorted(fields), ["checked_at", "listed_count", "ok", "reason", "source"])
        self.assertTrue(L.ListingSnapshot.__dataclass_params__.frozen, "快照不可变")
        import inspect
        src = inspect.getsource(L.ListingSnapshot)
        self.assertIn("fail-open", src, "ok=False 的语义（不阻塞）写在字段注释里")
        self.assertIn("绝不填 0", src, "listed_count 未知 = None 的铁律写在字段注释里")


class FetchDirectoryTest(unittest.TestCase):
    def _run(self, venue, environment="demo", payload=None, simulated=False):
        prof = MagicMock()
        prof.simulated_trading = simulated
        requested = {}
        p = patch.object(L.env_profiles, "get_profile", return_value=prof)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(L.env_profiles, "resolve_base_url",
                          return_value="https://api.example")
        p2.start()
        self.addCleanup(p2.stop)

        def _urlopen(req, timeout=None):
            requested["url"] = req.full_url
            requested["headers"] = dict(req.headers)
            return _Resp(payload if payload is not None else {})
        p3 = patch.object(L, "urlopen", side_effect=_urlopen)
        p3.start()
        self.addCleanup(p3.stop)
        out = L._fetch_directory(venue, environment)
        return out, requested

    def test_each_venue_uses_its_own_public_path(self):
        for venue, path, payload in (
                ("okx", "/api/v5/public/instruments?instType=SWAP", {"data": []}),
                ("binance", "/fapi/v1/exchangeInfo", {"symbols": []}),
                ("gate", "/api/v4/futures/usdt/contracts", [])):
            with self.subTest(venue=venue):
                _out, req = self._run(venue, payload=payload)
                self.assertTrue(req["url"].endswith(path), f"{venue} 的公共目录路径")

    def test_okx_demo_carries_the_simulated_header_and_live_does_not(self):
        """★ 钱路相邻：模拟盘**必须**带 `x-simulated-trading`，实盘**必须不带**。"""
        _out, req = self._run("okx", environment="demo", simulated=True, payload={"data": []})
        lowered = {k.lower(): v for k, v in req["headers"].items()}
        self.assertEqual(lowered.get("x-simulated-trading"), "1", "演示档带模拟头")
        _out2, req2 = self._run("okx", environment="live", simulated=False, payload={"data": []})
        lowered2 = {k.lower(): v for k, v in req2["headers"].items()}
        self.assertNotIn("x-simulated-trading", lowered2, "实盘档不得带模拟头")

    def test_fields_are_normalised_per_venue(self):
        out, _ = self._run("okx", payload={"data": [{"instId": "btc-usdt-swap",
                                                      "state": "live"}]})
        self.assertEqual(out, {"BTC-USDT-SWAP": {"state": "live"}})
        out2, _ = self._run("binance", payload={"symbols": [{"symbol": "ethusdt",
                                                              "status": "TRADING"}]})
        self.assertEqual(out2, {"ETHUSDT": {"status": "TRADING"}})
        out3, _ = self._run("gate", payload=[{"name": "btc_usdt", "in_delisting": True},
                                             {"name": "eth_usdt"}])
        self.assertEqual(out3, {"BTC_USDT": {"in_delisting": "true"},
                                "ETH_USDT": {"in_delisting": "false"}},
                         "gate 的退市标记统一成小写字符串（缺失视为 false）")

    def test_unsupported_venue_is_refused_explicitly(self):
        with self.assertRaises(ExchangeCapabilityError) as ctx:
            self._run("kraken")
        self.assertIn("kraken", str(ctx.exception), "拒绝时要报出是哪个场所")

    def test_network_failure_propagates_instead_of_becoming_an_empty_directory(self):
        """★ **不吞失败**：抛出去，让调用方按 fail-open 语义决定怎么呈现。

        若这里吞成空目录，「**读不到**」就会被下游读成「**这个合约没上架**」。
        """
        prof = MagicMock()
        prof.simulated_trading = False
        p = patch.object(L.env_profiles, "get_profile", return_value=prof)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(L.env_profiles, "resolve_base_url", return_value="https://x")
        p2.start()
        self.addCleanup(p2.stop)
        p3 = patch.object(L, "urlopen", side_effect=TimeoutError("超时"))
        p3.start()
        self.addCleanup(p3.stop)
        with self.assertRaises(TimeoutError):
            L._fetch_directory("okx", "demo")

    def test_malformed_json_body_propagates(self):
        prof = MagicMock()
        prof.simulated_trading = False
        p = patch.object(L.env_profiles, "get_profile", return_value=prof)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(L.env_profiles, "resolve_base_url", return_value="https://x")
        p2.start()
        self.addCleanup(p2.stop)

        class _Bad(_Resp):
            def __init__(self):
                self._body = b"{ not json"
        p3 = patch.object(L, "urlopen", return_value=_Bad())
        p3.start()
        self.addCleanup(p3.stop)
        with self.assertRaises(json.JSONDecodeError):
            L._fetch_directory("binance", "live")


if __name__ == "__main__":
    unittest.main()
