"""Ledger rebuild whitelist: current pool union this-account filled history."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.sync_full_ledger as sfl


class LedgerUnionWhitelistTests(unittest.TestCase):
    def setUp(self):
        self._pool = sfl.TARGET_INSTRUMENTS
        sfl.TARGET_INSTRUMENTS = [{"instId": "BTC-USDT-SWAP", "name": "BTC"}]

    def tearDown(self):
        sfl.TARGET_INSTRUMENTS = self._pool

    def test_retired_coin_from_old_ledger_survives(self):
        with patch.object(sfl, "_sqlite_traded_names", return_value=set()):
            allowed = sfl.allowed_inst_ids([{"inst": "XRP", "status": "closed"}])
        self.assertIn("BTC-USDT-SWAP", allowed)
        self.assertIn("XRP-USDT-SWAP", allowed)
        self.assertNotIn("ZZZNEVERTRADED-USDT-SWAP", allowed)

    def test_holding_coin_from_tracker_survives(self):
        with patch.object(sfl, "_sqlite_traded_names", return_value=set()):
            allowed = sfl.allowed_inst_ids([], {"ARB-USDT-SWAP_long": {"entryPx": 1}})
        self.assertIn("ARB-USDT-SWAP", allowed)

    def test_never_traded_noise_is_filtered(self):
        with patch.object(sfl, "_sqlite_traded_names", return_value=set()):
            allowed = sfl.allowed_inst_ids([])
        self.assertEqual(allowed, {"BTC-USDT-SWAP"})

    def test_full_instid_entry_not_double_suffixed(self):
        with patch.object(sfl, "_sqlite_traded_names", return_value=set()):
            allowed = sfl.allowed_inst_ids([{"inst": "ETH-USD-SWAP", "status": "closed"}])
        self.assertIn("ETH-USD-SWAP", allowed)
        self.assertNotIn("ETH-USD-SWAP-USDT-SWAP", allowed)

    def test_scoped_sqlite_trades_survive_without_json_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "r20_quant.db")
            connection = sqlite3.connect(db)
            connection.execute("CREATE TABLE trades (inst TEXT NOT NULL)")
            connection.execute("INSERT INTO trades(inst) VALUES ('SOL')")
            connection.commit()
            connection.close()
            with patch("scripts.db_manager.db_path", return_value=db):
                allowed = sfl.allowed_inst_ids([])
        self.assertIn("SOL-USDT-SWAP", allowed)
        self.assertIn("BTC-USDT-SWAP", allowed)

    def test_other_identity_sqlite_does_not_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            mine = os.path.join(tmp, "mine.db")
            other = os.path.join(tmp, "other.db")
            for path, inst in ((mine, "BTC"), (other, "LEAK")):
                connection = sqlite3.connect(path)
                connection.execute("CREATE TABLE trades (inst TEXT NOT NULL)")
                connection.execute("INSERT INTO trades(inst) VALUES (?)", (inst,))
                connection.commit()
                connection.close()
            with patch("scripts.db_manager.db_path", return_value=mine):
                allowed = sfl.allowed_inst_ids([])
        self.assertIn("BTC-USDT-SWAP", allowed)
        self.assertNotIn("LEAK-USDT-SWAP", allowed)

    def test_live_position_without_tracker_keeps_delisted_coin(self):
        with patch.object(sfl, "_sqlite_traded_names", return_value=set()):
            allowed = sfl.allowed_inst_ids([], {}, [{"instId": "SUI-USDT-SWAP", "pos": "1.5"}])
            allowed_flat = sfl.allowed_inst_ids([], {}, [{"instId": "SUI-USDT-SWAP", "pos": "0"}])
        self.assertIn("SUI-USDT-SWAP", allowed)
        self.assertNotIn("SUI-USDT-SWAP", allowed_flat)

    def test_submitted_journal_does_not_keep_delisted_coin(self):
        with patch.object(sfl, "_sqlite_traded_names", return_value=set()):
            allowed = sfl.allowed_inst_ids([])
        self.assertNotIn("DOGE-USDT-SWAP", allowed)


if __name__ == "__main__":
    unittest.main()
