# -*- coding: utf-8 -*-
"""保护缺口**端到端**可见性：外所腿被撤/缺失 ⇒ 一个缓存周期内就出现在 `data_health`。

## 这个门在守什么

第 18–26 刀把"保护判定"这条链一路修过来（跨所覆盖口径、整仓平腿、`cloud_oco_verified`
契约、净持仓方向、挂单时间…）。修完后有个**安全性质**必须钉住，否则前面全是空谈：

> 外所持仓的止损腿被**外部撤销**、或从来没挂上时，系统多久能发现？

答案必须是"**一个缓存周期内**"，而不是"等谁手动跑一次巡检"——因为
`R20_VENUE_PROTECTION_WATCHDOG` **默认关闭**（它会真的补挂腿，属实盘写操作，须人工开闸），
所以"发现"这件事只能靠**默认开启**的面板载荷路径：

    collect_cross_venue_positions（每周期都跑，腿本来就要取来展示）
      → scan_protective_orders 判定「无活止损腿」
      → source_errors.append(保护缺口 …)
      → cache_payload 的 data_health.errors / status=PARTIAL
      → 面板 DataStatus 显示

本门用**真实的 `update_cache_cycle()`** 走完整条链（不是只测单个函数），断言：
缺口进了 `data_health.errors`、状态翻 `PARTIAL`，且该持仓行在载荷里确实标成
`unprotected`。

⚠️ 边界（诚实）：**发现 ≠ 自动修复**。自动补挂腿是 watchdog 的事，默认关闭；
本门只保证"不会被无声吞掉"。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from r20_backend import dashboard_cache as app


class _Ad:
    """外所适配器：一笔持仓 + **零保护腿**（模拟"腿被外部撤销/从没挂上"）。"""

    def __init__(self, positions, algos=()):
        self._pos = positions
        self._algos = list(algos)

    def positions(self):
        return self._pos

    def open_orders(self):
        return []

    def list_protective_orders(self, *a, **k):
        return self._algos


_EMPTY = _Ad([], [])


def _run_cycle(adapter):
    bal = [{"details": [{"ccy": "USDT", "eq": "10000", "availBal": "9000",
                         "cashBal": "8000", "upl": "0"}]}]
    bills = [{"ts": 1789900000000, "subType": "5", "type": "2", "instId": "BTC-USDT-SWAP",
              "pnl": 1.0, "fee": -0.1, "balChg": 0.9, "sz": 1.0}]

    def fetch(fn, *a, **k):
        name = getattr(fn, "__name__", "")
        if name == "balances":
            return True, bal, ""
        if name == "bills":
            return True, bills, ""
        return True, [], ""

    local = {"adaptive_cfg": {}, "ai_history_list": [], "ai_last_prompt_text": "",
             "ai_memory_md_content": "", "disk_free_gb": 100.0,
             "factor_lib_snapshot": {}, "news_data": {}, "review_data": {},
             "snapshots_list": []}
    with patch.object(app, "_fetch_json", fetch), \
         patch.object(app, "load_position_trackers", lambda: {}), \
         patch.object(app, "_load_cross_venue_data", lambda: {}), \
         patch.object(app, "_load_local_factor_library", lambda: {}), \
         patch.object(app, "_build_factors_from_local_files", lambda p, ts: ([], {})), \
         patch.object(app, "build_ai_health", lambda ai: {}), \
         patch.object(app, "_core_load_ledger_lifecycle_trades", lambda *a, **k: ([], [])), \
         patch.object(app, "_core_load_local_reads", lambda *a, **k: dict(local)), \
         patch.object(app, "_core_collect_algo_protection", lambda *a, **k: None), \
         patch("r20_backend.exchanges.get_adapter",
               lambda v, *a, **k: adapter if v == "binance" else _EMPTY), \
         patch("r20_backend.dashboard_payload.multi_venue._global_env_axis", lambda: "demo"):
        app.CACHE_DATA = {}
        app.LAST_CACHE_TIME = 0.0
        app.update_cache_cycle()
    return app.CACHE_DATA


class ProtectionGapReachesDataHealthTest(unittest.TestCase):
    def setUp(self):
        # ⚠️ 本用例真调 `update_cache_cycle()` ⇒ **必须**进沙箱：否则它会写/删生产
        # `data/`（实测触发 `[tests] 提示：测试正在删除生产 data/ 下的
        # .llm_models.json-xxxx` —— 第 18/19 刀修过的同一类问题，`dashboard_cache`
        # 的大写路径常量与 `llm_manager` 都在 `isolate_config` 白名单里）。
        from tests import config_sandbox
        config_sandbox.isolate_config(self)
        self._cache = app.CACHE_DATA
        self._t = app.LAST_CACHE_TIME

    def tearDown(self):
        app.CACHE_DATA = self._cache
        app.LAST_CACHE_TIME = self._t

    def test_unprotected_position_is_visible_in_one_cycle(self):
        pos = [{"base": "UNI", "symbol": "UNIUSDT", "size_signed": -82.0, "side": "short",
                "entry_price": 9.0, "mark_price": 8.9, "leverage": 5}]
        data = _run_cycle(_Ad(pos, algos=[]))

        health = data["data_health"]
        self.assertEqual(health["status"], "PARTIAL",
                         "有保护缺口时载荷不得报 LIVE（否则面板看起来一切正常）")
        self.assertTrue(health["partial"])
        gaps = [e for e in health["errors"] if "没有活止损腿" in e]
        self.assertTrue(gaps, f"保护缺口必须进 data_health.errors，实际 {health['errors']}")
        self.assertIn("binance", gaps[0])
        self.assertIn("UNI-USDT-SWAP", gaps[0])

        rows = [p for p in data.get("positions", []) if p.get("venue") == "binance"]
        self.assertEqual(len(rows), 1, "外所持仓行本身也要在载荷里")
        self.assertEqual(rows[0]["protectionStatus"], "unprotected")
        self.assertEqual(rows[0]["protectionCoveragePct"], 0.0)

    def test_protected_position_keeps_payload_live(self):
        """有满量双腿 ⇒ 不报缺口、状态仍 LIVE（告警不能一直响）。"""
        pos = [{"base": "UNI", "symbol": "UNIUSDT", "size_signed": -82.0, "side": "short",
                "entry_price": 9.0, "mark_price": 8.9, "leverage": 5}]
        algos = [{"symbol": "UNIUSDT", "side": "buy", "type": "STOP_MARKET",
                  "raw": {"orderType": "STOP_MARKET", "triggerPrice": "9.5", "quantity": "82"}},
                 {"symbol": "UNIUSDT", "side": "buy", "type": "TAKE_PROFIT_MARKET",
                  "raw": {"orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "8.4", "quantity": "82"}}]
        data = _run_cycle(_Ad(pos, algos=algos))

        errs = [e for e in data["data_health"]["errors"] if "没有活止损腿" in e]
        self.assertEqual(errs, [], "已保护不得报缺口（永远在响的告警等于没有告警）")
        rows = [p for p in data.get("positions", []) if p.get("venue") == "binance"]
        self.assertEqual(rows[0]["protectionStatus"], "fully_protected")


if __name__ == "__main__":
    unittest.main()
