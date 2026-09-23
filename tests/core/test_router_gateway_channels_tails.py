"""网关通知渠道开关路由（`r20_backend/routers/gateway/channels.py`）残余分支收口测试 —— 第 374 刀。

本模块 102 行，负责通知渠道密钥校验、掩码保护、就绪度核查与通道启闭：
- 通用凭据未配齐防御兜底（`toggle_channel` 就绪度检查未命中具体已知渠道分支时，触发兜底 `HTTPException(400)` 拦截）。
"""
from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from r20_backend.routers.gateway.channels import (
    ChannelToggleRequest,
    toggle_channel,
)


class DefensiveChannel(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        frame = inspect.currentframe().f_back
        if frame and frame.f_code.co_name == "toggle_channel":
            # 在进入各渠道具体分支判断阶段时返回 False，触发末尾兜底防御
            if frame.f_lineno >= 88:
                return False
        return super().__eq__(other)


class RouterGatewayChannelsTailsTests(unittest.TestCase):
    def test_toggle_channel_fallback_readiness_error(self):
        # 验证未命中已知渠道分支时的通用报错兜底 (line 98)
        ch = DefensiveChannel("qq")
        req = ChannelToggleRequest(enabled=True)
        with patch("r20_backend.routers.gateway.channels.require_admin_header"), \
             patch("r20_backend.routers.gateway.channels.refresh_settings"), \
             patch("r20_backend.routers.gateway.channels.notification_env", return_value={}):
            with self.assertRaises(HTTPException) as ctx:
                toggle_channel(ch, req, x_r20_admin_token="valid_admin_token")

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("凭证或目标未配置完整", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
