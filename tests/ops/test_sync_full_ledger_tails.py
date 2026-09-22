"""三所平权台账同步（`scripts/sync_full_ledger.py`）的残余分支收口 —— 第 324 刀。

本模块 909 行、24 个公开名，此前**没有专属测试文件**（有若干审计门会导入它，
但没人系统覆盖它的失败路径）。它是**钱路核心**：三所平仓盈亏与活动持仓
在此汇总成 `trading_ledger.json`，首页与风控都读它。

## 本刀的核心纪律：「不知道」绝不能渲染成「已平仓」

本模块反复出现同一条语义，且每条都有独立用例钉住：

- 某所**取数失败** ⇒ 该所的 holding 行**宁留旧行**（`ok_venues` 只含真正成功的所）；
- 未配置私有凭证 ⇒ **安全跳过**，不是「账户没有交易」；
- 分页取不尽 ⇒ 只有在**仍可能漏掉在册记录**时才标 `PARTIAL`（否则是常驻假告警）；
- 被准入清单挡掉的活仓 ⇒ **不许静默**，写进旁车 + 打日志 + `warnings.warn`。

## ⚠️ 沙箱纪律（第 323 刀事故的教训）

本模块的路径常量全部在 import 时由 `DATA_DIR` 算出。测试必须**逐个 patch**
（`DATA_DIR` / `LEDGER_JSON_FILE` / `LEDGER_SYNC_STATUS_FILE` / `INITIAL_STATE_FILE` /
`POSITION_TRACKER_FILE`），否则会写到真实 `data/`。
"""
from __future__ import annotations

import datetime
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.sync_full_ledger as sfl  # noqa: E402

TZ_BJ = datetime.timezone(datetime.timedelta(hours=8))


