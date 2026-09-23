"""网关通道适配器与事件模型（`r20_gateway/channels.py` & `events.py`）残余分支收口测试 —— 第 377 刀。

本测试针对网关消息分发与事件模型：
- 通道适配器向底层连接器发送与结果结构化（`NotificationChannelAdapter.send` 调用 `send_channel` 并返回 `DeliveryResult`）；
- 网关事件不可变对象与字典导出（`GatewayEvent.as_dict` 包含自动生成的唯一事件 ID 与格式化时间戳）。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from r20_gateway.channels import DeliveryResult, NotificationChannelAdapter
from r20_gateway.events import GatewayEvent


class GatewayChannelsAndEventsTailsTests(unittest.TestCase):
    def test_notification_channel_adapter_send(self):
        # 验证通道适配器发送消息并包装 DeliveryResult 返回
        adapter = NotificationChannelAdapter("wechat")
        self.assertEqual(adapter.channel_id, "wechat")

        with patch("r20_gateway.channels.send_channel", return_value=(True, "ok_delivered")):
            res = adapter.send("risk alert message")

        self.assertIsInstance(res, DeliveryResult)
        self.assertTrue(res.success)
        self.assertEqual(res.detail, "ok_delivered")
        self.assertEqual(res.status, "delivered")

    def test_gateway_event_as_dict(self):
        # 验证网关事件模型序列化
        event = GatewayEvent(
            event_type="order.placed",
            title="新订单",
            message="开多 BTC 100 张",
            priority=80,
            payload={"order_id": "ord_123"},
        )
        data = event.as_dict()
        self.assertEqual(data["event_type"], "order.placed")
        self.assertEqual(data["title"], "新订单")
        self.assertEqual(data["message"], "开多 BTC 100 张")
        self.assertEqual(data["priority"], 80)
        self.assertEqual(data["payload"], {"order_id": "ord_123"})
        self.assertTrue(bool(data["event_id"]))
        self.assertTrue(bool(data["created_at"]))


if __name__ == "__main__":
    unittest.main()
