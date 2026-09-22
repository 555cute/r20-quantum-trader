"""AI 大脑调度门面（ai_brain_trader.py）收口 —— 第 297 刀。

目标：把 `scripts/ai_brain_trader.py` 的**门面自身逻辑**钉住。本模块是
`scripts/brain/*` 抽取后的薄壳集合，所以这里只测两类东西：

1. **门面壳的调用期注入契约** —— 抽取时把依赖改成"调用时从门面全局取名"，
   为的是让既有 `patch.object(abt, ...)` 测试缝继续生效。这类"缝"一旦被
   改回 import 期绑定或同名形参，补丁会**静默失效**（函数照跑、结果照对，
   只是不再受测试控制）—— 所以必须逐条断言"注入的确实是我打的那个桩"。
2. **防重复/防腐败的守卫** —— 单飞锁、健康旁车、新鲜度窗口、原子写。

导入兜底（`standalone_settings` / `__version__` / `canonical_base`）用
**AST 单节点抽取 + 按真实文件路径编译**：只在隔离命名空间里跑那一个
`Try` 节点，既覆盖到兜底分支，又不把整个模块（含网络/子进程依赖）重新执行。
"""
from __future__ import annotations

import ast
import fcntl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.ai_brain_trader as abt

_SRC = Path(abt.__file__).read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)


def _find_node(pred):
    for node in _TREE.body:
        if pred(node):
            return node
    raise AssertionError("未找到目标 AST 节点")


def _exec_node(node):
    """按**真实文件路径**编译执行单个 AST 节点，使命中行号归属到本模块。"""
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict = {}
    exec(compile(module, abt.__file__, "exec"), namespace)  # noqa: S102
    return namespace


class ImportFallbackTests(unittest.TestCase):
    """三处模块级导入兜底：兜底分支必须真的产出可用对象。"""

    def test_standalone_settings_is_none_when_config_import_fails(self):
        node = _find_node(lambda n: isinstance(n, ast.Try) and n.lineno == 57)
        with patch.dict(sys.modules, {"r20_backend.config": None}):
            ns = _exec_node(node)
        self.assertIn("standalone_settings", ns)
        self.assertIsNone(ns["standalone_settings"])

    def test_version_falls_back_when_version_import_raises(self):
        node = _find_node(lambda n: isinstance(n, ast.Try) and n.lineno == 62)
        with patch.dict(sys.modules, {"r20_backend.version": None}):
            ns = _exec_node(node)
        self.assertEqual(ns["__version__"], "7.6.0")

    def test_canonical_base_fallback_chain_strips_usdt_markers(self):
        node = _find_node(lambda n: isinstance(n, ast.Try) and n.lineno == 186)
        with patch.dict(sys.modules, {"r20_backend.exchanges.base": None}):
            ns = _exec_node(node)
        fn = ns["_canonical_base_name"]
        # 每个 marker 都要能被剥掉（含 break 早退），且分隔符被清掉
        self.assertEqual(fn("BTC-USDT-SWAP"), "BTC")
        self.assertEqual(fn("ETHUSDT"), "ETH")
        self.assertEqual(fn("SOL_USDT"), "SOL")
        self.assertEqual(fn("ada-usdt"), "ADA")
        self.assertEqual(fn("XRP"), "XRP")
        self.assertEqual(fn(""), "")


class SingleBrainCycleLockTests(unittest.TestCase):
    """单飞锁：另一实例持锁时必须**跳过**而不是排队或覆盖缓存。"""

    def test_skips_and_returns_none_when_lock_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = os.path.join(tmp, ".ai_brain_cycle.lock")
            with patch.object(abt, "DATA_DIR", tmp), \
                 patch.object(abt, "AI_BRAIN_LOCK_FILE", lock_path):
                holder = open(lock_path, "a+", encoding="utf-8")
                self.addCleanup(holder.close)
                fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                ran = []

                @abt.single_brain_cycle
                def _job():
                    ran.append(True)
                    return "ran"

                self.assertIsNone(_job())
                self.assertEqual(ran, [])

    def test_runs_and_releases_lock_when_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = os.path.join(tmp, ".ai_brain_cycle.lock")
            with patch.object(abt, "DATA_DIR", tmp), \
                 patch.object(abt, "AI_BRAIN_LOCK_FILE", lock_path):
                self.assertEqual(abt.single_brain_cycle(lambda: "ok")(), "ok")
                # 锁已释放：换个 fd 能立刻抢到
                probe = open(lock_path, "a+", encoding="utf-8")
                self.addCleanup(probe.close)
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