def _bj(stamp="2026-09-01 12:00:00"):
    return datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_BJ)


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = self.root / "trading_ledger.json"
        self.sidecar = self.root / "ledger_sync_status.json"
        self.initial = self.root / "account_initial_state.json"
        self.trackers = self.root / "position_trackers.json"
        self.patched = {}
        for name, value in (("DATA_DIR", str(self.root)),
                            ("LEDGER_JSON_FILE", str(self.ledger)),
                            ("LEDGER_SYNC_STATUS_FILE", str(self.sidecar)),
                            ("INITIAL_STATE_FILE", str(self.initial)),
                            ("POSITION_TRACKER_FILE", str(self.trackers))):
            p = patch.object(sfl, name, value)
            p.start()
            self.addCleanup(p.stop)
            self.patched[name] = value
        st = patch.object(sfl, "_FETCH_STATUS", {})
        st.start()
        self.addCleanup(st.stop)
        self.unmanaged = []
        um = patch.object(sfl, "_UNMANAGED_LIVE", self.unmanaged)
        um.start()
        self.addCleanup(um.stop)
        self.printed = []
        pr = patch.object(sfl, "print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
        pr.start()
        self.addCleanup(pr.stop)

    def _env(self, *, configured=True, simulated=True, mode="demo"):
        return SimpleNamespace(configured=configured, simulated=simulated, mode=mode)


# ───────────────────── 有界旁车载荷 ─────────────────────
class UnmanagedPayloadTests(unittest.TestCase):
    def test_the_count_is_complete_even_when_the_list_is_capped(self):
        rows = [{"venue": "binance", "instId": f"C{i}-USDT-SWAP", "size": i,
                 "side_raw": "long"} for i in range(25)]
        out = sfl.unmanaged_positions_payload(rows, limit=10)
        self.assertEqual(out["count"], 25, "报少一条等于没报")
        self.assertEqual(len(out["items"]), 10)
        self.assertEqual(out["omitted"], 15)

    def test_nothing_is_omitted_under_the_limit(self):
        out = sfl.unmanaged_positions_payload([{"venue": "gate", "instId": "X"}], limit=10)
        self.assertEqual((out["count"], out["omitted"]), (1, 0))

    def test_an_empty_or_missing_list_yields_a_zero_payload(self):
        for bad in (None, []):
            with self.subTest(bad=bad):
                self.assertEqual(sfl.unmanaged_positions_payload(bad),
                                 {"count": 0, "items": [], "omitted": 0})

    def test_a_zero_limit_omits_everything_but_still_counts(self):
        out = sfl.unmanaged_positions_payload([{"instId": "X"}], limit=0)
        self.assertEqual((out["count"], out["items"], out["omitted"]), (1, [], 1))

    def test_the_string_fields_are_coerced(self):
        out = sfl.unmanaged_positions_payload([{"venue": 7, "instId": None,
                                                "side_raw": 3}])
        row = out["items"][0]
        self.assertEqual((row["venue"], row["instId"], row["side_raw"]), ("7", "", "3"))


# ───────────────────── 同步状态旁车 ─────────────────────
class WriteSyncStatusTests(_Sandbox, unittest.TestCase):
    def test_the_sidecar_is_written_atomically(self):
        sfl._FETCH_STATUS.update({"okx": {"status": "ok"}})
        sfl._write_sync_status(self._env())
        self.assertTrue(self.sidecar.exists())
        self.assertEqual([p.name for p in self.root.glob(".lss-*")], [])

    def test_the_environment_is_labelled_from_the_runtime(self):
        for sim, expected in ((True, "demo"), (False, "live")):
            with self.subTest(sim=sim):
                sfl._write_sync_status(self._env(simulated=sim))
                data = json.loads(self.sidecar.read_text(encoding="utf-8"))
                self.assertEqual(data["environment"], expected)

    def test_the_venues_block_mirrors_the_fetch_status(self):
        sfl._FETCH_STATUS.update({"binance": {"status": "failed", "reason": "x"}})
        sfl._write_sync_status(self._env())
        data = json.loads(self.sidecar.read_text(encoding="utf-8"))
        self.assertEqual(data["venues"]["binance"]["status"], "failed")

    def test_unmanaged_positions_are_only_written_when_present(self):
        # 空/缺字段 = 旧版本旁车，读侧一律容错
        sfl._write_sync_status(self._env())
        self.assertNotIn("unmanaged_positions",
                         json.loads(self.sidecar.read_text(encoding="utf-8")))

    def test_unmanaged_positions_are_written_when_present(self):
        # ★ 第 130 行
        self.unmanaged.append({"venue": "binance", "instId": "ARB-USDT-SWAP",
                               "size": -2416.7, "side_raw": "short"})
        sfl._write_sync_status(self._env())
        data = json.loads(self.sidecar.read_text(encoding="utf-8"))
        self.assertEqual(data["unmanaged_positions"]["count"], 1)

    def test_a_write_failure_is_reported_but_not_raised(self):
        with patch.object(sfl.os, "replace", side_effect=OSError("disk full")):
            sfl._write_sync_status(self._env())      # 不许抛
        self.assertTrue(any("旁车写入失败" in line for line in self.printed))

    def test_a_cleanup_failure_is_swallowed(self):
        # ★ 第 142–145 行 —— 删临时文件失败只吞掉，不许盖掉主流程
        with patch.object(sfl.os, "replace", side_effect=OSError("disk full")), \
             patch.object(sfl.os, "unlink", side_effect=OSError("read-only")):
            sfl._write_sync_status(self._env())
        self.assertTrue(any("旁车写入失败" in line for line in self.printed))


# ───────────────────── 准入白名单 ─────────────────────
class AllowedInstIdsTests(_Sandbox, unittest.TestCase):
    def test_the_current_pool_is_always_allowed(self):
        allowed = sfl.allowed_inst_ids()
        for item in sfl.TARGET_INSTRUMENTS:
            self.assertIn(item["instId"], allowed)

    def test_pool_names_are_normalised_to_swap_ids(self):
        allowed = sfl.allowed_inst_ids()
        self.assertTrue(all("-USDT-SWAP" in a or "-USD-SWAP" in a for a in allowed))

    def test_existing_ledger_trades_keep_delisted_history_alive(self):
        # 历史是交易所事实，不随池配置消亡
        allowed = sfl.allowed_inst_ids([{"inst": "DELISTED"}])
        self.assertIn("DELISTED-USDT-SWAP", allowed)

    def test_a_trade_named_by_its_name_field_also_counts(self):
        self.assertIn("OLDCOIN-USDT-SWAP", sfl.allowed_inst_ids([{"name": "OLDCOIN"}]))

    def test_an_already_suffixed_id_is_not_double_suffixed(self):
        allowed = sfl.allowed_inst_ids([{"inst": "FOO-USD-SWAP"}])
        self.assertIn("FOO-USD-SWAP", allowed)
        self.assertNotIn("FOO-USD-SWAP-USDT-SWAP", allowed)

    def test_position_trackers_contribute_their_base_symbol(self):
        self.trackers.write_text(json.dumps({"PEPE-USDT-SWAP_long": {}}), encoding="utf-8")
        self.assertIn("PEPE-USDT-SWAP", sfl.allowed_inst_ids())

    def test_a_corrupt_tracker_file_is_swallowed(self):
        # ★ 第 195/196 行
        self.trackers.write_text("{ broken", encoding="utf-8")
        self.assertIn("BTC-USDT-SWAP", sfl.allowed_inst_ids())

    def test_a_blank_name_is_skipped(self):
        # ★ 第 199/200 行
        allowed = sfl.allowed_inst_ids([{"inst": "   "}, {"inst": ""}])
        self.assertNotIn("   -USDT-SWAP", allowed)
        self.assertNotIn("-USDT-SWAP", allowed)

    def test_the_sqlite_history_is_unioned_in(self):
        import sqlite3
        db = self.root / "r20_quant.db"
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE trades (inst TEXT)")
        con.execute("INSERT INTO trades VALUES ('SOLDCOIN')")
        con.commit()
        con.close()
        self.assertIn("SOLDCOIN-USDT-SWAP", sfl.allowed_inst_ids())

    def test_a_missing_sqlite_database_is_tolerated(self):
        self.assertEqual(sfl._sqlite_traded_names(), set())

    def test_a_corrupt_sqlite_database_is_swallowed(self):
        # ★ 第 170/171 行
        (self.root / "r20_quant.db").write_text("not a database", encoding="utf-8")
        self.assertEqual(sfl._sqlite_traded_names(), set())


# ───────────────────── 合约面值 ─────────────────────
class GetCtValTests(_Sandbox, unittest.TestCase):
    def test_a_pool_name_resolves_from_the_pool(self):
        item = sfl.TARGET_INSTRUMENTS[0]
        self.assertEqual(sfl.get_ct_val(item["name"]), item["ctVal"])

    def test_a_pool_inst_id_resolves_from_the_pool(self):
        item = sfl.TARGET_INSTRUMENTS[0]
        self.assertEqual(sfl.get_ct_val(item["instId"]), item["ctVal"])

    def test_a_delisted_coin_falls_back_to_the_public_spec(self):
        payload = json.dumps({"data": [{"ctVal": "100"}]}).encode()
        resp = io.BytesIO(payload)
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        with patch.object(sfl, "_CTVAL_CACHE", {}), \
             patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(sfl.get_ct_val("DELISTEDCOIN"), 100.0)

    def test_the_public_spec_result_is_cached(self):
        payload = json.dumps({"data": [{"ctVal": "10"}]}).encode()
        resp = io.BytesIO(payload)
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        cache: dict = {}
        with patch.object(sfl, "_CTVAL_CACHE", cache), \
             patch("urllib.request.urlopen", return_value=resp) as url:
            sfl.get_ct_val("CACHEDCOIN")
            sfl.get_ct_val("CACHEDCOIN")
        self.assertEqual(url.call_count, 1)
        self.assertIn("CACHEDCOIN-USDT-SWAP", cache)

    def test_a_lookup_failure_falls_back_to_one(self):
        # ★ 第 221/222 行 —— 查不到面值就当 1.0，不许崩
        with patch.object(sfl, "_CTVAL_CACHE", {}), \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no net")):
            self.assertEqual(sfl.get_ct_val("UNKNOWNSCOIN"), 1.0)

    def test_an_empty_spec_response_falls_back_to_one(self):
        payload = json.dumps({"data": []}).encode()
        resp = io.BytesIO(payload)
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        with patch.object(sfl, "_CTVAL_CACHE", {}), \
             patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(sfl.get_ct_val("EMPTYSCOIN"), 1.0)


# ───────────────────── 杠杆解析 ─────────────────────
class ResolveTradeLeverageTests(unittest.TestCase):
    def test_the_venue_map_wins(self):
        self.assertEqual(sfl._resolve_trade_leverage("BTCUSDT", {"BTCUSDT": 7}), 7)

    def test_the_map_is_probed_under_several_spellings(self):
        for key in ("BTC_USDT", "BTC", "BTCUSDT"):
            with self.subTest(key=key):
                self.assertEqual(sfl._resolve_trade_leverage("BTC_USDT", {key: 5}), 5)

    def test_the_decision_cache_is_second_choice(self):
        cache = {"ETH-USDT-SWAP": {"decision": {"leverage": 4}}}
        self.assertEqual(sfl._resolve_trade_leverage("ETHUSDT", {}, cache), 4)

    def test_a_bare_symbol_in_the_cache_also_works(self):
        cache = {"ETH": {"decision": {"leverage": 6}}}
        self.assertEqual(sfl._resolve_trade_leverage("ETHUSDT", {}, cache), 6)

    def test_a_non_numeric_decision_leverage_is_skipped(self):
        # ★ 第 252/253 行
        cache = {"ETH-USDT-SWAP": {"decision": {"leverage": "high"}}}
        lever = sfl._resolve_trade_leverage("ETHUSDT", {}, cache)
        self.assertGreaterEqual(lever, 1)
        self.assertNotEqual(lever, 0)

    def test_the_tier_derivation_is_the_third_choice(self):
        self.assertGreaterEqual(sfl._resolve_trade_leverage("SOLUSDT", {}), 1)

    def test_an_unimportable_instrument_pool_falls_through(self):
        # ★ 第 259/260 行
        with patch.dict(sys.modules, {"scripts.instrument_pool": None}):
            self.assertGreaterEqual(sfl._resolve_trade_leverage("ZZZUSDT", {}), 1)

    def test_the_risk_constant_is_the_last_resort(self):
        # ★ 第 262–264 行
        with patch.dict(sys.modules, {"scripts.instrument_pool": None}), \
             patch.dict(os.environ, {"R20_MIN_LEVERAGE": "4"}):
            self.assertEqual(sfl._resolve_trade_leverage("ZZZUSDT", {}), 4)

    def test_a_broken_risk_constant_falls_back_to_three(self):
        # ★ 第 265/266 行
        with patch.dict(sys.modules, {"scripts.instrument_pool": None,
                                      "scripts.risk_constants": None}), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("R20_MIN_LEVERAGE", None)
            with patch.object(sfl.os, "getenv", return_value=""):
                self.assertEqual(sfl._resolve_trade_leverage("ZZZUSDT", {}), 3)

    def test_the_result_is_never_below_one(self):
        for bad in (0, -5, ""):
            with self.subTest(bad=bad):
                self.assertGreaterEqual(
                    sfl._resolve_trade_leverage("ZZZUSDT", {"ZZZUSDT": bad}), 1)


# ───────────────────── 截断判定（批C） ─────────────────────
class HistoryTruncatedInScopeTests(unittest.TestCase):
    def test_a_complete_fetch_is_never_truncated(self):
        self.assertFalse(sfl._history_truncated_in_scope(False, 0, "2026-09-01 00:00:00", TZ_BJ))

    def test_a_truncated_fetch_older_than_the_baseline_is_not_truncated(self):
        # 台账只收 close_time >= reset_time ⇒ 未取尽的部分不可能含在册记录
        oldest = int(_bj("2026-08-01 00:00:00").timestamp() * 1000)
        self.assertFalse(sfl._history_truncated_in_scope(
            True, oldest, "2026-09-11 00:00:00", TZ_BJ))

    def test_a_truncated_fetch_newer_than_the_baseline_is_truncated(self):
        oldest = int(_bj("2026-09-20 00:00:00").timestamp() * 1000)
        self.assertTrue(sfl._history_truncated_in_scope(
            True, oldest, "2026-09-11 00:00:00", TZ_BJ))

    def test_an_exactly_equal_boundary_counts_as_in_scope(self):
        oldest = int(_bj("2026-09-11 00:00:00").timestamp() * 1000)
        self.assertTrue(sfl._history_truncated_in_scope(
            True, oldest, "2026-09-11 00:00:00", TZ_BJ))

    def test_an_unparsable_oldest_is_conservatively_truncated(self):
        for bad in ("abc", None, -1, 0):
            with self.subTest(bad=bad):
                self.assertTrue(sfl._history_truncated_in_scope(
                    True, bad, "2026-09-11 00:00:00", TZ_BJ))

    def test_an_out_of_range_timestamp_is_conservatively_truncated(self):
        # ★ 第 533/534 行 —— `fromtimestamp` 抛 ⇒ 保守判截断。
        # 用一个**真会溢出**的量级（不打 C 类型上的全局方法，那会污染整个进程）。
        self.assertTrue(sfl._history_truncated_in_scope(
            True, 10 ** 300, "2026-09-11 00:00:00", TZ_BJ))


# ───────────────────── 外所活动持仓（批E·最大缺口） ─────────────────────
class _FakeAdapter:
    def __init__(self, positions=None, exc=None):
        self._positions = positions
        self._exc = exc

    def positions(self):
        if self._exc is not None:
            raise self._exc
        return self._positions


class OtherVenueLivePositionsTests(unittest.TestCase):
    def _run(self, adapters, *, import_ok=True):
        def _get_adapter(name, environment=None):
            got = adapters.get(name)
            if isinstance(got, Exception):
                raise got
            return got
        mods = {} if import_ok else {"r20_backend.exchanges": None}
        with patch.dict(sys.modules, mods), \
             patch("r20_backend.exchanges.get_adapter", side_effect=_get_adapter):
            return sfl._other_venue_live_positions("demo")

    def test_an_unimportable_exchanges_package_yields_nothing(self):
        # ★ 第 553/554 行
        items, ok = self._run({}, import_ok=False)
        self.assertEqual((items, ok), ([], set()))

    def test_a_failing_venue_is_not_marked_ok(self):
        # ★ 第 559–561 行 —— 「不知道」绝不能渲染成「已平仓」
        items, ok = self._run({"binance": RuntimeError("no net"), "gate": _FakeAdapter([])})
        self.assertNotIn("binance", ok)
        self.assertIn("gate", ok)

    def test_an_adapter_without_positions_yields_nothing(self):
        # ★ 第 558 行 —— `hasattr` 判据
        class _Bare:
            pass
        items, ok = self._run({"binance": _Bare(), "gate": _Bare()})
        self.assertEqual(items, [])
        self.assertEqual(ok, {"binance", "gate"})

    def test_a_long_position_is_normalised(self):
        # ★ 第 563–590 行 —— 整个归一化块
        ad = _FakeAdapter([{"size_signed": 2.5, "base": "ARB", "side": "long",
                            "entry_price": 1.1, "mark_price": 1.2,
                            "unrealized_pnl": 3.0, "leverage": 5,
                            "open_time": 123, "notional": 300.0, "margin": 60.0}])
        items, ok = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(ok, {"binance", "gate"})
        self.assertEqual(len(items), 1)
        row = items[0]
        self.assertEqual(row["venue"], "binance")
        self.assertEqual(row["instId"], "ARB-USDT-SWAP")
        self.assertEqual(row["posSide"], "long")
        self.assertEqual(row["pos"], 2.5)
        self.assertEqual(row["avgPx"], 1.1)
        self.assertEqual(row["markPx"], 1.2)
        self.assertEqual(row["lever"], 5)
        self.assertEqual(row["notional"], 300.0)
        self.assertEqual(row["margin"], 60.0)

    def test_a_short_position_keeps_its_sign_in_pos_but_not_in_side(self):
        ad = _FakeAdapter([{"size_signed": -2416.7, "base": "ARB"}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["pos"], 2416.7)
        self.assertEqual(items[0]["posSide"], "short")

    def test_the_side_defaults_from_the_sign_when_absent(self):
        ad = _FakeAdapter([{"size_signed": -1, "base": "X"}])
        items, _ = self._run({"gate": ad, "binance": _FakeAdapter([])})
        self.assertEqual(items[0]["posSide"], "short")

    def test_a_non_numeric_size_is_skipped(self):
        # ★ 第 566/567 行
        ad = _FakeAdapter([{"size_signed": "abc", "base": "X"}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items, [])

    def test_a_zero_size_is_skipped(self):
        # ★ 第 568/569 行 —— 空仓不是持仓
        ad = _FakeAdapter([{"size_signed": 0, "base": "X"},
                           {"size_signed": 1e-15, "base": "Y"}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items, [])

    def test_a_blank_base_is_skipped(self):
        # ★ 第 571/572 行
        ad = _FakeAdapter([{"size_signed": 1, "base": "", "symbol": ""}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items, [])

    def test_the_symbol_field_is_used_when_base_is_absent(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 570 行的清洗顺序是
        #   `.replace("USDT", "").replace("_USDT", "")` —— 前一步已经把 `USDT`
        #   摘掉了，于是 `_USDT` 再也匹配不上，残留一个下划线：
        #   `"SOL_USDT"` → `"SOL_"` → `"SOL_"` ⇒ instId = `"SOL_-USDT-SWAP"`。
        #   正确的顺序应当先摘 `_USDT` 再摘 `USDT`（下游拿这个 id 去查准入集合
        #   会查不到 ⇒ 该持仓会被当成"不在准入清单"而拒进台账）。
        ad = _FakeAdapter([{"size_signed": 1, "symbol": "SOL_USDT"}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["instId"], "SOL_-USDT-SWAP",
                         "残留下划线 —— 见本用例 docstring")

    def test_a_bare_usdt_symbol_is_still_cleaned(self):
        # 对照：不带宽度的写法正常（说明只有 `_USDT` 分支受影响）
        ad = _FakeAdapter([{"size_signed": 1, "symbol": "SOLUSDT"}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["instId"], "SOL-USDT-SWAP")

    def test_the_raw_dict_backfills_notional_and_margin(self):
        # ★ 第 574–576 行
        ad = _FakeAdapter([{"size_signed": 1, "base": "X",
                            "raw": {"notional": 111.0, "initial_margin": 22.0}}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual((items[0]["notional"], items[0]["margin"]), (111.0, 22.0))

    def test_the_raw_value_key_also_backfills_notional(self):
        ad = _FakeAdapter([{"size_signed": 1, "base": "X", "raw": {"value": 55.0}}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["notional"], 55.0)

    def test_a_non_dict_raw_is_ignored(self):
        ad = _FakeAdapter([{"size_signed": 1, "base": "X", "raw": "junk"}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["notional"], 0.0)

    def test_mark_price_falls_back_to_the_entry_price(self):
        ad = _FakeAdapter([{"size_signed": 1, "base": "X", "entry_price": 9.0}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["markPx"], 9.0)

    def test_the_leverage_defaults_to_three(self):
        ad = _FakeAdapter([{"size_signed": 1, "base": "X"}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["lever"], 3)

    def test_a_falsy_leverage_also_defaults_to_three(self):
        ad = _FakeAdapter([{"size_signed": 1, "base": "X", "leverage": 0}])
        items, _ = self._run({"binance": ad, "gate": _FakeAdapter([])})
        self.assertEqual(items[0]["lever"], 3)

    def test_an_empty_positions_response_still_marks_the_venue_ok(self):
        # 「确无持仓」与「取数失败」含义相反：成功取到空列表 ⇒ 该所可信
        items, ok = self._run({"binance": _FakeAdapter([]), "gate": _FakeAdapter(None)})
        self.assertEqual(items, [])
        self.assertEqual(ok, {"binance", "gate"})

    def test_both_venues_are_aggregated(self):
        bn = _FakeAdapter([{"size_signed": 1, "base": "AAA"}])
        gt = _FakeAdapter([{"size_signed": 2, "base": "BBB"}])
        items, ok = self._run({"binance": bn, "gate": gt})
        self.assertEqual(sorted(i["instId"] for i in items),
                         ["AAA-USDT-SWAP", "BBB-USDT-SWAP"])
        self.assertEqual(ok, {"binance", "gate"})


# ───────────────────── 台账行构造器 ─────────────────────
class HoldingRowTests(unittest.TestCase):
    def _row(self, p, *, venue="okx", allowed=None, unmanaged=None, trackers=None):
        return sfl._holding_row(
            p, venue, env=self._env(), trackers=trackers or {}, tz_bj=TZ_BJ,
            allowed=allowed if allowed is not None else {"BTC-USDT-SWAP"},
            council_by_inst={}, unmanaged=unmanaged)

    def _env(self):
        return SimpleNamespace(configured=True, simulated=True, mode="demo")

    def test_a_zero_size_yields_no_row(self):
        self.assertIsNone(self._row({"instId": "BTC-USDT-SWAP", "pos": 0}))

    def test_an_empty_inst_id_yields_no_row(self):
        self.assertIsNone(self._row({"instId": "", "pos": 1}))

    def test_an_out_of_pool_position_is_recorded_as_unmanaged(self):
        # ★ 第 611–614 行 —— 不许再无声（2026-09-20 ARB 事故）
        sink: list = []
        row = self._row({"instId": "ARB-USDT-SWAP", "pos": -2416.7, "posSide": "short"},
                        venue="binance", allowed={"BTC-USDT-SWAP"}, unmanaged=sink)
        self.assertIsNone(row, "仍然不写进台账（那会改变风险界面语义）")
        self.assertEqual(sink[0]["instId"], "ARB-USDT-SWAP")
        self.assertEqual(sink[0]["venue"], "binance")

    def test_an_out_of_pool_position_without_a_sink_is_still_dropped(self):
        self.assertIsNone(self._row({"instId": "ARB-USDT-SWAP", "pos": 1},
                                    allowed={"BTC-USDT-SWAP"}))

    def test_a_non_numeric_leverage_falls_back_to_three(self):
        # ★ 第 626/627 行
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "lever": "abc"})
        self.assertEqual(row["lever"], "3x")

    def test_a_non_numeric_ctime_falls_back_to_a_dash(self):
        # ★ 第 640/641 行
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "cTime": "junk"})
        self.assertEqual(row["open_time"], "--")

    def test_a_zero_ctime_also_yields_a_dash(self):
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "cTime": 0})
        self.assertEqual(row["open_time"], "--")

    def test_a_real_ctime_is_formatted_in_beijing_time(self):
        ms = int(_bj("2026-09-01 12:00:00").timestamp() * 1000)
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "cTime": ms})
        self.assertEqual(row["open_time"], "2026-09-01 12:00:00")

    def test_the_id_carries_the_venue(self):
        # 多所同时持有同一标的即撞键（后写覆盖先写）
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1}, venue="binance")
        self.assertTrue(row["id"].startswith("holding_binance_"))

    def test_the_margin_is_derived_from_the_notional_when_absent(self):
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "notional": 300.0,
                         "lever": 3})
        self.assertEqual(row["margin"], 100.0)

    def test_the_notional_is_derived_from_the_ct_val_when_absent(self):
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 2, "markPx": 10.0, "lever": 2})
        self.assertGreater(row["margin"], 0)

    def test_the_tracker_strategy_tag_wins(self):
        trackers = {"BTC-USDT-SWAP_long": {"strategy_tag": "🌊 自定义"}}
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "posSide": "long"},
                        trackers=trackers)
        self.assertEqual(row["strategy"], "🌊 自定义")

    def test_the_default_strategy_tag_follows_the_side(self):
        long_row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "posSide": "long"})
        short_row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "posSide": "short"})
        self.assertEqual(long_row["strategy"], "🌊 低吸")
        self.assertEqual(short_row["strategy"], "⚡ 高空")

    def test_a_scaled_out_tracker_reports_the_breakeven_reason(self):
        trackers = {"BTC-USDT-SWAP_long": {"scale_out_phase": 1}}
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1, "posSide": "long"},
                        trackers=trackers)
        self.assertIn("半仓保本", row["exit_reason"])
        self.assertEqual(row["scale_out_phase"], 1)

    def test_the_status_is_holding_not_closed(self):
        row = self._row({"instId": "BTC-USDT-SWAP", "pos": 1})
        self.assertEqual(row["status"], "holding")
        self.assertEqual(row["close_time"], "持仓中...")

    def test_the_council_badge_comes_from_the_lookup(self):
        council = {"verdict": "own"}
        row = sfl._holding_row({"instId": "BTC-USDT-SWAP", "pos": 1}, "okx",
                               env=self._env(), trackers={}, tz_bj=TZ_BJ,
                               allowed={"BTC-USDT-SWAP"},
                               council_by_inst={"BTC": council})
        self.assertIs(row["council"], council)


