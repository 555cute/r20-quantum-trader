import json
import unittest
from requests import Response
import unittest.mock
from unittest.mock import patch
from scripts.market_data_service import (
    _public_get,
    _public_post,
    fetch_ticker,
    fetch_tickers_bulk,
    fetch_orderbook_depth,
    fetch_indicators_batch,
    fetch_single_indicator,
    fetch_candles,
    fetch_funding_rate,
    normalize_bar,
    _local_math_indicators,
)


def _synth_candles_1h(n=80, start=100.0, step=1.0):
    """OKX 倒序（最新在前）合成 K 线：[ts, o, h, l, c, vol, ...]"""
    rows = []
    for i in range(n):
        c = start + step * (n - 1 - i)
        o = c - step * 0.5
        rows.append([str(1700000000000 + (n - 1 - i) * 3600000), str(o), str(c + 0.8), str(o - 0.8), str(c), "100"])
    return rows


class TestBarNormalization(unittest.TestCase):
    def test_lowercase_hours_fixed(self):
        self.assertEqual(normalize_bar("1h"), "1H")
        self.assertEqual(normalize_bar("4h"), "4H")
        self.assertEqual(normalize_bar(" 4h "), "4H")
        self.assertEqual(normalize_bar("15M"), "15m")
        self.assertEqual(normalize_bar("1D"), "1D")
        self.assertEqual(normalize_bar("1d"), "1D")

    def test_valid_and_ambiguous_passthrough(self):
        self.assertEqual(normalize_bar("1M"), "1M")   # 月份大写不得转成 1 分钟
        self.assertEqual(normalize_bar("1m"), "1m")
        self.assertEqual(normalize_bar("1Hutc8"), "1Hutc8")
        self.assertEqual(normalize_bar("weird"), "weird")
        self.assertEqual(normalize_bar(""), "15m")

    def test_fetch_candles_normalizes_bar_and_caps_limit(self):
        seen = {}

        def fake_get(path, params=None, timeout=3.5):
            seen.update(params or {})
            return {"data": [["1", "2", "3", "4", "5", "6"]]}

        with patch("scripts.market_data_service._public_get", side_effect=fake_get):
            out = fetch_candles("BTC-USDT-SWAP", bar="1h", limit=999)
        self.assertEqual(len(out), 1)
        self.assertEqual(seen["bar"], "1H")
        self.assertEqual(seen["limit"], 300)


class TestAwsHostFailover(unittest.TestCase):
    def test_primary_host_down_secondary_serves(self):
        import scripts.market_data_service as mds

        class FakeResp:
            def __init__(self, url):
                self.status_code = 200
                self._url = url

            def json(self):
                return {"code": "0", "data": [["1", "2", "3", "4", "5", "6"]]}

        class FakeSession:
            def __init__(self):
                self.urls = []

            def get(self, url, params=None, timeout=None):
                self.urls.append(url)
                if "www.okx.com" in url:
                    raise ConnectionError("blocked in region")
                return FakeResp(url)

        sess = FakeSession()
        with patch.object(mds, "get_market_session", return_value=sess):
            data = mds._public_get("/api/v5/market/candles", params={"instId": "X", "bar": "1H", "limit": 3})
        self.assertIsNotNone(data)
        self.assertTrue(any("aws.okx.com" in u for u in sess.urls))


