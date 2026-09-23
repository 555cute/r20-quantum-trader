"""多所路由决策选择（`r20_backend/venue_routing/selection.py`）残余分支收口测试 —— 第 375 刀。

本模块 258 行，负责智能跨所路由决策、硬筛门禁（上市校验/准入币种池/名义额）与综合打分：
- 准入币种池读取异常容错（`_venue_pool_assets` 异常时告警并安全返回 `None` 不淘汰）；
- 准入币种池非空标准化（`_venue_pool_assets` 成功提取并大写去重清洗币种清单）；
- 准入币种池硬筛拦截（`_hard_filters` 校验标的不在目标交易所准入币种池时硬筛淘汰）。
"""
from __future__ import annotations

import unittest
import warnings
from unittest.mock import MagicMock, patch

from r20_backend.venue_routing.selection import (
    _hard_filters,
    _venue_pool_assets,
)


class VenueRoutingSelectionTailsTests(unittest.TestCase):
    def test_venue_pool_assets_read_exception_handled(self):
        # 读取准入池抛异常时发出 RuntimeWarning 并返回 None (lines 76-81)
        with patch("r20_backend.exchanges.routing_policy.load_venue_pool", side_effect=OSError("disk read fail")):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                res = _venue_pool_assets("binance")
                self.assertIsNone(res)
                self.assertTrue(any("准入币种池不可读" in str(item.message) for item in w))

    def test_venue_pool_assets_valid_cleaned(self):
        # 正常清洗大写币种列表 (lines 82-85)
        with patch("r20_backend.exchanges.routing_policy.load_venue_pool", return_value={"assets": [" btc ", "eth", ""]}):
            res = _venue_pool_assets("binance")
            self.assertEqual(res, ["BTC", "ETH"])

    def test_hard_filters_rejects_asset_not_in_venue_pool(self):
        # 候选所准入币种池非空且标的不在其中时硬淘汰 (lines 156-160)
        with patch("r20_backend.exchanges.routing_policy.load_venue_pool", return_value={"assets": ["BTC", "ETH"]}):
            cand = {"venue": "binance", "symbol": "SOL-USDT", "executable": True}
            signal = {"inst_id": "SOL-USDT", "size_usdt": 100, "price": 150}
            fails = _hard_filters(signal, cand, cfg=MagicMock(), budget_view=MagicMock())
            self.assertTrue(any("不在 BINANCE 准入币种清单" in f for f in fails))


if __name__ == "__main__":
    unittest.main()