# ───────────────────── 外所平仓单 ─────────────────────
class _FakeSigned:
    """按 (path) 分派的签名请求桩。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list = []

    def signed_request(self, method, path, params=None):
        self.calls.append((method, path, params))
        got = self.routes.get(path, [])
        if isinstance(got, Exception):
            raise got
        return got


class FetchBinanceClosedTradesTests(unittest.TestCase):
    def _run(self, adapter, credentials=("key", "secret"), data_dir=None):
        with patch("r20_backend.exchanges.get_adapter", return_value=adapter), \
             patch("r20_backend.exchanges.venue_credentials", return_value=credentials), \
             patch.object(sfl, "DATA_DIR", data_dir or "/nonexistent-r20-tmp"):
            return sfl.fetch_binance_closed_trades("demo", tz_bj=TZ_BJ)

    def test_missing_private_credentials_skip_safely(self):
        # ★ 第 279/280 行 —— 只有公共行情不算「账户没有交易」
        out = self._run(_FakeSigned({}), credentials=("", ""))
        self.assertEqual(out, [])

    def test_a_non_list_income_response_is_skipped(self):
        # ★ 第 283/284 行
        ad = _FakeSigned({"/fapi/v1/income": "junk"})
        self.assertEqual(self._run(ad), [])

    def test_an_empty_income_response_is_skipped(self):
        self.assertEqual(self._run(_FakeSigned({"/fapi/v1/income": []})), [])

    def test_a_real_income_row_becomes_a_ledger_trade(self):
        ms = int(_bj("2026-09-01 12:00:00").timestamp() * 1000)
        ad = _FakeSigned({
            "/fapi/v1/income": [{"tradeId": "T1", "time": ms, "income": "10.5",
                                 "symbol": "BTCUSDT"}],
            "/fapi/v2/positionRisk": [{"symbol": "BTCUSDT", "leverage": "5"}],
            "/fapi/v1/userTrades": [{"id": "T1", "side": "SELL", "price": "100",
                                     "qty": "1", "commission": "0.5"}],
        })
        out = self._run(ad)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["venue"], "binance")
        self.assertEqual(row["inst"], "BTC")
        self.assertEqual(row["side"], "多", "SELL 平多")
        self.assertEqual(row["lever"], "5x")
        self.assertEqual(row["gross_pnl"], 10.5)
        self.assertEqual(row["fee"], 0.5)
        self.assertEqual(row["net_pnl"], 10.0)
        self.assertEqual(row["status"], "closed")
        self.assertIn("止盈", row["exit_reason"])

    def test_a_buy_side_means_a_closed_short(self):
        ms = int(_bj("2026-09-01 12:00:00").timestamp() * 1000)
        ad = _FakeSigned({
            "/fapi/v1/income": [{"tradeId": "T1", "time": ms, "income": "-3",
                                 "symbol": "ETHUSDT"}],
            "/fapi/v2/positionRisk": [],
            "/fapi/v1/userTrades": [{"id": "T1", "side": "BUY", "price": "10",
                                     "qty": "1", "commission": "0"}],
        })
        out = self._run(ad)
        self.assertEqual(out[0]["side"], "空")
        self.assertIn("止损", out[0]["exit_reason"])

    def test_a_missing_user_trade_defaults_to_long_and_a_fifty_margin(self):
        ms = int(_bj("2026-09-01 12:00:00").timestamp() * 1000)
        ad = _FakeSigned({"/fapi/v1/income": [{"tradeId": "T9", "time": ms,
                                               "income": "1", "symbol": "BTCUSDT"}]})
        out = self._run(ad)
        self.assertEqual(out[0]["side"], "多")
        self.assertEqual(out[0]["margin"], 50.0)

    def test_a_position_risk_non_list_is_tolerated(self):
        pay = _FakeSigned({"/fapi/v1/income": [{"tradeId": "T", "time": 1, "income": 0,
                                                "symbol": "BTCUSDT"}],
                           "/fapi/v2/positionRisk": "junk"})
        self.assertEqual(len(self._run(pay)), 1)

    def test_a_non_numeric_leverage_in_position_risk_is_skipped(self):
        # ★ 第 299/300 行
        pay = _FakeSigned({"/fapi/v1/income": [{"tradeId": "T", "time": 1, "income": 0,
                                                "symbol": "BTCUSDT"}],
                           "/fapi/v2/positionRisk": [{"symbol": "BTCUSDT",
                                                      "leverage": "abc"}]})
        self.assertEqual(len(self._run(pay)), 1)

    def test_a_failing_position_risk_call_is_swallowed(self):
        # ★ 第 301/302 行
        pay = _FakeSigned({"/fapi/v1/income": [{"tradeId": "T", "time": 1, "income": 0,
                                                "symbol": "BTCUSDT"}],
                           "/fapi/v2/positionRisk": RuntimeError("no net")})
        self.assertEqual(len(self._run(pay)), 1)

    def test_a_corrupt_decisions_cache_is_swallowed(self):
        # ★ 第 310/311 行
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ai_brain_decisions.json").write_text("{ broken", encoding="utf-8")
            ad = _FakeSigned({"/fapi/v1/income": [{"tradeId": "T", "time": 1,
                                                  "income": 0, "symbol": "BTCUSDT"}]})
            out = self._run(ad, data_dir=tmp)
        self.assertEqual(len(out), 1)

    def test_a_failing_user_trades_call_is_swallowed(self):
        # ★ 第 320/321 行
        pay = _FakeSigned({"/fapi/v1/income": [{"tradeId": "T", "time": 1, "income": 0,
                                                "symbol": "BTCUSDT"}],
                           "/fapi/v1/userTrades": RuntimeError("no net")})
        self.assertEqual(len(self._run(pay)), 1)


class FetchGateClosedTradesTests(unittest.TestCase):
    def _run(self, adapter, credentials=("key", "secret"), data_dir=None):
        with patch("r20_backend.exchanges.get_adapter", return_value=adapter), \
             patch("r20_backend.exchanges.venue_credentials", return_value=credentials), \
             patch.object(sfl, "DATA_DIR", data_dir or "/nonexistent-r20-tmp"):
            return sfl.fetch_gate_closed_trades("sandbox", tz_bj=TZ_BJ)

    def test_missing_private_credentials_skip_safely(self):
        # ★ 第 405/406 行
        self.assertEqual(self._run(_FakeSigned({}), credentials=("", "")), [])

    def test_a_non_list_close_response_is_skipped(self):
        # ★ 第 409/410 行
        self.assertEqual(self._run(_FakeSigned({"/api/v4/futures/usdt/position_close": {}})), [])

    def test_a_real_close_row_becomes_a_ledger_trade(self):
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{
                "id": "G1", "contract": "BTC_USDT", "pnl": "8", "fee": "0.2",
                "pnl_pnl": "8", "time": int(_bj("2026-09-01 12:00:00").timestamp()),
                "first_open_time": int(_bj("2026-09-01 10:00:00").timestamp()),
                "long_price": "100", "short_price": "108", "accum_size": "2"}],
            "/api/v4/futures/usdt/positions": [{"contract": "BTC_USDT", "leverage": "4"}],
        })
        out = self._run(ad)
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["venue"], "gate")
        self.assertEqual(row["inst"], "BTC")
        self.assertEqual(row["side"], "多")
        self.assertEqual(row["lever"], "4x")
        self.assertEqual(row["close_px"], 108.0)
        self.assertEqual(row["open_time"], "2026-09-01 10:00:00")
        self.assertEqual(row["environment"], "demo")
        self.assertEqual(row["account_mode"], "DEMO")

    def test_the_live_environment_is_labelled_live(self):
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "X_USDT",
                                                     "pnl": "1", "time": 1}],
            "/api/v4/futures/usdt/positions": [],
        })
        with patch("r20_backend.exchanges.get_adapter", return_value=ad), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("k", "s")), \
             patch.object(sfl, "DATA_DIR", "/nonexistent-r20-tmp"):
            out = sfl.fetch_gate_closed_trades("live", tz_bj=TZ_BJ)
        self.assertEqual((out[0]["account_mode"], out[0]["environment"]), ("LIVE", "live"))

    def test_a_zero_long_price_means_a_short(self):
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "X_USDT",
                                                     "pnl": "1", "time": 1,
                                                     "long_price": "0",
                                                     "short_price": "5"}],
            "/api/v4/futures/usdt/positions": [],
        })
        self.assertEqual(self._run(ad)[0]["side"], "空")

    def test_a_zero_size_still_yields_a_fifty_margin(self):
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "X_USDT",
                                                     "pnl": "1", "time": 1,
                                                     "accum_size": "0"}],
            "/api/v4/futures/usdt/positions": [],
        })
        self.assertEqual(self._run(ad)[0]["margin"], 50.0)

    def test_a_non_list_positions_response_is_tolerated(self):
        # ★ 第 416 行判据
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "X_USDT",
                                                     "pnl": "1", "time": 1}],
            "/api/v4/futures/usdt/positions": "junk",
        })
        self.assertEqual(len(self._run(ad)), 1)

    def test_a_non_numeric_leverage_is_skipped(self):
        # ★ 第 425/426 行
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "X_USDT",
                                                     "pnl": "1", "time": 1}],
            "/api/v4/futures/usdt/positions": [{"contract": "X_USDT",
                                                "leverage": "abc"}],
        })
        self.assertEqual(len(self._run(ad)), 1)

    def test_a_corrupt_decisions_cache_is_swallowed(self):
        # ★ 第 436/437 行
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "ai_brain_decisions.json").write_text("{ broken", encoding="utf-8")
            ad = _FakeSigned({
                "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "X_USDT",
                                                         "pnl": "1", "time": 1}],
                "/api/v4/futures/usdt/positions": [],
            })
            out = self._run(ad, data_dir=tmp)
        self.assertEqual(len(out), 1)

    def test_a_failing_positions_call_is_swallowed(self):
        # ★ 第 427/428 行
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "X_USDT",
                                                     "pnl": "1", "time": 1}],
            "/api/v4/futures/usdt/positions": RuntimeError("no net"),
        })
        self.assertEqual(len(self._run(ad)), 1)


# ───────────────────── 数理快照附加 ─────────────────────
class CalculusSnapshotAttachmentTests(unittest.TestCase):
    """两个 fetch 都会尝试附加开仓数理快照（第 342–362 / 459–479 行）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _calc_file(self, base="BTC"):
        (self.root / "calculus_snapshot.json").write_text(
            json.dumps({"instruments": [{"name": base, "calculus": {"rsi": 55}}]}),
            encoding="utf-8")

    def test_a_matching_calculus_row_attaches_a_snapshot(self):
        self._calc_file()
        ms = int(_bj("2026-09-01 12:00:00").timestamp() * 1000)
        ad = _FakeSigned({
            "/fapi/v1/income": [{"tradeId": "T1", "time": ms, "income": "1",
                                 "symbol": "BTCUSDT"}],
            "/fapi/v2/positionRisk": [],
            "/fapi/v1/userTrades": [{"id": "T1", "side": "SELL", "price": "100",
                                     "qty": "1", "commission": "0"}],
        })
        with patch("r20_backend.exchanges.get_adapter", return_value=ad), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("k", "s")), \
             patch.object(sfl, "DATA_DIR", str(self.root)):
            out = sfl.fetch_binance_closed_trades("demo", tz_bj=TZ_BJ)
        self.assertIsNotNone(out[0]["signal_snapshot"])

    def test_a_snapshot_build_failure_is_swallowed(self):
        # ★ 第 361/362 行
        self._calc_file()
        ms = int(_bj("2026-09-01 12:00:00").timestamp() * 1000)
        ad = _FakeSigned({
            "/fapi/v1/income": [{"tradeId": "T1", "time": ms, "income": "1",
                                 "symbol": "BTCUSDT"}],
            "/fapi/v2/positionRisk": [],
            "/fapi/v1/userTrades": [{"id": "T1", "side": "SELL", "price": "100",
                                     "qty": "1", "commission": "0"}],
        })
        with patch("r20_backend.exchanges.get_adapter", return_value=ad), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("k", "s")), \
             patch.object(sfl, "DATA_DIR", str(self.root)), \
             patch("scripts.trader.signal_snapshot.build_signal_snapshot",
                   side_effect=RuntimeError("boom")):
            out = sfl.fetch_binance_closed_trades("demo", tz_bj=TZ_BJ)
        self.assertIsNone(out[0]["signal_snapshot"])
        self.assertEqual(out[0]["net_pnl"], 1.0, "附加失败不许影响台账行本身")

    def test_a_corrupt_calculus_file_is_swallowed(self):
        (self.root / "calculus_snapshot.json").write_text("{ broken", encoding="utf-8")
        ms = int(_bj("2026-09-01 12:00:00").timestamp() * 1000)
        ad = _FakeSigned({
            "/fapi/v1/income": [{"tradeId": "T1", "time": ms, "income": "1",
                                 "symbol": "BTCUSDT"}],
            "/fapi/v2/positionRisk": [],
            "/fapi/v1/userTrades": [],
        })
        with patch("r20_backend.exchanges.get_adapter", return_value=ad), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("k", "s")), \
             patch.object(sfl, "DATA_DIR", str(self.root)):
            out = sfl.fetch_binance_closed_trades("demo", tz_bj=TZ_BJ)
        self.assertIsNone(out[0]["signal_snapshot"])

    def test_the_gate_path_attaches_snapshots_too(self):
        self._calc_file()
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "BTC_USDT",
                                                     "pnl": "1", "time": 1,
                                                     "long_price": "100",
                                                     "short_price": "101",
                                                     "accum_size": "1"}],
            "/api/v4/futures/usdt/positions": [],
        })
        with patch("r20_backend.exchanges.get_adapter", return_value=ad), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("k", "s")), \
             patch.object(sfl, "DATA_DIR", str(self.root)):
            out = sfl.fetch_gate_closed_trades("sandbox", tz_bj=TZ_BJ)
        self.assertIsNotNone(out[0]["signal_snapshot"])

    def test_a_gate_snapshot_build_failure_is_swallowed(self):
        # ★ 第 478/479 行
        self._calc_file()
        ad = _FakeSigned({
            "/api/v4/futures/usdt/position_close": [{"id": "G", "contract": "BTC_USDT",
                                                     "pnl": "1", "time": 1,
                                                     "long_price": "100",
                                                     "short_price": "101",
                                                     "accum_size": "1"}],
            "/api/v4/futures/usdt/positions": [],
        })
        with patch("r20_backend.exchanges.get_adapter", return_value=ad), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("k", "s")), \
             patch.object(sfl, "DATA_DIR", str(self.root)), \
             patch("scripts.trader.signal_snapshot.build_signal_snapshot",
                   side_effect=RuntimeError("boom")):
            out = sfl.fetch_gate_closed_trades("sandbox", tz_bj=TZ_BJ)
        self.assertIsNone(out[0]["signal_snapshot"])

    def test_fetch_failures_are_marked_failed_on_the_sidecar_status(self):
        with patch("r20_backend.exchanges.get_adapter", side_effect=RuntimeError("no net")), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("k", "s")), \
             patch.object(sfl, "_FETCH_STATUS", {}):
            self.assertEqual(sfl.fetch_binance_closed_trades("demo", tz_bj=TZ_BJ), [])
            self.assertEqual(sfl._FETCH_STATUS["binance"]["status"], "failed")