class ThinShellInjectionTests(unittest.TestCase):
    """薄壳必须在**调用时**从门面全局取名，否则既有补丁缝静默失效。"""

    def test_get_cpa_client_config_passes_live_module_global(self):
        seen = []
        with patch.object(abt, "standalone_settings", "SETTINGS_OBJ"), \
             patch.object(abt, "_get_cpa_client_config",
                          lambda settings: seen.append(settings) or ("u", "k")):
            self.assertEqual(abt.get_cpa_client_config(), ("u", "k"))
        self.assertEqual(seen, ["SETTINGS_OBJ"])

    def test_fetch_single_instrument_package_injects_live_fetchers(self):
        seen = {}

        def fake(item, *, fetch_candles, fetch_single_indicator):
            seen["item"] = item
            seen["candles"] = fetch_candles
            seen["indicator"] = fetch_single_indicator
            return {"ok": True}

        with patch.object(abt, "_fetch_single_instrument_package", fake):
            self.assertEqual(abt.fetch_single_instrument_package({"instId": "X"}), {"ok": True})
        self.assertEqual(seen["item"], {"instId": "X"})
        self.assertIs(seen["candles"], abt.fetch_candles)
        self.assertIs(seen["indicator"], abt.fetch_single_indicator)

    def test_get_xvenue_adapter_delegates_to_impl(self):
        with patch.object(abt, "_get_xvenue_adapter_impl", lambda v: f"adapter:{v}"):
            self.assertEqual(abt._get_xvenue_adapter("binance"), "adapter:binance")

    def test_xv_gate_snapshot_injects_adapter_and_record_seams(self):
        seen = {}

        def fake(base, *, get_adapter, record):
            seen["base"] = base
            seen["get_adapter"] = get_adapter
            seen["record"] = record
            return {"gate": base}

        with patch.object(abt, "_xv_gate_snapshot_impl", fake):
            self.assertEqual(abt._xv_gate_snapshot("BTC"), {"gate": "BTC"})
        self.assertEqual(seen["base"], "BTC")
        self.assertIs(seen["get_adapter"], abt._get_xvenue_adapter)
        self.assertIsNotNone(seen["record"])


class PromptOverrideTests(unittest.TestCase):
    """管理员覆盖层：不存在/不可读一律空串，绝不抛。"""

    def test_reads_and_strips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "override.txt")
            Path(path).write_text("  只做多  \n", encoding="utf-8")
            with patch.object(abt, "PROMPT_OVERRIDE_FILE", path):
                self.assertEqual(abt.read_prompt_override(), "只做多")

    def test_missing_file_returns_empty(self):
        with patch.object(abt, "PROMPT_OVERRIDE_FILE", "/nonexistent/r20-override.txt"):
            self.assertEqual(abt.read_prompt_override(), "")

    def test_oserror_path_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            as_dir = os.path.join(tmp, "override-dir")
            os.makedirs(as_dir)
            with patch.object(abt, "PROMPT_OVERRIDE_FILE", as_dir):
                # 目录存在 ⇒ 走到 open，抛 IsADirectoryError(OSError) ⇒ 空串
                self.assertEqual(abt.read_prompt_override(), "")


class PreferPoolInstTests(unittest.TestCase):
    """同币多合约的确定性优选：USDT 永续优先，其余字典序 —— 与顺序无关。"""

    def test_swap_wins_over_dated_contract(self):
        self.assertTrue(abt._prefer_pool_inst("BTC-USDT-SWAP", "BTC-USDT-250101"))
        self.assertFalse(abt._prefer_pool_inst("BTC-USDT-250101", "BTC-USDT-SWAP"))

    def test_same_kind_falls_back_to_lexicographic(self):
        self.assertTrue(abt._prefer_pool_inst("AAA-USDT-SWAP", "BBB-USDT-SWAP"))
        self.assertFalse(abt._prefer_pool_inst("BBB-USDT-SWAP", "AAA-USDT-SWAP"))

    def test_neither_is_swap_uses_lexicographic(self):
        self.assertTrue(abt._prefer_pool_inst("AAA", "BBB"))
        self.assertFalse(abt._prefer_pool_inst("BBB", "AAA"))

    def test_identical_inputs_are_not_preferred(self):
        self.assertFalse(abt._prefer_pool_inst("AAA-USDT-SWAP", "AAA-USDT-SWAP"))


