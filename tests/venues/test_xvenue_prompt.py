"""跨所比对矩阵注入 Prompt 的单测（全 mock、零网络）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
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


class _XvModuleBase(unittest.TestCase):
    """直连子模块测**内部分支**（上面几类走门面集成，这里补内部降级路径）。

    子模块的状态刻意留在门面（`_XV_HEALTH` 必须与重载后的那个对象同一个），
    所以这里把 `health` 当参数传进去 —— 正是门面调用期的做法。
    """

    def setUp(self):
        from scripts.brain import xvenue as xv
        self.xv = xv
        self.health: dict = {}
        self.records: list = []

    def _record(self, venue, name, ok, latency_ms, err=""):
        self.records.append((venue, name, ok, round(latency_ms), err))


class TestHealthFlushFallbacks(_XvModuleBase):
    """`_xv_flush_health` 的三处静默降级（第 95 / 129 / 139 / 152 行）。"""

    def _flush(self, packages, **over):
        wrote = {}
        kw = {"health": self.health, "safe_float": lambda v: float(v or 0),
              "atomic_write_json": lambda path, payload: wrote.update(payload),
              "venue_health_file": "/tmp/should-not-be-used.json"}
        kw.update(over)
        self.xv._xv_flush_health(packages, **kw)
        return wrote

    def test_okx_testnet_env_fallback_when_runtime_module_is_unavailable(self):
        # ★ 第 95 行：`from scripts.okx_runtime import current_environment` 抛 ⇒
        #   回落到直接读环境变量（`R20_OKX_ENV == "demo"`）
        pkgs = [{"name": "BTC", "price": 100.0}]
        with patch.dict(sys.modules, {"scripts.okx_runtime": None}):
            with patch.dict(os.environ, {"R20_OKX_ENV": "DEMO"}):
                out = self._flush(pkgs)
        self.assertTrue(out["venues"]["okx"]["testnet"], "回落分支必须按环境变量判 demo")

    def test_okx_testnet_env_fallback_says_false_for_live(self):
        pkgs = [{"name": "BTC", "price": 100.0}]
        with patch.dict(sys.modules, {"scripts.okx_runtime": None}), \
             patch.dict(os.environ, {"R20_OKX_ENV": "live"}):
            out = self._flush(pkgs)
        self.assertFalse(out["venues"]["okx"]["testnet"])

    def test_okx_testnet_uses_the_runtime_module_when_available(self):
        from scripts.okx_runtime import current_environment
        pkgs = [{"name": "BTC", "price": 100.0}]
        out = self._flush(pkgs)
        self.assertEqual(out["venues"]["okx"]["testnet"],
                         bool(current_environment().simulated))

    def test_basis_returns_none_for_unparsable_prices(self):
        # ★ 第 129 行：`_basis` 的 `except (TypeError, ValueError): return None`
        pkgs = [{"name": "BTC", "price": 100.0,
                 "xvenue": {"bin_last": "not-a-number"}}]
        out = self._flush(pkgs)
        self.assertIsNone(out["symbols"]["BTC"]["bin_basis_pct"])
        self.assertEqual(out["symbols"]["BTC"]["bin_last"], "not-a-number")

    def test_basis_is_none_for_non_positive_prices(self):
        pkgs = [{"name": "BTC", "price": 100.0, "xvenue": {"bin_last": 0}}]
        out = self._flush(pkgs)
        self.assertIsNone(out["symbols"]["BTC"]["bin_basis_pct"])

    def test_basis_is_computed_for_a_real_price(self):
        pkgs = [{"name": "BTC", "price": 100.0, "xvenue": {"bin_last": 101.0}}]
        out = self._flush(pkgs)
        self.assertEqual(out["symbols"]["BTC"]["bin_basis_pct"], 1.0)

    def test_symbols_are_skipped_when_price_or_snapshot_is_missing(self):
        pkgs = [{"name": "BTC", "price": 0.0, "xvenue": {"bin_last": 1.0}},
                {"name": "ETH", "price": 100.0},
                {"name": "SOL", "price": 100.0, "xvenue": {"bin_last": 1.0}}]
        out = self._flush(pkgs)
        self.assertEqual(sorted(out["symbols"]), ["SOL"])

    def test_a_package_without_a_name_aborts_the_whole_flush(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 88 行 `okx_ok = [p["name"] ...]`
        #    用的是**硬下标**，而这一行在整个函数的最外层 `try` 之内 ⇒
        #    一个**没有 `name` 键**的包会抛 KeyError，被第 151 行的 `except: pass`
        #    吞掉，于是**整份 venue_health.json 都不落盘**（不是只跳过那一个包）。
        #    调用方眼里只是"这次健康度没更新"，没有任何告警。
        pkgs = [{"name": "BTC", "price": 100.0},
                {"price": 100.0, "xvenue": {"bin_last": 1.0}}]
        self.assertEqual(self._flush(pkgs), {})

    def test_symbol_snapshot_build_failure_leaves_symbols_empty_but_keeps_venues(self):
        # ★ 第 139 行 `pass`：逐币快照是**纯附加**，异常不许影响健康度落盘。
        #   要走到这里，`xv` 必须**真值**（否则第 121 行的 `not xv` 会先 continue），
        #   且 `.get` 会抛。
        class _BadXv(dict):
            def get(self, k, d=None):
                raise RuntimeError("坏快照对象")

        pkgs = [{"name": "BTC", "price": 100.0, "xvenue": _BadXv({"a": 1})}]
        out = self._flush(pkgs)
        self.assertEqual(out["symbols"], {}, "符号块整体失败 ⇒ 留空")
        self.assertIn("okx", out["venues"], "但场所健康度仍要落盘")

    def test_whole_flush_failure_is_swallowed(self):
        # ★ 第 152 行 `pass`：健康度是观测面，写失败不许把交易周期打挂
        pkgs = [{"name": "BTC", "price": 100.0}]
        def boom(path, payload):
            raise OSError("write failed")
        out = self._flush(pkgs, atomic_write_json=boom)
        self.assertEqual(out, {}, "写失败 ⇒ 什么都没落，但**不抛**")

    def test_health_snapshot_is_a_copy_not_a_live_view(self):
        self.health["binance"] = {"latency": {"BTC": 12}, "failed": {}}
        out = self._flush([{"name": "BTC", "price": 100.0}])
        self.health["binance"]["latency"]["ETH"] = 99
        self.assertEqual(out["venues"]["binance"]["latency_ms"], {"BTC": 12})

    def test_venue_testnet_flag_comes_from_the_upper_case_env_key(self):
        pkgs = [{"name": "BTC", "price": 100.0}]
        self.health["gate"] = {"latency": {"BTC": 5}, "failed": {}}
        with patch.dict(os.environ, {"R20_GATE_TESTNET": "1"}):
            out = self._flush(pkgs)
        self.assertTrue(out["venues"]["gate"]["testnet"])

    def test_average_latency_is_rounded_and_zero_when_empty(self):
        self.health["binance"] = {"latency": {"BTC": 11, "ETH": 12}, "failed": {}}
        out = self._flush([{"name": "BTC", "price": 100.0}])
        self.assertEqual(out["venues"]["binance"]["avg_ms"], 12)
        self.health["gate"] = {"latency": {}, "failed": {}}
        out2 = self._flush([{"name": "BTC", "price": 100.0}])
        self.assertEqual(out2["venues"]["gate"]["avg_ms"], 0)

    def test_ok_venues_exclude_names_that_also_failed(self):
        self.health["binance"] = {"latency": {"BTC": 9, "ETH": 9},
                                  "failed": {"ETH": "boom"}}
        out = self._flush([{"name": "BTC", "price": 100.0}])
        self.assertEqual(out["venues"]["binance"]["ok"], ["BTC"])

    def test_okx_latency_is_collected_from_the_packages(self):
        pkgs = [{"name": "BTC", "price": 100.0, "okx_latency_ms": 40}]
        out = self._flush(pkgs)
        self.assertEqual(out["venues"]["okx"]["latency_ms"], {"BTC": 40})
        self.assertEqual(out["venues"]["okx"]["avg_ms"], 40)

    def test_okx_failed_lists_symbols_without_a_price(self):
        pkgs = [{"name": "BTC", "price": 100.0}, {"name": "ETH", "price": 0.0}]
        out = self._flush(pkgs)
        self.assertEqual(out["venues"]["okx"]["ok"], ["BTC"])
        self.assertIn("ETH", out["venues"]["okx"]["failed"])

    def test_provenance_fields_are_written(self):
        out = self._flush([{"name": "BTC", "price": 100.0}])
        self.assertEqual(out["v"], 1)
        self.assertEqual(out["writer_pid"], os.getpid())
        self.assertEqual(out["package_count"], 1)


class TestSnapshotFallbacks(_XvModuleBase):
    """两个快照函数与矩阵装配的降级路径（第 158 / 192 / 195 / 220 / 225 / 236 / 239 行）。"""

    def test_get_xvenue_adapter_forwards_to_the_backend_registry(self):
        # ★ 第 158 行：该函数是**既定 mock 缝**，必须真的转调 backend registry
        import r20_backend.exchanges as exchanges
        with patch.object(exchanges, "get_adapter", lambda v: f"adapter:{v}"):
            self.assertEqual(self.xv._get_xvenue_adapter("binance"), "adapter:binance")

    def test_binance_snapshot_shapes_a_success(self):
        class Ad:
            def fetch_ticker(self, base): return {"last": 101.0}
            def fetch_top_trader_ratio(self, base): return 2.1
            def fetch_funding_rate(self, base): return 0.00003
        out = self.xv._xv_binance_snapshot("BTC", get_adapter=lambda v: Ad(),
                                           record=self._record)
        self.assertEqual(out["last"], 101.0)
        self.assertEqual(out["ls"], 2.1)
        self.assertEqual(out["funding_rate"], 0.00003)
        self.assertTrue(self.records[0][2], "成功要记 ok=True")

    def test_binance_snapshot_tolerates_a_missing_funding_rate(self):
        class Ad:
            def fetch_ticker(self, base): return {"last": 101.0}
            def fetch_top_trader_ratio(self, base): return 2.1
            def fetch_funding_rate(self, base): raise RuntimeError("no funding")
        out = self.xv._xv_binance_snapshot("BTC", get_adapter=lambda v: Ad(),
                                           record=self._record)
        self.assertIsNone(out["funding_rate"])
        self.assertEqual(out["last"], 101.0)

    def test_binance_snapshot_empty_ticker_is_recorded_and_returns_none(self):
        class Ad:
            def fetch_ticker(self, base): return {}
            def fetch_top_trader_ratio(self, base): return 2.1
            def fetch_funding_rate(self, base): return None
        out = self.xv._xv_binance_snapshot("BTC", get_adapter=lambda v: Ad(),
                                           record=self._record)
        self.assertIsNone(out)
        self.assertFalse(self.records[0][2])
        self.assertIn("empty ticker", self.records[0][4])

    def test_binance_snapshot_exception_is_recorded(self):
        def boom(v): raise RuntimeError("registry down")
        out = self.xv._xv_binance_snapshot("BTC", get_adapter=boom, record=self._record)
        self.assertIsNone(out)
        self.assertIn("registry down", self.records[0][4])

    def test_gate_snapshot_tolerates_a_missing_trader_ratio(self):
        # ★ 第 192 行 `ls = None`
        class Ad:
            def fetch_ticker(self, base): return {"last": 99.0}
            def fetch_top_trader_ratio(self, base): raise RuntimeError("no stats")
        out = self.xv._xv_gate_snapshot("BTC", get_adapter=lambda v: Ad(),
                                        record=self._record)
        self.assertIsNone(out["ls"])
        self.assertEqual(out["last"], 99.0)

    def test_gate_snapshot_empty_ticker_returns_none(self):
        # ★ 第 195 行
        class Ad:
            def fetch_ticker(self, base): return {}
            def fetch_top_trader_ratio(self, base): return 1.1
        out = self.xv._xv_gate_snapshot("BTC", get_adapter=lambda v: Ad(),
                                        record=self._record)
        self.assertIsNone(out)
        self.assertFalse(self.records[0][2])

    def test_gate_snapshot_exception_is_recorded(self):
        def boom(v): raise RuntimeError("gate down")
        out = self.xv._xv_gate_snapshot("BTC", get_adapter=boom, record=self._record)
        self.assertIsNone(out)
        self.assertIn("gate down", self.records[0][4])

    def _matrix(self, packages, *, binance=None, gate=None, enabled=True, flush=None):
        flushed = []
        # ⚠️ 必须真的把 `flush` 用上 —— 忽略了它的话，"flush 抛错"的用例
        #    实际上测的是内部那个永不抛的 lambda（本刀就在这里自伤过一次）
        self.xv.fetch_cross_venue_matrix(
            packages, enabled=enabled,
            snapshot_binance=binance or (lambda name: None),
            snapshot_gate=gate or (lambda name: None),
            flush_health=flush or (lambda pkgs: flushed.append(pkgs)))
        return flushed

    def test_disabled_matrix_touches_nothing(self):
        pkgs = [{"name": "BTC", "price": 100.0}]
        self.assertEqual(self._matrix(pkgs, enabled=False), [])
        self.assertNotIn("xvenue", pkgs[0])

    def test_snapshot_result_exception_is_treated_as_no_data(self):
        # ★ 第 220 行 `val = None`：单个 future 抛（含超时）不许影响其它标的
        def boom(name): raise RuntimeError("timeout")
        def good(name): return {"venue": "gate", "name": name, "last": 101.0}
        pkgs = [{"name": "BTC", "price": 100.0}]
        self._matrix(pkgs, binance=boom, gate=good)
        self.assertEqual(pkgs[0]["xvenue"], {"gate_last": 101.0})

    def test_non_dict_snapshot_result_is_skipped(self):
        pkgs = [{"name": "BTC", "price": 100.0}]
        self._matrix(pkgs, binance=lambda n: "junk", gate=lambda n: 42)
        self.assertNotIn("xvenue", pkgs[0])

    def test_result_for_an_unknown_symbol_is_skipped(self):
        # ★ 第 225 行 `continue`：快照回的 name 不在 by_name 里（竞态/改名）不许崩
        pkgs = [{"name": "BTC", "price": 100.0}]
        self._matrix(pkgs, binance=lambda n: {"venue": "binance", "name": "GHOST",
                                              "last": 1.0})
        self.assertNotIn("xvenue", pkgs[0])
        self.assertEqual(len(self._matrix(pkgs)), 1, "flush 仍要执行")

    def test_funding_rate_conversion_failure_is_swallowed(self):
        # ★ 第 236 行 `pass`
        pkgs = [{"name": "BTC", "price": 100.0}]
        self._matrix(pkgs, binance=lambda n: {"venue": "binance", "name": n,
                                              "last": 1.0, "funding_rate": "abc"})
        self.assertEqual(pkgs[0]["xvenue"], {"bin_last": 1.0})

    def test_funding_rate_is_scaled_to_percent(self):
        pkgs = [{"name": "BTC", "price": 100.0}]
        self._matrix(pkgs, binance=lambda n: {"venue": "binance", "name": n,
                                              "last": 1.0, "funding_rate": 0.00032})
        self.assertEqual(pkgs[0]["xvenue"]["bin_funding_pct"], 0.032)

    def test_zero_values_are_kept_but_none_is_skipped(self):
        pkgs = [{"name": "BTC", "price": 100.0}]
        self._matrix(pkgs, binance=lambda n: {"venue": "binance", "name": n,
                                              "last": 0.0, "ls": None,
                                              "funding_rate": 0.0})
        self.assertEqual(pkgs[0]["xvenue"], {"bin_last": 0.0, "bin_funding_pct": 0.0})

    def test_matrix_level_failure_is_swallowed(self):
        # ★ 第 239 行 `pass`：整个矩阵采集炸掉也不许打挂交易周期
        # （注意：`(x for x in ()).throw(...)` 是**惰性**的，根本不会执行 ——
        #   必须用真正的函数才会抛）
        def boom_flush(pkgs):
            raise RuntimeError("flush boom")

        pkgs = [{"name": "BTC", "price": 100.0}]
        self.assertEqual(self._matrix(pkgs, flush=boom_flush), [])

    def test_flush_is_called_once_with_the_packages(self):
        pkgs = [{"name": "BTC", "price": 100.0}]
        flushed = self._matrix(pkgs, binance=lambda n: {"venue": "binance", "name": n,
                                                        "last": 1.0})
        self.assertEqual(len(flushed), 1)
        self.assertIs(flushed[0], pkgs)

    def test_packages_without_a_name_are_not_snapshotted(self):
        seen = []
        pkgs = [{"price": 100.0}]
        self._matrix(pkgs, binance=lambda n: seen.append(n) or None)
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
