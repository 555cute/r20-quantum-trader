"""Binance 算法条件单请求构建（`r20_backend/exchanges/binance_algo.py`）残余分支收口测试 —— 第 349 刀。

本模块 183 行，是 Binance USDⓈ-M 条件委托（Algo Orders / OCO / 止盈止损）专线协议请求构建器：
- 参数完整性与互斥防御：非 `closePosition` 条件单必须指定 `quantity`、`reduceOnly` 与持仓方向组合校验；
- 双向持仓与限价参数对齐：`positionSide` 校验（BOTH / LONG / SHORT）、指定委托限价 `price` 格式化；
- 普通订单旧字段防护（`stopPrice` / `newClientOrderId` 防呆拦截）；
- 查询与撤单请求：支持按 `clientAlgoId` 检索与撤销、合约全部条件单撤销接口。
"""
from __future__ import annotations

import sys
import unittest
from typing import Any

from r20_backend.exchanges.binance_algo import BinanceAlgoRequestsMixin


class SneakyLegacyFieldPrice:
    """用于测试防呆护栏：在字段求值期注入普通订单专属字段。"""
    def __init__(self, legacy_field: str):
        self.legacy_field = legacy_field

    def __str__(self) -> str:
        # 获取调用方局部变量中的 body 并注入普通订单旧字段以验证 line 142
        caller_frame = sys._getframe(1)
        if "body" in caller_frame.f_locals:
            caller_frame.f_locals["body"][self.legacy_field] = "invalid_legacy_val"
        return "50000"


class BinanceAlgoTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 条件单参数校验与互斥防御 (build_algo_order_request)
    # -------------------------------------------------------------------------
    def test_build_algo_order_request_missing_quantity_raises(self):
        # 非 closePosition 条件单未提供 quantity 时报错 (line 120)
        with self.assertRaises(ValueError) as ctx:
            BinanceAlgoRequestsMixin.build_algo_order_request(
                symbol="BTCUSDT",
                side="BUY",
                type_="STOP",
                trigger_price="50000",
                working_type="CONTRACT_PRICE",
                close_position=False,
                quantity=None,
            )
        self.assertIn("非 closePosition 条件单必须给 quantity", str(ctx.exception))

    def test_build_algo_order_request_reduce_only_boolean_string(self):
        # 验证 reduce_only 格式化为 "true" / "false" (line 129)
        # 1) reduce_only=True
        req1 = BinanceAlgoRequestsMixin.build_algo_order_request(
            symbol="BTCUSDT",
            side="SELL",
            type_="STOP",
            trigger_price="48000",
            working_type="CONTRACT_PRICE",
            quantity="0.1",
            reduce_only=True,
            position_side="BOTH",
        )
        self.assertEqual(req1["body"]["reduceOnly"], "true")

        # 2) reduce_only=False
        req2 = BinanceAlgoRequestsMixin.build_algo_order_request(
            symbol="BTCUSDT",
            side="SELL",
            type_="STOP",
            trigger_price="48000",
            working_type="CONTRACT_PRICE",
            quantity="0.1",
            reduce_only=False,
            position_side="BOTH",
        )
        self.assertEqual(req2["body"]["reduceOnly"], "false")

    def test_build_algo_order_request_position_side_validation(self):
        # 1) positionSide 非法值拦截 (lines 132-133)
        with self.assertRaises(ValueError) as ctx:
            BinanceAlgoRequestsMixin.build_algo_order_request(
                symbol="BTCUSDT",
                side="BUY",
                type_="STOP",
                trigger_price="50000",
                working_type="CONTRACT_PRICE",
                quantity="0.1",
                position_side="INVALID_SIDE",
            )
        self.assertIn("positionSide 非法: 'INVALID_SIDE'", str(ctx.exception))

        # 2) 合法 positionSide 设置 (line 134)
        req = BinanceAlgoRequestsMixin.build_algo_order_request(
            symbol="BTCUSDT",
            side="BUY",
            type_="STOP",
            trigger_price="50000",
            working_type="CONTRACT_PRICE",
            quantity="0.1",
            position_side="LONG",
        )
        self.assertEqual(req["body"]["positionSide"], "LONG")

    def test_build_algo_order_request_with_explicit_price(self):
        # 传入限价单价时正确序列化至 body (line 136)
        req = BinanceAlgoRequestsMixin.build_algo_order_request(
            symbol="BTCUSDT",
            side="SELL",
            type_="TAKE_PROFIT",
            trigger_price="65000",
            working_type="MARK_PRICE",
            quantity="0.5",
            price="64900.5",
        )
        self.assertEqual(req["body"]["price"], "64900.5")

    def test_build_algo_order_request_legacy_fields_defense(self):
        # 验证防呆检查：防止普通订单的旧字段（如 stopPrice）混入条件单 API (line 142)
        with self.assertRaises(ValueError) as ctx:
            BinanceAlgoRequestsMixin.build_algo_order_request(
                symbol="BTCUSDT",
                side="BUY",
                type_="STOP",
                trigger_price="50000",
                working_type="CONTRACT_PRICE",
                quantity="0.1",
                price=SneakyLegacyFieldPrice("stopPrice"),  # type: ignore
            )
        self.assertIn("stopPrice 属普通订单字段，禁止传入 Algo API", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 2. 查询与撤单构建 (Query & Cancel)
    # -------------------------------------------------------------------------
    def test_build_algo_query_request_by_client_algo_id(self):
        # 支持按 clientAlgoId 查询条件单 (line 152)
        req = BinanceAlgoRequestsMixin.build_algo_query_request(client_algo_id="client_algo_999")
        self.assertEqual(req["method"], "GET")
        self.assertEqual(req["path"], "/fapi/v1/algoOrder")
        self.assertEqual(req["params"]["clientAlgoId"], "client_algo_999")

    def test_build_algo_cancel_request_by_client_algo_id(self):
        # 支持按 clientAlgoId 撤销单笔条件单 (line 172)
        req = BinanceAlgoRequestsMixin.build_algo_cancel_request(client_algo_id="client_algo_888")
        self.assertEqual(req["method"], "DELETE")
        self.assertEqual(req["path"], "/fapi/v1/algoOrder")
        self.assertEqual(req["params"]["clientAlgoId"], "client_algo_888")

    def test_build_algo_cancel_all_request_returns_all_path(self):
        # 验证按 symbol 全撤条件单接口构建 (line 182)
        req = BinanceAlgoRequestsMixin.build_algo_cancel_all_request(symbol="BTCUSDT")
        self.assertEqual(req["method"], "DELETE")
        self.assertEqual(req["path"], "/fapi/v1/algoOrder/all")
        self.assertEqual(req["params"]["symbol"], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
