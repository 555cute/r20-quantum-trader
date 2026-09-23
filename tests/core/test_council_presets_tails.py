"""投委会预设套件与角色模板复位（`r20_backend/council/presets.py`）残余分支收口测试 —— 第 355 刀。

本模块 65 行，是多角色 AI 投委会预设模板与套件复位装配核心：
- 可用预设列表提取（`get_available_presets`）：返回系统全量内置预设列表；
- 预设套件应用容错（`apply_preset_suite`）：未知 `suite_id` 安全回退首选默认套件并保留原模型；
- 角色模板复位容错（`reset_role_template`）：
  - 角色 ID 不存在时报错；
  - 仲裁者/CIO 角色模板回退默认 `cio` 出厂模板；
  - 自定义无出厂模板角色拦截报错。
"""
from __future__ import annotations

import unittest

from r20_backend.council.presets import (
    apply_preset_suite,
    get_available_presets,
    reset_role_template,
)


class CouncilPresetsTailsTests(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. 预设列表与套件回退
    # -------------------------------------------------------------------------
    def test_get_available_presets_returns_list(self):
        # 覆盖 line 21
        presets = get_available_presets()
        self.assertIsInstance(presets, list)
        self.assertGreater(len(presets), 0)

    def test_apply_preset_suite_unknown_suite_falls_back_to_default(self):
        # 未知 suite_id 时回退为首个预设套件 (lines 30-31)
        config = {"roles": {"risk_controller": {"model_id": "gpt-4o"}}}
        saved_holder = {}

        def mock_save(c):
            saved_holder.update(c)
            return c

        res = apply_preset_suite(lambda: config, mock_save, "non_existent_suite_id")
        self.assertIn("consensus_mode", res)
        self.assertEqual(saved_holder, res)

    # -------------------------------------------------------------------------
    # 2. 角色模板复位容错
    # -------------------------------------------------------------------------
    def test_reset_role_template_missing_role_raises_value_error(self):
        # 角色不存在时报错 (lines 50-51)
        with self.assertRaises(ValueError) as ctx:
            reset_role_template(lambda: {"roles": {}}, lambda c: c, "missing_role_id")
        self.assertIn("未找到角色 ID: missing_role_id", str(ctx.exception))

    def test_reset_role_template_arbitrator_or_cio_fallback(self):
        # 仲裁者或 CIO 角色回退默认出厂模板 (lines 55-56)
        config = {
            "roles": {
                "arbitrator": {"model_id": "claude-3-opus", "is_arbitrator": True, "name": "Old Custom Name"}
            }
        }
        res = reset_role_template(lambda: config, lambda c: c, "arbitrator")
        role = res["roles"]["arbitrator"]
        self.assertEqual(role["model_id"], "claude-3-opus")
        self.assertIn("首席投资官", role["name"])

    def test_reset_role_template_custom_role_without_preset_raises_value_error(self):
        # 自定义无出厂模板的角色无法复位，报错 (line 58)
        config = {"roles": {"custom_bot_99": {"model_id": "deepseek-v3"}}}
        with self.assertRaises(ValueError) as ctx:
            reset_role_template(lambda: config, lambda c: c, "custom_bot_99")
        self.assertIn("该角色无内置出厂模板: custom_bot_99", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
