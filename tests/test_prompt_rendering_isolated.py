"""Offline rendering regression tests; no application/trader module import.

Only audited source AST nodes are executed. All runtime paths are temporary;
file opens outside the sandbox, sockets and child processes are forbidden.
"""
import ast
import builtins
import copy
import datetime
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, patch

import scripts.prompt_library as prompts
from r20_backend import news_config
# 纯配置数据模块（import 时只读 .env，早于 IO 栅栏安装）：风控提示词函数引用的
# 全部大写常量由此注入沙箱命名空间，新增风控参数无需再改本测试。
import scripts.risk_constants as risk_constants

# Read code only, before installing the runtime IO fence.
PROJECT = Path(__file__).resolve().parents[1]
TRADER_TREE = ast.parse((PROJECT / "scripts/ai_brain_trader.py").read_text(encoding="utf-8"))
APP_TREE = ast.parse((PROJECT / "r20_backend/app.py").read_text(encoding="utf-8"))
OLD_TREE = ast.parse((PROJECT / "tests/test_control_plane_v2.py").read_text(encoding="utf-8"))


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(prompts, "ROOT", self.root))
        self.stack.enter_context(patch.object(prompts, "LIBRARY_FILE", self.root / "library.json"))
        self.stack.enter_context(patch.object(news_config, "ENV_FILE", self.root / ".env"))
        self.stack.enter_context(patch.dict(os.environ, {"R20_NEWS_SOURCES": "okx"}))
        original_open, original_io_open, original_os_open = builtins.open, io.open, os.open
        from path_guard import contained


        def check(path):
            if isinstance(path, int):
                return
            if not contained(path, self.root):
                raise AssertionError(f"Non-sandbox file access blocked: {Path(path).resolve()}")


        def guarded(fn):
            def call(path, *args, **kwargs):
                check(path)
                return fn(path, *args, **kwargs)
            return call

        for obj, name, original in ((builtins, "open", original_open), (io, "open", original_io_open), (os, "open", original_os_open)):
            self.stack.enter_context(patch.object(obj, name, guarded(original)))
        for obj, name in ((socket, "socket"), (socket, "create_connection"), (subprocess, "Popen"), (os, "system")):
            self.stack.enter_context(patch.object(obj, name, side_effect=AssertionError("Network/process blocked")))
        shield = types.ModuleType("scripts.evolution_shield")
        self.memory = Mock(return_value="隔离心法")
        shield.render_trading_memory = self.memory
        self.stack.enter_context(patch.dict(sys.modules, {"scripts.evolution_shield": shield}))
        self.profile = {"name": "隔离策略", "pipelines": {"trading_user": [{"id": "u", "title": "自定义", "source": "custom", "enabled": True, "content": "余额={{account_balance}} 持仓={{account_positions}} 挂单={{pending_orders}}"}]}}
        node = next(n for n in TRADER_TREE.body if isinstance(n, ast.FunctionDef) and n.name == "construct_full_market_prompt")
        self.ns = dict(List=List, Dict=Dict, Any=Any, datetime=datetime, os=os, json=json,
                       __version__="0.0.0-sandbox",
                       safe_float=lambda x: float(x or 0), active_profile=lambda: prompts.resolve_profile(self.profile),
                       apply_module_layout=prompts.apply_module_layout,
                       AI_MEMORY_MD_FILE=str(self.root / "memory.md"), AI_MEMORY_FILE=str(self.root / "memory.json"),
                       NEWS_SENTIMENT_FILE=str(self.root / "news.json"))
        self.ns.update({k: v for k, v in vars(risk_constants).items() if k.isupper()})
        exec(compile(ast.Module(body=[node], type_ignores=[]), "scripts/ai_brain_trader.py", "exec"), self.ns)
        self.construct = self.ns["construct_full_market_prompt"]


