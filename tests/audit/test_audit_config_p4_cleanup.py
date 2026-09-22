"""批 4（P2/P3 清理收口）回归：死键落实、env 注入、池可信度、跨所聚合、无锁 RMW、
per-instrument 参数、极端值确认、委员会预算、文档漂移。

每条都对应 plan_local/STRATEGY_CONFIG_AUDIT_20260913.md 的 P2 编号；
测试用 tests/config_sandbox.isolate_config 全局隔离（tests/__init__.py 已装），
需要模块已在 sys.modules 的先 import 再 isolate（沙箱只 patch 已加载模块）。
"""
from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import r20_backend.app  # noqa: F401  预导入：沙箱只重定向已加载模块
import r20_backend.execution_router  # noqa: F401
import r20_backend.routers.risk  # noqa: F401
import scripts.ai_brain_trader  # noqa: F401
import scripts.instrument_pool  # noqa: F401
import scripts.prompt_library  # noqa: F401
from tests.config_sandbox import isolate_config

ROOT = Path(__file__).resolve().parents[2]


class _Base(unittest.TestCase):
    def setUp(self):
        # isolate_config 返回**沙箱根 Path**（临时目录）。旧写法误判成 dict → 回落到项目根，
        # 结果测试直接改写了生产 data/venue_routing.json 与 data/instrument_pool.json
        # （2026-09-14 01:11 事故）。这里以返回值直接为根，并断言它确实在临时目录下。
        self.root = Path(isolate_config(self))
        real_data = (ROOT / "data").resolve()
        self.assertNotEqual(self.root.resolve(), ROOT.resolve(), "沙箱根不得等于项目根")
        self.assertFalse(str(self.root.resolve()).startswith(str(real_data)),
                         f"沙箱根落在生产 data/ 内: {self.root}")


class EnvInjectionTests(_Base):
    """P2-7：.env 里值带换行就等于追加新键（可伪造 R20_BINANCE_EXECUTION=1）。"""

    def setUp(self):
        super().setUp()
        import r20_backend.settings_store as ss
        self.ss = ss
        self.env_file = self.root / ".env"
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("R20_MAX_LEVERAGE=5.0\n", encoding="utf-8")
        # ⚠️ 原实现 `addCleanup(setattr, self.ss, "ENV_FILE", self.ss.ENV_FILE)` 有 bug：
        # addCleanup 的**实参是立即求值**的，而上一行已经把 ENV_FILE 改成了临时路径
        # ⇒ 清理时又把**临时路径**写回去，`settings_store.ENV_FILE` 从此永久指向一个
        # 已被删除的临时目录（本刀由 `test_settings_store.py::ManagedKeysTests` 抓出）。
        # 必须先存原值，再改。
        self._orig_env_file = self.ss.ENV_FILE
        self.ss.ENV_FILE = self.env_file
        self.addCleanup(setattr, self.ss, "ENV_FILE", self._orig_env_file)

    def test_newline_in_value_is_rejected_and_file_untouched(self):
        before = self.env_file.read_text(encoding="utf-8")
        with self.assertRaises(self.ss.EnvValueError):
            self.ss.update_env({"R20_MAX_LEVERAGE": "5\nR20_BINANCE_EXECUTION=1"})
        self.assertEqual(self.env_file.read_text(encoding="utf-8"), before)
        self.assertNotIn("BINANCE_EXECUTION", self.env_file.read_text(encoding="utf-8"))

    def test_carriage_return_and_nul_rejected(self):
        for bad in ("5\r\nR20_GATE_EXECUTION=1", "5\x00"):
            with self.assertRaises(self.ss.EnvValueError):
                self.ss.update_env({"R20_MAX_LEVERAGE": bad})

    def test_normal_write_still_works(self):
        self.ss.update_env({"R20_MAX_LEVERAGE": "7.5"})
        self.assertIn("R20_MAX_LEVERAGE=7.5", self.env_file.read_text(encoding="utf-8"))

    def test_remove_env_rejects_illegal_key_name(self):
        with self.assertRaises(self.ss.EnvValueError):
            self.ss.remove_env({"BAD KEY\nEVIL=1"})

    def test_route_maps_env_value_error_to_400(self):
        import r20_backend.app as app_mod
        handlers = getattr(app_mod.app, "exception_handlers", {}) or {}
        self.assertIn(self.ss.EnvValueError, handlers, "EnvValueError 必须映射为 400，而不是裸 500")


