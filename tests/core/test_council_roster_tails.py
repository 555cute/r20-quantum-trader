"""投委会席位花名册与模型绑定（`r20_backend/council/roster.py`）残余分支收口测试 —— 第 356 刀。

本模块 146 行，是投委会席位模型绑定校验、席位健康度核验与超时区间夹取核心：
- 席位模型健康度（`seat_model_health`）：模型库读取异常自愈、非字典角色条目跳过；
- 席位可调用模型解析（`resolve_seat_model`）：模型配置读取异常显式暴露与兜底结构返回；
- 席位模型绑定写闸（`validate_seat_model_bindings`）：非字典角色条目跳过；
- 角色结构校验（`validate_council_roles`）：空/非字典配置拦截报错（"没有任何席位配置"）；
- 超时区间截断（`clamp_council_timeout`）：非数值非法参数回退默认超时。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from r20_backend.council.roster import (
    DEFAULT_COUNCIL_TIMEOUT,
    clamp_council_timeout,
    resolve_seat_model,
    seat_model_health,
    validate_council_roles,
    validate_seat_model_bindings,
)


class CouncilRosterTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 席位健康度与模型解析 (seat_model_health & resolve_seat_model)
    # -------------------------------------------------------------------------
    def test_seat_model_health_config_exception_and_non_dict_role(self):
        # 模型配置读取抛异常时回退空集合 (line 28)；非字典角色自动跳过 (line 32)
        with patch("r20_backend.llm_manager.load_llm_config", side_effect=RuntimeError("db error")):
            roles = {
                "invalid_role": "not_a_dict",
                "risk_controller": {"model_id": "claude-3-5-sonnet"},
            }
            health = seat_model_health(roles)
            self.assertEqual(len(health), 1)
            self.assertEqual(health[0]["role_id"], "risk_controller")
            self.assertEqual(health[0]["mode"], "missing")
            self.assertFalse(health[0]["registered"])

    def test_resolve_seat_model_config_exception_returns_explicit_failure(self):
        # 模型配置读取异常时显式返回失败结构而不静默崩溃 (lines 54-57)
        with patch("r20_backend.llm_manager.load_llm_config", side_effect=RuntimeError("config broken")):
            res = resolve_seat_model({"model_id": "gpt-4o", "reasoning_effort": "high"})
            self.assertEqual(res["model"], "")
            self.assertFalse(res["registered"])
            self.assertTrue(res["fallback"])
            self.assertIn("模型库读取失败：config broken", res["reason"])
            self.assertEqual(res["effort"], "high")

    # -------------------------------------------------------------------------
    # 2. 绑定校验与席位完整性 (validate_seat_model_bindings & validate_council_roles)
    # -------------------------------------------------------------------------
    def test_validate_seat_model_bindings_skips_non_dict_role(self):
        # 非字典形式的角色条目在校验中安全跳过 (line 109)
        with patch("r20_backend.llm_manager.load_llm_config", return_value={"models": [{"id": "m1"}]}):
            roles = {
                "bad_entry": None,
                "role1": {"model_id": "m1"},
            }
            problems = validate_seat_model_bindings(roles)
            self.assertEqual(problems, [])

    def test_validate_council_roles_empty_or_none(self):
        # roles 为空或非字典时返回 "没有任何席位配置" (lines 128-129)
        self.assertEqual(validate_council_roles({}), "没有任何席位配置")
        self.assertEqual(validate_council_roles(None), "没有任何席位配置")
        self.assertEqual(validate_council_roles([]), "没有任何席位配置")

    # -------------------------------------------------------------------------
    # 3. 超时区间安全限制 (clamp_council_timeout)
    # -------------------------------------------------------------------------
    def test_clamp_council_timeout_non_numeric_returns_default(self):
        # 无法转为浮点数的非数值参数安全回退为 DEFAULT_COUNCIL_TIMEOUT (lines 142-143)
        self.assertEqual(clamp_council_timeout("not-a-number"), DEFAULT_COUNCIL_TIMEOUT)
        self.assertEqual(clamp_council_timeout(None), DEFAULT_COUNCIL_TIMEOUT)
        self.assertEqual(clamp_council_timeout([]), DEFAULT_COUNCIL_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