class RenderingTests(Sandbox):
    def test_compile_preserves_slots_and_enabled_order(self):
        modules = self.profile["pipelines"]["trading_user"]
        modules += [{"content": "disabled {{timezone}}", "enabled": False}, {"content": "{{unknown}}"}]
        result = prompts.compile_modules(modules)
        self.assertIn("{{account_positions}}", result)
        self.assertTrue(result.endswith("{{unknown}}"))
        self.assertNotIn("disabled", result)

    def test_resolve_without_base_preserves_placeholders_and_input(self):
        before = copy.deepcopy(self.profile)
        result = prompts.resolve_profile(self.profile)
        self.assertIn("{{account_balance}}", result["trading_user"])
        self.assertEqual(before, self.profile)
        self.memory.assert_not_called()

    def test_final_custom_resolved_uses_explicit_values(self):
        result = prompts.apply_module_layout("BASE", prompts.resolve_profile(self.profile), "trading_user", "x", context={"account_balance": "321.09 USDT", "account_positions": "SOL long 3张", "pending_orders": "order-42 @ 101"})
        for text in ("BASE", "321.09 USDT", "SOL long 3张", "order-42 @ 101"):
            self.assertIn(text, result)
        self.assertNotIn("{{account_", result)
        self.memory.assert_not_called()

    def test_unresolved_custom_layout_also_works(self):
        self.assertIn("余额=0", prompts.apply_module_layout("", self.profile, "trading_user", "x", context={"account_balance": 0}))

    def test_missing_account_never_becomes_empty(self):
        text = prompts.apply_module_layout("", self.profile, "trading_user", "x", context={})
        for key in ("account_balance", "account_positions", "pending_orders"):
            self.assertIn(f"[MISSING_CONTEXT:{key}]", text)
        self.assertNotIn("空仓", text)
        self.assertNotIn("挂单池为空", text)

    def test_none_is_missing_zero_is_known(self):
        self.assertEqual(prompts.render_variables("{{account_balance}}/{{pending_orders}}", {"account_balance": 0, "pending_orders": None}), "0/[MISSING_CONTEXT:pending_orders]")

    def test_unknown_is_marked_even_if_context_supplies_it(self):
        self.assertEqual(prompts.render_variables("{{typo}}", {"typo": "fake"}), "[UNKNOWN_VARIABLE:typo]")

    def test_unknown_module_is_rejected_on_validation(self):
        self.profile["pipelines"]["trading_user"][0]["content"] = "{{typo}}"
        self.assertFalse(prompts.validate_profile(self.profile)["valid"])

    def test_preview_preserves_slots_without_side_effects(self):
        text = prompts.apply_module_layout("{{timezone}}", self.profile, "trading_user", "x")
        self.assertIn("{{timezone}}", text)
        self.assertIn("{{pending_orders}}", text)
        self.memory.assert_not_called()

    def test_single_pass_does_not_reinterpret_runtime_text(self):
        context = {"account_balance": "{{pending_orders}}", "pending_orders": "MUST_NOT_RECURSE"}
        with patch.object(prompts, "render_variables", wraps=prompts.render_variables) as render:
            text = prompts.apply_module_layout("", {"trading_user": "{{account_balance}}"}, "trading_user", "x", context=context)
        self.assertEqual(text, "{{pending_orders}}")
        self.assertEqual(render.call_count, 1)

    def test_base_layout_single_pass_and_live_nested_values(self):
        base = "【账户】\n【余额】\n321.09\n\n【新增实时】\nfresh"
        layout = [{"title": "账户", "source": "base", "content": "旧摘要"}, {"title": "额外", "source": "custom", "content": "{{pending_orders}}"}]
        with patch.object(prompts, "render_variables", wraps=prompts.render_variables) as render:
            text = prompts.apply_module_layout(base, {"pipelines": {"trading_user": layout}}, "trading_user", "x", context={"pending_orders": "{{account_balance}}"})
        self.assertIn("321.09", text)
        self.assertIn("fresh", text)
        self.assertIn("{{account_balance}}", text)
        self.assertNotIn("旧摘要", text)
        self.assertEqual(render.call_count, 1)

    def test_storage_roundtrip_retains_slots(self):
        profile = prompts._clean_profile(self.profile, "custom-test")
        prompts.save_library({"version": 2, "profiles": {"custom-test": profile}, "active_profile_id": "custom-test", "revisions": []})
        disk = json.loads(prompts.LIBRARY_FILE.read_text(encoding="utf-8"))
        for value in (disk["profiles"]["custom-test"]["trading_user"], prompts.active_profile()["trading_user"]):
            self.assertIn("{{account_balance}}", value)
            self.assertIn("{{account_positions}}", value)
            self.assertIn("{{pending_orders}}", value)
        self.memory.assert_not_called()

    def test_legacy_storage_roundtrip_retains_slots(self):
        prompts.save_library({"active_style": "custom", "custom": {"trading_user": "{{account_balance}}"}})
        self.assertEqual(prompts.active_profile()["trading_user"], "{{account_balance}}")

    def test_actual_construct_and_system_share_runtime(self):
        context = {}
        text = self.construct([], "1/6", [{"instId": "SOL-USDT-SWAP", "side": "long", "pos": "3"}], [{"instId": "SOL-USDT-SWAP", "ordId": "order-42", "px": "101", "sz": "2"}], "2026-09-05 12:00", 321.09, runtime_context_out=context)
        system = prompts.apply_module_layout("RULES", {"trading_system": "{{account_balance}} {{account_positions}} {{pending_orders}}"}, "trading_system", "x", context=context)
        for value in (text, system):
            for expected in ("321.09 USDT", "SOL-USDT-SWAP", "order-42"):
                self.assertIn(expected, value)
            self.assertNotIn("100% 现金空仓", value)
        self.memory.assert_called_once_with(str(self.root / "memory.md"), str(self.root / "memory.json"))

    def test_actual_construct_missing_context_is_unknown(self):
        text = self.construct([])
        for key in ("account_balance", "account_positions", "pending_orders"):
            self.assertIn(f"[MISSING_CONTEXT:{key}]", text)
        self.assertNotIn("100% 现金空仓", text)
        self.assertNotIn("挂单池为空", text)

    def test_actual_construct_known_empty_and_zero(self):
        text = self.construct([], "0/6", [], [], usdt_available=0)
        self.assertIn("0.00 USDT", text)
        self.assertIn("100% 现金空仓", text)
        self.assertIn("挂单池为空", text)

    def test_append_layer_does_not_render_early(self):
        self.assertIn("{{account_balance}}", prompts.append_layer("BASE", "{{account_balance}}", "x"))

    def test_committee_original_expression_preserves_base_modules(self):
        assignment = next(n for n in ast.walk(APP_TREE) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "test_sys" for t in n.targets) and isinstance(n.value, ast.IfExp))
        mods = [{"source": "base", "title": "规则", "content": "规则 {{market_matrix}} {{account_balance}}"}]
        result = eval(compile(ast.Expression(body=assignment.value), "r20_backend/app.py", "eval"), {
            "sys_mods": mods, "prof": {"name": "测试"}, "test_market": "隔离行情",
            "compile_modules": prompts.compile_modules, "apply_module_layout": prompts.apply_module_layout,
        })
        self.assertIn("规则 隔离行情", result)
        self.assertIn("[MISSING_CONTEXT:account_balance]", result)

    def test_sandbox_blocks_files_network_and_processes(self):
        with self.assertRaises(AssertionError):
            Path("/blocked-business-data").read_text(encoding="utf-8")
        with self.assertRaises(AssertionError):
            socket.create_connection(("example.invalid", 443))
        with self.assertRaises(AssertionError):
            subprocess.run(["forbidden"])


# Reuse the six existing layout tests, without their unrelated production imports.
old_class = next(n for n in OLD_TREE.body if isinstance(n, ast.ClassDef) and n.name == "PromptModuleTests")
old_class.bases = [ast.Name(id="Sandbox", ctx=ast.Load())]
ast.fix_missing_locations(old_class)
exec(compile(ast.Module(body=[old_class], type_ignores=[]), "tests/test_control_plane_v2.py", "exec"), globals())

if __name__ == "__main__":
    unittest.main()
