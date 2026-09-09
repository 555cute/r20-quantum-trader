"""sync_full_ledger 历史币种白名单回归测试(2026-09-09 修复钉扎)。

事故背景：台账重建按"当前标的池"过滤交易所持仓史，用户删除币种后下一次
同步即把该币全部已平仓历史从 trading_ledger.json 抹掉(页面台账消失)。
修复语义：白名单 = 当前池 ∪ 历史留痕(SQLite trades/旧台账 JSON/持仓追踪器)，
噪声过滤(从未交易过的币不进台账)继续成立。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.okx_rest as okx_rest
import scripts.okx_runtime as okx_runtime
import scripts.sync_full_ledger as sfl


class LedgerUnionWhitelistTests(unittest.TestCase):
    def setUp(self):
        self._pool = sfl.TARGET_INSTRUMENTS
        sfl.TARGET_INSTRUMENTS = [{"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 0.01}]

    def tearDown(self):
        sfl.TARGET_INSTRUMENTS = self._pool

    def test_retired_coin_from_old_ledger_survives(self):
        allowed = sfl.allowed_inst_ids([{"inst": "XRP", "status": "closed"}])
        self.assertIn("BTC-USDT-SWAP", allowed)          # 现池币种
        self.assertIn("XRP-USDT-SWAP", allowed)          # 已下架但旧台账有记录
        self.assertNotIn("ZZZNEVERTRADED-USDT-SWAP", allowed)  # 噪声过滤仍在

    def test_holding_coin_from_tracker_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "trackers.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump({"ARB-USDT-SWAP_long": {"entryPx": 1}}, f)
            with patch.object(sfl, "POSITION_TRACKER_FILE", fp):
                allowed = sfl.allowed_inst_ids([])
        self.assertIn("ARB-USDT-SWAP", allowed)          # 持仓中途删币不隐身

    def test_full_instid_entry_not_double_suffixed(self):
        allowed = sfl.allowed_inst_ids([{"inst": "ETH-USD-SWAP", "status": "closed"}])
        self.assertIn("ETH-USD-SWAP", allowed)
        self.assertNotIn("ETH-USD-SWAP-USDT-SWAP", allowed)


class RetiredCoinContractSpecTests(unittest.TestCase):
    def setUp(self):
        # 封闭三律·律①（2026-09-09 修复）：本测试原把红绿钉在运行环境的标的池残留上——
        # 新检出/无 data/instruments.json 时，代码默认池含 XRP(ctVal=100) 使 get_ct_val
        # 提前返回、永远绕不过 patch 的 urlopen 桩。此处钉扎「池内无 XRP」的确定前提。
        self._pool = sfl.TARGET_INSTRUMENTS
        sfl.TARGET_INSTRUMENTS = [
            item for item in self._pool
            if item.get("name") != "XRP" and "XRP" not in str(item.get("instId", ""))
        ]
        sfl._CTVAL_CACHE.clear()

    def tearDown(self):
        sfl.TARGET_INSTRUMENTS = self._pool
        sfl._CTVAL_CACHE.clear()

    def test_ct_val_falls_back_to_public_instruments(self):
        sfl._CTVAL_CACHE.clear()
        payload = json.dumps({"code": "0", "data": [{"ctVal": "100.0"}]}).encode()

        class _Resp:
            def read(self): return payload
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with patch("urllib.request.urlopen", return_value=_Resp()) as mock_open:
            self.assertEqual(sfl.get_ct_val("XRP"), 100.0)   # 不在池内 → 走公共规格端点
            self.assertEqual(sfl.get_ct_val("XRP"), 100.0)   # 第二次命中进程缓存
        mock_open.assert_called_once()
        sfl._CTVAL_CACHE.clear()


class ClosedTradeSizeTests(unittest.TestCase):
    """生命周期抽屉数量恒为 0 的回归钉扎：closed 行必须写入真实张数。"""

    def setUp(self):
        self._pool = sfl.TARGET_INSTRUMENTS
        sfl.TARGET_INSTRUMENTS = [{"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 0.01}]

    def tearDown(self):
        sfl.TARGET_INSTRUMENTS = self._pool

    def test_closed_row_sz_from_close_total_pos(self):
        from unittest.mock import MagicMock
        hist = [{
            "instId": "BTC-USDT-SWAP", "direction": "long", "type": "2",
            "openAvgPx": "50000", "closeAvgPx": "51000", "pnl": "10", "fee": "-1",
            "lever": "3", "closeTotalPos": "2", "openMaxPos": "2", "pnlRatio": "3.0",
            "cTime": "1700000000000", "uTime": "1700003600000",
        }]

        # US-005 迁移后：CLI subprocess 边界已删，钉扎改打 HTTP 边界（律①/②），
        # 数据与旧版 fake_run 逐字同构——positions / orders-history 返回空列表。
        class _Resp:
            def __init__(self, payload: bytes) -> None: self._p = payload
            def read(self) -> bytes: return self._p
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_open(req, timeout=None):
            url = req.full_url
            body = {"code": "0", "data": hist if "positions-history" in url else []}
            return _Resp(json.dumps(body).encode())

        okx_runtime.freeze_environment({
            "R20_OKX_ENV": "demo",
            "OKX_DEMO_API_KEY": "AKD", "OKX_DEMO_SECRET_KEY": "SKD",
            "OKX_DEMO_PASSPHRASE": "PPD",
        })
        self.addCleanup(okx_runtime.unfreeze_environment)
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = os.path.join(tmp, "trading_ledger.json")
            with patch.object(okx_rest, "urlopen", fake_open), \
                 patch.object(sfl, "DATA_DIR", tmp), \
                 patch.object(sfl, "LEDGER_JSON_FILE", ledger_path), \
                 patch.object(sfl, "POSITION_TRACKER_FILE", os.path.join(tmp, "trackers.json")), \
                 patch.object(sfl, "INITIAL_STATE_FILE", os.path.join(tmp, "no_such_state.json")), \
                 patch.dict("sys.modules", {"qq_notifier": MagicMock()}):
                trades = sfl.build_lifecycle_ledger()
            with open(ledger_path, encoding="utf-8") as f:
                written = json.load(f)

        closed = [t for t in trades if t.get("status") == "closed"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["sz"], 2.0)                       # 不再恒为 0
        self.assertEqual(written[0]["sz"], 2.0)                      # 落盘同样真实
        self.assertEqual(closed[0]["margin"], 333.33)                # 2 * 0.01 * 50000 / 3


if __name__ == "__main__":
    unittest.main()
