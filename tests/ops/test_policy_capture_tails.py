"""策略全量包捕获（`r20_backend/policy/capture.py`）残余分支收口测试 —— 第 351 刀。

本模块 190 行，是策略快照生成与整包归档捕获核心：
- 提示词库捕获容错：`load_library` 缺失属性时回退 `load_prompt_config`、两阶段异常安全自愈返回 `{}`；
- 心法记忆捕获容错：文件不存在与快照异常安全回退 `{"version": "missing", "lessons": []}`；
- 拦截器、投委会、风控参数、多所路由单项捕获故障隔离与回退空字典；
- 环境路径复原异常防御：`sys.path.remove` 遇 `ValueError` 安全 pass。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from r20_backend.policy.capture import capture_full_strategy_package


class PolicyCaptureTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_policy_capture_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    # -------------------------------------------------------------------------
    # 1. 提示词库捕获回退分支
    # -------------------------------------------------------------------------
    def test_capture_prompt_library_fallback_to_load_prompt_config(self):
        # load_library 抛 AttributeError 时回退调用 load_prompt_config (lines 119-121)
        with patch("prompt_library.load_library", side_effect=AttributeError("no load_library")):
            with patch("prompt_library.load_prompt_config", return_value={"fallback_loaded": True}):
                pkg = capture_full_strategy_package(self.tmp_path, root_dir=self.tmp_path)
                self.assertEqual(pkg["package"]["prompt_config"], {"fallback_loaded": True})

    # -------------------------------------------------------------------------
    # 2. 各组件捕获异常隔离自愈
    # -------------------------------------------------------------------------
    def test_capture_components_exceptions_isolated_fallbacks(self):
        # 当提示词、心法、拦截器、委员会、风控、路由捕获均抛异常时，安全返回对齐默认空值 (lines 124, 136, 143, 150, 157, 164)
        with patch("prompt_library.load_library", side_effect=RuntimeError("prompt error")):
            with patch("evolution_shield.STRUCTURED_MEMORY_FILE", self.tmp_path / "missing_mem.json"):
                with patch("evolution_shield.read_memory_snapshot", side_effect=RuntimeError("memory error")):
                    with patch("r20_backend.interceptor_manager.load_config", side_effect=RuntimeError("int error")):
                        with patch("r20_backend.council_manager.load_council_config", side_effect=RuntimeError("ccl error")):
                            with patch("r20_backend.risk_config.current_values", side_effect=RuntimeError("risk error")):
                                with patch("r20_backend.exchanges.routing_policy._read_raw_routing", side_effect=RuntimeError("rout error")):
                                    pkg = capture_full_strategy_package(self.tmp_path, root_dir=self.tmp_path)
                                    p = pkg["package"]
                                    self.assertEqual(p["prompt_config"], {})
                                    self.assertEqual(p["evolution_memory"], {"version": "missing", "lessons": []})
                                    self.assertEqual(p["interceptor_config"], {})
                                    self.assertEqual(p["council_config"], {})
                                    self.assertEqual(p["risk_config"], {})
                                    self.assertEqual(p["venue_routing"], {})

    # -------------------------------------------------------------------------
    # 3. 环境路径复原异常处理
    # -------------------------------------------------------------------------
    def test_capture_sys_path_remove_value_error_suppressed(self):
        # finally 块中 sys.path.remove 抛 ValueError 时静默忽略 pass (line 171)
        class MockPathList(list):
            def remove(self, item):
                raise ValueError("path not found in sys.path")

        with patch.object(sys, "path", MockPathList(sys.path)):
            pkg = capture_full_strategy_package(self.tmp_path, root_dir=self.tmp_path)
            self.assertEqual(pkg["format"], "r20_policy_package_v1")


if __name__ == "__main__":
    unittest.main()
