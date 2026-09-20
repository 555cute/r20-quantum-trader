"""批1 · 策略配置 P0 加固回归（审计 2026-09-13）。

覆盖四条 P0（每条都对应一次真实事故/可复现缺陷）：
1. P0-4 策略回滚成功后不再报 HTTP 500（曾因 `payload.hash` AttributeError 把
   「已改完 6 个存储」的破坏性操作报成失败，且 policy.restore 审计永不落库）；
2. P0-2 `.env` 读-改-写必须整体持 flock（曾并发丢配置，且 UI 显示已生效）；
3. P0-1 多所下单保证金闸门（曾把 LLM 原始 margin 直接 ×leverage 当名义额，
   OKX 那套权益占比/单标的封顶在多所路径完全不存在）；
4. P0-3 归档标识必须覆盖整包内容（曾只差风控/路由的两个版本同名互相覆盖，
   且回滚后的哈希校验对风控恢复失败完全失明）。

封闭性：全部沙箱化（ENV_FILE → tmp、data/* 路径 → tmp、load_dotenv 置空、
合约目录对账打桩），零网络、零生产文件读写。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi.testclient import TestClient

import r20_backend.app as app_module
import r20_backend.settings_store as settings_store
from r20_backend import policy_snapshot as ps
from r20_backend.admin_auth import AdminAuthStore
from r20_backend.schemas import PolicyRestoreRequest
from r20_backend.exchanges import routing_policy
from scripts.risk_constants import RISK_ENV_KEYS

RISK_KEYS = set(RISK_ENV_KEYS)


class _SandboxBase(unittest.TestCase):
    """共享沙箱：config 路径隔离 + .env 隔离 + 风控 env 快照清空。"""

    def setUp(self):
        from tests.config_sandbox import isolate_config
        self.root = isolate_config(self)
        self.temp = tempfile.TemporaryDirectory(prefix="r20-b1-")
        self.addCleanup(self.temp.cleanup)
        # .env 不在 data/ 下，config_sandbox 不会替换 → 必须显式沙箱化
        self.env_file = Path(self.temp.name) / ".env"
        self.env_file.write_text("", encoding="utf-8")
        p = patch.object(settings_store, "ENV_FILE", self.env_file)
        p.start(); self.addCleanup(p.stop)
        # 生产 .env 回灌隔离
        import r20_backend.config as backend_config
        self.orig_loader = backend_config.load_dotenv
        backend_config.load_dotenv = lambda path: None
        self.addCleanup(lambda: setattr(backend_config, "load_dotenv", self.orig_loader))
        # venue_routing 显式沙箱化（trace 到真实文件就是事故）
        self.routing_file = Path(self.root) / "data" / "venue_routing.json"
        p = patch.object(routing_policy, "ROUTING_FILE", self.routing_file)
        p.start(); self.addCleanup(p.stop)
        self.saved_env = {k: os.environ.pop(k, None) for k in RISK_KEYS}

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# =========================================================================
# P0-2 · .env 读-改-写并发
# =========================================================================

class EnvWriteLockTests(_SandboxBase):
    """并发保存必须串行化：后写者要基于前写者的最新文本，而不是各自的旧副本。"""

    def _slow_read(self, delay: float = 0.25):
        """把 RMW 窗口拉宽，让无锁实现必然丢更新（有锁则被串行化）。"""
        real_read = Path.read_text

        def slow(self, *a, **k):
            text = real_read(self, *a, **k)
            if Path(str(self)) == settings_store.ENV_FILE:
                time.sleep(delay)
            return text
        return slow

    def setUp(self):
        super().setUp()
        # 沙箱里不需要真实 refresh_settings 的副作用
        p = patch.object(settings_store, "refresh_settings", lambda: None)
        p.start(); self.addCleanup(p.stop)
        self.env_file.write_text("R20_MAX_LEVERAGE=5.0\nLLM_MODEL=old\n", encoding="utf-8")

    def test_concurrent_updates_do_not_lose_config(self):
        with patch.object(Path, "read_text", self._slow_read()):
            t1 = threading.Thread(target=settings_store.update_env, args=({"R20_MAX_LEVERAGE": "7.0"},))
            t2 = threading.Thread(target=settings_store.update_env, args=({"LLM_MODEL": "new-model"},))
            t1.start(); time.sleep(0.05); t2.start(); t1.join(); t2.join()
        final = self.env_file.read_text(encoding="utf-8")
        self.assertIn("R20_MAX_LEVERAGE=7.0", final, "风控保存被并发写静默丢弃（P0-2 回归）")
        self.assertIn("LLM_MODEL=new-model", final, "通知/模型保存被并发写静默丢弃")

    def test_concurrent_update_and_remove_are_serialized(self):
        self.env_file.write_text("R20_MAX_LEVERAGE=5.0\nLLM_MODEL=keep\n", encoding="utf-8")
        with patch.object(Path, "read_text", self._slow_read()):
            t1 = threading.Thread(target=settings_store.update_env, args=({"R20_MAX_LEVERAGE": "9.0"},))
            t2 = threading.Thread(target=settings_store.remove_env, args=({"LLM_MODEL"},))
            t1.start(); time.sleep(0.05); t2.start(); t1.join(); t2.join()
        final = self.env_file.read_text(encoding="utf-8")
        self.assertIn("R20_MAX_LEVERAGE=9.0", final)
        self.assertNotIn("LLM_MODEL", final)

    def test_rmw_holds_process_lock_and_not_only_the_write(self):
        """反漂移：锁必须包住整个 RMW；只锁写那一半等价于另一种丢更新。"""
        entered = []
        real_lock = settings_store.file_lock

        def spy(target):
            entered.append(str(target))
            return real_lock(target)

        with patch.object(settings_store, "file_lock", spy):
            settings_store.update_env({"R20_MAX_LEVERAGE": "6.0"})
            settings_store.remove_env({"R20_MAX_LEVERAGE"})
        self.assertEqual(len(entered), 2, "update_env/remove_env 必须各自持锁一次")
        self.assertTrue(all(str(settings_store.ENV_FILE) == e for e in entered))
        source = (ROOT / "r20_backend" / "settings_store.py").read_text(encoding="utf-8")
        self.assertIn("with file_lock(ENV_FILE):", source)


# =========================================================================
# P0-1 · 多所下单保证金闸门
# =========================================================================

class OrderMarginGateTests(_SandboxBase):
    def setUp(self):
        super().setUp()
        import ai_factor_trader as trader
        self.trader = trader

    def test_size_implied_margin_is_the_tightest_cap(self):
        """AI 计划额再大也不得超过「执行层已夹张数」隐含的保证金。"""
        got = self.trader.order_margin_gate(
            3011.1, size=100.0, price=1.0, ct_val=1.0, leverage=3.0, usdt_available=4989.41)
        self.assertAlmostEqual(got, round(100.0 / 3.0, 4), places=4)

    def test_single_asset_absolute_cap_binds(self):
        with patch.object(self.trader, "MAX_SINGLE_ASSET_MARGIN", 600.0):
            got = self.trader.order_margin_gate(
                3011.1, size=1000000.0, price=1.0, ct_val=1.0,
                leverage=1.0, usdt_available=4989.41)
        self.assertEqual(got, 600.0, "单标的绝对封顶未生效（账本曾出现 3011U 单笔多所持仓）")

    def test_equity_ratio_cap_binds_when_tighter_than_absolute(self):
        with patch.object(self.trader, "MAX_SINGLE_ASSET_MARGIN", 5000.0), \
             patch.object(self.trader, "MAX_MARGIN_EQUITY_RATIO", 0.20):
            got = self.trader.order_margin_gate(
                3000.0, size=1000000.0, price=1.0, ct_val=1.0,
                leverage=1.0, usdt_available=1000.0)
        self.assertEqual(got, 200.0)

    def test_missing_equity_does_not_fabricate_a_ratio_cap(self):
        """缺失≠0：权益不可得时不臆造占比上限，但绝对封顶仍必须兜底。"""
        with patch.object(self.trader, "MAX_SINGLE_ASSET_MARGIN", 600.0):
            got = self.trader.order_margin_gate(
                3011.1, size=1000000.0, price=1.0, ct_val=1.0,
                leverage=1.0, usdt_available=0.0)
        self.assertEqual(got, 600.0)
        self.assertEqual(self.trader.equity_margin_cap(0.0), 0.0)

    def test_planned_zero_falls_back_to_size_implied(self):
        got = self.trader.order_margin_gate(
            0.0, size=30.0, price=10.0, ct_val=1.0, leverage=3.0, usdt_available=100000.0)
        self.assertAlmostEqual(got, 100.0, places=4)

    def test_both_call_sites_pass_gate_and_equity_cap(self):
        """反漂移：多空两条开仓路径都要带闸门与权益顶（漏一条就是裸奔）。

        ## 为什么不是"门面单文件里 `order_margin_gate(` 恰 3 次"

        原来的写法是 `source.count("order_margin_gate(") == 3  # 1 定义 + 开多 + 开空`。
        它把**三件不同的事**压进了一个数字：闸门有定义、开多过闸、开空过闸。
        后果是：只要有人把长/空开仓块抽进子包，计数立刻变 1 → 翻红，而那**恰恰是
        结构优化想要的**（`scripts/trader/` 已按 B3 抽取）。一条把"可维护性改进"
        误判为"风控回归"的锚点，会逼着后人别去拆 —— 这就是它一直在挡路的原因。

        现改为把三件事**分别**断言，各自用最贴切的定位：

        | 断言 | 定位 | 表达的不变量 |
        |---|---|---|
        | 门面里恰有 1 个 `def order_margin_gate(` | 门面文本 | 门面仍暴露该闸门（调用点按全局名解析） |
        | 入场执行模块里恰有 2 处 `_order_margin = order_margin_gate(` | `scripts/trader/entry_execution.py` | 开多/开空都在**入场主执行路径**上过闸 |
        | 领域 AST：`defs == 2` 且 `refs == 2` | 领域（含 `scripts/trader/`） | 恰"一份薄壳 + 一份实现"、"开多 + 开空"两次调用 |

        ⚠️ 第九十刀：`execute_portfolio` 的入场循环整体搬入
        `scripts/trader/entry_execution.py` ⇒ 上述第二行判据的**载体随实现迁移**
        （数字 2 与语义一字不变），门面侧加反证防"残留/孪生"虚 Hits。

        最后一行是关键：它把"搬家"变成**允许**（定义搬进子包 → defs 仍是 2），
        同时仍能抓住"漏了一条路径"（refs 变 1）或"又写了一份本地孪生"（defs 变 3）。

        **为什么不直接文本计数**：实测 `order_margin_gate` 在领域文本里出现 7 次，
        而代码里只有 1 定义 + 2 调用 —— 其余 4 次全在 `gates.py` / `__init__.py` 的
        注释与 docstring 里（它们解释的正是这条锚点本身）。文本数字会把
        "文档写得多细"变成测试条件，所以领域侧一律走 AST。
        """
        from tests.source_scan import count_name_references, count_keyword_argument

        facade = (ROOT / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8")
        self.assertEqual(facade.count("def order_margin_gate("), 1,
                         "门面必须恰有一处 def order_margin_gate（否则全局名解析不到）")
        entry = (ROOT / "scripts" / "trader" / "entry_execution.py").read_text(encoding="utf-8")
        self.assertEqual(entry.count("_order_margin = order_margin_gate("), 2,
                         "开多/开空必须各自在入场主执行路径上经过 order_margin_gate"
                         "（第九十刀：载体随入场循环迁入 entry_execution.py）")
        self.assertNotIn("_order_margin = order_margin_gate(", facade,
                         "门面残留该行 ⇒ 载体迁移不彻底（或出现孪生）")
        # 权益顶：改走 AST 计数 —— 原来是整行字面量
        # `'"max_margin_usdt": equity_margin_cap(usdt_available)'`，
        # 抽取时只是把该行拆成两行，计数就从 2 变 0：**行为没变，排版一变就翻红**。
        # 这类锚点与"允许格式化/抽公共代码"直接冲突，故换成不受换行影响的 AST 计数，
        # 同时钉住实参内容必须是 equity_margin_cap(usdt_available)。
        self.assertEqual(
            count_keyword_argument("scripts/ai_factor_trader.py", "build_order_intent",
                                   "max_margin_usdt",
                                   value_must_contain="equity_margin_cap(usdt_available)",
                                   pkg_name="trader"),
            2, "开多/开空必须各自携带权益顶（AST 计数，不受换行影响；域定位含子包）")

        counts = count_name_references("scripts/ai_factor_trader.py", "order_margin_gate",
                                       pkg_name="trader")
        self.assertEqual(counts["defs"], 2,
                         "全交易层领域必须恰有 2 个 order_margin_gate 定义"
                         "（门面薄壳 + 子包实现）；多了是本地孪生")
        self.assertEqual(counts["refs"], 3,
                         "全交易层领域必须恰有 3 次 order_margin_gate 引用："
                         "开多调用 1 + 开空调用 1（均在 entry_execution.py）"
                         " + 第九十刀新增的**调用点注入** 1"
                         "（`order_margin_gate=order_margin_gate`，证明闸门被传入入场模块）；"
                         "少了说明有条路径被绕过，多了说明出现本地孪生")
        entry_src = (ROOT / "scripts" / "trader" / "entry_execution.py").read_text(encoding="utf-8")
        self.assertEqual(entry_src.count("order_margin_gate("), 2,
                         "开多/开空必须各自在入场执行模块里调用闸门")
        facade_src = (ROOT / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8")
        self.assertIn("order_margin_gate=order_margin_gate", facade_src,
                      "门面必须把闸门注入入场模块（调用期解析，patch 面有效）")


class RouterMarginClampTests(_SandboxBase):
    """execution_router 侧兜底：调用方漏传也不允许把任意 margin 变成名义额。"""

    @classmethod
    def setUpClass(cls):
        from r20_backend.exchanges import listing as _listing
        cls._lp = patch.object(_listing, "ensure_contract_listed",
                               lambda *a, **k: _listing.ListingCheck(
                                   ok=True, reason=None, checked_at="", source="cache"))
        cls._lp.start()

    @classmethod
    def tearDownClass(cls):
        cls._lp.stop()

    def setUp(self):
        super().setUp()
        from r20_backend import execution_router as router
        self.router = router
        self._ambient = {k: v for k, v in os.environ.items()
                         if k.startswith(("R20_GATE_TESTNET", "R20_GATE_EXECUTION",
                                          "R20_BINANCE_TESTNET", "R20_BINANCE_DEMO_EXECUTION"))}
        for k in self._ambient:
            os.environ.pop(k, None)
        self.addCleanup(lambda: os.environ.update(self._ambient))
        # 本类只钉"保证金夹取链"：把每所池门禁（P1-7 新增的 dry_run/资产/上限/置信度）
        # 置空，避免测试依赖生产 data/venue_routing.json 与 Gate 凭证就绪态。
        p = patch.object(router, "_load_venue_pool_soft", lambda venue: {})
        p.start(); self.addCleanup(p.stop)

    def _stub_adapter(self):
        from r20_backend.exchanges.gate import GateAdapter

        class _Stub(GateAdapter):
            """只打桩私有 IO / 规格 / 行情，保护单与名义额换算走真实基类实现。"""

            def __init__(self):
                self.calls = []
                self.price_orders = []

            def _keys(self):
                return ("k", "s")

            def detect_position_mode(self):
                # 第八刀：router 新增持仓模式只读体检（policy：探测不到就禁新开仓）。
                # 本桩继承真实 GateAdapter（声明 position_modes）但打桩了私有 IO，
                # 探测会返回 unknown ⇒ 整条开仓路径被拒。桩必须像真适配器一样**明确**
                # 给出模式，否则这些用例测的就不再是它们本来要测的东西。
                return "single"

            def positions(self):
                return []

            def fetch_instrument_spec(self, symbol, refresh=False):
                from r20_backend.exchanges import InstrumentSpec
                return InstrumentSpec(venue="gate", inst_id="BTC_USDT", base="BTC",
                                      tick_size=0.1, step_size=0.0001, ct_val=0.0001, min_size=1)

            def fetch_ticker(self, symbol):
                return {"last": 79000.0, "mark_price": 79000.0}

            def set_leverage(self, symbol, leverage, margin_mode="cross"):
                return {"leverage": str(int(leverage))}

            def place_order(self, symbol, side, contracts, price=None, tif="gtc", text=""):
                self.calls.append(("place", symbol, side, contracts, price))
                return {"id": 9001, "text": "t", "size": contracts}

            def attach_protective_orders(self, symbol, pos_side, tp_px=None, sl_px=None,
                                         expiration=604800, price_type=0):
                self.price_orders = [{"id": "tp1"}, {"id": "sl1"}]
                return {"tp": "tp1", "sl": "sl1"}

            def list_protective_orders(self, symbol):
                return list(self.price_orders)

            def cancel_order(self, symbol, order_id):
                return {"cancelled": True}

        return _Stub()

    @staticmethod
    def _decision(**over):
        d = {"asset": "BTC", "action": "BUY_LONG", "margin_usdt": 5000.0, "leverage": 3,
             "entry_price": 79000.0, "take_profit_price": 85000.0, "stop_loss_price": 77000.0}
        d.update(over)
        return d

    def test_router_clamps_to_caller_equity_cap(self):
        ad = self._stub_adapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = self.router.open_protected_position(
                self._decision(max_margin_usdt=200.0), adapter=ad, price_ref=79000.0)
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(r["margin_usdt"], 200.0)
        self.assertEqual(r["margin_clamped_from_usdt"], 5000.0)
        # 200U × 3x = 600U 名义 @79000、每张面值 0.0001 → 75.95 张 → **75**
        # （向下取整，第一百五十三刀用户拍板；原四舍五入→76，会最坏向上多买半张、
        #   在大面值标的上使实际名义超出按笔保证金上限）
        self.assertEqual([c for c in ad.calls if c[0] == "place"][0][3], 75)

    def test_router_applies_absolute_cap_even_without_caller_cap(self):
        ad = self._stub_adapter()
        with patch.object(self.router, "MAX_SINGLE_ASSET_MARGIN", 100.0), \
             patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = self.router.open_protected_position(
                self._decision(), adapter=ad, price_ref=79000.0)
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(r["margin_usdt"], 100.0)
        self.assertEqual(r["margin_clamped_from_usdt"], 5000.0)

    def test_router_keeps_margin_when_within_caps(self):
        ad = self._stub_adapter()
        with patch.dict(os.environ, {"R20_GATE_EXECUTION": "1"}):
            r = self.router.open_protected_position(
                self._decision(margin_usdt=150.0, max_margin_usdt=200.0),
                adapter=ad, price_ref=79000.0)
        self.assertTrue(r["ok"], r.get("detail"))
        self.assertEqual(r["margin_usdt"], 150.0)
        self.assertIsNone(r["margin_clamped_from_usdt"])


# =========================================================================
# P0-3 · 归档标识覆盖整包内容
# =========================================================================

class PolicyPackageIdentityTests(_SandboxBase):
    @staticmethod
    def _payload(risk_leverage: float = 5.0, preferred: str = "auto") -> dict:
        return {
            "prompt_config": {"active_profile_id": "stable",
                              "profiles": {"stable": {"id": "stable", "name": "S",
                                                      "pipelines": {"trading_system": []},
                                                      "created_at": "t1", "updated_at": "t1"}}},
            "evolution_memory": {"schema_version": 1, "revision": "r1",
                                 "lessons": [{"id": "l1", "rule_text": "顺势", "enabled": True,
                                              "health_score": 90.0, "shield_status": "PASSED"}]},
            "interceptor_config": {"pipeline_order": [], "enabled": {}},
            "council_config": {"enabled": False, "consensus_mode": "standard",
                               "timeout_seconds": 240.0, "updated_at": "t1",
                               "roles": {"cio": {"id": "cio", "enabled": True, "is_arbitrator": True,
                                                 "prompt": "p", "model_id": "m"}}},
            "risk_config": {"R20_MAX_LEVERAGE": risk_leverage,
                            "R20_MAX_DAILY_LOSS_USDT": 150.0},
            "venue_routing": {"preferred_venue": preferred, "routing_mode": "balanced"},
        }

    def test_risk_only_change_changes_identity(self):
        a = ps.package_identity(self._payload(risk_leverage=5.0))
        b = ps.package_identity(self._payload(risk_leverage=2.0))
        self.assertNotEqual(a, b, "只差风控的版本被判为同一版本 → 归档互相覆盖（P0-3 回归）")
        self.assertEqual(len(a), 16)

    def test_routing_only_change_changes_identity(self):
        a = ps.package_identity(self._payload(preferred="auto"))
        b = ps.package_identity(self._payload(preferred="gate"))
        self.assertNotEqual(a, b)

    def test_identity_ignores_volatile_fields(self):
        """时间戳/评分/revision 每次写都会变，绝不能进标识（否则回滚校验必然误报）。"""
        first = self._payload()
        second = json.loads(json.dumps(first))
        second["council_config"]["updated_at"] = "2099-01-01 00:00:00"
        second["evolution_memory"]["revision"] = "r999"
        second["evolution_memory"]["lessons"][0]["health_score"] = 42.0
        second["evolution_memory"]["lessons"][0]["shield_status"] = "REJECTED"
        second["prompt_config"]["profiles"]["stable"]["updated_at"] = "2099-01-01 00:00:00"
        self.assertEqual(ps.package_identity(first), ps.package_identity(second))

    def test_restore_diff_names_the_failed_unit(self):
        archived = self._payload(risk_leverage=5.0)
        current = self._payload(risk_leverage=2.0)
        self.assertEqual(ps.package_restore_diff(archived, current), ["risk_config.R20_MAX_LEVERAGE"])

    def test_restore_diff_ignores_keys_absent_from_archive(self):
        """旧包不可能恢复「它诞生之后才新增的键」，这不算恢复失败（但要如实披露）。"""
        archived = self._payload()
        current = json.loads(json.dumps(archived))
        current["risk_config"]["R20_NEW_KEY_AFTER_ARCHIVE"] = 1.0
        self.assertEqual(ps.package_restore_diff(archived, current), [])

    def test_restore_diff_skips_units_absent_from_archive(self):
        """归档没装的单元不能判成恢复失败（旧包无法清空它诞生之后才有的内容）。"""
        archived = {"risk_config": {"A": 1}, "interceptor_config": {}}
        current = {"risk_config": {"A": 1, "B": 2},
                   "interceptor_config": {"pipeline_order": ["x"], "enabled": {"x": True}}}
        self.assertEqual(ps.package_restore_diff(archived, current), [])
        self.assertEqual(ps.package_restore_diff({"risk_config": {"A": 1}}, {"risk_config": {"A": 9, "B": 2}}),
                         ["risk_config.A"])

    def test_restore_diff_detects_prompt_drift(self):
        archived = self._payload()
        current = json.loads(json.dumps(archived))
        current["prompt_config"]["active_profile_id"] = "other"
        self.assertEqual(ps.package_restore_diff(archived, current), ["prompt_config"])

    # ── 端到端：归档 → 改风控 → 回滚 ──
    def test_archive_then_restore_round_trip_recovers_risk_config(self):
        from r20_backend import risk_config
        settings_store.update_env({"R20_MAX_LEVERAGE": "4.0"})
        entry = ps.archive_current_policy(name="A", archive_dir=ps.ARCHIVE_DIR,
                                         root_dir=Path(self.root))
        self.assertTrue(entry.get("package_hash"))
        settings_store.update_env({"R20_MAX_LEVERAGE": "2.0"})
        self.assertEqual(risk_config.current_values()["R20_MAX_LEVERAGE"], 2.0)

        res = ps.restore_archived_policy(policy_hash=entry["package_hash"],
                                        archive_dir=ps.ARCHIVE_DIR, root_dir=Path(self.root))
        self.assertEqual(res["status"], "restored")
        self.assertEqual(risk_config.current_values()["R20_MAX_LEVERAGE"], 4.0,
                         "回滚没收复风控值")

    def test_two_risk_variants_do_not_overwrite_each_other(self):
        settings_store.update_env({"R20_MAX_LEVERAGE": "4.0"})
        first = ps.archive_current_policy(name="稳健", archive_dir=ps.ARCHIVE_DIR,
                                         root_dir=Path(self.root))
        settings_store.update_env({"R20_MAX_LEVERAGE": "2.0"})
        second = ps.archive_current_policy(name="激进", archive_dir=ps.ARCHIVE_DIR,
                                          root_dir=Path(self.root))
        self.assertNotEqual(first["package_hash"], second["package_hash"])
        files = sorted(p.name for p in Path(ps.ARCHIVE_DIR).glob("policy_*.json"))
        self.assertEqual(len(files), 2, f"只差风控的两个版本被写进同一个文件: {files}")
        index = ps.load_archive_index(archive_dir=ps.ARCHIVE_DIR)
        self.assertEqual(len(index), 2, "索引项被同 hash 覆盖，旧版本从列表消失")

        from r20_backend import risk_config
        ps.restore_archived_policy(policy_hash=first["package_hash"],
                                   archive_dir=ps.ARCHIVE_DIR, root_dir=Path(self.root))
        self.assertEqual(risk_config.current_values()["R20_MAX_LEVERAGE"], 4.0,
                         "回滚第一版时恢复成了第二版的风控（版本标识未覆盖风控）")

    def test_legacy_hash_named_archive_still_restorable(self):
        """向后兼容：历史归档以 policy_hash 命名，索引两种标识都要能解析到。"""
        settings_store.update_env({"R20_MAX_LEVERAGE": "4.0"})
        package = ps.capture_full_strategy_package(root_dir=Path(self.root))
        legacy = Path(ps.ARCHIVE_DIR)
        legacy.mkdir(parents=True, exist_ok=True)
        fname = f"policy_{package['policy_hash']}.json"
        package["metadata"] = {"name": "legacy", "archive_file": fname}
        (legacy / fname).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
        ps.save_archive_index([{
            "policy_version": package["policy_version"], "policy_hash": package["policy_hash"],
            "name": "legacy", "description": "", "author": "t", "archived_at": "2026-09-01 00:00:00",
            "summary": package["summary"], "archive_file": fname}], archive_dir=legacy)

        settings_store.update_env({"R20_MAX_LEVERAGE": "2.0"})
        res = ps.restore_archived_policy(policy_hash=package["policy_hash"],
                                        archive_dir=legacy, root_dir=Path(self.root))
        self.assertEqual(res["status"], "restored")
        from r20_backend import risk_config
        self.assertEqual(risk_config.current_values()["R20_MAX_LEVERAGE"], 4.0)


class PolicyRestoreRouteTests(_SandboxBase):
    """P0-4：回滚成功后必须 200 + ok，而不是「状态已改、接口报失败」。"""

    def setUp(self):
        super().setUp()
        # 路由不带 root_dir → restore 用模块级 ROOT（真实项目根）重算四单元指纹，
        # 沙箱下必然与归档不一致。把 ROOT 也指向沙箱，才是在测「回滚本身」而不是路径。
        p = patch.object(ps, "ROOT", Path(self.root))
        p.start(); self.addCleanup(p.stop)
        self.orig_auth = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.addCleanup(lambda: setattr(app_module, "admin_auth", self.orig_auth))
        self.client = TestClient(app_module.app)

    def _session(self) -> dict[str, str]:
        res = self.client.post("/api/v1/admin/auth/login",
                               json={"username": "admin", "password": "InitialAdmin123456"})
        self.assertEqual(res.status_code, 200, res.text)
        return {"X-R20-Session": res.json()["session_token"]}

    def test_schema_has_no_hash_attribute(self):
        """契约钉：hash 只是请求别名（model_validator 归一到 policy_hash），不是属性。"""
        self.assertEqual(list(PolicyRestoreRequest.model_fields), ["policy_hash"])
        self.assertFalse(hasattr(PolicyRestoreRequest(policy_hash="abcdef12"), "hash"))

    def test_route_does_not_reference_payload_hash(self):
        # 第九十六刀：strategy 已拆包 ⇒ 按**域**取源（不绑文件位置）
        from tests.source_scan import router_domain_source
        source = router_domain_source("strategy", root=ROOT)
        self.assertNotIn("payload.hash", source,
                         "回滚审计又用了不存在的 payload.hash（P0-4 回归）")
        self.assertIn('"policy_hash": p_hash', source)

    def test_successful_restore_returns_200_and_is_audited(self):
        settings_store.update_env({"R20_MAX_LEVERAGE": "4.0"})
        entry = ps.archive_current_policy(name="回滚点", archive_dir=ps.ARCHIVE_DIR,
                                         root_dir=Path(self.root))
        settings_store.update_env({"R20_MAX_LEVERAGE": "2.0"})
        audit_rows: list[tuple] = []
        # strategy 路由持有自己的 audit_record 绑定（from ... import record as ...），
        # patch app 模块的绑定拦不到，必须打路由模块。
        # 第九十六刀：strategy 拆包 ⇒ patch 目标必须落到**归属子模块**
        # （`policy.py` 持有自己的 audit_record 绑定；打包属性拦不到）
        from r20_backend.routers.strategy import policy as strategy_policy
        with patch.object(strategy_policy, "audit_record",
                          lambda action, status, payload=None, **kw: audit_rows.append((action, status, payload))):
            res = self.client.post("/api/v1/admin/policy/restore",
                                   headers=self._session(),
                                   json={"policy_hash": entry["package_hash"]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json().get("ok"), res.text)
        from r20_backend import risk_config
        self.assertEqual(risk_config.current_values()["R20_MAX_LEVERAGE"], 4.0)
        restored = [row for row in audit_rows if row[0] == "policy.restore"]
        self.assertTrue(restored, "policy.restore 审计未落库")
        self.assertEqual(restored[0][1], "success")
        self.assertEqual(restored[0][2]["actor"], "admin")


if __name__ == "__main__":
    unittest.main()