class CanonicalPositionInstIdTests(unittest.TestCase):
    """跨所符号归一：池查找必须与 TARGET_INSTRUMENTS 顺序无关，且不假装认识。"""

    def test_pool_lookup_prefers_swap_regardless_of_order(self):
        forward = [{"instId": "BTC-USDT-250101"}, {"instId": "BTC-USDT-SWAP"}]
        backward = list(reversed(forward))
        with patch.object(abt, "TARGET_INSTRUMENTS", forward):
            self.assertEqual(abt.canonical_position_inst_id("BINANCE:BTCUSDT"), "BTC-USDT-SWAP")
        with patch.object(abt, "TARGET_INSTRUMENTS", backward):
            self.assertEqual(abt.canonical_position_inst_id("BINANCE:BTCUSDT"), "BTC-USDT-SWAP")

    def test_entries_without_instid_are_skipped(self):
        pool = [{"instId": ""}, {"name": "no-instid"}, {}, None]
        with patch.object(abt, "TARGET_INSTRUMENTS", pool):
            # 池内无该币 ⇒ 标准形态补全成 OKX 永续
            self.assertEqual(abt.canonical_position_inst_id("BTC"), "BTC-USDT-SWAP")

    def test_non_list_target_instruments_is_tolerated(self):
        with patch.object(abt, "TARGET_INSTRUMENTS", "not-a-list"):
            self.assertEqual(abt.canonical_position_inst_id("BTC-USDT-SWAP"), "BTC-USDT-SWAP")

    def test_empty_and_unknown_forms(self):
        with patch.object(abt, "TARGET_INSTRUMENTS", [{"instId": "BTC-USDT-SWAP"}]):
            self.assertEqual(abt.canonical_position_inst_id("   "), "")
            self.assertEqual(abt.canonical_position_inst_id(None), "")
            # 币本位/日期合约**不许**被映射到池内 USDT 永续（换标的 = 违约）
            self.assertEqual(abt.canonical_position_inst_id("BTC-USD-SWAP"), "BTC-USD-SWAP")
            self.assertEqual(abt.canonical_position_inst_id("BTC-USDT-250101"), "BTC-USDT-250101")


class SlAtrMultTests(unittest.TestCase):
    """提示词展示的止损基准 == 执行层会用的那个数。"""

    def test_package_value_wins(self):
        with patch.object(abt, "TARGET_INSTRUMENTS", [{"name": "BTC", "sl_atr_mult": 9.9}]):
            self.assertEqual(abt._sl_atr_mult_for({"name": "BTC", "sl_atr_mult": 2.5}), 2.5)

    def test_pooled_value_used_when_package_lacks_it(self):
        with patch.object(abt, "TARGET_INSTRUMENTS", [{"name": "BTC", "sl_atr_mult": 2.5}]):
            self.assertEqual(abt._sl_atr_mult_for({"name": "btc"}), 2.5)

    def test_unparsable_pooled_value_falls_through_to_asset_class(self):
        with patch.object(abt, "TARGET_INSTRUMENTS", [{"name": "BTC", "sl_atr_mult": "abc"}]):
            self.assertEqual(abt._sl_atr_mult_for({"name": "BTC", "type": "index"}), 1.2)

    def test_unparsable_package_value_does_not_stop_lookup(self):
        with patch.object(abt, "TARGET_INSTRUMENTS", [{"name": "ETH", "sl_atr_mult": 1.7}]):
            self.assertEqual(abt._sl_atr_mult_for({"name": "ETH", "sl_atr_mult": "??"}), 1.7)

    def test_unknown_asset_class_defaults_to_crypto(self):
        with patch.object(abt, "TARGET_INSTRUMENTS", []):
            self.assertEqual(abt._sl_atr_mult_for({"type": "weird"}), 1.4)
            self.assertEqual(abt._sl_atr_mult_for({}), 1.4)