class RoutingAssetsTests(_Base):
    """P2-10：assets 写成字符串会被逐个字符迭代成 ['B','C','T']。"""

    def setUp(self):
        super().setUp()
        from r20_backend.exchanges import routing_policy
        self.rp = routing_policy
        self.file = self.root / "data" / "venue_routing.json"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.rp.ROUTING_FILE = self.file

    def _pool(self, assets):
        self.file.write_text(json.dumps({"gate": {"assets": assets}}), encoding="utf-8")
        with patch.object(self.rp, "_gate_execution_ready", lambda: True):
            return self.rp.load_venue_pool("gate")

    def test_single_string_is_one_asset_not_chars(self):
        self.assertEqual(self._pool("BTC")["assets"], ["BTC"])

    def test_junk_and_non_string_dropped(self):
        self.assertEqual(self._pool(["BTC", "btc", " eth ", "!!", 5])["assets"], ["BTC", "ETH"])

    def test_non_list_type_becomes_empty_pool(self):
        self.assertEqual(self._pool(42)["assets"], [])

    def test_empty_list_stays_empty(self):
        self.assertEqual(self._pool([])["assets"], [])


class LeverageFloorTests(_Base):
    """P2-8：执行层此前只夹杠杆上限，风控页的 MIN_LEVERAGE 在市场侧不成立。"""

    def setUp(self):
        super().setUp()
        import r20_backend.execution_router as er
        from scripts.risk_constants import MAX_LEVERAGE, MIN_LEVERAGE
        self.er = er
        self.min_lev, self.max_lev = MIN_LEVERAGE, MAX_LEVERAGE

    def test_floor_is_applied_at_execution_layer(self):
        """1x 决策（低于配置下限）必须在执行层被抬到下限。"""
        import os
        from tests.test_gate_execution_router import _StubAdapter

        ad = _StubAdapter()
        decision = {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 50.0, "leverage": 1.0,
                    "entry_price": 79000.0, "take_profit_price": 82000.0, "stop_loss_price": 77000.0}
        with patch.object(self.er, "_load_venue_pool_soft", lambda venue: {}), \
             patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            result = self.er.open_protected_position(decision, adapter=ad, max_margin_usdt=1000.0)
        leverage_calls = [c for c in ad.calls if c[0] == "leverage"]
        self.assertTrue(leverage_calls or result.get("stage") in {"risk_gate", "margin", "exposure", "precheck"},
                        f"未进入杠杆阶段: {result}")
        if leverage_calls:
            self.assertGreaterEqual(leverage_calls[0][2], self.min_lev, "执行层未抬升到 MIN_LEVERAGE")
            self.assertLessEqual(leverage_calls[0][2], self.max_lev)

    def test_per_instrument_cap_tightens_global_upper(self):
        self.assertLessEqual(min(self.max_lev, 3.0), self.max_lev)
        # 结构优化阶段 4·B3 第三十八刀：杠杆夹取实现已迁至
        # `r20_backend/execution/risk_gates.py`（`clamp_leverage`），定位随之改到那里。
        src = (ROOT / "r20_backend" / "execution" / "risk_gates.py").read_text(encoding="utf-8")
        self.assertIn('decision.get("max_leverage")', src,
                      "调用方给的池内杠杆上限必须被并入夹取")


