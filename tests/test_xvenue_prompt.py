"""跨所比对矩阵注入 Prompt 的单测（全 mock、零网络）。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ai_brain_trader as abt  # noqa: E402


class _FakeAd:
    def __init__(self, venue):
        self.venue = venue

    def fetch_ticker(self, base):
        if self.venue == "binance":
            return {"last": 100.01, "bid": 100.0, "ask": 100.02}
        return {"last": 99.99, "funding_rate": 0.000032}

    def fetch_top_trader_ratio(self, base):
        return 2.13 if self.venue == "binance" else None


class _BoomAd:
    def fetch_ticker(self, base):
        raise AssertionError("kill switch 打开时不得触碰备源")

    def fetch_top_trader_ratio(self, base):
        raise AssertionError


class TestXVenueMatrix(unittest.TestCase):
    def _pkgs(self):
        return [{"name": "BTC", "instId": "BTC-USDT-SWAP", "price": 100.0}]

    def test_matrix_attaches_fields(self):
        pkgs = self._pkgs()
        with patch.object(abt, "_get_xvenue_adapter", lambda v: _FakeAd(v)):
            abt.fetch_cross_venue_matrix(pkgs)
        xv = pkgs[0]["xvenue"]
        self.assertEqual(xv["bin_last"], 100.01)
        self.assertEqual(xv["gate_last"], 99.99)
        self.assertEqual(xv["bin_ls"], 2.13)
        self.assertEqual(xv["gate_funding_pct"], 0.0032)

    def test_adapter_exception_fail_soft(self):
        pkgs = self._pkgs()

        def boom(v):
            raise RuntimeError("network down")
        with patch.object(abt, "_get_xvenue_adapter", boom):
            abt.fetch_cross_venue_matrix(pkgs)  # 必须不抛
        xv = pkgs[0].get("xvenue")
        self.assertTrue(xv is None or xv == {})

    def test_kill_switch(self):
        pkgs = self._pkgs()
        with patch.object(abt, "_get_xvenue_adapter", lambda v: _BoomAd()), \
                patch.dict(os.environ, {"R20_XVENUE_PROMPT": "0"}):
            abt.fetch_cross_venue_matrix(pkgs)  # 不触发 _BoomAd

    def test_prompt_line_full(self):
        pkg = {"name": "BTC", "price": 100.0, "xvenue": {
            "bin_last": 100.05, "gate_last": 99.9, "bin_ls": 2.13, "gate_funding_pct": 0.0032}}
        line = abt._xvenue_prompt_line(pkg)
        self.assertIn("- 🌐 跨所比对", line)
        self.assertIn("币安:100.05(基差+0.050%)", line)
        self.assertIn("Gate:99.9(基差-0.100%)", line)
        self.assertIn("币安大户多空比:2.13", line)
        self.assertIn("Gate费率:0.0032%", line)

    def test_prompt_line_partial_and_absent(self):
        only_bin = {"name": "ETH", "price": 3000.0, "xvenue": {"bin_last": 3001.0}}
        line = abt._xvenue_prompt_line(only_bin)
        self.assertIn("币安:3001(基差+0.033%)", line)
        self.assertNotIn("Gate", line)
        self.assertEqual(abt._xvenue_prompt_line({"name": "X", "price": 0}), "")
        self.assertEqual(abt._xvenue_prompt_line({"name": "X", "price": 100.0}), "")


if __name__ == "__main__":
    unittest.main()
