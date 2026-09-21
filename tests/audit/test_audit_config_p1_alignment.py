"""批2 · 策略配置 P1 口径对齐回归（审计 2026-09-13）。

每条 P1 都对应「系统对外说的」与「引擎实际做的」不一致——即 UI/提示词在说谎：

- P1-1 提示词风控口径 ≠ 引擎口径：提示词写「单标的 1496.82U / 日亏 −249.47U」，
  引擎执行 min(600, 1496.82)=600 / min(150, 249.47)=150（虚高 2.49× / 1.66×），
  而 SYSTEM PROMPT 要求模型"一切金额以该小节为准"。
- P1-2 单页保存把**别的**管线整段翻倍（保存心法页 → 交易提示词 ×2.00）。
- P1-3 管理员提示词覆盖层根本没进最终 prompt（接口却承诺"将自动叠加"）。
- P1-6 组合风险预算 4800 是 UI 编的（引擎 0=不封顶）。
- P1-9 Evolution/快照页编造审计证据。

封闭性：全部沙箱化（data/* 路径 + .env 重定向，零网络）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（第二百三十六刀）：
    本文件把**线上**提示词库抄进沙箱，核对线上布局与契约对齐 —— 有意的线上守卫。

    只读、不改；声明在此是为了把「依赖线上配置内容」从**静默**变成**可审计**
    （守卫见 `tests/__init__.py`；`R20_TESTS_STRICT_READS=1` 下未声明的读会报错）。
    """
    global _READ_SCOPE
    from tests import allow_real_data_reads
    _READ_SCOPE = allow_real_data_reads()
    _READ_SCOPE.__enter__()


def tearDownModule():
    global _READ_SCOPE
    if _READ_SCOPE is not None:
        _READ_SCOPE.__exit__(None, None, None)
        _READ_SCOPE = None


class _SandboxBase(unittest.TestCase):
    def setUp(self):
        # 先导入相关模块再隔离：isolate_config 只重定向**已导入**模块里的 data/ 路径，
        # 后导入的模块会保留真实路径（测试就会去读/写生产文件）。
        import ai_brain_trader  # noqa: F401
        import prompt_library  # noqa: F401
        # 路由走 `from scripts.ai_brain_trader import ...`（与顶层 ai_brain_trader 是
        # 两个模块实例）→ 两个实例都必须先导入，隔离才会同时改到它们的 data/ 路径。
        import scripts.ai_brain_trader  # noqa: F401
        import r20_backend.app  # noqa: F401（连带 routers/dependencies：覆盖层路径也在其中）
        import r20_backend.settings_store  # noqa: F401
        from tests.config_sandbox import isolate_config
        self.root = isolate_config(self)
        import r20_backend.settings_store as settings_store
        self.temp = tempfile.TemporaryDirectory(prefix="r20-b2-")
        self.addCleanup(self.temp.cleanup)
        self.env_file = Path(self.temp.name) / ".env"
        self.env_file.write_text("", encoding="utf-8")
        p = patch.object(settings_store, "ENV_FILE", self.env_file)
        p.start(); self.addCleanup(p.stop)


# =========================================================================
# P1-1 提示词口径 == 引擎口径
# =========================================================================

