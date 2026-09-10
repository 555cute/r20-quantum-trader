import json
import unittest
from requests import Response
from unittest.mock import patch
from scripts.market_data_service import (
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
        for forbidden in ("subprocess", "okx market", "okx --", "replace_cli_prefix"):
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