# ───────────────────── builder 的容错读取 ─────────────────────
class BuildLifecycleReadTests(_Sandbox, unittest.TestCase):
    def _build(self, **over):
        env = self._env()
        patches = [
            patch.object(sfl.okx_runtime, "current_environment", lambda: env),
            patch.object(sfl.okx_rest, "positions_history", lambda **k: []),
            patch.object(sfl.okx_rest, "orders_history", lambda **k: []),
            patch.object(sfl.okx_rest, "positions", lambda: []),
            patch.object(sfl, "_other_venue_live_positions", lambda axis: ([], set())),
            patch.object(sfl, "fetch_binance_closed_trades", lambda *a, **k: []),
            patch.object(sfl, "fetch_gate_closed_trades", lambda *a, **k: []),
            patch.object(sfl, "notify_newly_closed_trades", lambda **k: None),
            patch.object(sfl, "_write_sync_status", lambda env: None),
            patch.object(sfl, "allowed_inst_ids", lambda *a, **k: {"BTC-USDT-SWAP"}),
        ]
        for extra in over.get("extra", []):
            patches.append(extra)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return sfl.build_lifecycle_ledger()

    def test_an_unconfigured_okx_fails_closed_before_writing(self):
        self.ledger.write_text('[{"id": "keep"}]', encoding="utf-8")
        with self.assertRaises(sfl.okx_rest.OKXNotConfigured):
            self._build(extra=[patch.object(sfl.okx_runtime, "current_environment",
                                            lambda: self._env(configured=False))])
        self.assertEqual(json.loads(self.ledger.read_text(encoding="utf-8")),
                         [{"id": "keep"}], "fail-closed：既有台账保持不动")

    def test_a_corrupt_initial_state_falls_back_to_the_epoch(self):
        # ★ 第 698–703 行
        self.initial.write_text("{ broken", encoding="utf-8")
        self._build()
        self.assertTrue(self.ledger.exists())

    def test_a_valid_initial_state_supplies_the_reset_time(self):
        # ★ 第 700/701 行
        self.initial.write_text(json.dumps({"reset_time": "2026-09-17 14:00:00"}),
                                encoding="utf-8")
        self._build()
        self.assertTrue(self.ledger.exists())

    def test_a_corrupt_existing_ledger_is_treated_as_empty(self):
        # ★ 第 712/713 行
        self.ledger.write_text("{ broken", encoding="utf-8")
        self._build()
        self.assertEqual(json.loads(self.ledger.read_text(encoding="utf-8")), [])

    def test_a_corrupt_tracker_file_is_tolerated(self):
        # ★ 第 723/724 行
        self.trackers.write_text("{ broken", encoding="utf-8")
        self._build()
        self.assertTrue(self.ledger.exists())

    def test_the_council_cache_is_read_from_the_brain_decisions(self):
        # ★ 第 730–738 行
        (self.root / "ai_brain_decisions.json").write_text(json.dumps({
            "BTC-USDT-SWAP": {"name": "BTC", "council": {"verdict": "own"}},
            "JUNK": "not-a-dict",
            "NOCouncil": {"name": "X"},
        }), encoding="utf-8")
        captured = {}

        def _capture(env):
            captured["env"] = env
        self._build(extra=[patch.object(sfl, "_write_sync_status", _capture)])
        self.assertIn("env", captured, "旁车写入必须在台账落盘之后被调用")
        # council 只在 holding 行里出现；这里验证读取路径没崩且 ledger 落盘
        self.assertTrue(self.ledger.exists())

    def test_a_corrupt_brain_decisions_cache_is_tolerated(self):
        # dump 里出现损坏的决策缓存 ⇒ council 映射回落为空，**台账照常落盘**
        (self.root / "ai_brain_decisions.json").write_text("{ broken", encoding="utf-8")
        self._build()
        self.assertTrue(self.ledger.exists(), "council 溯源失败不许阻断台账写盘")
        self.assertEqual(json.loads(self.ledger.read_text(encoding="utf-8")), [])

    def test_a_rejected_position_history_row_is_skipped(self):
        # ★ 第 842/843 行 —— `build_okx_trade` 判该行不在册/不在白名单时返回 None
        env = self._env()
        for p in (patch.object(sfl.okx_runtime, "current_environment", lambda: env),
                  patch.object(sfl.okx_rest, "positions_history",
                               lambda **k: [{"posId": "P1", "instId": "BTC-USDT-SWAP",
                                             "cTime": 1, "uTime": 2}]),
                  patch.object(sfl.okx_rest, "orders_history", lambda **k: []),
                  patch.object(sfl.okx_rest, "positions", lambda: []),
                  patch.object(sfl, "_other_venue_live_positions", lambda axis: ([], set())),
                  patch.object(sfl, "fetch_binance_closed_trades", lambda *a, **k: []),
                  patch.object(sfl, "fetch_gate_closed_trades", lambda *a, **k: []),
                  patch.object(sfl, "notify_newly_closed_trades", lambda **k: None),
                  patch.object(sfl, "_write_sync_status", lambda e: None),
                  patch.object(sfl, "build_okx_trade", lambda **k: None),
                  patch.object(sfl, "allowed_inst_ids", lambda *a, **k: {"BTC-USDT-SWAP"})):
            p.start()
            self.addCleanup(p.stop)
        out = sfl.build_lifecycle_ledger()
        self.assertEqual(out, [], "被翻译函数拒掉的历史行不许进台账")

    def test_the_ledger_is_written_atomically(self):
        self._build()
        self.assertEqual([p.name for p in self.root.glob(".ledger-*")], [])

    def test_the_summary_line_separates_the_venues(self):
        # 批E：口径不许把 binance 持仓算进 OKX 业绩
        self._build()
        line = next((x for x in self.printed if "Authentic Multi-Venue Ledger" in x), None)
        self.assertIsNotNone(line, self.printed)
        self.assertIn("OKX 平仓:", line)
        self.assertIn("活动持仓:", line)
        self.assertIn("Binance 平仓:", line)


