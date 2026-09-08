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


if __name__ == "__main__":
    unittest.main()
