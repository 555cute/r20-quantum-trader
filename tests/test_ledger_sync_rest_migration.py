"""US-005 封闭测试：台账/账单同步脚本群迁移 V5 直签 REST（HTTP 边界 mock，封闭三律）。

纪律：一切断言打在 `scripts.okx_rest.urlopen` 边界（patch import 绑定名，律②）；
凭证经 freeze_environment(values) 注入完全脱离真实 .env（tearDown 必 unfreeze）；
不 mock subprocess——迁移后的生产代码本就零 CLI 子进程（源码 tripwire 直接读文件钉）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:  # 同步脚本顶层 from instrument_pool import 需要
    sys.path.insert(0, _SCRIPTS_DIR)

import scripts.okx_rest as okx_rest
import scripts.okx_runtime as okx_runtime
import scripts.sync_full_ledger as sfl
import scripts.sync_web_data as swd
import scripts.generate_snapshots as gs

DEMO_VALUES = {
    "R20_OKX_ENV": "demo",
    "OKX_DEMO_API_KEY": "AKD", "OKX_DEMO_SECRET_KEY": "SKD", "OKX_DEMO_PASSPHRASE": "PPD",
}
EMPTY_VALUES = {"R20_OKX_ENV": "demo"}  # 无任何密钥 → configured False

_NOW_MS = str(int(time.time() * 1000))

TABLE = {
    "/api/v5/account/positions-history": {"code": "0", "data": [{
        "instId": "BTC-USDT-SWAP", "instType": "SWAP", "mgnMode": "cross", "type": "2",
        "posSide": "long", "direction": "long", "pos": "0", "closePos": "1",
        "realizedPnl": "1.5", "pnl": "1.5", "fee": "-0.02", "fundingFee": "0",
        "liqPenalty": "0", "lever": "3", "ctVal": "0.01",
        "avgOpenPx": "27000", "avgClosePx": "27150",
        "cTime": "1757000000000", "uTime": _NOW_MS, "state": "2",
    }]},
    "/api/v5/account/positions": {"code": "0", "data": [{
        "instId": "BTC-USDT-SWAP", "pos": "1", "posSide": "long", "avgPx": "27000",
        "markPx": "27100", "upl": "1", "uplRatio": "0.01", "lever": "3", "fee": "0",
        "cTime": "1757000000000", "mgnMode": "cross", "liqPx": "", "instType": "SWAP",
    }]},
    "/api/v5/trade/orders-history": {"code": "0", "data": [{
        "instId": "BTC-USDT-SWAP", "ordId": "9001", "clOrdId": "", "reduceOnly": "true",
        "state": "filled", "posSide": "long", "side": "sell", "sz": "1", "avgPx": "27150",
        "fee": "-0.02", "feeCcy": "USDT", "cTime": "1757003500000", "uTime": _NOW_MS,
        "tdMode": "cross", "ordType": "market", "instType": "SWAP",
    }]},
    "/api/v5/account/balance": {"code": "0", "data": [{
        "details": [{"ccy": "USDT", "eq": "12345.67", "availEq": "1000.5",
                     "cashBal": "11000", "upl": "10", "ordFroz": "0"}],
        "totalEq": "12345.67",
    }]},
    "/api/v5/account/bills": {"code": "0", "data": [{
        "ts": _NOW_MS, "instId": "BTC-USDT-SWAP", "instType": "SWAP", "type": "2",
        "subType": "5", "pnl": "1.23", "fee": "-0.01", "balChg": "1.22", "bal": "12345",
        "ordId": "9001", "ccy": "USDT",
    }]},
}


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._p = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _transport(seen: list):
    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        seen.append(url)
        for frag, payload in TABLE.items():
            if frag in url:
                return _Resp(payload)
        raise AssertionError(f"unexpected URL hit by migrated scripts: {url}")
    return _open


class LedgerRestMigrationTests(unittest.TestCase):
    def setUp(self):
        okx_runtime.freeze_environment(dict(DEMO_VALUES))
        self._pool = sfl.TARGET_INSTRUMENTS
        sfl.TARGET_INSTRUMENTS = [{"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 0.01}]
        self._swd_pool = swd.TARGET_INSTRUMENTS
        swd.TARGET_INSTRUMENTS = [{"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 0.01}]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        swd.TARGET_INSTRUMENTS = self._swd_pool
        sfl.TARGET_INSTRUMENTS = self._pool
        okx_runtime.unfreeze_environment()

    # ---------- sync_full_ledger ----------
    def _sfl_paths(self):
        d = self.tmp.name
        return {
            "DATA_DIR": d,
            "LEDGER_JSON_FILE": os.path.join(d, "trading_ledger.json"),
            "INITIAL_STATE_FILE": os.path.join(d, "account_initial_state.json"),
            "POSITION_TRACKER_FILE": os.path.join(d, "position_trackers.json"),
        }

    def test_ledger_builds_ledger_via_v5_rest(self):
        seen: list = []
        paths = self._sfl_paths()
        with patch.object(okx_rest, "urlopen", _transport(seen)), \
             patch.multiple(sfl, **paths), \
             patch.object(sfl, "get_ct_val", lambda inst: 0.01):
            rows = sfl.build_lifecycle_ledger()
        self.assertTrue(rows)
        joined = "\n".join(seen)
        self.assertIn("/api/v5/account/positions-history", joined)
        self.assertIn("/api/v5/account/positions?", joined)
        self.assertIn("/api/v5/trade/orders-history", joined)
        self.assertIn("limit=100", joined)      # 旧 CLI --limit 100 语义等价
        self.assertIn("instType=SWAP", joined)  # 旧 CLI swap/account SWAP 默认语义
        with open(paths["LEDGER_JSON_FILE"], "r", encoding="utf-8") as f:
            ledger = json.load(f)
        self.assertTrue(any("BTC" in str(t.get("inst", "")) for t in ledger))

    def test_ledger_not_ready_never_touches_network_or_ledger(self):
        okx_runtime.freeze_environment(dict(EMPTY_VALUES))
        paths = self._sfl_paths()
        sentinel = json.dumps([{"id": "keep", "inst": "BTC", "status": "closed"}])
        with open(paths["LEDGER_JSON_FILE"], "w", encoding="utf-8") as f:
            f.write(sentinel)
        seen: list = []
        with patch.object(okx_rest, "urlopen", _transport(seen)), patch.multiple(sfl, **paths):
            with self.assertRaises(okx_rest.OKXNotConfigured):
                sfl.build_lifecycle_ledger()
        self.assertEqual(seen, [])  # fail-closed 在任何请求之前
        with open(paths["LEDGER_JSON_FILE"], "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), sentinel)  # 既有台账零覆盖

    # ---------- sync_web_data ----------
    def _swd_paths(self):
        d = self.tmp.name
        return {
            "DATA_DIR": d,
            "DATA_JSON_PATH": os.path.join(d, "trading_data.json"),
            "SNAPSHOTS_JSON_FILE": os.path.join(d, "snapshots.json"),
            "LEDGER_JSON_FILE": os.path.join(d, "trading_ledger.json"),
            "LOG_FILE": os.path.join(d, "trading.log"),
        }

    def test_web_data_auth_block_and_equity_via_rest(self):
        seen: list = []
        paths = self._swd_paths()
        with patch.object(okx_rest, "urlopen", _transport(seen)), \
             patch.multiple(swd, **paths), \
             patch.object(swd, "fetch_tickers_bulk", lambda **kw: {}), \
             patch.object(swd, "fetch_ticker", lambda inst_id, **kw: None):
            swd.generate_trading_data()
        with open(paths["DATA_JSON_PATH"], "r", encoding="utf-8") as f:
            data = json.load(f)
        auth = data["auth"]
        self.assertEqual(auth["connection"], "static-v5-api-key")
        self.assertEqual(auth["mode"], "demo")
        self.assertTrue(auth["configured"])
        self.assertNotIn("userCode", auth)        # OAuth 设备码残渣清零
        self.assertNotIn("verificationUri", auth)
        self.assertEqual(data["account"]["total_eq"], 12345.67)
        joined = "\n".join(seen)
        self.assertIn("/api/v5/account/balance", joined)
        self.assertIn("/api/v5/account/bills", joined)
        self.assertIn("limit=100", joined)

    def test_web_data_not_ready_keeps_cache(self):
        okx_runtime.freeze_environment(dict(EMPTY_VALUES))
        paths = self._swd_paths()
        with open(paths["DATA_JSON_PATH"], "w", encoding="utf-8") as f:
            f.write('{"sentinel": 1}')
        seen: list = []
        with patch.object(okx_rest, "urlopen", _transport(seen)), patch.multiple(swd, **paths):
            with self.assertRaises(okx_rest.OKXNotConfigured):
                swd.generate_trading_data()
        self.assertEqual(seen, [])
        with open(paths["DATA_JSON_PATH"], "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"sentinel": 1}')

    def test_web_data_entry_not_ready_exits_without_network_or_writes(self):
        okx_runtime.freeze_environment(dict(EMPTY_VALUES))
        paths = self._swd_paths()
        target = Path(paths["DATA_JSON_PATH"])
        sentinel = b'{"sentinel": "keep existing web cache"}\n'
        target.write_bytes(sentinel)
        os.utime(target, ns=(1_600_000_000_000_000_000,) * 2)
        before_mtime = target.stat().st_mtime_ns
        before_files = set(Path(self.tmp.name).iterdir())
        stdout = io.StringIO()
        with patch.object(okx_rest, "urlopen", side_effect=AssertionError("unexpected network")) as network, \
             patch("urllib.request.urlopen", side_effect=AssertionError("unexpected public network")) as public_network, \
             patch.multiple(swd, **paths), redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as raised:
                swd.main()
        self.assertEqual(raised.exception.code, 3)
        self.assertIn("[NOT READY]", stdout.getvalue())
        self.assertNotIn("successfully", stdout.getvalue())
        self.assertEqual(network.call_count, 0)
        self.assertEqual(public_network.call_count, 0)
        self.assertEqual(target.read_bytes(), sentinel)
        self.assertEqual(target.stat().st_mtime_ns, before_mtime)
        self.assertEqual(set(Path(self.tmp.name).iterdir()), before_files)

    # ---------- generate_snapshots ----------
    def test_snapshots_written_via_rest_and_guarded(self):
        d = self.tmp.name
        snap_file = os.path.join(d, "snapshots.json")
        init_file = os.path.join(d, "account_initial_state.json")
        with open(init_file, "w", encoding="utf-8") as f:
            json.dump({"reset_time": "2020-01-01 00:00:00", "initial_capital": 1000.0}, f)
        seen: list = []
        with patch.object(okx_rest, "urlopen", _transport(seen)), \
             patch.multiple(gs, SNAPSHOTS_FILE=snap_file, ACCOUNT_INIT_FILE=init_file):
            gs.generate_live_snapshots()
            with open(snap_file, "r", encoding="utf-8") as f:
                snaps = json.load(f)
            self.assertGreaterEqual(len(snaps), 2)
            self.assertEqual(snaps[-1]["total_eq"], 12345.67)
            self.assertIn("/api/v5/account/bills", "\n".join(seen))
            # fail-closed：未配置 Key 不再动 snapshots.json
            with open(snap_file, "w", encoding="utf-8") as f:
                f.write("[[keep-me]]")
            okx_runtime.freeze_environment(dict(EMPTY_VALUES))
            with self.assertRaises(okx_rest.OKXNotConfigured):
                gs.generate_live_snapshots()
            with open(snap_file, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "[[keep-me]]")

    # ---------- 源码 tripwire（迁移面零 CLI） ----------
    def test_migrated_scripts_have_no_cli(self):
        scripts_dir = os.path.join(_REPO_ROOT, "scripts")
        pure = ["sync_full_ledger.py", "generate_snapshots.py",
                "debug_aggregate_orders.py", "debug_audit_bills.py"]
        texts = {}
        for name in pure + ["sync_web_data.py"]:
            with open(os.path.join(scripts_dir, name), encoding="utf-8") as f:
                texts[name] = f.read()
        for name, text in texts.items():
            for banned in ("okx account", "okx swap", "okx market", "okx auth",
                           ("replace_" + "cli_" + "prefix"), "okx_private_command"):
                self.assertNotIn(banned, text, f"{name} 残留 {banned}")
        for name in pure:
            self.assertNotIn("subprocess", texts[name], f"{name} 应零 subprocess")
        self.assertNotIn("run_json_cmd", texts["sync_web_data.py"])


if __name__ == "__main__":
    unittest.main()