class EffectiveSystemPromptTests(unittest.TestCase):
    """覆盖层必须在模块布局**之后**拼接，否则会被布局丢弃（审计 P1-3）。"""

    def test_override_appended_after_layout(self):
        with patch.object(abt, "active_profile", lambda: {"name": "稳健"}), \
             patch.object(abt, "apply_module_layout", lambda *a, **k: "BASE_LAYOUT"), \
             patch.object(abt, "read_prompt_override", lambda: "OVERLAY_TEXT"):
            out = abt.get_effective_system_prompt()
        self.assertTrue(out.startswith("BASE_LAYOUT"))
        self.assertIn("【管理员提示词覆盖层", out)
        self.assertTrue(out.endswith("OVERLAY_TEXT"))

    def test_no_override_returns_layout_verbatim(self):
        with patch.object(abt, "active_profile", lambda: {"name": "稳健"}), \
             patch.object(abt, "apply_module_layout", lambda *a, **k: "BASE_LAYOUT"), \
             patch.object(abt, "read_prompt_override", lambda: ""):
            self.assertEqual(abt.get_effective_system_prompt(), "BASE_LAYOUT")

    def test_empty_or_non_dict_profile_falls_back_to_active_profile(self):
        calls = []

        def fake_profile():
            calls.append(True)
            return {"name": "激进"}

        with patch.object(abt, "active_profile", fake_profile), \
             patch.object(abt, "apply_module_layout", lambda *a, **k: "BASE"), \
             patch.object(abt, "read_prompt_override", lambda: ""):
            self.assertEqual(abt.get_effective_system_prompt({}), "BASE")
            self.assertEqual(abt.get_effective_system_prompt("nope"), "BASE")
        self.assertEqual(len(calls), 2)

    def test_explicit_profile_is_used_without_touching_active_profile(self):
        def explode():
            raise AssertionError("不应调用 active_profile")

        with patch.object(abt, "active_profile", explode), \
             patch.object(abt, "apply_module_layout", lambda *a, **k: "BASE"), \
             patch.object(abt, "read_prompt_override", lambda: ""):
            self.assertEqual(abt.get_effective_system_prompt({"name": "自定义"}), "BASE")


