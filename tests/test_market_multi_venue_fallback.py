"""Phase 1.5/2 接线测试（全 mock、零网络）：
- market_data_service 多场所备源容灾（ticker/candles/funding + 离线熔断开关）
- trades 表 venue 列幂等迁移与写入
- OKX 公共适配器与注册表三所齐备
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import market_data_service as mds

from r20_backend.exchanges import (
    OKXPublicAdapter,
    canonical_base,
    get_adapter,
    registered_venues,
    require_execution,
)
from r20_backend.exchanges.base import ExchangeCapabilityError


class _FakeAdapter:
    """模拟币安备源：适配器契约 = 升序 K 线 + 归一 ticker。"""

    def canonical(self, inst_id: str) -> str:
        return canonical_base(inst_id)

    def fetch_candles(self, base, bar, limit):
        # 升序 3 根
        return [["60000", "1", "2", "0.5", "1.5", "10"],
                ["120000", "1.5", "2.5", "1.4", "2", "11"],
                ["180000", "2", "3", "1.9", "2.5", "12"]]

    def fetch_ticker(self, base):
        return {"venue": "binance", "inst_id": "BTCUSDT", "last": 100.5,
                "bid": 100.4, "ask": 100.6, "open_24h": 99.0,
                "high_24h": 101.0, "low_24h": 98.0,
                "vol_24h_base": 123.0, "quote_vol_24h": 12000.0,
                "ts_ms": 1788000000000}

    def fetch_funding_rate(self, base):
        return 0.000032


class _DeadAdapter(_FakeAdapter):
    def fetch_candles(self, base, bar, limit):
        return None

    def fetch_ticker(self, base):
        return None

    def fetch_funding_rate(self, base):
        return None


def _adapters(binance, gate):
    table = {"binance": binance, "gate": gate}
    return lambda venue: table[venue]


class TestMultiVenueFallback(unittest.TestCase):
    def _fail_okx(self):
        return [
            patch("scripts.market_data_service._public_get", return_value=None),
            patch("scripts.market_data_service.subprocess.run",
                  side_effect=FileNotFoundError("no okx cli")),
        ]

    def test_candles_fallback_newest_first(self):
        with self._fail_okx()[0], self._fail_okx()[1], \
                patch("scripts.market_data_service._get_venue_adapter",
                      _adapters(_FakeAdapter(), _FakeAdapter())):
            rows = mds.fetch_candles("BTC-USDT-SWAP", bar="1H", limit=3)
        self.assertEqual(len(rows), 3)
        ts = [int(r[0]) for r in rows]
        self.assertEqual(ts, sorted(ts, reverse=True))  # OKX 契约：最新在前
        self.assertEqual(rows[0][4], "2.5")

    def test_ticker_fallback_okx_shape(self):
        with patch("scripts.market_data_service._public_get", return_value=None), \
                patch("scripts.market_data_service.subprocess.run",
                      side_effect=FileNotFoundError("no okx cli")), \
                patch("scripts.market_data_service._get_venue_adapter",
                      _adapters(_FakeAdapter(), _FakeAdapter())):
            t = mds.fetch_ticker("BTC-USDT-SWAP")
        self.assertIsNotNone(t)
        self.assertEqual(t["instId"], "BTC-USDT-SWAP")
        self.assertEqual(t["last"], "100.5")
        self.assertEqual(t["bidPx"], "100.4")
        self.assertEqual(t["open24h"], "99.0")
        self.assertEqual(t["venue"], "binance")

    def test_funding_fallback_percent(self):
        with patch("scripts.market_data_service._public_get", return_value=None), \
                patch("scripts.market_data_service._get_venue_adapter",
                      _adapters(_FakeAdapter(), _FakeAdapter())):
            r = mds.fetch_funding_rate("BTC-USDT-SWAP")
        self.assertEqual(r, 0.0032)

    def test_all_dead_returns_empty(self):
        with patch("scripts.market_data_service._public_get", return_value=None), \
                patch("scripts.market_data_service.subprocess.run",
                      side_effect=FileNotFoundError("no okx cli")), \
                patch("scripts.market_data_service._get_venue_adapter",
                      _adapters(_DeadAdapter(), _DeadAdapter())):
            self.assertEqual(mds.fetch_candles("BTC-USDT-SWAP"), [])
            self.assertIsNone(mds.fetch_ticker("BTC-USDT-SWAP"))
            self.assertIsNone(mds.fetch_funding_rate("BTC-USDT-SWAP"))

    def test_kill_switch_no_adapter_touch(self):
        def boom(venue):
            raise AssertionError("kill switch 打开时不得触碰备源")
        with patch("scripts.market_data_service._public_get", return_value=None), \
                patch("scripts.market_data_service.subprocess.run",
                      side_effect=FileNotFoundError("no okx cli")), \
                patch("scripts.market_data_service._get_venue_adapter", boom), \
                patch.dict(os.environ, {"R20_ALT_VENUE_FALLBACK": "0"}):
            self.assertEqual(mds.fetch_candles("BTC-USDT-SWAP"), [])
            self.assertIsNone(mds.fetch_ticker("BTC-USDT-SWAP"))


class TestOkxPublicAdapter(unittest.TestCase):
    def test_ticker_normalized(self):
        ad = OKXPublicAdapter()
        ad._get = lambda path, params=None, timeout=4.0: [{
            "instId": "BTC-USDT-SWAP", "last": "79000", "bidPx": "78999",
            "askPx": "79001", "open24h": "78000", "high24h": "79500",
            "low24h": "77500", "24hPct": "1.28", "volCcy24h": "2500", "ts": "1788000000000"}]
        t = ad.fetch_ticker("BTC-USDT-SWAP")
        self.assertEqual(t["last"], 79000)
        self.assertEqual(t["chg_24h_pct"], 1.28)
        self.assertEqual(t["vol_24h_base"], 2500)

    def test_candles_ascending(self):
        ad = OKXPublicAdapter()
        ad._get = lambda path, params=None, timeout=4.0: [
            ["300", "3", "4", "2", "3.5", "10"],
            ["100", "1", "2", "0.5", "2.5", "10"],
            ["200", "2", "3", "1.5", "3", "10"],
        ]
        kl = ad.fetch_candles("BTC", "15m", 10)
        self.assertEqual([int(r[0]) for r in kl], [100, 200, 300])

    def test_spec_parse(self):
        ad = OKXPublicAdapter()
        ad._get = lambda path, params=None, timeout=4.0: [{
            "instId": "BTC-USDT-SWAP", "ctVal": "0.01", "tickSz": "0.1",
            "lotSz": "1", "minSz": "0.01", "state": "trading"}]
        spec = ad.fetch_instrument_spec("BTC")
        self.assertEqual(spec.ct_val, 0.01)
        self.assertEqual(spec.min_size, 0.01)
        self.assertEqual(ad.to_bar("1H"), "1H")  # OKX 混合大小写


class TestRegistryThreeVenues(unittest.TestCase):
    def test_three_venues_registered_readonly_gate(self):
        self.assertEqual(registered_venues(), ["binance", "gate", "okx"])
        for v in ("okx", "binance", "gate"):
            with self.assertRaises(ExchangeCapabilityError):
                require_execution(v)
        self.assertIs(get_adapter("OKX"), get_adapter("okx"))


class TestTradesVenueColumn(unittest.TestCase):
    OLD_SCHEMA = """
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id TEXT UNIQUE, time TEXT NOT NULL, inst TEXT NOT NULL,
        action TEXT NOT NULL, direction TEXT NOT NULL, size REAL, price REAL,
        fee REAL, gross_pnl REAL, pnl REAL, comment TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    INSERT INTO trades (bill_id, time, inst, action, direction)
    VALUES ('b1', '2026-09-01', 'BTC-USDT-SWAP', 'closed', 'long');
    """

    def test_migration_backfills_and_writes(self):
        from scripts import db_manager
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "t.db")
            con = sqlite3.connect(db)
            con.executescript(self.OLD_SCHEMA)
            con.commit()
            con.close()
            with patch.object(db_manager, "DB_PATH", db):
                db_manager.init_database()          # 幂等迁移
                db_manager.init_database()          # 二次调用不炸
                db_manager.record_trade_sqlite({
                    "bill_id": "b2", "time": "2026-09-09", "inst": "BTCUSDT",
                    "action": "closed", "direction": "long", "price": 79000,
                    "pnl": 12.5, "venue": "binance"})
                con = sqlite3.connect(db)
                con.row_factory = sqlite3.Row
                rows = {r["bill_id"]: dict(r) for r in con.execute("SELECT * FROM trades")}
                con.close()
            self.assertEqual(rows["b1"]["venue"], "okx")       # 历史行回填
            self.assertEqual(rows["b2"]["venue"], "binance")   # 新行按源写入


if __name__ == "__main__":
    unittest.main()
