"""指标中枢（Prometheus exposition）契约测试。

守四件事（每件都对应一条设计约束，见 `r20_backend/metrics.py` docstring）：

1. **格式合法**：HELP/TYPE 成对、label 转义、NaN/Inf 不落盘（部分抓取器会整条丢弃）；
2. **fail-soft 且失败可见**：源缺失时渲染仍成功，但 `r20_metrics_source_ok{source}` 必须为 0
   —— "全绿"与"取数挂了"必须可区分（本仓第 137 刀就是静默 except 吞掉失败）；
3. **绝不泄露内容**：模型调用只出计数/耗时/token；prompt、响应、prompt_fingerprint
   一律不得出现在文本里；
4. **不臆造数字**：风控旋钮取不到就跳过（不补 0）；venue 缺字段就少一行，而不是写 0。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from r20_backend import metrics as M


class FakeStore:
    def __init__(self, stats):
        self._stats = stats

    def model_stats(self):
        if isinstance(self._stats, Exception):
            raise self._stats
        return self._stats


class FakeRisk:
    MAX_LEVERAGE = 5.0
    MIN_LEVERAGE = 2
    MIN_ENTRY_CONFIDENCE = 80
    # 其余白名单键**故意缺席** → 必须跳过而不是报 0


class RenderFormatTest(unittest.TestCase):
    def _snapshot(self, **over):
        snap = {
            "generated_at": 1789000000.0,
            "sources": {"venue_health": True, "model_calls": True, "risk_limits": True},
            "venue_health": {
                "updated_utc": "2026-09-20 07:45:32",
                "venues": {
                    "okx": {"ok": ["BTC", "ETH"], "failed": {"SOL": "timeout"},
                            "avg_ms": 105, "testnet": True},
                    "gate": {"ok": [], "failed": {}, "avg_ms": 0, "testnet": False},
                },
            },
            "model_stats": {"total_calls": 12, "successful_calls": 10,
                            "avg_duration_ms": 4200, "total_tokens": 98765},
            "risk_limits": {"max_leverage": 5.0, "min_entry_confidence": 80.0},
        }
        snap.update(over)
        return snap

    def test_core_families_present(self):
        text = M.render_prometheus(self._snapshot())
        self.assertIn("r20_up 1", text)
        self.assertIn('r20_metrics_source_ok{source="venue_health",required="1"} 1', text)
        self.assertIn('r20_venue_instruments_ok{venue="okx"} 2', text)
        self.assertIn('r20_venue_instruments_failed{venue="okx"} 1', text)
        self.assertIn('r20_venue_latency_avg_ms{venue="okx"} 105', text)
        self.assertIn('r20_venue_testnet{venue="okx"} 1', text)
        self.assertIn('r20_venue_testnet{venue="gate"} 0', text)
        self.assertIn("r20_model_calls_total 12", text)
        self.assertIn("r20_model_calls_successful_total 10", text)
        self.assertIn("r20_model_tokens_total 98765", text)
        self.assertIn('r20_risk_limit{name="max_leverage"} 5', text)
        self.assertTrue(text.endswith("\n"), "exposition 必须以换行结尾")

    def test_help_and_type_are_paired(self):
        text = M.render_prometheus(self._snapshot())
        helps = [ln for ln in text.splitlines() if ln.startswith("# HELP ")]
        types = [ln for ln in text.splitlines() if ln.startswith("# TYPE ")]
        self.assertTrue(helps)
        self.assertEqual([ln.split()[2] for ln in helps],
                         [ln.split()[2] for ln in types],
                         "HELP/TYPE 不配对 ⇒ 抓取器解析异常")

    def test_each_family_declared_exactly_once(self):
        """Prometheus 文本解析器对同一 metric name 的第二条 HELP/TYPE **直接报错并
        丢弃整次抓取** —— 带 label 的族（source/venue/name）尤其容易踩。"""
        text = M.render_prometheus(self._snapshot())
        helps = [ln.split()[2] for ln in text.splitlines() if ln.startswith("# HELP ")]
        self.assertEqual(len(helps), len(set(helps)),
                         f"同一指标族声明了多次 HELP：{helps}")
        # 样本必须聚在同一族下（不得被 HELP 行劈成两段）
        body = [ln.split("{")[0].split(" ")[0]
                for ln in text.splitlines() if ln and not ln.startswith("#")]
        seen, order = set(), []
        for name in body:
            if name not in seen:
                seen.add(name)
                order.append(name)
        self.assertEqual(len(order), len(seen), "样本族出现顺序被劈开")
        for name in set(body):
            first = body.index(name)
            last = len(body) - 1 - body[::-1].index(name)
            between = {body[i] for i in range(first, last + 1)}
            self.assertEqual(between, {name}, f"{name} 的样本被其它族插入劈开")

    def test_counter_type_declared_for_monotonic_families(self):
        text = M.render_prometheus(self._snapshot())
        self.assertIn("# TYPE r20_model_calls_total counter", text)
        self.assertIn("# TYPE r20_model_tokens_total counter", text)

    def test_label_values_are_escaped(self):
        snap = self._snapshot(venue_health={"updated_utc": "",
                                           "venues": {'we"ird\\name': {"ok": ["x"]}}})
        text = M.render_prometheus(snap)
        self.assertIn(r'r20_venue_instruments_ok{venue="we\"ird\\name"} 1', text)
        # 转义后不得出现**未转义**的裸引号（计数时排除 \" ）
        for line in text.splitlines():
            if line.startswith("r20_"):
                unescaped = re.findall(r'(?<!\\)"', line)
                self.assertEqual(len(unescaped) % 2, 0, f"引号未闭合：{line}")

    def test_nan_and_inf_are_dropped_not_emitted(self):
        snap = self._snapshot(model_stats={"total_calls": float("nan"),
                                           "successful_calls": float("inf"),
                                           "avg_duration_ms": "-inf",
                                           "total_tokens": 7})
        text = M.render_prometheus(snap)
        self.assertNotIn("nan", text.lower())
        self.assertNotIn("inf", text.lower().replace("info", ""))
        self.assertIn("r20_model_tokens_total 7", text)

    def test_missing_venue_fields_are_omitted(self):
        snap = self._snapshot(venue_health={"updated_utc": "", "venues": {"okx": {}}})
        text = M.render_prometheus(snap)
        self.assertIn('r20_venue_instruments_ok{venue="okx"} 0', text)
        self.assertNotIn("r20_venue_latency_avg_ms", text, "缺字段不得写 0 冒充测量值")
        self.assertNotIn("r20_venue_health_updated_timestamp_seconds", text,
                         "时间戳解析失败不得写 0")


class FailSoftTest(unittest.TestCase):
    def test_missing_sources_render_but_are_flagged(self):
        snap = M.build_snapshot(venue_health=None, model_stats=None, risk_limits=None,
                                now=1789000000.0)
        # 真取数在本机可能成/败；这里只钉"渲染必须成功且带 source 行"
        text = M.render_prometheus(snap)
        for source in ("venue_health", "model_calls", "risk_limits"):
            self.assertRegex(text, rf'r20_metrics_source_ok\{{source="{source}",required="[01]"\}} [01]')

    def test_explicit_failure_is_visible_as_zero(self):
        snap = {
            "generated_at": 1.0,
            "sources": {"venue_health": False, "model_calls": False, "risk_limits": False},
            "venue_health": {}, "model_stats": {}, "risk_limits": {},
        }
        text = M.render_prometheus(snap)
        for source in ("venue_health", "model_calls", "risk_limits"):
            self.assertIn(f'r20_metrics_source_ok{{source="{source}",required="1"}} 0', text)
        self.assertIn("r20_up 1", text, "数据源全挂也不影响进程存活指标")

    def test_store_failure_returns_none_not_raises(self):
        self.assertIsNone(M.collect_model_stats(FakeStore(RuntimeError("db locked"))))

    def test_broken_health_file_returns_none(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "venue_health.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(M.collect_venue_health(root))
            self.assertIsNone(M.collect_venue_health(root / "missing-dir"))


class NoContentLeakTest(unittest.TestCase):
    def test_model_fingerprint_and_prompt_never_rendered(self):
        """模型调用记录里的 fingerprint / caller 等字段一律不得出现在文本里。"""
        snap = M.build_snapshot(
            venue_health={}, risk_limits={},
            model_stats={"total_calls": 3, "successful_calls": 3,
                         "avg_duration_ms": 100, "total_tokens": 42,
                         # 就算 store 多给了字段，渲染层也不许把它们带出去
                         "prompt_fingerprint": "deadbeefdeadbeef",
                         "caller": "brain", "error_type": "SECRET"},
        )
        text = M.render_prometheus(snap)
        for leaked in ("deadbeef", "prompt_fingerprint", "SECRET", "brain"):
            self.assertNotIn(leaked, text, f"指标泄露了内部字段：{leaked}")


class RiskLimitsTest(unittest.TestCase):
    def test_only_allowlisted_keys_and_missing_ones_skipped(self):
        limits = M.collect_risk_limits(FakeRisk)
        self.assertEqual(limits, {"max_leverage": 5.0, "min_leverage": 2.0,
                                  "min_entry_confidence": 80.0})
        self.assertNotIn("max_daily_loss_usdt", limits,
                         "取不到的旋钮被补了 0 ⇒ 编造了一个不存在的阈值")

    def test_all_allowlisted_attrs_exist_in_real_module(self):
        """白名单必须与真实常量模块对得上（旋钮改名后这里会红）。"""
        try:
            from scripts import risk_constants as rc
        except ImportError:
            import risk_constants as rc
        real = M.collect_risk_limits(rc)
        self.assertIsNotNone(real, "真实 risk_constants 一个键都没取到 ⇒ 白名单全过期")
        missing = [attr for _, attr in M.RISK_LIMIT_NAMES if not hasattr(rc, attr)]
        self.assertEqual(missing, [], f"白名单里有 risk_constants 不存在的键：{missing}")
        self.assertGreaterEqual(len(real), 10, "生效值太少，白名单可能被改窄")

    def test_import_failure_is_none_not_empty(self):
        class Boom:
            def __getattr__(self, name):
                raise RuntimeError("nope")
        self.assertIsNone(M.collect_risk_limits(Boom()))


class MetricsRouteTest(unittest.TestCase):
    """路由级：`/api/v1/admin/metrics` 必须管理员鉴权，鉴权后给 Prometheus 文本。

    指标里含账户规模与风控阈值 ⇒ 属控制面数据，**不能**做成公开端点；这里用真实的
    管理员会话走一遍（不是 patch 掉鉴权），确保鉴权真的生效。
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        from fastapi.testclient import TestClient
        from tests.config_sandbox import isolate_config
        import r20_backend.app as app_module
        from r20_backend.admin_auth import AdminAuthStore

        isolate_config(self)
        self.temp = tempfile.TemporaryDirectory()
        self._orig = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        import r20_backend.app as app_module
        app_module.admin_auth = self._orig
        self.temp.cleanup()

    def _headers(self):
        r = self.client.post("/api/v1/admin/auth/login",
                             json={"username": "admin", "password": "InitialAdmin123456"})
        self.assertEqual(r.status_code, 200, r.text)
        return {"X-R20-Session": r.json()["session_token"]}

    def test_requires_admin(self):
        r = self.client.get("/api/v1/admin/metrics")
        self.assertIn(r.status_code, (401, 403),
                      f"未鉴权就能拉指标 ⇒ 控制面数据裸奔：{r.status_code}")

    def test_prometheus_text_when_authorized(self):
        r = self.client.get("/api/v1/admin/metrics", headers=self._headers())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("text/plain", r.headers.get("content-type", ""))
        self.assertIn("r20_up 1", r.text)
        self.assertIn('r20_metrics_source_ok{source="risk_limits",required="1"}', r.text)

    def test_json_format_returns_snapshot(self):
        r = self.client.get("/api/v1/admin/metrics?format=json", headers=self._headers())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        for key in ("generated_at", "sources", "risk_limits", "model_stats", "venue_health"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()


class MarketDataSourceTest(unittest.TestCase):
    """第 138 刀：行情取数健康（跨进程文件）接进 /metrics。

    事故背景：失败计数在 worker 进程内存里，后端 `/metrics` 在另一个进程 ——
    进程内计数器**永远看不到对方**，故走文件（与 venue_health.json 同法）。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, payload):
        (self.data_dir / "market_data_health.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def _payload(self):
        return {
            "schema_version": 1,
            "written_at_ms": 1789000000_000 - 30_000,     # 快照年龄 30 秒
            "calls": {"okx_public_get_ticker": 12},
            "failed_calls": {"okx_public_get_ticker": 3},
            "latency": {"okx_public_get_ticker": {"count": 12, "avg_ms": 150.0,
                                                  "max_ms": 1200.0,
                                                  "p50_ms": 120.0, "p95_ms": 980.0}},
            "last_success_ms": {"okx_public_get_ticker": 1789000000_000 - 5_000},
            "failures": {"total": 3, "by_kind": {"okx_public_get_ticker": 3},
                         "last_error": {"okx_public_get_ticker": "Timeout: read timeout"}},
        }

    def test_missing_file_is_none_not_empty_dict(self):
        """不可判定必须与"全为 0"可区分（0 次调用 ≠ 没有数据）。"""
        self.assertIsNone(M.collect_market_data_health(self.data_dir / "market_data_health.json"))

    def test_wrong_schema_version_is_refused(self):
        self._write({"schema_version": 999, "calls": {"k": 1}})
        self.assertIsNone(M.collect_market_data_health(self.data_dir / "market_data_health.json"))

    def test_renders_calls_failures_latency_and_ages(self):
        self._write(self._payload())
        snap = M.build_snapshot(data_dir=self.data_dir, venue_health={}, model_stats={},
                                risk_limits={}, now=1789000000.0)
        self.assertTrue(snap["sources"]["market_data"])
        text = M.render_prometheus(snap)
        self.assertIn('r20_market_data_calls_total{kind="okx_public_get_ticker"} 12', text)
        self.assertIn('r20_market_data_call_failures_total{kind="okx_public_get_ticker"} 3', text)
        self.assertIn('r20_market_data_latency_p50_seconds{kind="okx_public_get_ticker"} 0.12', text)
        self.assertIn('r20_market_data_latency_p95_seconds{kind="okx_public_get_ticker"} 0.98', text)
        self.assertIn("r20_market_data_snapshot_age_seconds 30", text)
        self.assertIn('r20_market_data_last_success_age_seconds{kind="okx_public_get_ticker"} 5', text)

    def test_source_ok_zero_when_file_absent(self):
        snap = M.build_snapshot(data_dir=self.data_dir, venue_health={}, model_stats={},
                               risk_limits={}, now=1789000000.0)
        self.assertFalse(snap["sources"]["market_data"])
        text = M.render_prometheus(snap)
        self.assertIn('r20_metrics_source_ok{source="market_data",required="1"} 0', text,
                      "取数挂了必须可见（第 137 刀：静默失败 30 小时无信号）")

    def test_no_family_is_declared_twice_with_market_data_present(self):
        """带 label 的新族最容易把 HELP/TYPE 印两遍 ⇒ 抓取器丢弃整次抓取。"""
        self._write(self._payload())
        snap = M.build_snapshot(data_dir=self.data_dir, venue_health={}, model_stats={},
                               risk_limits={}, now=1789000000.0)
        text = M.render_prometheus(snap)
        helps = [ln.split()[2] for ln in text.splitlines() if ln.startswith("# HELP ")]
        self.assertEqual(len(helps), len(set(helps)), f"重复 HELP：{helps}")

    def test_no_snapshot_renders_no_market_data_family(self):
        """源缺失时不得凭空发指标（发 0 会假装"调用过 0 次"）。"""
        snap = M.build_snapshot(data_dir=self.data_dir, venue_health={}, model_stats={},
                               risk_limits={}, now=1789000000.0)
        text = M.render_prometheus(snap)
        self.assertNotIn("r20_market_data_calls_total", text)
        self.assertIn('r20_metrics_source_ok{source="market_data",required="1"} 0', text)

    def test_negative_age_is_clamped_not_emitted_as_negative(self):
        """快照时间戳来自另一个进程，时钟回拨时不许出现负年龄（会让告警逻辑发疯）。"""
        payload = self._payload()
        payload["written_at_ms"] = 1789000000_000 + 60_000      # 比 now 还晚 60 秒
        payload["last_success_ms"] = {"okx_public_get_ticker": 1789000000_000 + 60_000}
        self._write(payload)
        snap = M.build_snapshot(data_dir=self.data_dir, venue_health={}, model_stats={},
                               risk_limits={}, now=1789000000.0)
        text = M.render_prometheus(snap)
        ages = [ln for ln in text.splitlines() if "_age_seconds" in ln]
        self.assertTrue(ages)
        for line in ages:
            self.assertNotIn(" -", line, f"年龄为负：{line}")

    def test_failure_detail_string_never_leaks_into_metrics(self):
        """last_error 是给人看的排障文本，不该作为标签进监控面。"""
        self._write(self._payload())
        snap = M.build_snapshot(data_dir=self.data_dir, venue_health={}, model_stats={},
                               risk_limits={}, now=1789000000.0)
        text = M.render_prometheus(snap)
        self.assertNotIn("read timeout", text)


class OptionalSourceTest(unittest.TestCase):
    """`required` 标签：可选源缺失**不算故障**（否则告警永远在响 = 没有告警）。

    真实场景：公共行情流探测是**按需/非常驻**的（`scripts/market_stream.py --probe`），
    若把它也当作"必需源"，`R20MetricsSourceMissing` 会 7×24 一直响。
    """

    def _snapshot(self, sources):
        return {"generated_at": 1789000000.0, "sources": sources, "venue_health": {},
                "model_stats": {}, "risk_limits": {}, "market_data_health": {},
                "market_stream_health": {}}

    def test_required_sources_are_labelled(self):
        text = M.render_prometheus(self._snapshot({"venue_health": True, "market_stream": False}))
        self.assertIn('r20_metrics_source_ok{source="venue_health",required="1"} 1', text)
        self.assertIn('r20_metrics_source_ok{source="market_stream",required="0"} 0', text,
                      "可选源缺失必须可区分，否则告警会永远在响")

    def test_the_alert_expression_only_targets_required_sources(self):
        """规则文件必须按 required 过滤 —— 这条断言是防"改回去"的钉子。"""
        from pathlib import Path
        alerts = (Path(__file__).resolve().parents[2]
                  / "deploy" / "observability" / "alerts.yml").read_text(encoding="utf-8")
        self.assertIn('r20_metrics_source_ok{required="1"} == 0', alerts)

    def test_market_stream_is_declared_optional(self):
        self.assertNotIn("market_stream", M.REQUIRED_SOURCES)
        self.assertIn("market_data", M.REQUIRED_SOURCES, "行情取数缺失必须算故障")


class MarketStreamSourceTest(unittest.TestCase):
    """公共行情流健康接入 /metrics（只读探测；**可选源**）。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, payload):
        (self.data_dir / "market_stream_health.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def _snap(self, **kw):
        return M.build_snapshot(data_dir=self.data_dir, venue_health={}, model_stats={},
                                risk_limits={}, market_data_health={}, now=1789000000.0, **kw)

    def test_missing_or_wrong_version_is_none(self):
        self.assertIsNone(M.collect_market_stream_health(self.data_dir / "market_stream_health.json"))
        self._write({"schema_version": 999, "venues": {}})
        self.assertIsNone(M.collect_market_stream_health(self.data_dir / "market_stream_health.json"))

    def test_families_render_with_venue_labels(self):
        self._write({"schema_version": 1, "written_at_ms": 1789000000_000 - 12_000,
                     "venues": {"okx": {"frames": 30, "ticks": 29, "parse_errors": 0,
                                        "errors": 0, "tick_age_s": 12.0},
                                "gate": {"frames": 1, "ticks": 0, "parse_errors": 0,
                                         "errors": 1, "tick_age_s": None}}})
        text = M.render_prometheus(self._snap())
        self.assertIn('r20_market_stream_ticks_total{venue="okx"} 29', text)
        self.assertIn('r20_market_stream_errors_total{venue="gate"} 1', text)
        self.assertIn('r20_market_stream_tick_age_seconds{venue="okx"} 12', text)
        self.assertNotIn('r20_market_stream_tick_age_seconds{venue="gate"}', text,
                         "从未收到 tick ⇒ 不发该序列（发 0 会被读成『刚刚有数据』）")
        self.assertIn("r20_market_stream_snapshot_age_seconds 12", text)

    def test_absent_stream_is_optional_not_a_failure(self):
        snap = self._snap()
        self.assertFalse(snap["sources"]["market_stream"])
        text = M.render_prometheus(snap)
        self.assertIn('r20_metrics_source_ok{source="market_stream",required="0"} 0', text)
        self.assertNotIn("r20_market_stream_ticks_total", text, "没在跑就不该凭空发指标")

class CycleDisclosureMetricsTest(unittest.TestCase):
    """周期披露指标的**诚实性**（第 51 刀）。

    关键契约（对齐本文件既有的"不可判定≠0"先例）：快照缺失/损坏 ⇒ 只标
    `source_ok=0`，**绝不发零值计数** —— 否则面板会把"不知道"读成"本轮很干净"。
    """

    def _snap(self, cd):
        return {
            "generated_at": 1789000000.0,
            "sources": {"cycle_disclosure": cd is not None},
            "cycle_disclosure": cd or {},
        }

    def test_counts_are_emitted_when_snapshot_present(self):
        text = M.render_prometheus(self._snap({
            "written_at_ms": int(1788999900.0 * 1000),
            "broken_venue_count": 2, "shape_violation_count": 1,
            "entries_blocked": True, "watchdog_enabled": True,
            "watchdog_errors": 3, "watchdog_critical": 1,
        }))
        for needle in ("r20_cycle_disclosure_broken_venues 2",
                       "r20_cycle_disclosure_shape_violations 1",
                       "r20_cycle_disclosure_entries_blocked 1",
                       "r20_cycle_disclosure_watchdog_enabled 1",
                       "r20_cycle_disclosure_watchdog_errors 3",
                       "r20_cycle_disclosure_watchdog_critical 1"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_missing_snapshot_emits_no_counts_only_source_flag(self):
        text = M.render_prometheus(self._snap(None))
        self.assertIn('r20_metrics_source_ok{source="cycle_disclosure",required="0"} 0', text)
        self.assertNotIn("r20_cycle_disclosure_broken_venues", text,
                         "读不到不得渲染成 0（那是把'不知道'说成'很干净'）")

    def test_disabled_watchdog_emits_no_error_counts(self):
        """没跑就没有"错误数"：发 0 会被读成"跑了且没问题"。"""
        text = M.render_prometheus(self._snap({
            "written_at_ms": int(1788999900.0 * 1000),
            "broken_venue_count": 0, "shape_violation_count": 0,
            "entries_blocked": False, "watchdog_enabled": False,
            "watchdog_errors": 0, "watchdog_critical": 0,
        }))
        self.assertIn("r20_cycle_disclosure_watchdog_enabled 0", text)
        self.assertNotIn("r20_cycle_disclosure_watchdog_errors", text)
        self.assertNotIn("r20_cycle_disclosure_watchdog_critical", text)

    def test_collector_returns_none_for_missing_or_broken_file(self):
        import tempfile
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as td:
            missing = _P(td) / "nope.json"
            self.assertIsNone(M.collect_cycle_disclosure(missing))
            broken = _P(td) / "broken.json"
            broken.write_text("{ 半截", encoding="utf-8")
            self.assertIsNone(M.collect_cycle_disclosure(broken))
            good = _P(td) / "good.json"
            good.write_text(json.dumps({"broken_venue_count": 1}), encoding="utf-8")
            self.assertEqual(M.collect_cycle_disclosure(good)["broken_venue_count"], 1)

class CycleDisclosureFileAgreementTest(unittest.TestCase):
    """worker 写的文件名 与 后端读的文件名 必须一致（第 51 刀）。

    这是本仓"同一语义两处写 ⇒ 必然漂移"的最典型形态：一边写 `data/x.json`、
    另一边读 `data/y.json`，两边测试都绿，线上永远读不到。故直接钉文件名。
    """

    def test_worker_and_backend_agree_on_the_filename(self):
        root = Path(__file__).resolve().parents[2]
        facade = (root / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8")
        backend = (root / "r20_backend" / "metrics.py").read_text(encoding="utf-8")
        self.assertIn('CYCLE_DISCLOSURE_FILE = os.path.join(DATA_DIR, "cycle_disclosure.json")',
                      facade, "worker 侧文件名变了？")
        self.assertIn('cd_base / "cycle_disclosure.json"', backend,
                      "后端读的文件名与 worker 写的不一致 ⇒ 指标永远读不到")

class ProtectionOrphanMetricsTest(unittest.TestCase):
    """第一百七十六刀：孤儿保护腿的**可观测**（且"读不到"绝不渲染成 0）。"""

    def _text(self, **kw):
        return M.render_prometheus(M.build_snapshot(**kw))

    def _lines(self, text, needle):
        return [ln for ln in text.splitlines() if needle in ln and not ln.startswith("#")]

    def test_collector_dedupes_per_venue_and_reports_counts(self):
        info = {"readable": True, "attributed": [{"id": "a"}, {"id": "b"}],
                "unattributed": [{"id": "u"}], "ledgerRows": "ok"}
        cache = {"positions": [{"venue": "binance", "protectionOrphans": info},
                               {"venue": "binance", "protectionOrphans": info},
                               {"venue": "okx"}]}
        got = M.collect_protection_orphans(cache)
        self.assertEqual(set(got), {"binance"}, "未按所去重（面板每行都带同一份汇总）")
        self.assertEqual(got["binance"]["candidates"], 2)
        self.assertEqual(got["binance"]["unattributed"], 1)
        self.assertTrue(got["binance"]["ledger_evidence"])

    def test_no_data_yields_none_not_zero(self):
        self.assertIsNone(M.collect_protection_orphans(None))
        self.assertIsNone(M.collect_protection_orphans({"positions": [{"venue": "okx"}]}),
                          "缓存里还没有该字段 ⇒ 不可判定，不得当成 0 条孤儿腿")

    def test_unreadable_venue_emits_readable_zero_and_no_counts(self):
        summary = {"gate": {"readable": False, "candidates": None, "unattributed": None,
                            "ledger_evidence": None}}
        text = self._text(protection_orphans=summary)
        self.assertTrue(self._lines(text, 'r20_protection_orphans_readable{venue="gate"} 0'),
                        "读腿失败必须被看见（readable=0）")
        self.assertEqual(self._lines(text, "r20_protection_orphan_candidates"), [],
                         "读不到时**绝不能**发 candidates=0（会被读成'该所很干净'）")

    def test_readable_venue_emits_counts(self):
        summary = {"binance": {"readable": True, "candidates": 3, "unattributed": 1,
                               "ledger_evidence": False}}
        text = self._text(protection_orphans=summary)
        self.assertTrue(self._lines(text, 'r20_protection_orphan_candidates{venue="binance"} 3'))
        self.assertTrue(self._lines(text, 'r20_protection_orphan_unattributed{venue="binance"} 1'))
        self.assertTrue(self._lines(text,
                        'r20_protection_orphans_ledger_evidence{venue="binance"} 0'),
                        "台账读不到必须如实披露（否则候选偏少会被当成'就是这么多'）")

    def test_help_text_says_never_auto_cancel(self):
        text = self._text(protection_orphans={"binance": {"readable": True, "candidates": 1,
                                                         "unattributed": 0,
                                                         "ledger_evidence": True}})
        helps = [ln for ln in text.splitlines() if ln.startswith("# HELP r20_protection_orphan")]
        self.assertTrue(any("绝不自动撤" in ln for ln in helps),
                        "指标说明里必须写明'系统绝不自动撤'（否则读者会以为运维会自动清）")

    def test_snapshot_marks_the_source(self):
        snap = M.build_snapshot(protection_orphans={"binance": {"readable": True, "candidates": 0,
                                                               "unattributed": 0,
                                                               "ledger_evidence": True}})
        self.assertTrue((snap.get("sources") or {}).get("protection_orphans"),
                        "取到数据时 source_ok 必须为真")

class ProtectionMismatchMetricsTest(unittest.TestCase):
    """第一百八十一刀：mismatch 腿按 kind 分开出指标（两种语义不同）。"""

    def test_mismatch_kinds_are_emitted_separately(self):
        cache = {"positions": [{"venue": "binance", "protectionOrphans": {
            "readable": True, "attributed": [], "unattributed": [],
            "sideMismatch": [{"id": "s1"}, {"id": "s2"}], "sizeMismatch": [{"id": "z1"}],
            "ledgerRows": "ok"}}]}
        summary = M.collect_protection_orphans(cache)
        self.assertEqual(summary["binance"]["side_mismatch"], 2)
        self.assertEqual(summary["binance"]["size_mismatch"], 1)
        text = M.render_prometheus(M.build_snapshot(protection_orphans=summary))
        self.assertIn('r20_protection_side_mismatch_legs{venue="binance"} 2', text)
        self.assertIn('r20_protection_size_mismatch_legs{venue="binance"} 1', text)

    def test_help_distinguishes_both_semantics(self):
        text = M.render_prometheus(M.build_snapshot(protection_orphans={
            "binance": {"readable": True, "candidates": 0, "unattributed": 0,
                        "side_mismatch": 1, "size_mismatch": 1, "ledger_evidence": True}}))
        # 一个指标名只能有一个 HELP ⇒ 两种语义**必须**是两个名字（否则"仍被计入覆盖"
        # 这句永远发不出去；本用例第一版就是这么红的）
        side_help = [ln for ln in text.splitlines()
                     if ln.startswith("# HELP r20_protection_side_mismatch_legs")]
        size_help = [ln for ln in text.splitlines()
                     if ln.startswith("# HELP r20_protection_size_mismatch_legs")]
        self.assertTrue(any("不计入覆盖" in ln for ln in side_help), "方向不符必须明说'不计入覆盖'")
        self.assertTrue(any("正被计入覆盖" in ln for ln in size_help), "量不符必须明说'仍被计入覆盖'")

    def test_unreadable_venue_emits_no_mismatch_counts(self):
        text = M.render_prometheus(M.build_snapshot(protection_orphans={
            "gate": {"readable": False, "candidates": None, "unattributed": None,
                     "side_mismatch": None, "size_mismatch": None, "ledger_evidence": None}}))
        self.assertEqual([ln for ln in text.splitlines()
                          if ln.startswith("r20_protection_mismatch_legs")], [],
                         "读不到 ⇒ 不发 mismatch 计数（不可判定≠0）")

class UnclassifiedLegMetricsTest(unittest.TestCase):
    """第一百八十二刀：认不出/解析不了的腿各自一个指标名（语义不同）。"""

    def test_counts_are_emitted_per_semantics(self):
        cache = {"positions": [{"venue": "gate", "protectionOrphans": {
            "readable": True, "attributed": [], "unattributed": [], "sideMismatch": [],
            "sizeMismatch": [], "foreignCount": 2, "unparsedCount": 1, "ledgerRows": "ok"}}]}
        summary = M.collect_protection_orphans(cache)
        self.assertEqual(summary["gate"]["foreign"], 2)
        self.assertEqual(summary["gate"]["unparsed"], 1)
        text = M.render_prometheus(M.build_snapshot(protection_orphans=summary))
        self.assertIn('r20_protection_foreign_legs{venue="gate"} 2', text)
        self.assertIn('r20_protection_unparsed_legs{venue="gate"} 1', text)

    def test_help_says_they_are_not_counted_in_coverage(self):
        text = M.render_prometheus(M.build_snapshot(protection_orphans={
            "gate": {"readable": True, "candidates": 0, "unattributed": 0, "side_mismatch": 0,
                     "size_mismatch": 0, "foreign": 1, "unparsed": 1, "ledger_evidence": True}}))
        for metric in ("r20_protection_foreign_legs", "r20_protection_unparsed_legs"):
            helps = [ln for ln in text.splitlines() if ln.startswith(f"# HELP {metric}")]
            self.assertTrue(helps, f"{metric} 缺 HELP")
            self.assertTrue(any("不计入覆盖" in ln for ln in helps),
                            f"{metric} 的 HELP 必须写明'不计入覆盖'（否则读者以为它算保护）")