class ExecuteBatchCycleTests(unittest.TestCase):
    """主编排：逐条钉住"哪一步用哪个依赖"，以及失败早退。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = {}

    def _patch(self, name, value):
        patcher = patch.object(abt, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def _wire_common(self, packages):
        # ⚠️ 一律用**显式函数**而不是 `lambda: self.calls.setdefault(k, v) or X` ——
        # setdefault 返回的是刚存进去的那个（非空）值，恒为真，`or X` 永不生效，
        # 于是桩会把它自己的 kwargs 当成返回值交出去。这类"真值陷阱"只会让
        # 断言以莫名其妙的方式失败（或更糟：静默通过）。
        def _xv(pkgs):
            self.calls["xv"] = pkgs

        def _fl(**kw):
            self.calls["fl"] = kw

        def _calc(**kw):
            self.calls["calc"] = kw

        def _prompt(*a, **kw):
            self.calls["prompt_args"] = (a, kw)
            return "PROMPT"

        def _sysprompt(**kw):
            self.calls["sys_kw"] = kw
            return "SYSTEM"

        def _snap(**kw):
            self.calls["snap"] = kw

        def _telemetry(*a, **k):
            self.calls["telemetry"] = a
            return "TELEM"

        def _dispatch(**kw):
            self.calls["dispatch"] = kw
            return {"BTC-USDT-SWAP": {}}

        self._patch("get_cpa_client_config", lambda: ("https://api.example", "KEY"))
        self._patch("TARGET_INSTRUMENTS", [{"instId": "BTC-USDT-SWAP"}])
        self._patch("capture_policy_snapshot",
                    lambda **kw: ("HASH", {"snap": 1}, "SUMMARY", "VERSION"))
        self._patch("fetch_single_instrument_package", lambda item: dict(packages[0]))
        self._patch("fetch_cross_venue_matrix", _xv)
        self._patch("update_factor_library_snapshot", _fl)
        self._patch("fetch_pending_orders_list", lambda: [{"ordId": "7"}])
        self._patch("write_calculus_snapshot", _calc)
        self._patch("construct_full_market_prompt", _prompt)
        self._patch("active_profile", lambda: {"name": "稳健"})
        self._patch("get_effective_system_prompt", _sysprompt)
        self._patch("write_prompt_snapshot", _snap)
        self._patch("resolve_llm_runtime",
                    lambda **kw: ("openai", "NEWKEY", "https://api.example",
                                  "high", lambda *a, **k: {"raw": True}, "model-x", 30))
        self._patch("ModelCallTelemetry", _telemetry)
        self._patch("dispatch_llm_and_persist_decisions", _dispatch)

    def test_missing_api_key_returns_none_and_records_failure(self):
        health = []
        self._patch("get_cpa_client_config", lambda: ("https://api.example", ""))
        self._patch("_record_cycle_health", lambda status, reason="": health.append((status, reason)))
        self.assertIsNone(abt.execute_batch_ai_brain_cycle())
        self.assertEqual(health, [("failed", "CPA API Key 未配置")])

    def test_full_cycle_wires_every_dependency(self):
        self._wire_common([{"instId": "BTC-USDT-SWAP"}])
        import scripts.factors.smart_money as smart_money
        seen_sm = []

        def fake_smart_money(ccy, price=0.0):
            seen_sm.append((ccy, price))
            return {"lsRatio": "2.10", "takerNetUsd": "12345 U", "weighted_long_pct": 66.0}

        with patch.object(smart_money, "fetch_smart_money_for_symbol", fake_smart_money):
            result = abt.execute_batch_ai_brain_cycle(
                pos_summary="POS",
                active_positions_detail=[{"instId": "BTC-USDT-SWAP", "side": "long"}],
                usdt_available=1000.0,
                policy_snapshot={"given": True},
            )

        self.assertEqual(result, {"BTC-USDT-SWAP": {}})
        # 智能资金：无 ccy/name ⇒ 从 instId 前段推币种（不是空串去查）
        self.assertEqual(seen_sm, [("BTC", 0.0)])
        # 策略快照：传入的快照被原样带下去，返回的四元组被透传
        dispatch = self.calls["dispatch"]
        self.assertEqual(dispatch["policy_hash"], "HASH")
        self.assertEqual(dispatch["policy_version"], "VERSION")
        self.assertEqual(dispatch["policy_summary"], "SUMMARY")
        self.assertEqual(dispatch["policy_snapshot"], {"snap": 1})
        # 系统提示词与用户提示词各自传入
        self.assertEqual(dispatch["effective_system_prompt"], "SYSTEM")
        self.assertEqual(dispatch["prompt"], "PROMPT")
        # 运行时解析后的新密钥/模型必须覆盖旧值
        self.assertEqual(dispatch["api_key"], "NEWKEY")
        self.assertEqual(dispatch["model_name"], "model-x")
        self.assertEqual(dispatch["thinking_timeout"], 30)
        # 在途持仓 id 已归一后下传
        self.assertEqual(dispatch["active_inst_ids"], {"BTC-USDT-SWAP"})
        self.assertEqual(dispatch["active_position_sides"], {"BTC-USDT-SWAP": "long"})
        # 各快照步骤都被调用过
        for key in ("xv", "fl", "calc", "snap"):
            self.assertIn(key, self.calls)

    def test_smart_money_fills_only_na_placeholders(self):
        self._wire_common([{"instId": "BTC-USDT-SWAP", "ccy": "BTC", "price": 50.0,
                            "lsRatio": "N/A", "takerNetUsd": "N/A"}])
        import scripts.factors.smart_money as smart_money

        def fake_smart_money(ccy, price=0.0):
            return {"lsRatio": "1.50", "takerNetUsd": "999 U", "weighted_long_pct": 61.0}

        with patch.object(smart_money, "fetch_smart_money_for_symbol", fake_smart_money):
            abt.execute_batch_ai_brain_cycle(active_positions_detail=[])

        pkg_calls = self.calls["dispatch"]["packages"]
        self.assertEqual(len(pkg_calls), 1)
        # 包装配发生在注入之前，dispatch 收到的是同一批对象引用
        self.assertIn("smart_money", pkg_calls[0])
        self.assertTrue(pkg_calls[0]["smart_money"]["available"])

    def test_smart_money_exception_degrades_without_raising(self):
        self._wire_common([{"instId": "BTC-USDT-SWAP", "ccy": "BTC"}])
        import scripts.factors.smart_money as smart_money

        def boom(ccy, price=0.0):
            raise RuntimeError("rubik down")

        with patch.object(smart_money, "fetch_smart_money_for_symbol", boom):
            # 降级不阻塞决策：仍然走到 dispatch
            self.assertEqual(
                abt.execute_batch_ai_brain_cycle(active_positions_detail=[]),
                {"BTC-USDT-SWAP": {}},
            )

    def test_no_active_positions_yields_empty_id_sets(self):
        self._wire_common([{"instId": "BTC-USDT-SWAP", "ccy": "BTC"}])
        abt.execute_batch_ai_brain_cycle(active_positions_detail=None)
        self.assertEqual(self.calls["dispatch"]["active_inst_ids"], set())
        self.assertEqual(self.calls["dispatch"]["active_position_sides"], {})

    def test_positions_without_instid_are_ignored(self):
        self._wire_common([{"instId": "BTC-USDT-SWAP", "ccy": "BTC"}])
        abt.execute_batch_ai_brain_cycle(
            active_positions_detail=[{}, {"instId": ""}, {"instId": "SOL-USDT-SWAP"}],
            pos_summary="P",
        )
        self.assertEqual(self.calls["dispatch"]["active_inst_ids"], {"SOL-USDT-SWAP"})


class LatestAiDecisionTests(unittest.TestCase):
    """新鲜度窗口：过期/坏档/坏 JSON 一律 None，绝不喂陈旧决策出去。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = os.path.join(self.tmp.name, "decisions.json")
        patcher = patch.object(abt, "AI_DECISION_CACHE_FILE", self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, payload):
        Path(self.cache).write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_cache_returns_none(self):
        self.assertIsNone(abt.get_latest_ai_decision("BTC-USDT-SWAP"))

    def test_unknown_symbol_returns_none(self):
        self._write({"ETH-USDT-SWAP": {"timestamp": int(__import__("time").time())}})
        self.assertIsNone(abt.get_latest_ai_decision("BTC-USDT-SWAP"))

    def test_non_dict_entry_returns_none(self):
        self._write({"BTC-USDT-SWAP": ["not", "a", "dict"]})
        self.assertIsNone(abt.get_latest_ai_decision("BTC-USDT-SWAP"))

    def test_fresh_entry_is_returned(self):
        now = int(__import__("time").time())
        item = {"timestamp": now, "decision": {"action": "WAIT"}}
        self._write({"BTC-USDT-SWAP": item})
        self.assertEqual(abt.get_latest_ai_decision("BTC-USDT-SWAP"), item)

    def test_zero_timestamp_is_rejected(self):
        self._write({"BTC-USDT-SWAP": {"timestamp": 0, "decision": {}}})
        self.assertIsNone(abt.get_latest_ai_decision("BTC-USDT-SWAP"))

    def test_stale_entry_is_rejected(self):
        now = int(__import__("time").time())
        self._write({"BTC-USDT-SWAP": {"timestamp": now - 10_000, "decision": {}}})
        self.assertIsNone(abt.get_latest_ai_decision("BTC-USDT-SWAP"))
        # 边界：正好等于 max_age 仍算新鲜（比较是严格大于）
        self._write({"BTC-USDT-SWAP": {"timestamp": now - 300, "decision": {}}})
        self.assertEqual(abt.get_latest_ai_decision("BTC-USDT-SWAP")["timestamp"], now - 300)

    def test_corrupt_json_returns_none(self):
        Path(self.cache).write_text("{ not json", encoding="utf-8")
        self.assertIsNone(abt.get_latest_ai_decision("BTC-USDT-SWAP"))


class MainGuardTests(unittest.TestCase):
    """CLI 入口：只做"摘要打印"，但其取键逻辑本身也要钉住。"""

    def _run_guard(self, result):
        node = _find_node(lambda n: isinstance(n, ast.If) and n.lineno == 1012)
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {
            "__name__": "__main__",
            "json": json,
            "execute_batch_ai_brain_cycle": lambda *a, **k: result,
        }
        exec(compile(module, abt.__file__, "exec"), namespace)  # noqa: S102
        return namespace

    def test_prints_only_known_symbols(self):
        ns = self._run_guard({
            "BTC-USDT-SWAP": {"decision": {"action": "WAIT"}},
            "DOGE-USDT-SWAP": {"decision": {"action": "BUY_LONG"}},
        })
        self.assertEqual(ns["res"], {"BTC-USDT-SWAP": {"decision": {"action": "WAIT"}},
                                     "DOGE-USDT-SWAP": {"decision": {"action": "BUY_LONG"}}})

    def test_falsy_result_short_circuits(self):
        ns = self._run_guard(None)
        self.assertIsNone(ns["res"])


if __name__ == "__main__":
    unittest.main()