class TestLocalMathIndicatorFallback(unittest.TestCase):
    def test_indicators_computed_from_candles(self):
        with patch("scripts.market_data_service.fetch_candles", return_value=_synth_candles_1h()):
            out = _local_math_indicators("FAKE-USDT-SWAP", ["adx", "kdj", "bbwidth", "cmf"], "1H")
        self.assertIn("ADX", out)
        self.assertIn("KDJ", out)
        self.assertIn("BBWIDTH", out)
        self.assertIn("CMF", out)
        adx = float(out["ADX"]["adx"])
        self.assertGreaterEqual(adx, 20.0)  # 单边趋势市 ADX 必然拉高
        self.assertLessEqual(adx, 100.0)
        self.assertGreaterEqual(float(out["KDJ"]["j"]), 80.0)  # 纯上升趋势 KDJ 高位
        self.assertGreater(float(out["CMF"]["cmf"]), 0.0)  # 收在振幅上半区 → 正资金流
        self.assertGreater(float(out["BBWIDTH"]["bbWidth"]), 0.0)

    def test_batch_falls_back_to_local_when_mcp_dead(self):
        with patch("scripts.market_data_service._public_post", return_value=None), \
             patch("scripts.market_data_service.fetch_candles", return_value=_synth_candles_1h()):
            inds = fetch_indicators_batch("FAKE-USDT-SWAP", ["adx", "kdj", "bbwidth", "cmf"], bar="1h")
        self.assertEqual(set(inds.keys()) >= {"ADX", "KDJ", "BBWIDTH", "CMF"}, True)

    def test_single_indicator_falls_back_to_local(self):
        with patch("scripts.market_data_service._public_post", return_value=None), \
             patch("scripts.market_data_service.fetch_candles", return_value=_synth_candles_1h()):
            adx = fetch_single_indicator("FAKE-USDT-SWAP", "ADX", bar="1h")
        self.assertIn("adx", adx)


class TestZeroProcessGuarantee(unittest.TestCase):
    """US-004 契约：行情容灾链 www→aws→异所→纯 Python，进程派生层已物理删除。

    律③反钉：这里钉的是「不存在进程层」的架构不变式，不是历史 CLI 行为。
    """

    def test_module_has_no_process_spawning_layer(self):
        import inspect
        import scripts.market_data_service as mds
        src = inspect.getsource(mds)
        for forbidden in ("subprocess", "okx market", "okx --", ("replace_" + "cli_" + "prefix")):
            self.assertNotIn(forbidden, src, f"行情模块禁止出现进程派生残留：{forbidden}")
        self.assertFalse(hasattr(mds, "subprocess"))

    def test_ticker_and_candles_dead_rest_no_process_escape_hatch(self):
        """REST 双域全断 + 备源全断时安静落空/落本地数学，绝不派生任何进程。"""
        import scripts.market_data_service as mds
        with patch("scripts.market_data_service._public_get", return_value=None), \
                patch("scripts.market_data_service._alt_venue_ticker", return_value=None), \
                patch("scripts.market_data_service._alt_venue_candles", return_value=[]):
            self.assertIsNone(mds.fetch_ticker("FAKE-USDT-SWAP"))
            self.assertEqual(mds.fetch_candles("FAKE-USDT-SWAP"), [])


class TestMarketDataServiceHttpBoundary(unittest.TestCase):
    """Exercise real decoding/local math with deterministic HTTP responses, never OKX."""
    def setUp(self):
        def response(url, **kwargs):
            if url.endswith('/tickers'):
                data = [{'instId': i, 'last': '100'} for i in ('BTC-USDT-SWAP', 'ETH-USDT-SWAP')]
            elif url.endswith('/ticker'):
                data = [{'instId': 'BTC-USDT-SWAP', 'last': '100'}]
            elif url.endswith('/books'):
                data = [{'bids': [['99', '2']], 'asks': [['101', '2']]}]
            elif url.endswith('/candles'):
                data = _synth_candles_1h()
            elif url.endswith('/funding-rate'):
                data = [{'fundingRate': '0.0001'}]
            elif url.endswith('/aigc/mcp/indicators'):
                data = []  # Indicator API absence exercises local candle math.
            else:
                self.fail('Unexpected HTTP fixture URL: ' + url)
            fixture = Response()
            fixture.status_code = 200
            fixture._content = json.dumps({'code': '0', 'data': data}).encode()
            return fixture
        self.http = patch('requests.Session.get', side_effect=response)
        self.http_post = patch('requests.Session.post', side_effect=response)
        self.http.start(); self.addCleanup(self.http.stop)
        self.http_post.start(); self.addCleanup(self.http_post.stop)

    def test_fetch_ticker(self):
        ticker = fetch_ticker("BTC-USDT-SWAP")
        self.assertIsNotNone(ticker)
        self.assertIn("last", ticker)
        self.assertEqual(float(ticker["last"]), 100.0)

    def test_fetch_tickers_bulk(self):
        tickers = fetch_tickers_bulk("SWAP")
        self.assertIsInstance(tickers, dict)
        self.assertIn("BTC-USDT-SWAP", tickers)
        self.assertIn("ETH-USDT-SWAP", tickers)

    def test_fetch_orderbook_depth(self):
        ob = fetch_orderbook_depth("BTC-USDT-SWAP", sz=5)
        self.assertIsNotNone(ob)
        self.assertIn("bids", ob)
        self.assertIn("asks", ob)
        self.assertGreaterEqual(len(ob["bids"]), 1)

    def test_fetch_indicators_batch(self):
        inds = fetch_indicators_batch("BTC-USDT-SWAP", ["adx", "kdj", "bbwidth", "cmf"], bar="1H")
        self.assertIsInstance(inds, dict)
        self.assertIn("ADX", inds)
        self.assertIn("adx", inds["ADX"])

    def test_fetch_candles(self):
        candles = fetch_candles("BTC-USDT-SWAP", bar="15m", limit=10)
        self.assertIsInstance(candles, list)
        self.assertGreaterEqual(len(candles), 1)

    def test_fetch_funding_rate(self):
        fr = fetch_funding_rate("BTC-USDT-SWAP")
        self.assertIsNotNone(fr)
        self.assertIsInstance(fr, float)
        self.assertEqual(fr, 0.01)  # Public service exposes percent, not fraction.