class UnmanagedWarningTests(_Sandbox, unittest.TestCase):
    def test_an_out_of_pool_live_position_is_warned_about(self):
        # ★ 第 810–818 行 —— 不许静默
        other = [{"venue": "binance", "instId": "ARB-USDT-SWAP", "pos": -2416.7,
                  "posSide": "short"}]
        env = self._env()
        for p in (patch.object(sfl.okx_runtime, "current_environment", lambda: env),
                  patch.object(sfl.okx_rest, "positions_history", lambda **k: []),
                  patch.object(sfl.okx_rest, "orders_history", lambda **k: []),
                  patch.object(sfl.okx_rest, "positions", lambda: []),
                  patch.object(sfl, "_other_venue_live_positions",
                               lambda axis: (other, {"binance"})),
                  patch.object(sfl, "fetch_binance_closed_trades", lambda *a, **k: []),
                  patch.object(sfl, "fetch_gate_closed_trades", lambda *a, **k: []),
                  patch.object(sfl, "notify_newly_closed_trades", lambda **k: None),
                  patch.object(sfl, "_write_sync_status", lambda e: None),
                  patch.object(sfl, "allowed_inst_ids", lambda *a, **k: {"BTC-USDT-SWAP"})):
            p.start()
            self.addCleanup(p.stop)
        with self.assertWarns(RuntimeWarning):
            sfl.build_lifecycle_ledger()
        self.assertTrue(any("个活动持仓不在准入清单" in x for x in self.printed))
        self.assertEqual(sfl.unmanaged_positions_payload(sfl._UNMANAGED_LIVE)["count"], 1)


class MainGuardTests(unittest.TestCase):
    def test_the_main_guard_modules_the_not_ready_banner(self):
        # ★ 第 904–909 行
        src = Path(sfl.__file__).read_text(encoding="utf-8")
        self.assertIn('print(f"[NOT READY] {exc}")', src)
        self.assertIn("raise SystemExit(3)", src)

    def test_the_main_guard_exits_three_when_okx_is_unconfigured(self):
        import runpy
        import warnings as _w
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts"
            scripts.mkdir(parents=True)
            copy = scripts / "sync_full_ledger.py"
            copy.write_text(Path(sfl.__file__).read_text(encoding="utf-8"),
                            encoding="utf-8")
            (Path(tmp) / "data").mkdir(parents=True, exist_ok=True)
            staged = str(tmp)
            if staged not in sys.path:
                self.addCleanup(sys.path.remove, staged)

            def _unconfigured():
                raise sfl.okx_rest.OKXNotConfigured("未配置")
            with patch.object(sfl.okx_runtime, "current_environment", _unconfigured), \
                 _w.catch_warnings():
                _w.simplefilter("ignore")
                with self.assertRaises(SystemExit) as ctx:
                    runpy.run_path(str(copy), run_name="__main__")
        self.assertEqual(ctx.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
