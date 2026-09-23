"""策略拦截器管理路由（`r20_backend/routers/strategy/interceptors.py`）残余分支收口测试 —— 第 373 刀。

本模块 93 行，负责策略风控拦截器动态挂载、代码保存、启停切换与执行审计：
- 拦截器创建成功正常返回（`admin_create_interceptor` 在 `create_plugin` 成功时记录审计并返回创建结果）。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from r20_backend.routers.strategy.interceptors import (
    InterceptorCreateRequest,
    admin_create_interceptor,
)


class RouterStrategyInterceptorsTailsTests(unittest.TestCase):
    def test_admin_create_interceptor_success(self):
        # 验证拦截器创建成功分支记录审计并返回插件创建元信息 (lines 61-63)
        req = InterceptorCreateRequest(filename="my_guard.py", code="def intercept(): pass")
        with patch("r20_backend.routers.strategy.interceptors.require_superadmin", return_value={"username": "superadmin"}), \
             patch("r20_backend.routers.strategy.interceptors.audit_record") as mock_audit, \
             patch("r20_backend.interceptor_manager.create_plugin", return_value={"ok": True, "filename": "my_guard.py"}):
            res = admin_create_interceptor(req, x_r20_session="valid_token")

        self.assertEqual(res, {"ok": True, "filename": "my_guard.py"})
        mock_audit.assert_called_once_with("interceptor.create", "success", {"actor": "superadmin", "filename": "my_guard.py"})


if __name__ == "__main__":
    unittest.main()