if __name__ == "__main__":
    unittest.main()


def _health_module():
    """取**服务模块真正绑定的那个** `market_data_health` 实例。

    双拼写陷阱（`scripts/README.md` §双拼写）：`market_data_health` 与
    `scripts.market_data_health` 在 `sys.modules` 里是**两个不同实例**。
    靠"先 import 哪个"或"哪个已加载"来猜都不可靠 —— 实测在**全量套件**里会因
    导入顺序不同而猜错、读到另一份空计数器（单跑却绿）。故这里从服务模块绑定的
    函数对象反查其定义模块：`fn.__module__` 精确指向运行时那个实例。
    """
    import sys
    mds = __import__("scripts.market_data_service", fromlist=["x"])
    fn = getattr(mds, "note_call", None)
    module = sys.modules.get(getattr(fn, "__module__", "")) if fn is not None else None
    if module is not None and hasattr(module, "note_call"):
        return module
    raise AssertionError("定位不到 market_data_service 绑定的 market_data_health 实例")


class PublicFetchInstrumentationTest(unittest.TestCase):
    """第 138 刀：`_public_get/_public_post` 的埋点必须**一字不改取值行为**。

    事故背景（第 137 刀）：静默 `except` 吞掉取数失败 ⇒ 现价恒 0 ⇒ 主脑 P0 拦截、
    30 小时无信号。本类钉住：失败仍返回 None（行为不变），但**必须留痕**。
    """

    def setUp(self):
        health = _health_module()
        self.health = health
        health.reset()

    def tearDown(self):
        self.health.reset()

    def _resp(self, status=200, payload=None):
        resp = Response()
        resp.status_code = status
        resp._content = json.dumps(payload if payload is not None
                                   else {"code": "0", "data": [{"last": "70000"}]}).encode()
        return resp

    def test_success_records_call_and_returns_identical_payload(self):
        session = unittest.mock.MagicMock()
        session.get.return_value = self._resp()
        with patch("scripts.market_data_service.get_market_session", return_value=session), \
             patch("scripts.market_data_service.OKX_PUBLIC_HOSTS", ["https://www.okx.com"]):
            out = _public_get("/api/v5/market/ticker", {"instId": "BTC-USDT-SWAP"})
        self.assertEqual(out, {"code": "0", "data": [{"last": "70000"}]},
                         "埋点不得改变返回值")
        snap = self.health.snapshot()
        self.assertEqual(snap["calls"]["okx_public_get_ticker"], 1)
        self.assertEqual(snap["failed_calls"].get("okx_public_get_ticker", 0), 0)
        self.assertIn("okx_public_get_ticker", snap["last_success_ms"])

    def test_non_zero_code_is_a_failed_call_not_a_success(self):
        session = unittest.mock.MagicMock()
        session.get.return_value = self._resp(payload={"code": "51001", "msg": "no data"})
        with patch("scripts.market_data_service.get_market_session", return_value=session), \
             patch("scripts.market_data_service.OKX_PUBLIC_HOSTS", ["https://www.okx.com"]):
            out = _public_get("/api/v5/market/ticker")
        self.assertIsNone(out, "非 0 code 仍旧返回 None（行为不变）")
        snap = self.health.snapshot()
        self.assertEqual(snap["calls"]["okx_public_get_ticker"], 1)
        self.assertEqual(snap["failed_calls"]["okx_public_get_ticker"], 1)

    def test_all_hosts_failing_still_returns_none_but_leaves_a_trace(self):
        """本仓最贵的一次事故就是这样：返回 None 且**零痕迹**。"""
        session = unittest.mock.MagicMock()
        session.get.side_effect = RuntimeError("connection reset by peer")
        with patch("scripts.market_data_service.get_market_session", return_value=session):
            out = _public_get("/api/v5/market/ticker")
        self.assertIsNone(out, "取值行为必须一字不变")
        snap = self.health.snapshot()
        attempts = len(__import__("scripts.market_data_service", fromlist=["x"]).OKX_PUBLIC_HOSTS)
        self.assertEqual(snap["calls"]["okx_public_get_ticker"], attempts,
                         "每个 host 的每次尝试都要计入（双域直连的失败面必须可见）")
        self.assertEqual(snap["failed_calls"]["okx_public_get_ticker"], attempts)
        self.assertEqual(snap["failures"]["by_kind"]["okx_public_get_ticker"], attempts,
                         "失败必须进失败账本（第 137 刀的口径）")

    def test_post_path_is_instrumented_too(self):
        session = unittest.mock.MagicMock()
        session.post.return_value = self._resp()
        with patch("scripts.market_data_service.get_market_session", return_value=session), \
             patch("scripts.market_data_service.OKX_PUBLIC_HOSTS", ["https://www.okx.com"]):
            _public_post("/api/v5/market/candles", {"instId": "BTC-USDT-SWAP"})
        self.assertEqual(self.health.snapshot()["calls"]["okx_public_post_candles"], 1)

    def test_call_kind_is_bounded_and_sanitised(self):
        """标签基数必须有界：绝不把带参数的整条 URL（instId/limit…）当指标标签。"""
        mds = __import__("scripts.market_data_service", fromlist=["x"])
        kind = mds._call_kind("get", "/api/v5/market/ticker?instId=BTC-USDT-SWAP&limit=100")
        self.assertEqual(kind, "okx_public_get_ticker",
                         "query 混进标签 ⇒ 基数爆炸 + instId 泄进监控面")
        self.assertNotIn("/", kind)
        self.assertEqual(mds._call_kind("get", "/api/v5/market/candles"), "okx_public_get_candles")
        self.assertEqual(mds._call_kind("get", ""), "okx_public_get_unknown")

    def test_non_exception_failure_enters_both_ledgers(self):
        """HTTP/code 失败**没有异常**，但也是"取数失败"——两本账必须对得上。

        实测（第 138 刀）：第一版只记了 `failed_calls`、没记 `failures`，
        于是指标里两个口径给出不同的数（2 vs 1）—— 运维看到两个不同的失败数
        就会不再信任监控。这条门钉住"两账一致"。
        """
        session = unittest.mock.MagicMock()
        session.get.return_value = self._resp(payload={"code": "51001", "msg": "instrument not found"})
        with patch("scripts.market_data_service.get_market_session", return_value=session), \
             patch("scripts.market_data_service.OKX_PUBLIC_HOSTS", ["https://www.okx.com"]):
            self.assertIsNone(_public_get("/api/v5/market/ticker"))
        snap = self.health.snapshot()
        self.assertEqual(snap["failed_calls"]["okx_public_get_ticker"],
                         snap["failures"]["by_kind"]["okx_public_get_ticker"],
                         "failed_calls 与 failures 必须同口径")
        self.assertIn("51001", snap["failures"]["last_error"]["okx_public_get_ticker"],
                      "排障文本要带上交易所自己的 code/msg，否则无法定位")