class PromptRiskBudgetAlignmentTests(_SandboxBase):
    def setUp(self):
        super().setUp()
        import ai_brain_trader as brain
        from scripts import risk_constants as rc
        self.brain = brain
        self.rc = rc

    def test_capped_values_match_engine(self):
        """回归：提示词里的单标的封顶/日亏熔断必须等于引擎 min() 后的真实值。"""
        eq = 4989.41
        text = self.brain.build_risk_budget_text(eq)
        asset_cap = self.rc.effective_single_asset_margin(eq)
        daily_cap = self.rc.effective_daily_loss_limit(eq)
        self.assertEqual(asset_cap, 600.0, "前置：本例中绝对封顶应生效")
        self.assertEqual(daily_cap, 150.0)
        self.assertIn(f"{asset_cap} USDT", text)
        self.assertIn(f"-{daily_cap} USDT", text)
        # 旧实现在此写的是未取 min 的「权益×30% / 权益×5%」
        self.assertNotIn("1496.82", text, "单标的封顶又回到未取 min 的虚高值（P1-1 回归）")
        self.assertNotIn("249.47", text, "日亏熔断线又回到未取 min 的虚高值（P1-1 回归）")

    def test_single_order_cap_is_min_of_equity_and_asset_cap(self):
        """单笔上限同样受单标的累计封顶约束（首笔即计入累计），不得只写权益 20%。"""
        eq = 4989.41
        text = self.brain.build_risk_budget_text(eq)
        line = next(l for l in text.splitlines() if l.startswith("- 强信号单笔保证金上限"))
        self.assertIn("600.0", line)
        self.assertIn("min(", line)
        self.assertNotIn("997.88 USDT (20%", line)

    def test_small_account_uses_ratio_when_absolute_cap_not_binding(self):
        eq = 80.0
        text = self.brain.build_risk_budget_text(eq)
        self.assertIn(f"{self.rc.effective_single_asset_margin(eq)} USDT", text)  # 24.0
        self.assertIn(f"-{self.rc.effective_daily_loss_limit(eq)} USDT", text)   # -4.0
        self.assertIn("小资金账户提示", text)

    def test_missing_equity_is_marked_not_fabricated(self):
        for missing in (None, -1.0):
            self.assertEqual(self.brain.build_risk_budget_text(missing), "[MISSING_CONTEXT:risk_budget]")

    def test_all_20_knobs_are_visible_to_the_model(self):
        """覆盖扫描：全部旋钮（含此前完全不可见的 5 个）都必须在小节里露面。

        批4 P2-1 新增第 20 个旋钮 R20_MAX_TOTAL_EXPOSURE_USDT（跨所同向敞口上限），
        它此前只在 MANAGED_KEYS 里、零消费者；现在执行层真拒绝且提示词同源披露。"""
        text = self.brain.build_risk_budget_text(4989.41)
        required = {
            "R20_MAX_MARGIN_EQUITY_RATIO": "强信号单笔保证金上限",
            "R20_RISK_PER_TRADE_RATIO": "单笔最大可承受亏损",
            "R20_MAX_SAME_DIRECTION_POSITIONS": "全系统同向持仓上限",
            "R20_MAX_CONCURRENT_POSITIONS": "全系统并发持仓上限",
            "R20_PORTFOLIO_RISK_BUDGET_USDT": "组合风险总预算",
            "R20_SINGLE_ASSET_EQUITY_RATIO": f"可用余额 {self.rc.SINGLE_ASSET_EQUITY_RATIO:.0%}",
            "R20_MAX_SINGLE_ASSET_MARGIN_USDT": f"{self.rc.MAX_SINGLE_ASSET_MARGIN:g} 绝对封顶",
            "R20_MAX_LEVERAGE": f"{self.rc.MAX_LEVERAGE:g}x",
            "R20_MIN_LEVERAGE": f"{self.rc.MIN_LEVERAGE:g}x",
            "R20_MIN_RISK_REWARD": f"{self.rc.MIN_RISK_REWARD_RATIO:.1f}",
            "R20_MIN_ENTRY_CONFIDENCE": f"{self.rc.MIN_ENTRY_CONFIDENCE:g}%",
            "R20_MAX_DAILY_LOSS_USDT": f"{self.rc.MAX_DAILY_LOSS_USDT:g} 绝对封顶",
            "R20_DAILY_LOSS_EQUITY_RATIO": f"可用余额 {self.rc.DAILY_LOSS_EQUITY_RATIO:.0%}",
            "R20_TIME_STOP_HOURS": f"{self.rc.TIME_STOP_HOURS:g} 小时",
            "R20_TIME_STOP_ATR_BAND": f"±{self.rc.TIME_STOP_ATR_BAND:.0%} ATR",
            "R20_STOP_COOLDOWN_MINUTES": f"{self.rc.STOP_COOLDOWN_MINUTES} 分钟",
            "R20_MAX_SCALE_IN_COUNT": "金字塔加仓",
            "R20_MIN_SCALE_IN_PROFIT_RATIO": f"{self.rc.MIN_SCALE_IN_PROFIT_RATIO:.1%}",
            "R20_MIN_SCALE_IN_CONFIDENCE": f"{self.rc.MIN_SCALE_IN_CONFIDENCE:g}%",
            "R20_MAX_TOTAL_EXPOSURE_USDT": "跨所同向敞口上限",
            "R20_SCALE_OUT_ENABLED": "分批止盈机制",
            "R20_SCALE_OUT_RATIO": f"{self.rc.SCALE_OUT_RATIO:.0%}",
            "R20_SCALE_OUT_TRIGGER_ATR": f"{self.rc.SCALE_OUT_TRIGGER_ATR:g}x ATR",
            "R20_MAX_RISK_REWARD": f"上限 {self.rc.MAX_RISK_REWARD_RATIO:.1f}",
            "R20_STOP_LOSS_ATR_MULT": f"基准止损 {self.rc.STOP_LOSS_ATR_MULT:g}x 1H ATR",
            "R20_MAX_TAKE_PROFIT_ATR": f"最大止盈宽度 ≤ {self.rc.MAX_TAKE_PROFIT_ATR:g}x 1H ATR",
        }
        self.assertEqual(set(required), set(self.rc.RISK_ENV_KEYS),
                         f"覆盖表必须覆盖全部 {len(self.rc.RISK_ENV_KEYS)} 个旋钮")
        missing = [key for key, needle in required.items() if needle not in text]
        self.assertEqual(missing, [], f"以下旋钮对模型不可见: {missing}")

    def test_engine_prompt_and_execution_share_one_definition(self):
        """反漂移：公式只能定义在 risk_constants 一处，sizing/trader 都只是引用。

        批6 起 `risk_constants` 与 `scripts.risk_constants` 已是**同一个模块对象**
        （模块内把自己登记到两个名字下，先导入者胜出），所以这里可以直接断言对象同一性；
        同时钉住仓位规模三件套不再有本地孪生实现。
        """
        import inspect
        import ai_factor_trader as trader
        from r20_backend.execution import sizing
        for name in ("effective_daily_loss_limit", "effective_single_asset_margin"):
            rc_fn = getattr(self.rc, name)
            self.assertTrue(rc_fn.__module__.endswith("risk_constants"), rc_fn.__module__)
            self.assertTrue(getattr(sizing, name).__module__.endswith("risk_constants"),
                            f"sizing.{name} 又自带实现")
            self.assertTrue(getattr(trader, name).__module__.endswith("risk_constants"),
                            f"trader.{name} 又自带实现")
            self.assertEqual(inspect.getsource(getattr(sizing, name)), inspect.getsource(rc_fn))
            self.assertEqual(inspect.getsource(getattr(trader, name)), inspect.getsource(rc_fn))
            for eq in (80.0, 1000.0, 4989.41, 0.0, None):
                self.assertEqual(getattr(sizing, name)(eq), rc_fn(eq))
                self.assertEqual(getattr(trader, name)(eq), rc_fn(eq))
        # 领域定位：下面两条是**负向**断言（不得自带实现）。单文件定位在搬家后会
        # **静默空转**——本地孪生搬进子包照样违规，却因为门面里找不到而永远通过。
        from tests.source_scan import combined
        trader_src = combined("scripts/ai_factor_trader.py", pkg_name="trader")
        self.assertNotIn("def effective_daily_loss_limit", trader_src,
                         "本地拷贝复活 → 又是两份 min() 公式漂移之源")
        sizing_src = (ROOT / "r20_backend" / "execution" / "sizing.py").read_text(encoding="utf-8")
        self.assertNotIn("def effective_daily_loss_limit", sizing_src)

        # 批6：两个导入名必须是同一个模块对象（曾经是两个各自读一次 .env 的实例）
        import risk_constants as bare_rc
        import scripts.risk_constants as dotted_rc
        self.assertIs(bare_rc, dotted_rc, "risk_constants 又裂成两个实例：单一事实源名不副实")

        # 批6：仓位规模三件套只有一份实现（曾与 sizing.py 逐字重复两份）
        for name in ("quantize_size", "max_size_within_margin", "effective_risk_per_trade"):
            self.assertIs(getattr(trader, name), getattr(sizing, name),
                          f"trader.{name} 不是共享实现，本地孪生复活")
            self.assertNotIn(f"def {name}", trader_src, f"ai_factor_trader 又自带 {name} 实现")

    def test_final_prompt_carries_the_same_capped_values(self):
        """端到端：真正发给模型的整段 prompt 里就是 min() 后的值。"""
        eq = 4989.41
        prompt = self.brain.construct_full_market_prompt([], usdt_available=eq)
        self.assertIn(f"-{self.rc.effective_daily_loss_limit(eq)} USDT", prompt)
        self.assertIn(f"{self.rc.effective_single_asset_margin(eq)} USDT", prompt)
        self.assertNotIn("1496.82", prompt)
        self.assertNotIn("249.47", prompt)


