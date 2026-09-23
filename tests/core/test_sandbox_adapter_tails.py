"""沙箱交易所适配器（`r20_backend/sandbox/adapter.py`）残余分支收口测试 —— 第 337 刀。

本模块 466 行，是全真沙箱回测与点在时间（Point-In-Time）模拟执行的核心：
- 撮合引擎：挂单限价匹配、买卖双向滑点成交、仓位合并与均价推算；
- 风控系统：止盈（TP）与止损（SL）触发检测、未实现盈亏（UPL）按标记价格实时重算；
- 订单与仓位管理：快速平仓、订单撤销、保护单生命周期管理；
- 行情接口：严格无未来数据的点在时间 K 线快照、深度盘口生成与费率模拟。
"""
from __future__ import annotations

import unittest

from r20_backend.sandbox.adapter import SandboxExchangeAdapter


class SandboxAdapterTailsTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SandboxExchangeAdapter(initial_balance=10000.0, slippage=0.001)

    # -------------------------------------------------------------------------
    # 1. 撮合与挂单 (Match Open Orders & Execution)
    # -------------------------------------------------------------------------
    def test_open_order_with_missing_candle_is_skipped(self):
        # 挂 ETH 限价买单，但当前 step 只注入 BTC 行情 -> ETH 挂单保持 pending
        self.adapter.place_order("ETH", "buy", contracts=1.0, price=2000.0, order_type="limit")
        self.assertEqual(len(self.adapter.open_orders()), 1)
        self.adapter.step({"BTC": {"open": 50000, "high": 50100, "low": 49900, "close": 50000, "ts_ms": 1000}})
        self.assertEqual(len(self.adapter.open_orders()), 1)
        self.assertEqual(len(self.adapter.positions()), 0)

    def test_limit_sell_order_execution_above_open(self):
        # 卖单：high >= limit_px 触发成交
        # 情况 A: open <= limit_px -> fill_px 取 limit_px
        self.adapter.place_order("BTC", "sell", contracts=0.5, price=50500.0, order_type="limit")
        self.adapter.step({"BTC": {"open": 50000, "high": 51000, "low": 49800, "close": 50800, "ts_ms": 1000}})
        self.assertEqual(len(self.adapter.open_orders()), 0)
        positions = self.adapter.positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["posSide"], "short")
        self.assertEqual(positions[0]["entry_price"], 50500.0)

    def test_limit_sell_order_execution_with_gap_up_open(self):
        # 卖单：开盘跳空高开 open > limit_px -> fill_px 取 open
        self.adapter.place_order("BTC", "sell", contracts=0.5, price=50500.0, order_type="limit")
        self.adapter.step({"BTC": {"open": 51200, "high": 51500, "low": 50400, "close": 51000, "ts_ms": 2000}})
        self.assertEqual(len(self.adapter.open_orders()), 0)
        positions = self.adapter.positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["entry_price"], 51200.0)

    def test_position_accumulation_averages_entry_price_long(self):
        # 多头加仓：已有 1.0 @ 100，再买入 2.0 @ 130 -> 均价 120.0，数量 3.0
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="market")
        pos = self.adapter.positions()[0]
        self.assertEqual(pos["amount"], 1.0)
        self.assertEqual(pos["entry_price"], 100.0 * (1.0 + self.adapter.slippage))

        # 再以市价买入 1.0，当前价 130
        self.adapter.step({"BTC": {"open": 130, "high": 135, "low": 125, "close": 130, "ts_ms": 2000}})
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=130.0, order_type="market")
        pos2 = self.adapter.positions()[0]
        self.assertEqual(pos2["amount"], 2.0)
        self.assertEqual(pos2["size_signed"], 2.0)
        expected_entry = ((1.0 * (100.0 * 1.001)) + (1.0 * (130.0 * 1.001))) / 2.0
        self.assertAlmostEqual(pos2["entry_price"], expected_entry, places=4)

    def test_position_accumulation_averages_entry_price_short(self):
        # 空头加仓：已有 1.0 @ 100，再卖出 1.0 @ 80 -> 均价计算与 size_signed 为负
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "sell", contracts=1.0, price=100.0, order_type="market")
        pos = self.adapter.positions()[0]
        self.assertEqual(pos["posSide"], "short")
        self.assertEqual(pos["size_signed"], -1.0)

        self.adapter.step({"BTC": {"open": 80, "high": 85, "low": 75, "close": 80, "ts_ms": 2000}})
        self.adapter.place_order("BTC", "sell", contracts=1.0, price=80.0, order_type="market")
        pos2 = self.adapter.positions()[0]
        self.assertEqual(pos2["amount"], 2.0)
        self.assertEqual(pos2["size_signed"], -2.0)
        expected_entry = ((1.0 * (100.0 * 0.999)) + (1.0 * (80.0 * 0.999))) / 2.0
        self.assertAlmostEqual(pos2["entry_price"], expected_entry, places=4)

    # -------------------------------------------------------------------------
    # 2. 保护单与风控检测 (Protective Triggers: SL/TP)
    # -------------------------------------------------------------------------
    def test_protective_triggers_skipped_when_candle_missing(self):
        # 持有 BTC 仓位，但行情字典为空 -> 保护检测正常跳过不崩溃
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="market")
        self.assertEqual(len(self.adapter.positions()), 1)
        # 清空当前行情
        self.adapter._current_candles.clear()
        self.adapter._check_protective_triggers()
        self.assertEqual(len(self.adapter.positions()), 1)

    def test_short_position_unrealized_pnl_calculation(self):
        # 空头持仓未实现盈亏 = (entry_px - cur_px) * amt
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "sell", contracts=2.0, price=100.0, order_type="market")
        # 币价下跌到 80 -> 空头盈利
        self.adapter.step({"BTC": {"open": 80, "high": 85, "low": 75, "close": 80, "ts_ms": 2000}})
        pos = self.adapter.positions()[0]
        entry = pos["entry_price"]
        expected_upl = round((entry - 80.0) * 2.0, 4)
        self.assertEqual(pos["unrealized_pnl"], expected_upl)
        self.assertEqual(pos["upl"], expected_upl)
        self.assertEqual(pos["mark_price"], 80.0)

    def test_protective_triggers_algo_order_filtering(self):
        # 针对不匹配的 base、pos_side 或已不是 live 状态的 algo 单，予以忽略
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="market")
        
        # 1) 不同 base (ETH)
        self.adapter.attach_protective_orders("ETH", "long", sl_px=90.0)
        # 2) 不同 pos_side (short)
        self.adapter.attach_protective_orders("BTC", "short", sl_px=110.0)
        # 3) 已完成状态 (state != live)
        res = self.adapter.attach_protective_orders("BTC", "long", sl_px=95.0)
        self.adapter._algo_orders[res["algo_id"]]["state"] = "cancelled"

        # 价格跌至 90，上述三个 algo 都不能触发 BTC 多头平仓
        self.adapter.step({"BTC": {"open": 92, "high": 93, "low": 89, "close": 90, "ts_ms": 2000}})
        self.assertEqual(len(self.adapter.positions()), 1)

    def test_long_stop_loss_trigger(self):
        # 多头止损：low <= sl_px 触发，以 sl_px * (1.0 - slippage) 平仓
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="market")
        self.adapter.attach_protective_orders("BTC", "long", sl_px=92.0)

        # Bar low 击穿 92.0 -> 止损平仓
        self.adapter.step({"BTC": {"open": 98, "high": 99, "low": 91.0, "close": 94, "ts_ms": 2000}})
        self.assertEqual(len(self.adapter.positions()), 0)
        last_trade = self.adapter._trades[-1]
        self.assertAlmostEqual(last_trade["exit_price"], 92.0 * (1.0 - self.adapter.slippage), places=4)

    def test_short_stop_loss_trigger(self):
        # 空头止损：high >= sl_px 触发，以 sl_px * (1.0 + slippage) 平仓
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "sell", contracts=1.0, price=100.0, order_type="market")
        self.adapter.attach_protective_orders("BTC", "short", sl_px=110.0)

        # Bar high 冲破 110.0 -> 止损平仓
        self.adapter.step({"BTC": {"open": 102, "high": 112.0, "low": 101, "close": 108, "ts_ms": 2000}})
        self.assertEqual(len(self.adapter.positions()), 0)
        last_trade = self.adapter._trades[-1]
        self.assertAlmostEqual(last_trade["exit_price"], 110.0 * (1.0 + self.adapter.slippage), places=4)

    def test_short_take_profit_trigger(self):
        # 空头止盈：low <= tp_px 触发，以 tp_px * (1.0 + slippage) 平仓
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "sell", contracts=1.0, price=100.0, order_type="market")
        self.adapter.attach_protective_orders("BTC", "short", tp_px=85.0)

        # Bar low 跌至 85.0 -> 止盈平仓
        self.adapter.step({"BTC": {"open": 90, "high": 92, "low": 84.0, "close": 86, "ts_ms": 2000}})
        self.assertEqual(len(self.adapter.positions()), 0)
        last_trade = self.adapter._trades[-1]
        self.assertAlmostEqual(last_trade["exit_price"], 85.0 * (1.0 + self.adapter.slippage), places=4)

    def test_close_non_existent_virtual_position_returns_empty(self):
        # 尝试关闭不存在的 pos_key -> 返回 {}
        res = self.adapter._close_virtual_position("NOTEXIST:long", 100.0)
        self.assertEqual(res, {})

    # -------------------------------------------------------------------------
    # 3. 市场行情与合约规格 (Market Data & Instrument Spec)
    # -------------------------------------------------------------------------
    def test_fetch_ticker_returns_none_when_candle_missing(self):
        self.assertIsNone(self.adapter.fetch_ticker("SOL"))

    def test_fetch_ticker_returns_complete_fields(self):
        sim_ts = self.adapter.sim_time_ms + 5000
        self.adapter.step({"BTC": {"open": 100, "high": 110, "low": 90, "close": 105, "volume": 500, "ts_ms": sim_ts}})
        ticker = self.adapter.fetch_ticker("BTC-USDT-SWAP")
        self.assertIsNotNone(ticker)
        self.assertEqual(ticker["venue"], "sandbox")
        self.assertEqual(ticker["last"], 105.0)
        self.assertEqual(ticker["mark_price"], 105.0)
        self.assertAlmostEqual(ticker["bid"], 105.0 * 0.9998, places=4)
        self.assertAlmostEqual(ticker["ask"], 105.0 * 1.0002, places=4)
        self.assertEqual(ticker["open_24h"], 100.0)
        self.assertEqual(ticker["high_24h"], 110.0)
        self.assertEqual(ticker["low_24h"], 90.0)
        self.assertEqual(ticker["vol_24h_base"], 500.0)
        self.assertEqual(ticker["quote_vol_24h"], 500.0 * 105.0)
        self.assertEqual(ticker["ts_ms"], sim_ts)

    def test_fetch_candles_fallback_to_current_candle(self):
        # 没有 historical candles 时，若当前有 candle 则返回单条
        sim_ts = self.adapter.sim_time_ms + 5000
        self.adapter.step({"BTC": {"open": 100, "high": 110, "low": 90, "close": 105, "volume": 500, "ts_ms": sim_ts}})
        candles = self.adapter.fetch_candles("BTC", bar="15m")
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0], [sim_ts, 100, 110, 90, 105, 500])

    def test_fetch_candles_fallback_when_current_candle_also_missing(self):
        # 既无 historical candles 也无 current candles
        self.assertIsNone(self.adapter.fetch_candles("SOL"))

    def test_fetch_candles_filters_out_all_future_candles(self):
        # 所有历史 K 线的时间戳均晚于当前仿真时钟 sim_time_ms -> 返回 None
        candles = [
            [2000, 100.0, 105.0, 99.0, 102.0, 10.0],
            [3000, 102.0, 108.0, 101.0, 106.0, 15.0],
        ]
        self.adapter.set_historical_candles("BTC", "15m", candles)
        self.adapter.sim_time_ms = 1000  # 早于所有 K 线
        self.assertIsNone(self.adapter.fetch_candles("BTC", bar="15m"))

    def test_fetch_orderbook_when_ticker_missing(self):
        self.assertIsNone(self.adapter.fetch_orderbook("BTC"))

    def test_fetch_orderbook_generates_synthetic_depth(self):
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        ob = self.adapter.fetch_orderbook("BTC", depth=5)
        self.assertIsNotNone(ob)
        self.assertEqual(ob["venue"], "sandbox")
        self.assertEqual(len(ob["bids"]), 5)
        self.assertEqual(len(ob["asks"]), 5)
        # 买单第一档递减，卖单第一档递增
        self.assertLess(ob["bids"][0][0], 100.0)
        self.assertGreater(ob["asks"][0][0], 100.0)

    def test_fetch_funding_rate_and_top_trader_ratio(self):
        self.assertEqual(self.adapter.fetch_funding_rate("BTC"), 0.0001)
        self.assertEqual(self.adapter.fetch_top_trader_ratio("BTC"), 1.85)

    def test_load_spec(self):
        spec = self.adapter._load_spec("BTC-USDT-SWAP")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.venue, "sandbox")
        self.assertEqual(spec.base, "BTC")
        self.assertEqual(spec.tick_size, 0.1)
        self.assertEqual(spec.step_size, 0.01)
        self.assertEqual(spec.max_leverage, 20.0)
        self.assertEqual(spec.status, "trading")

    # -------------------------------------------------------------------------
    # 4. 订单与仓位管理 (Order & Position Management)
    # -------------------------------------------------------------------------
    def test_open_orders_filtering_by_symbol(self):
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="limit")
        self.adapter.place_order("ETH", "buy", contracts=2.0, price=2000.0, order_type="limit")
        btc_orders = self.adapter.open_orders("BTC")
        eth_orders = self.adapter.open_orders("ETH-USDT-SWAP")
        all_orders = self.adapter.open_orders()
        self.assertEqual(len(btc_orders), 1)
        self.assertEqual(btc_orders[0]["base"], "BTC")
        self.assertEqual(len(eth_orders), 1)
        self.assertEqual(eth_orders[0]["base"], "ETH")
        self.assertEqual(len(all_orders), 2)

    def test_cancel_order_by_order_id(self):
        res = self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="limit")
        ord_id = res["order_id"]
        # 取消成功
        cancel_res = self.adapter.cancel_order("BTC", order_id=ord_id)
        self.assertTrue(cancel_res["cancelled"])
        self.assertEqual(cancel_res["order_id"], ord_id)
        self.assertEqual(len(self.adapter.open_orders()), 0)

        # 重复取消 -> not found
        cancel_res2 = self.adapter.cancel_order("BTC", order_id=ord_id)
        self.assertFalse(cancel_res2["cancelled"])
        self.assertEqual(cancel_res2["reason"], "not found")

    def test_cancel_order_by_client_order_id(self):
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="limit", client_order_id="my_cl_123")
        cancel_res = self.adapter.cancel_order("BTC", client_order_id="my_cl_123")
        self.assertTrue(cancel_res["cancelled"])
        self.assertEqual(len(self.adapter.open_orders()), 0)

        # 错误 client_order_id
        cancel_res2 = self.adapter.cancel_order("BTC", client_order_id="not_exist")
        self.assertFalse(cancel_res2["cancelled"])

    def test_list_protective_orders_without_symbol_returns_all(self):
        self.adapter.attach_protective_orders("BTC", "long", sl_px=90.0)
        self.adapter.attach_protective_orders("ETH", "short", tp_px=1800.0)
        all_algos = self.adapter.list_protective_orders()
        self.assertEqual(len(all_algos), 2)

    def test_fast_close_position_raises_when_no_position(self):
        with self.assertRaises(ValueError) as ctx:
            self.adapter.fast_close_position("BTC")
        self.assertIn("未找到 BTC 的活动沙箱持仓", str(ctx.exception))

    def test_fast_close_position_success(self):
        self.adapter.step({"BTC": {"open": 100, "high": 105, "low": 95, "close": 100, "ts_ms": 1000}})
        self.adapter.place_order("BTC", "buy", contracts=1.0, price=100.0, order_type="market")
        self.assertEqual(len(self.adapter.positions()), 1)

        # 当前价格变为 120 时市价快平
        self.adapter.step({"BTC": {"open": 120, "high": 125, "low": 115, "close": 120, "ts_ms": 2000}})
        close_res = self.adapter.fast_close_position("BTC")
        self.assertEqual(close_res["venue"], "sandbox")
        self.assertEqual(close_res["symbol"], "BTC-USDT-SWAP")
        self.assertEqual(len(close_res["result"]), 1)
        self.assertEqual(len(self.adapter.positions()), 0)
        self.assertEqual(close_res["result"][0]["exit_price"], 120.0)


if __name__ == "__main__":
    unittest.main()
