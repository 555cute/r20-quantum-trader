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

import re
import unittest

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
        self.assertIn('r20_metrics_source_ok{source="venue_health"} 1', text)
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
            self.assertRegex(text, rf'r20_metrics_source_ok\{{source="{source}"\}} [01]')

    def test_explicit_failure_is_visible_as_zero(self):
        snap = {
            "generated_at": 1.0,
            "sources": {"venue_health": False, "model_calls": False, "risk_limits": False},
            "venue_health": {}, "model_stats": {}, "risk_limits": {},
        }
        text = M.render_prometheus(snap)
        for source in ("venue_health", "model_calls", "risk_limits"):
            self.assertIn(f'r20_metrics_source_ok{{source="{source}"}} 0', text)
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
        self.assertIn('r20_metrics_source_ok{source="risk_limits"}', r.text)

    def test_json_format_returns_snapshot(self):
        r = self.client.get("/api/v1/admin/metrics?format=json", headers=self._headers())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        for key in ("generated_at", "sources", "risk_limits", "model_stats", "venue_health"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()