class PipelineMergeNoDoublingTests(_SandboxBase):
    """P1-2：单页保存绝不能改写/降级**其它**管线（否则下一轮布局把 base 整段前置 → 翻倍）。

    两种真实 UI 形状都要钉住：
    - EvolutionPage：`pipelines` 里只有 1 条（activeTab）；
    - PromptStudio：4 条都提交，但视图会把 base 模块的 locked 抹平。
    """

    def setUp(self):
        super().setUp()
        import prompt_library as pl
        self.pl = pl
        self.lib_path = Path(self.root) / "data" / "prompt_library.json"
        self.lib_path.parent.mkdir(parents=True, exist_ok=True)
        self.lib_path.write_text((ROOT / "data" / "prompt_library.json").read_text(encoding="utf-8"),
                                 encoding="utf-8")
        p = patch.object(pl, "LIBRARY_FILE", self.lib_path)
        p.start(); self.addCleanup(p.stop)

    def _shape_evolution(self, profile):
        key = "evolution_system"
        return {key: [{k: m[k] for k in ("id", "title", "content", "enabled", "locked", "source") if k in m}
                      for m in profile["pipelines"][key]]}

    def _shape_studio(self, profile):
        return {k: [{**m, "locked": False} for m in (profile["pipelines"].get(k) or [])]
                for k in self.pl.TEMPLATE_KEYS}

    def _snapshot(self):
        profile = self.pl.active_profile()
        out = {}
        for key in self.pl.TEMPLATE_KEYS:
            modules = profile["pipelines"].get(key) or []
            base = self.pl.compile_modules(modules)
            rendered = self.pl.apply_module_layout(base, profile, key, "t")
            out[key] = (sorted({str(m.get("source")) for m in modules}), len(base), len(rendered))
        return out

    def _assert_unchanged_after(self, changes):
        before = self._snapshot()
        profile_id = self.pl.load_library()["active_profile_id"]
        self.pl.update_profile(profile_id, changes)
        after = self._snapshot()
        self.assertEqual(before, after, "保存单页后其它管线被改写（P1-2 回归）")
        # 渲染长度不得翻倍：布局若丢掉 base 标签，apply_module_layout 会把 base 整段前置
        for key, (_srcs, base_len, rendered_len) in after.items():
            self.assertLess(rendered_len, max(base_len * 1.5, base_len + 400),
                            f"{key} 渲染后长度异常膨胀（base 被重复前置）")
        return after

    def test_evolution_page_shape_does_not_downgrade_other_pipelines(self):
        profile = self.pl.active_profile()
        after = self._assert_unchanged_after({"pipelines": self._shape_evolution(profile)})
        for key in ("trading_system", "trading_user", "evolution_user"):
            self.assertIn("base", after[key][0], f"{key} 的 base 标签被降级成 legacy → 下次布局会翻倍")

    def test_studio_shape_keeps_base_tags_and_does_not_double(self):
        profile = self.pl.active_profile()
        after = self._assert_unchanged_after({"pipelines": self._shape_studio(profile)})
        self.assertIn("base", after["trading_system"][0])
        self.assertIn("base", after["evolution_system"][0])

    def test_submitted_pipeline_without_source_inherits_stored_base_tag(self):
        """API 客户端漏传 source 时，不能把 base 模块悄悄降级成 custom。"""
        profile = self.pl.active_profile()
        stripped = {"trading_system": [{k: m[k] for k in ("id", "title", "content", "enabled") if k in m}
                                       for m in profile["pipelines"]["trading_system"]]}
        profile_id = self.pl.load_library()["active_profile_id"]
        updated = self.pl.update_profile(profile_id, {"pipelines": stripped})
        self.assertEqual(sorted({m["source"] for m in updated["pipelines"]["trading_system"]}), ["base"])

    def test_clean_pipelines_keeps_stored_modules_for_unmentioned_pipeline(self):
        """_clean_pipelines 的兜底分支：已存定义在场时，未提到的管线不得被重建成 legacy。

        （update_profile 走的是逐键 merge；此分支保护 restore_revision / 导入等
        把完整 profile 传进来的调用方。）
        """
        import prompt_library as pl
        stored = {
            "pipelines": {"trading_system": [{"id": "m1", "title": "基准风控", "content": "R:R >= 2.0",
                                              "source": "base", "enabled": True}]},
            "trading_system": "R:R >= 2.0",
        }
        out = pl._clean_pipelines({"evolution_system": []}, stored)
        self.assertEqual([m["source"] for m in out["trading_system"]], ["base"])
        self.assertEqual(pl.compile_modules(out["trading_system"]), "R:R >= 2.0")


