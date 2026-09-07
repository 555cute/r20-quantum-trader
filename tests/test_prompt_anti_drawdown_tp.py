"""Offline isolated tests for peak-drawdown position feed and calculus power fields.

Does not import trader/app modules, fake fcntl, or read real config/.env.
"""
from __future__ import annotations

import ast
import builtins
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

PROJECT = Path(__file__).resolve().parents[1]
TRADER_TREE = ast.parse((PROJECT / "scripts/ai_brain_trader.py").read_text(encoding="utf-8"))


def _fn(name: str) -> ast.FunctionDef:
    return next(n for n in TRADER_TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)


class IsolatedPromptAntiDrawdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(prompts, "ROOT", self.root))
        self.stack.enter_context(patch.object(prompts, "LIBRARY_FILE", self.root / "library.json"))
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
        shield.render_trading_memory = Mock(return_value="隔离心法")
        self.stack.enter_context(patch.dict(sys.modules, {"scripts.evolution_shield": shield}))
        self.profile = {
            "name": "隔离策略",
            "pipelines": {
                "trading_user": [{
                    "id": "u",
                    "title": "自定义",
                    "source": "custom",
                    "enabled": True,
                    "content": "{{account_positions}}\n{{market_matrix}}",
                }]
            },
        }
        self.ns = dict(
            List=List, Dict=Dict, Any=Any, datetime=datetime, os=os, json=json,
            active_profile=lambda: prompts.resolve_profile(self.profile),
            apply_module_layout=prompts.apply_module_layout,
            AI_MEMORY_MD_FILE=str(self.root / "memory.md"),
            AI_MEMORY_FILE=str(self.root / "memory.json"),
            NEWS_SENTIMENT_FILE=str(self.root / "news.json"),
        )
        exec(
            compile(ast.Module(body=[_fn("safe_float"), _fn("construct_full_market_prompt")], type_ignores=[]),
                    "scripts/ai_brain_trader.py", "exec"),
            self.ns,
        )
        self.construct = self.ns["construct_full_market_prompt"]

    def _long_pos(self, **overrides):
        pos = {
            "instId": "ETH-USDT-SWAP",
            "name": "ETH",
            "side": "long",
            "lever": "3",
            "avgPx": "2500.0",
            "markPx": "2520.0",
            "pos": "2.0",
            "upl": "4.0",
            "uplRatio": "0.016",
            "highWaterMark": 2560.0,
            "lowWaterMark": 2495.0,
            "trailingStopPx": 2505.0,
            "takeProfitPx": 2600.0,
            "stage_desc": "已推保本无风险",
        }
        pos.update(overrides)
        return pos

    def test_long_feed_enriches_peak_and_drawdown(self):
        prompt_str = self.construct(
            packages=[],
            pos_summary="1多0空",
            active_positions_detail=[self._long_pos()],
            current_time_str="2026-09-07 15:00:00",
        )
        self.assertIn("曾最高到: 2560.0", prompt_str)
        self.assertIn("极值浮盈 +2.4%", prompt_str)
        self.assertIn("回撤 66.7%", prompt_str)
        self.assertIn("当前价: 2520.0", prompt_str)
        self.assertIn("持仓量: 2.0 (BASE)", prompt_str)
        self.assertIn("动态止损线: 2505.0", prompt_str)
        self.assertIn("目标止盈: 2600.0", prompt_str)
        self.assertNotIn("曾最低到", prompt_str)
        self.assertNotIn("liab", prompt_str)
        self.assertNotIn("notionalUsd", prompt_str)

    def test_short_feed_uses_low_water_mark(self):
        prompt_str = self.construct(
            packages=[],
            pos_summary="0多1空",
            active_positions_detail=[self._long_pos(
                side="short",
                pos="-2.0",
                markPx="2440.0",
                upl="12.0",
                uplRatio="0.024",
                highWaterMark=2510.0,
                lowWaterMark=2400.0,
            )],
        )
        self.assertIn("曾最低到: 2400.0", prompt_str)
        self.assertIn("极值浮盈 +4.0%", prompt_str)
        self.assertIn("回撤 40.0%", prompt_str)
        self.assertNotIn("曾最高到", prompt_str)

    def test_net_positive_pos_is_long_not_short(self):
        prompt_str = self.construct(
            packages=[],
            pos_summary="1多0空",
            active_positions_detail=[self._long_pos(side="net", posSide="net", pos="2.0")],
        )
        self.assertIn("方向: net", prompt_str)
        self.assertIn("曾最高到: 2560.0", prompt_str)
        self.assertNotIn("曾最低到", prompt_str)

    def test_net_negative_pos_is_short(self):
        prompt_str = self.construct(
            packages=[],
            pos_summary="0多1空",
            active_positions_detail=[self._long_pos(
                side="net",
                posSide="net",
                pos="-2.0",
                markPx="2440.0",
                highWaterMark=2510.0,
                lowWaterMark=2400.0,
            )],
        )
        self.assertIn("曾最低到: 2400.0", prompt_str)
        self.assertNotIn("曾最高到", prompt_str)

    def test_missing_watermarks_are_not_invented(self):
        prompt_str = self.construct(
            packages=[],
            pos_summary="1多0空",
            active_positions_detail=[self._long_pos(highWaterMark=0, lowWaterMark=0)],
        )
        self.assertNotIn("曾最高到", prompt_str)
        self.assertNotIn("曾最低到", prompt_str)
        self.assertNotIn("极值浮盈", prompt_str)
        self.assertIn("持仓量: 2.0 (BASE)", prompt_str)

    def test_curvature_power_flow_into_brain_context(self):
        pkg = {
            "name": "ETH",
            "instId": "ETH-USDT-SWAP",
            "price": 2500,
            "chg24h": 1.0,
            "bidPx": 2499,
            "askPx": 2501,
            "fundingRate": None,
            "oiUsd": "UNAVAILABLE",
            "lsRatio": "UNAVAILABLE",
            "takerNetUsd": "UNAVAILABLE",
            "data_quality": "valid",
            "smart_money": {"available": False},
            "calculus": {
                "valid": True,
                "regime": "BULL_DECELERATING",
                "velocity": 0.2,
                "acceleration": -0.3,
                "impulse": 0.1,
                "max_abs_jerk": 0.4,
                "curvature": 1.6,
                "power": -0.15,
                "power_regime": "KINETIC_EXHAUSTION",
                "quality": 0.8,
                "timeframes": {
                    "1H": {
                        "velocity": 0.21,
                        "acceleration": -0.31,
                        "jerk": 0.4,
                        "impulse": 0.1,
                        "curvature": 1.61,
                        "power": -0.16,
                        "power_regime": "KINETIC_EXHAUSTION",
                        "regime": "BULL_DECELERATING",
                        "definite_integrals": {},
                        "probability_theory": {},
                    }
                },
                "definite_integrals": {},
                "probability_theory": {},
            },
        }
        prompt_str = self.construct(packages=[pkg], pos_summary="0多0空", active_positions_detail=[])
        self.assertIn("曲率κ=1.6", prompt_str)
        self.assertIn("功率Φ=-0.15", prompt_str)
        self.assertIn("功率态=KINETIC_EXHAUSTION", prompt_str)
        self.assertIn("κ=1.61", prompt_str)
        self.assertIn("Φ=-0.16", prompt_str)


if __name__ == "__main__":
    unittest.main()
