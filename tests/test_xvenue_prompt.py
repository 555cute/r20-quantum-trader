"""跨所比对矩阵注入 Prompt 的单测（全 mock、零网络）。"""
from __future__ import annotations

import os
import sys
import tempfile
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
        return 2.13 if self.venue == "binance" else 1.19

    def fetch_funding_rate(self, base):
        return 0.000035 if self.venue == "binance" else None


class _BoomAd:
    def fetch_ticker(self, base):
        raise AssertionError("kill switch 打开时不得触碰备源")

    def fetch_top_trader_ratio(self, base):
        raise AssertionError


class TestXVenueMatrix(unittest.TestCase):
    def setUp(self):
        # 测试封闭性铁律：fetch_cross_venue_matrix 末尾会 flush 健康度落盘，
        # 必须把写入口钉到临时文件，严禁覆盖生产 data/venue_health.json。
        self._tmp = tempfile.TemporaryDirectory()
        self._vh = patch.object(abt, "VENUE_HEALTH_FILE", os.path.join(self._tmp.name, "vh.json"))
        self._vh.start()

    def tearDown(self):
        self._vh.stop()
        self._tmp.cleanup()

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
        self.assertEqual(xv["gate_ls"], 1.19)          # US-003 对称化
        self.assertEqual(xv["gate_funding_pct"], 0.0032)
        self.assertEqual(xv["bin_funding_pct"], 0.0035)  # 小数×100 → %口径

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
            "bin_last": 100.05, "gate_last": 99.9, "bin_ls": 2.13, "gate_ls": 1.19,
            "bin_funding_pct": 0.001, "gate_funding_pct": 0.0032}}
        line = abt._xvenue_prompt_line(pkg)
        self.assertIn("- 🌐 跨所比对", line)
        self.assertIn("币安:100.05(基差+0.050%)", line)
        self.assertIn("Gate:99.9(基差-0.100%)", line)
        self.assertIn("币安大户比:2.13", line)
        self.assertIn("Gate大户比:1.19", line)
        self.assertIn("币安费率:0.001%", line)
        self.assertIn("Gate费率:0.0032%", line)
        self.assertIn("大户比分歧2.13vs1.19→币安大户更乐观", line)
        self.assertIn("费率背离3.2x→Gate费率更高(0.0032%),空向持仓为收费方向", line)

    def test_prompt_line_partial_and_absent(self):
        only_bin = {"name": "ETH", "price": 3000.0, "xvenue": {"bin_last": 3001.0}}
        line = abt._xvenue_prompt_line(only_bin)
        self.assertIn("币安:3001(基差+0.033%)", line)
        self.assertNotIn("Gate", line)
        self.assertEqual(abt._xvenue_prompt_line({"name": "X", "price": 0}), "")
        self.assertEqual(abt._xvenue_prompt_line({"name": "X", "price": 100.0}), "")

    def test_empty_ticker_recorded_as_failure(self):
        # 端点被墙/拒连时 fetch_ticker 返回 None——必须记 failed，不得伪装 0ms 成功
        class EmptyAd:
            def fetch_ticker(self, base):
                return None

            def fetch_top_trader_ratio(self, base):
                return None
        with patch.object(abt, "_get_xvenue_adapter", lambda v: EmptyAd()):
            r = abt._xv_binance_snapshot("BTC")
        self.assertIsNone(r)
        h = abt._XV_HEALTH.get("binance", {})
        self.assertIn("BTC", h.get("failed", {}))
        self.assertNotIn("BTC", h.get("latency", {}))

    def test_flush_carries_provenance(self):
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            f = os.path.join(td, "vh.json")
            with patch.object(abt, "VENUE_HEALTH_FILE", f):
                abt._xv_flush_health([{"name": "BTC", "price": 100.0}])
            doc = _json.load(open(f))
            self.assertEqual(doc["package_count"], 1)
            self.assertIn("writer_pid", doc)
            self.assertIsInstance(doc["venues"]["okx"]["failed"], dict)

    def test_flush_symbols_cross_venue_snapshot(self):
        import json as _json
        pkg = {"name": "BTC", "price": 100.0, "xvenue": {
            "bin_last": 100.05, "gate_last": 99.9, "bin_ls": 2.1, "gate_ls": 1.4,
            "bin_funding_pct": 0.0032, "gate_funding_pct": 0.0098}}
        stale = {"name": "ETH", "price": 0, "xvenue": {"bin_last": 1}}
        with tempfile.TemporaryDirectory() as td:
            f = os.path.join(td, "vh.json")
            with patch.object(abt, "VENUE_HEALTH_FILE", f):
                abt._xv_flush_health([pkg, stale])
            doc = _json.load(open(f))
        s = doc["symbols"]["BTC"]
        self.assertEqual(s["okx"], 100.0)
        self.assertEqual(s["bin_basis_pct"], 0.05)
        self.assertEqual(s["gate_basis_pct"], -0.1)
        self.assertEqual(s["bin_ls"], 2.1)
        self.assertEqual(s["gate_funding_pct"], 0.0098)
        self.assertNotIn("ETH", doc["symbols"])   # price<=0 不进快照


class TestDivergenceNotes(unittest.TestCase):
    """US-003 分歧标注正反例（阈值 50% / 3x，依据价值研究实测基线）。"""

    def test_ls_divergence_positive(self):
        n = abt._xv_divergence_notes({"bin_ls": 2.13, "gate_ls": 1.19})
        self.assertIn("大户比分歧", n)
        self.assertIn("币安大户更乐观", n)

    def test_ls_direction_conflict_triggers(self):
        n = abt._xv_divergence_notes({"bin_ls": 1.5, "gate_ls": 0.8})
        # 1.5>1(多主导) vs 0.8<1(空主导) → 方向矛盾触发；1.5>0.8 乐观方=币安
        self.assertIn("大户比分歧", n)
        self.assertIn("币安大户更乐观", n)

    def test_ls_close_values_no_note(self):
        self.assertEqual(abt._xv_divergence_notes({"bin_ls": 2.10, "gate_ls": 1.95}), "")

    def test_funding_divergence_same_sign_only(self):
        n = abt._xv_divergence_notes({"bin_funding_pct": 0.001, "gate_funding_pct": -0.004})
        self.assertNotIn("费率背离", n)          # 异号不标
        n2 = abt._xv_divergence_notes({"bin_funding_pct": -0.004, "gate_funding_pct": -0.0012})
        self.assertIn("多向持仓为收费方向", n2)   # 负费率所收费方向=多头
        self.assertIn("币安费率更高", n2)          # hi=绝对值更大的所（-0.004 币安）

    def test_funding_below_threshold_silent(self):
        self.assertEqual(abt._xv_divergence_notes(
            {"bin_funding_pct": 0.005, "gate_funding_pct": 0.0065}), "")

    def test_garbage_inputs_never_raise(self):
        self.assertEqual(abt._xv_divergence_notes(
            {"bin_ls": "abc", "gate_ls": None, "bin_funding_pct": {}, "gate_funding_pct": "1e2"}),
            "")   # "1e2" 可转 float 但缺配对 → 无标注

    def test_zero_funding_no_divide_by_zero(self):
        self.assertEqual(abt._xv_divergence_notes(
            {"bin_funding_pct": 0.0, "gate_funding_pct": 0.003}), "")


if __name__ == "__main__":
    unittest.main()