class AdminOverrideReachesModelTests(_SandboxBase):
    """P1-3：管理员覆盖层必须真的出现在模型收到的 System Prompt 里（此前被布局丢弃）。"""

    MARK = "只在管理员覆盖层里出现的标记ZZZ"

    def setUp(self):
        super().setUp()
        import ai_brain_trader as brain
        self.brain = brain
        self.override_path = Path(brain.PROMPT_OVERRIDE_FILE)
        self._assert_sandboxed(self.override_path)
        self.override_path.parent.mkdir(parents=True, exist_ok=True)
        self.override_path.write_text(self.MARK + "\n", encoding="utf-8")

    def _assert_sandboxed(self, path: Path):
        real = ROOT / "data"
        self.assertTrue(str(path).startswith(str(self.root)) or str(path).startswith(str(self.temp.name)),
                        f"覆盖层路径未被沙箱化（会写生产文件）: {path}")
        self.assertNotEqual(Path(path).parent, real)

    def test_override_survives_module_layout(self):
        effective = self.brain.get_effective_system_prompt()
        self.assertIn(self.MARK, effective, "覆盖层被 apply_module_layout 丢弃（P1-3 回归）")
        self.assertTrue(effective.rstrip().endswith(self.MARK), "覆盖层应位于最终提示词末尾")
        self.assertIn("核心军规", effective, "布局本身必须仍然生效")

    def test_no_override_means_plain_base(self):
        self.override_path.unlink()
        effective = self.brain.get_effective_system_prompt()
        self.assertNotIn(self.MARK, effective)
        self.assertNotIn("管理员提示词覆盖层", effective)

    def test_admin_api_effective_prompt_uses_same_path(self):
        """接口返回的 effective_prompt 必须等于推演时用的那条（否则 UI 在骗人）。"""
        # 第九十六刀：`prompt_override` 现住 strategy/prompts.py ⇒ patch/调用都指向它
        from r20_backend.routers.strategy import prompts as strategy_prompts
        with patch.object(strategy_prompts, "require_admin_header", lambda *a, **k: {"username": "t"}), \
             patch.object(strategy_prompts, "refresh_settings", lambda: None):
            payload = strategy_prompts.prompt_override(None, None)
        self.assertEqual(payload["effective_prompt"], self.brain.get_effective_system_prompt())
        self.assertTrue(payload["override_applied"])
        self.assertIn(self.MARK, payload["effective_prompt"])
        self.assertIn(self.MARK, payload["content"])