class InstrumentPoolTrustTests(_Base):
    """P2-11：池文件坏掉时旧实现静默换成 10 币出厂默认池，交易侧照旧开新仓。"""

    def setUp(self):
        super().setUp()
        import scripts.instrument_pool as ip
        self.ip = ip
        self.pool_file = self.root / "data" / "instrument_pool.json"
        self.pool_file.parent.mkdir(parents=True, exist_ok=True)
        self.ip.POOL_FILE = self.pool_file

    def test_missing_file_is_marked_missing(self):
        got = self.ip.load_instruments()
        self.assertTrue(got, "仍要返回可展示的数据")
        self.assertFalse(self.ip.pool_is_trustworthy())
        self.assertEqual(self.ip.pool_state()["status"], "missing")

    def test_corrupt_file_is_not_silently_replaced(self):
        self.pool_file.write_text("{坏 JSON", encoding="utf-8")
        got = self.ip.load_instruments()
        self.assertFalse(self.ip.pool_is_trustworthy(), "损坏的池文件不得被当成可信池")
        self.assertEqual(self.ip.pool_state()["status"], "corrupt")
        self.assertTrue(got, "展示层仍有兜底数据")

    def test_missing_required_field_drops_entry_and_marks_untrusted(self):
        self.pool_file.write_text(json.dumps({"instruments": [
            {"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 0.01},
            {"instId": "ETH-USDT-SWAP", "name": "ETH"},          # 缺 ctVal
        ]}), encoding="utf-8")
        got = self.ip.load_instruments()
        self.assertEqual([i["instId"] for i in got], ["BTC-USDT-SWAP"])
        self.assertFalse(self.ip.pool_is_trustworthy())
        self.assertIn("ETH-USDT-SWAP", self.ip.pool_state()["detail"])

    def test_healthy_pool_is_trusted(self):
        self.pool_file.write_text(json.dumps({"instruments": [
            {"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 0.01, "tier": "tier_1_bluechip"},
        ]}), encoding="utf-8")
        self.ip.load_instruments()
        self.assertTrue(self.ip.pool_is_trustworthy())
        self.assertEqual(self.ip.pool_state()["status"], "ok")

    def test_trader_refuses_new_entries_when_pool_untrusted(self):
        from tests.source_scan import combined
        src = combined("scripts/ai_factor_trader.py", pkg_name="trader")
        self.assertIn("pool_is_trustworthy()", src, "开新仓前必须检查池可信度（fail-closed）")
        self.assertIn("if not cb_active and pool_is_trustworthy():", src)


class CrossVenueAggregationTests(_Base):
    """P2-12：跨所持仓 id（BINANCE:BTCUSDT）与 OKX 形态永不相等 → 守卫看不见外所持仓。"""

    def setUp(self):
        super().setUp()
        import scripts.ai_brain_trader as abt
        self.abt = abt

    def test_all_common_forms_collapse_to_okx_shape(self):
        for raw in ("BINANCE:BTCUSDT", "BTC_USDT", "BTCUSDT", "BTC-USDT", "BTC-USDT-SWAP", "BTC", "btc"):
            self.assertEqual(self.abt.canonical_position_inst_id(raw), "BTC-USDT-SWAP", raw)

    def test_unknown_forms_are_preserved_verbatim(self):
        for raw in ("SOL-USDT-250926", "BTC-USD-SWAP"):
            self.assertEqual(self.abt.canonical_position_inst_id(raw), raw)

    def test_empty_is_empty(self):
        self.assertEqual(self.abt.canonical_position_inst_id(None), "")

    def test_pool_choice_is_order_independent(self):
        """第一百八十三刀：同币多合约时的选择**不得**依赖 TARGET_INSTRUMENTS 的顺序。

        原来用 `setdefault`（首值优先）⇒ 增删一条配置就可能换掉下单标的（静默任意选择）。
        现在按"USDT 永续优先，其次字典序"确定性优选。
        """
        # ⚠️ 必须挑**真的会归一到同一个 base** 的两条：`BTC-USD-SWAP`（币本位）归一后是
        # `BTCUSDSWAP`，根本进不了同一个池位 ⇒ 我第一版用它造"重复"，反向验证时**没翻红**
        # （等于没测到）。`BTC_USDT`/`BTC-USDT`/`BTCUSDT` 都归一到 `BTC`。
        pool = [{"instId": "BTC_USDT"}, {"instId": "BTC-USDT-SWAP"}]
        original = self.abt.TARGET_INSTRUMENTS
        try:
            got = []
            for variant in (pool, list(reversed(pool))):
                self.abt.TARGET_INSTRUMENTS = variant
                got.append(self.abt.canonical_position_inst_id("BTCUSDT"))
            self.assertEqual(got[0], "BTC-USDT-SWAP")
            self.assertEqual(got[0], got[1], "同一池子的两种顺序给出了不同合约 ⇒ 顺序相关")
        finally:
            self.abt.TARGET_INSTRUMENTS = original

    def test_no_duplicate_bases_in_the_real_pool(self):
        """真机核对：当前目标合约**没有**同币重复 ⇒ 本刀的确定性优选不改变现行行为。"""
        from collections import Counter
        bases = Counter()
        for item in (self.abt.TARGET_INSTRUMENTS or []):
            iid = str((item or {}).get("instId") or "")
            if iid:
                bases[self.abt._canonical_base_name(iid)] += 1
        self.assertEqual([b for b, n in bases.items() if n > 1], [],
                         "池里出现同币多合约 ⇒ 请复核确定性优选是否符合预期")

    def test_guard_inputs_use_the_canonicalizer(self):
        src = (ROOT / "scripts" / "ai_brain_trader.py").read_text(encoding="utf-8")
        block = src[src.index("active_inst_ids = {"):src.index("active_position_sides.pop(")]
        self.assertEqual(block.count("_canonical_inst_id(p.get(\"instId\"))"), 2,
                         "active_inst_ids 与 active_position_sides 都必须归一，否则守卫只看单边")


class LockedRmwTests(_Base):
    """P2-6：四个 JSON 的 load→改→save 无锁 → 并发保存丢更新。"""

    def test_file_lock_is_reentrant_within_thread(self):
        from r20_backend.file_locks import file_lock, lock_is_held
        target = self.root / "data" / "probe.json"
        with file_lock(target):
            self.assertTrue(lock_is_held(target))
            with file_lock(target):
                self.assertTrue(lock_is_held(target))
            self.assertTrue(lock_is_held(target), "内层退出不应释放外层持有的锁")
        self.assertFalse(lock_is_held(target))

    def test_concurrent_pool_writes_do_not_lose_updates(self):
        import scripts.instrument_pool as ip
        pool_file = self.root / "data" / "instrument_pool.json"
        pool_file.parent.mkdir(parents=True, exist_ok=True)
        pool_file.write_text(json.dumps({"version": 1, "instruments": [
            {"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 0.01, "tier": "tier_1_bluechip"}]}), encoding="utf-8")
        ip.POOL_FILE = pool_file
        start = threading.Barrier(3)
        errors: list[str] = []

        def worker(coin: str) -> None:
            try:
                start.wait()
                for _ in range(20):
                    ip.mutate_instruments(lambda pool, c=coin: [
                        *pool, {"instId": f"{c}-USDT-SWAP", "name": c, "ctVal": 1.0}])
            except Exception as exc:  # pragma: no cover - 失败即测试失败
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(c,)) for c in ("ETH", "SOL")]
        for t in threads:
            t.start()
        start.wait()
        for t in threads:
            t.join(timeout=60)
        self.assertFalse(errors, errors)
        rows = json.loads(pool_file.read_text(encoding="utf-8"))["instruments"]
        self.assertEqual(len(rows), 41, f"并发 RMW 丢更新：{len(rows)} 条（期望 41）")

    def test_prompt_library_mutators_are_locked(self):
        src = (ROOT / "scripts" / "prompt_library.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(src.count("@_locked_library"), 6)
        for name in ("def save_library", "def update_profile", "def activate_profile", "def rollback_profile"):
            self.assertIn(name, src)

    def test_council_and_llm_writers_hold_the_lock(self):
        council = (ROOT / "r20_backend" / "council_manager.py").read_text(encoding="utf-8")
        self.assertIn("@_locked_council\ndef save_council_config", council)
        self.assertIn("with file_lock(COUNCIL_CONFIG_FILE):", council)
        llm = (ROOT / "r20_backend" / "llm_manager.py").read_text(encoding="utf-8")
        self.assertIn("with file_lock(LLM_CONFIG_FILE):", llm)


class PerInstrumentParamTests(_Base):
    """P2-5：池条目里的 max_leverage/sl_atr_mult 此前无人读，提示词又是第三套口径。"""

    def setUp(self):
        super().setUp()
        import scripts.ai_factor_trader as aft
        self.aft = aft

    def test_pool_value_overrides_asset_class_profile(self):
        inst = {"name": "SUI", "sl_atr_mult": 2.2, "max_leverage": 3}
        prof = self.aft.instrument_profile(inst, "crypto")
        self.assertEqual(prof["sl_atr_mult"], 2.2)
        self.assertEqual(prof["tp_atr_mult"], 2.8, "未覆盖的键仍取资产类别档")

    def test_asset_class_profile_used_when_pool_silent(self):
        prof = self.aft.instrument_profile({"name": "XAU"}, "commodity")
        self.assertEqual(prof["sl_atr_mult"], 1.3)

    def test_prompt_sl_baseline_is_derived_not_hardcoded(self):
        import scripts.ai_brain_trader as abt
        src = (ROOT / "scripts" / "ai_brain_trader.py").read_text(encoding="utf-8")
        self.assertNotIn("止损基准: 1.5~2.0x", src)
        self.assertEqual(abt._sl_atr_mult_for({"name": "BTC", "sl_atr_mult": 1.8}), 1.8)
        self.assertEqual(abt._sl_atr_mult_for({"name": "__nope__", "type": "crypto"}), 1.4)

    def test_brain_asset_class_table_matches_trader(self):
        """源码钉：两处资产类别止损档必须一致（否则又成两份口径）。"""
        import scripts.ai_brain_trader as abt
        for asset, expected in self.aft.ASSET_CLASS_PROFILES.items():
            self.assertAlmostEqual(abt._SL_ATR_BY_ASSET_CLASS[asset],
                                   float(expected["sl_atr_mult"]), places=6, msg=asset)


class ExposureCapTests(_Base):
    """P2-1：R20_MAX_TOTAL_EXPOSURE_USDT 自 US-005 起可写可存但零消费者。"""

    def test_key_is_in_single_source_of_truth(self):
        from scripts.risk_constants import DEFAULTS, RISK_ENV_KEYS
        self.assertIn("R20_MAX_TOTAL_EXPOSURE_USDT", DEFAULTS)
        self.assertIn("R20_MAX_TOTAL_EXPOSURE_USDT", RISK_ENV_KEYS)

    def test_router_refuses_when_projected_exposure_exceeds_cap(self):
        import r20_backend.execution_router as er

        class _Ad:
            environment = "demo"

            def positions(self):
                return [{"base": "BTC", "size_signed": 1.0, "side": "long", "mark_price": 100000.0}]

        # ⚠️ 本用例**过去是"因为错误的理由"通过的**（结构优化阶段 4·B3 第三十八刀查明）：
        #   ① `action` 曾写成 `"buy"`，而 R:R 闸门只认 `"BUY_LONG"`/`"SELL_SHORT"`
        #   —— 故物理风控**先**拒开，根本走不到敞口闸门；
        #   ② 敞口闸门那行 `_all_positions if _all_positions is not None else ...`
        #   的 `_all_positions` 是**局部变量**（赋值在其后）→ 抛 UnboundLocalError，
        #   被裸 `except Exception` 吞掉并返回 `_fail("exposure", ...)`。
        #   于是 `ok is False` 恒成立、`stage == "exposure"` 恒成立 —— 断言全中，
        #   但走的是**异常路径**，闸门一天也没真正生效过。
        # 现改用合法方向 + 真正会让闸门触发的上限，断言它**按设计**拒开。
        decision = {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 200.0, "leverage": 5.0,
                    "entry_price": 100.0, "take_profit_price": 110.0, "stop_loss_price": 95.0}
        # 已有同向 1.0 张 × 100000 = 100000U；本单名义 200×5 = 1000U。
        # 上限设 50000U → 预计敞口 101000U 必然超限。
        with patch.object(er, "TOTAL_EXPOSURE_CAP", 50000.0), \
             patch.object(er, "_load_venue_pool_soft", lambda venue: {}), \
             patch.object(er, "require_execution", lambda *a, **k: None):
            result = er.open_protected_position(decision, adapter=_Ad(), max_margin_usdt=5000.0)
        self.assertFalse(result.get("ok"), "同向敞口超上限必须拒开")
        self.assertEqual(result.get("stage"), "exposure")
        self.assertIn("同向敞口", result.get("detail") or "",
                      "必须是闸门给出的敞口理由，而不是异常兜底")

    def test_cap_zero_means_unlimited(self):
        import r20_backend.execution_router as er
        with patch.object(er, "TOTAL_EXPOSURE_CAP", 0.0):
            # 结构优化阶段 4·B3 第三十八刀：敞口闸门实现已迁至
            # `r20_backend/execution/risk_gates.py`（`check_total_exposure`），
            # 故定位随之改到那里。语义不变：0 / 负数 = 不限制（与其余风控键一致）——
            # 现在写成早退形式 `if exposure_cap <= 0: return None`。
            src = (ROOT / "r20_backend" / "execution" / "risk_gates.py").read_text(encoding="utf-8")
            router_src = (ROOT / "r20_backend" / "execution_router.py").read_text(encoding="utf-8")
        self.assertIn("if exposure_cap <= 0:", src,
                      "0 必须表示不限制（与其余风控键语义一致）")
        self.assertIn("_check_total_exposure(", router_src,
                      "门面仍须在发送前调用敞口闸门")


class HighRiskConfirmationTests(_Base):
    """P2-9：风控页可把单标的占比设到 100%、日亏 50% 权益，且零确认。"""

    def setUp(self):
        super().setUp()
        from r20_backend import risk_config
        self.rc = risk_config

    def test_thresholds_cover_the_audited_extremes(self):
        hits = self.rc.high_risk_changes({
            "R20_SINGLE_ASSET_EQUITY_RATIO": 1.0,
            "R20_DAILY_LOSS_EQUITY_RATIO": 0.5,
        })
        self.assertEqual({h["key"] for h in hits},
                         {"R20_SINGLE_ASSET_EQUITY_RATIO", "R20_DAILY_LOSS_EQUITY_RATIO"})

    def test_normal_values_pass_without_confirmation(self):
        self.assertEqual(self.rc.high_risk_changes({
            "R20_SINGLE_ASSET_EQUITY_RATIO": 0.30,
            "R20_DAILY_LOSS_EQUITY_RATIO": 0.05,
            "R20_MAX_LEVERAGE": 5.0,
        }), [])

    def test_schema_ships_thresholds_and_phrase(self):
        schema = self.rc.schema()
        self.assertEqual(schema["high_risk_phrase"], self.rc.HIGH_RISK_PHRASE)
        flagged = [p for p in schema["params"] if p.get("high_risk_at") is not None]
        self.assertTrue(flagged, "前端需要 high_risk_at 才能弹确认框")
        self.assertTrue(all("high_risk_at" in p for p in schema["params"]))

    def test_route_rejects_extreme_without_phrase(self):
        from fastapi.testclient import TestClient
        import r20_backend.app as app_mod
        from r20_backend.routers import risk as risk_router
        client = TestClient(app_mod.app)
        with patch.object(risk_router, "require_superadmin", lambda *a, **k: {"username": "t"}), \
             patch.object(risk_router, "audit_record", lambda *a, **k: None), \
             patch.object(risk_router, "update_env", lambda values: None):
            res = client.post("/api/v1/admin/risk", json={"values": {"R20_SINGLE_ASSET_EQUITY_RATIO": 1.0}})
        self.assertEqual(res.status_code, 400)
        self.assertIn(self.rc.HIGH_RISK_PHRASE, res.json()["detail"])

    def test_route_accepts_extreme_with_phrase(self):
        from fastapi.testclient import TestClient
        import r20_backend.app as app_mod
        from r20_backend.routers import risk as risk_router
        client = TestClient(app_mod.app)
        calls: list[dict] = []
        with patch.object(risk_router, "require_superadmin", lambda *a, **k: {"username": "t"}), \
             patch.object(risk_router, "audit_record", lambda *a, **k: None), \
             patch.object(risk_router, "refresh_settings", lambda: None), \
             patch.object(risk_router, "update_env", lambda values: calls.append(values)):
            res = client.post("/api/v1/admin/risk", json={
                "values": {"R20_SINGLE_ASSET_EQUITY_RATIO": 1.0},
                "confirmation": self.rc.HIGH_RISK_PHRASE,
            })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(calls, "确认后必须真的写入")


class CouncilBudgetTests(_Base):
    """P2-13/14：超时三套默认 + CIO 被席位吃光预算。"""

    def setUp(self):
        super().setUp()
        import r20_backend.council_manager as cm
        self.cm = cm

    def test_timeout_is_clamped_at_both_ends(self):
        self.assertEqual(self.cm.clamp_council_timeout(5000), self.cm.MAX_COUNCIL_TIMEOUT)
        self.assertEqual(self.cm.clamp_council_timeout(1), self.cm.MIN_COUNCIL_TIMEOUT)
        self.assertEqual(self.cm.clamp_council_timeout("junk"), self.cm.DEFAULT_COUNCIL_TIMEOUT)
        self.assertEqual(self.cm.clamp_council_timeout(float("nan")), self.cm.DEFAULT_COUNCIL_TIMEOUT)

    def test_max_budget_leaves_margin_below_scheduler_kill(self):
        self.assertLess(self.cm.MAX_COUNCIL_TIMEOUT, 600.0)

    def test_schema_default_matches_engine_default(self):
        from r20_backend.schemas import CouncilConfigUpdateRequest
        field = CouncilConfigUpdateRequest.model_fields["timeout_seconds"]
        self.assertEqual(float(field.default), self.cm.DEFAULT_COUNCIL_TIMEOUT)
        bounds = {type(m).__name__: m for m in (field.metadata or [])}
        self.assertEqual(float(bounds["Ge"].ge), self.cm.MIN_COUNCIL_TIMEOUT)
        self.assertEqual(float(bounds["Le"].le), self.cm.MAX_COUNCIL_TIMEOUT)

    def test_cio_reserve_exists_in_both_modes(self):
        """定位说明（结构优化阶段 2 / B5）：原先用 inspect.getsource(门面函数)，
        拆分后门面只剩转发薄壳，取到的源码不含预算逻辑；改为扫 council 运行时源码整体。
        断言强度不变。"""
        from tests.source_scan import assert_area_looks_real, combined
        src = combined(Path(self.cm.__file__))
        assert_area_looks_real(self, src, must_contain="cio_reserve")
        self.assertGreaterEqual(src.count("cio_reserve"), 2, "两种共识模式都要给 CIO 留预算")
        self.assertIn("CIO_MIN_ARBITRATION_TIME", src)

    def test_save_clamps_timeout_from_hand_edited_file(self):
        cfg = self.cm.save_council_config({
            "enabled": False, "consensus_mode": "standard", "timeout_seconds": 5000,
            "roles": {"cio": dict(self.cm.DEFAULT_PRESET_TEMPLATES["cio"])},
        })
        self.assertEqual(cfg["timeout_seconds"], self.cm.MAX_COUNCIL_TIMEOUT)


class DocsAndExampleDriftTests(_Base):
    """P2-2/3：env.example 缺键；DocsView 写死"17 项"而实际更多。"""

    def test_env_example_lists_the_missing_risk_keys(self):
        text = (ROOT / "env.example").read_text(encoding="utf-8")
        for key in ("R20_MIN_LEVERAGE", "R20_PORTFOLIO_RISK_BUDGET_USDT", "R20_MAX_TOTAL_EXPOSURE_USDT"):
            self.assertIn(f"{key}=", text, f"env.example 缺 {key}")

    def test_risk_schema_covers_every_single_source_key(self):
        from r20_backend import risk_config
        schema_keys = {p["key"] for p in risk_config.schema()["params"]}
        missing = sorted(set(risk_config.DEFAULTS) - schema_keys)
        self.assertEqual(missing, [], f"风控页 schema 未覆盖单一事实源的键: {missing}")

    def test_docsview_has_no_hardcoded_param_count(self):
        # 结构优化阶段 0（2026-09-14）：DocsView.vue 从 views/ 移入 views/docs/。
        # 原本按绝对路径钉死文件位置，文件一移动就 FileNotFoundError（对断言意图无意义）。
        # 改为按文件名定位 + 唯一性断言：断言强度不变（仍是同一批 needle），
        # 但今后再次整理目录不会再误伤这条防漂移检查。
        matches = sorted((ROOT / "frontend" / "src" / "views").rglob("DocsView.vue"))
        self.assertEqual(len(matches), 1, f"DocsView.vue 应恰好存在一份，实得 {matches}")
        text = matches[0].read_text(encoding="utf-8")
        for needle in ("17 项", "全部 17"):
            self.assertNotIn(needle, text, "docs 页不得写死风控项数（会随 schema 漂移）")

    def test_env_example_matches_managed_keys_risk_subset(self):
        from r20_backend.settings_store import MANAGED_KEYS
        from scripts.risk_constants import RISK_ENV_KEYS
        text = (ROOT / "env.example").read_text(encoding="utf-8")
        missing = [k for k in RISK_ENV_KEYS if k in MANAGED_KEYS and f"{k}=" not in text]
        self.assertEqual(missing, [], f"可在后台写入但 env.example 未列出的风控键: {missing}")


class ConfigEffectMatrixTests(_Base):
    """结构性防复发（审计「优化建议」第 1 条）：每个风控旋钮都必须"四处一致"——
    单一事实源有定义 / 引擎有执行者 / 提示词携带并等于 effective 值 / 风控页可配置。
    这一条能同时拦住 P0-1（提示词虚高）、P1-1（双口径）、P2-1（装饰键）三类问题。"""

    # key → 引擎执行点源码指纹（该键被真正消费的位置）
    ENFORCERS = {
        "R20_MAX_TOTAL_EXPOSURE_USDT": ("r20_backend/execution_router.py", "TOTAL_EXPOSURE_CAP"),
        "R20_MAX_LEVERAGE": ("r20_backend/execution_router.py", "MAX_LEVERAGE"),
        "R20_MIN_LEVERAGE": ("r20_backend/execution_router.py", "MIN_LEVERAGE"),
        "R20_MAX_SINGLE_ASSET_MARGIN_USDT": ("r20_backend/execution_router.py", "MAX_SINGLE_ASSET_MARGIN"),
        "R20_PORTFOLIO_RISK_BUDGET_USDT": ("scripts/ai_brain_trader.py", "PORTFOLIO_RISK_BUDGET_USDT"),
        "R20_MAX_CONCURRENT_POSITIONS": ("scripts/risk_constants.py", "effective_max_positions"),
        "R20_MAX_SAME_DIRECTION_POSITIONS": ("scripts/risk_constants.py", "MAX_SAME_DIRECTION_POSITIONS"),
        "R20_MAX_MARGIN_EQUITY_RATIO": ("r20_backend/execution_router.py", "MAX_MARGIN_EQUITY_RATIO"),
        "R20_SINGLE_ASSET_EQUITY_RATIO": ("scripts/risk_constants.py", "SINGLE_ASSET_EQUITY_RATIO"),
        "R20_RISK_PER_TRADE_RATIO": ("scripts/risk_constants.py", "RISK_PER_TRADE_RATIO"),
        "R20_MIN_RISK_REWARD": ("scripts/risk_constants.py", "MIN_RISK_REWARD_RATIO"),
        "R20_MIN_ENTRY_CONFIDENCE": ("scripts/risk_constants.py", "MIN_ENTRY_CONFIDENCE"),
        "R20_MAX_DAILY_LOSS_USDT": ("scripts/risk_constants.py", "effective_daily_loss_limit"),
        "R20_DAILY_LOSS_EQUITY_RATIO": ("scripts/risk_constants.py", "DAILY_LOSS_EQUITY_RATIO"),
        "R20_TIME_STOP_HOURS": ("scripts/risk_constants.py", "TIME_STOP_HOURS"),
        "R20_TIME_STOP_ATR_BAND": ("scripts/risk_constants.py", "TIME_STOP_ATR_BAND"),
        "R20_STOP_COOLDOWN_MINUTES": ("scripts/risk_constants.py", "STOP_COOLDOWN_MINUTES"),
        "R20_MAX_SCALE_IN_COUNT": ("scripts/risk_constants.py", "MAX_SCALE_IN_COUNT"),
        "R20_MIN_SCALE_IN_PROFIT_RATIO": ("scripts/risk_constants.py", "MIN_SCALE_IN_PROFIT_RATIO"),
        "R20_MIN_SCALE_IN_CONFIDENCE": ("scripts/risk_constants.py", "MIN_SCALE_IN_CONFIDENCE"),
        "R20_SCALE_OUT_ENABLED": ("scripts/trader/scale_out.py", "SCALE_OUT_ENABLED"),
        "R20_SCALE_OUT_RATIO": ("scripts/trader/scale_out.py", "SCALE_OUT_RATIO"),
        "R20_SCALE_OUT_TRIGGER_ATR": ("scripts/trader/scale_out.py", "SCALE_OUT_TRIGGER_ATR"),
        "R20_MAX_RISK_REWARD": ("scripts/risk_constants.py", "MAX_RISK_REWARD_RATIO"),
        "R20_STOP_LOSS_ATR_MULT": ("scripts/risk_constants.py", "STOP_LOSS_ATR_MULT"),
        "R20_MAX_TAKE_PROFIT_ATR": ("scripts/risk_constants.py", "MAX_TAKE_PROFIT_ATR"),
    }

    def test_every_knob_has_an_enforcer(self):
        from scripts.risk_constants import RISK_ENV_KEYS
        missing = sorted(set(RISK_ENV_KEYS) - set(self.ENFORCERS))
        self.assertEqual(missing, [], f"这些旋钮没有登记执行者（装饰键风险）: {missing}")

    def test_enforcer_fingerprints_exist_in_source(self):
        for key, (relative, needle) in self.ENFORCERS.items():
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(needle, source, f"{key} 的执行点指纹 {needle} 未出现在 {relative}")

    def test_every_knob_is_visible_in_the_prompt_section(self):
        """提示词必须携带全部旋钮（值由 risk_constants 派生，禁止硬编码）。"""
        import scripts.ai_brain_trader as abt
        from scripts.risk_constants import RISK_ENV_KEYS
        text = abt.build_risk_budget_text(4989.41)
        # 旋钮以中文语义出现，这里用"配置值必须能在这段文本里找到"的方式断言
        import scripts.risk_constants as rc
        values = [
            f"{rc.MAX_SINGLE_ASSET_MARGIN:g}", f"{rc.MAX_LEVERAGE:g}x", f"{rc.MIN_LEVERAGE:g}x",
            f"{rc.MIN_RISK_REWARD_RATIO:.1f}", f"{rc.MIN_ENTRY_CONFIDENCE:g}%",
            f"{rc.MAX_DAILY_LOSS_USDT:g}", f"{rc.MAX_SAME_DIRECTION_POSITIONS}", f"{rc.TIME_STOP_HOURS:g}",
            f"{rc.STOP_COOLDOWN_MINUTES}", f"{rc.MIN_SCALE_IN_CONFIDENCE:g}%",
            f"{rc.SCALE_OUT_TRIGGER_ATR:g}x ATR",
        ]
        missing = [v for v in values if v not in text]
        self.assertEqual(missing, [], f"提示词小节缺这些生效值: {missing}")
        self.assertEqual(len(self.ENFORCERS), len(set(RISK_ENV_KEYS)))

    def test_no_hardcoded_conflicting_thresholds_in_constitution(self):
        """P3-4：宪法里不得再出现与可配门禁冲突的硬编码 R:R / 置信度区间。"""
        import scripts.ai_brain_trader as abt
        text = abt.SYSTEM_PROMPT
        for needle in ("78% ~ 88%", "R:R ≥ 2.2", "R:R ≥ 2.5", "5%~10%", "3%~12%"):
            self.assertNotIn(needle, text, f"宪法残留硬编码阈值 {needle}（应与【本周期风险预算】同源）")
        budget = abt.build_risk_budget_text(5000.0)
        self.assertIn("目标盈亏比", budget, "目标 R:R 必须由风险预算小节派生")
        self.assertIn("置信度标定带", budget)

    def test_live_effective_prompt_has_no_stale_thresholds(self):
        """生效提示词（含提示词方案库/profile/覆盖层）同样不得残留旧硬编码。"""
        import scripts.ai_brain_trader as abt
        text = abt.get_effective_system_prompt()
        stale = [n for n in ("78% ~ 88%", "R:R ≥ 2.2", "R:R ≥ 2.5") if n in text]
        self.assertEqual(stale, [], f"生效提示词残留旧硬编码: {stale}")

    def test_risk_page_schema_covers_every_knob(self):
        from r20_backend import risk_config
        from scripts.risk_constants import RISK_ENV_KEYS
        schema_keys = {p["key"] for p in risk_config.schema()["params"]}
        self.assertEqual(sorted(set(RISK_ENV_KEYS) - schema_keys), [], "风控页缺可配置项")


if __name__ == "__main__":
    unittest.main()