class PortfolioBudgetHonestyTests(_SandboxBase):
    """P1-6：`R20_PORTFOLIO_RISK_BUDGET_USDT=0` = 引擎不封顶 → UI 不得编出 4800 的总闸。"""

    @classmethod
    def setUpClass(cls):
        # 同 test_venue_accounts_endpoint：r20_backend.dashboard_cache 导入即点火 2s 后台线程（真调 OKX）
        # → 本类期间钉死循环体。
        # ⚠️ 第一百二十五刀：**必须还原**。此前不还原 ⇒ 整个测试进程里
        # `dashboard_cache.update_cache_cycle` 都是 no-op，任何真调它的用例
        # （如 `tests/ui/test_protection_gap_reaches_data_health.py`）只会拿到空
        # `CACHE_DATA`（整包跑 KeyError('data_health')、单跑通过 —— 实测踩到）。
        import r20_backend.dashboard_cache as dashboard_app
        dashboard_app.stop_dashboard_background_worker()
        cls._orig_update_cache_cycle = dashboard_app.update_cache_cycle
        dashboard_app.update_cache_cycle = lambda *a, **k: None
        cls.dashboard = dashboard_app

    @classmethod
    def tearDownClass(cls):
        cls.dashboard.update_cache_cycle = cls._orig_update_cache_cycle
        cls.dashboard.stop_dashboard_background_worker()

    def setUp(self):
        super().setUp()
        self._saved = os.environ.pop("R20_PORTFOLIO_RISK_BUDGET_USDT", None)
        # 预留层管理器会缓存首个（沙箱）SQLite 路径，前序测试清理临时目录后连接即失效
        # （全量套件里报 "unable to open database file"）→ 本类只测预算口径，直接打桩。
        import r20_backend.risk_reservation as reservation
        stub = type("_Mgr", (), {
            "gross_exposure": lambda self, env: 0.0,
            "total_reserved_by_venue": lambda self, env: {},
        })()
        p = patch.object(reservation, "get_manager", lambda: stub)
        p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("R20_PORTFOLIO_RISK_BUDGET_USDT", None)
        else:
            os.environ["R20_PORTFOLIO_RISK_BUDGET_USDT"] = self._saved
        super().tearDown()

    def test_unconfigured_budget_reports_null_not_fabricated(self):
        os.environ.pop("R20_PORTFOLIO_RISK_BUDGET_USDT", None)
        row = self.dashboard._load_portfolio_risk_data()
        self.assertEqual(row.get("status"), "ok", f"预算行读取失败：{row.get('error')}")
        self.assertEqual(row["budget_mode"], "uncapped")
        self.assertIsNone(row["total_budget_usdt"], "未配置却在编造总预算（P1-6 回归）")
        self.assertIsNone(row["available_usdt"])
        self.assertIsNone(row["utilization_pct"], "未配置却给出占用率 → 前端会画进度条")
        self.assertNotEqual(row["total_budget_usdt"], 4800.0)
        # 展示参考可以给，但必须是单独字段，且不得冒充预算
        self.assertIn("reference_cap_usdt", row)

    def test_zero_budget_is_uncapped_too(self):
        os.environ["R20_PORTFOLIO_RISK_BUDGET_USDT"] = "0"
        row = self.dashboard._load_portfolio_risk_data()
        self.assertEqual(row["budget_mode"], "uncapped")
        self.assertIsNone(row["total_budget_usdt"])

    def test_configured_budget_is_reported_truthfully(self):
        os.environ["R20_PORTFOLIO_RISK_BUDGET_USDT"] = "4800"
        row = self.dashboard._load_portfolio_risk_data()
        self.assertEqual(row["budget_mode"], "configured")
        self.assertEqual(row["total_budget_usdt"], 4800.0)
        self.assertIsNone(row["reference_cap_usdt"])
        self.assertIsNotNone(row["utilization_pct"])

    def test_frontend_labels_uncapped_instead_of_drawing_a_bar(self):
        panel = (ROOT / "frontend" / "src" / "components" / "dashboard" / "VenueAccountsPanel.vue").read_text(encoding="utf-8")
        self.assertIn("budget_mode", panel)
        self.assertIn("portfolio-uncapped", panel)
        # 占用率仍只在总预算为真实数值时派生
        self.assertIn("pTotal.value !== null && pTotal.value > 0", panel)


class UIEvidenceHonestyTests(_SandboxBase):
    """P1-9：UI 不得编造审计证据（缺字段时显示「—」，而不是 10 笔 / PASSED / ACTIVE）。"""

    def _read(self, *parts):
        return (ROOT / "frontend" / "src" / Path(*parts)).read_text(encoding="utf-8")

    def test_evolution_page_does_not_fabricate_sample_and_shield(self):
        src = self._read("views", "admin", "EvolutionPage.vue")
        self.assertNotIn("item.sample_size || 10", src, "缺样本量时又在编「10 笔」")
        self.assertNotIn("item.shield_status || 'PASSED'", src, "缺状态时又在编「PASSED」")
        self.assertIn("typeof item.sample_size === 'number'", src)
        self.assertIn("typeof item.shield_status === 'string'", src)

    def test_guardrail_badge_is_derived_from_backend_state(self):
        src = self._read("views", "admin", "EvolutionPage.vue")
        self.assertIn("legacy_read_only", src, "护栏状态必须有后端字段支撑")
        self.assertIn("memoryStructured", src)

    def test_policy_snapshot_copy_matches_the_implementation(self):
        zh = self._read("locales", "zh", "admin", "policySnapshot.ts")
        self.assertNotIn("不可变指纹聚合", zh)
        self.assertNotIn("75%置信", zh, "冻结的阈值文案与本机实际门禁矛盾")
        self.assertIn("逐单元校验", zh)
        self.assertIn("风控/路由", zh)


if __name__ == "__main__":
    unittest.main()
