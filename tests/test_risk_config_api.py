"""后台「风控管理页」API 契约与安全回归测试。

覆盖：
- RBAC：未登录 401、普通 admin 只读、superadmin 可写；
- schema 与执行层 DEFAULTS 零漂移；
- 保存回写 .env（沙箱 ENV_FILE，绝不触碰生产配置与 os.environ 残留）；
- 越界 / 未知键 / 跨字段矛盾一律 400 并给出中文原因；
- 一键重置回退默认基线。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi.testclient import TestClient

import r20_backend.app as app_module
import r20_backend.settings_store as settings_store
from r20_backend.admin_auth import AdminAuthStore
from r20_backend.risk_config import GROUPS, schema
from scripts.risk_constants import DEFAULTS, RISK_ENV_KEYS

RISK_KEYS = set(RISK_ENV_KEYS)


class RiskConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_auth = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        # 沙箱化 .env，防止测试写入生产配置
        self.original_env_file = settings_store.ENV_FILE
        settings_store.ENV_FILE = Path(self.temp.name) / ".env"
        # 快照并清空风控环境变量，保证断言起点干净
        self.saved_env = {k: os.environ.pop(k, None) for k in RISK_KEYS}
        self.client = TestClient(app_module.app)

    def tearDown(self):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        settings_store.ENV_FILE = self.original_env_file
        app_module.admin_auth = self.original_auth
        self.temp.cleanup()

    def login(self, username: str, password: str, role: str = "superadmin") -> dict[str, str]:
        if role == "admin":  # 创建一个普通管理员
            root = self.login("admin", "InitialAdmin123456")
            self.client.post("/api/v1/admin/users", headers=root,
                             json={"username": username, "password": password, "role": "admin"})
        response = self.client.post("/api/v1/admin/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-R20-Session": response.json()["session_token"]}

    # ── RBAC ──
    def test_get_requires_auth(self):
        self.assertEqual(self.client.get("/api/v1/admin/risk").status_code, 401)

    def test_operator_can_read_but_not_write(self):
        operator = self.login("operator", "OperatorPassword123", role="admin")
        self.assertEqual(self.client.get("/api/v1/admin/risk", headers=operator).status_code, 200)
        denied = self.client.post("/api/v1/admin/risk", headers=operator,
                                  json={"values": {"R20_TIME_STOP_HOURS": 6}})
        self.assertEqual(denied.status_code, 403)

    # ── schema 契约 ──
    def test_schema_matches_execution_defaults(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.get("/api/v1/admin/risk", headers=headers)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        params = body["schema"]["params"]
        keys = {p["key"] for p in params}
        self.assertEqual(keys, set(DEFAULTS), "UI schema 与执行层 DEFAULTS 漂移")
        self.assertEqual({g["id"] for g in body["schema"]["groups"]}, {g["id"] for g in GROUPS})
        for p in params:
            self.assertEqual(p["default"], DEFAULTS[p["key"]])
            self.assertLessEqual(p["min"], p["default"])
            self.assertGreaterEqual(p["max"], p["default"])
        # 未配置时当前值 = 默认值
        for key, value in body["values"].items():
            self.assertAlmostEqual(value, float(DEFAULTS[key]), places=6)

    # ── 保存与回写 ──
    def test_update_persists_env_and_reflects_values(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.post("/api/v1/admin/risk", headers=headers, json={"values": {
            "R20_MAX_SAME_DIRECTION_POSITIONS": 5,
            "R20_MAX_MARGIN_EQUITY_RATIO": 0.15,
            "R20_TIME_STOP_HOURS": 12,
        }})
        self.assertEqual(res.status_code, 200, res.text)
        values = res.json()["values"]
        self.assertEqual(values["R20_MAX_SAME_DIRECTION_POSITIONS"], 5)
        self.assertAlmostEqual(values["R20_MAX_MARGIN_EQUITY_RATIO"], 0.15)
        self.assertAlmostEqual(values["R20_TIME_STOP_HOURS"], 12.0)
        # .env 沙箱文件与进程环境同步
        env_text = settings_store.ENV_FILE.read_text(encoding="utf-8")
        self.assertIn("R20_MAX_SAME_DIRECTION_POSITIONS=5", env_text)
        self.assertIn("R20_MAX_MARGIN_EQUITY_RATIO=0.15", env_text)
        self.assertAlmostEqual(float(os.environ["R20_TIME_STOP_HOURS"]), 12.0)
        # GET 再读一致
        again = self.client.get("/api/v1/admin/risk", headers=headers).json()["values"]
        self.assertEqual(again["R20_MAX_SAME_DIRECTION_POSITIONS"], 5)

    def test_int_field_rounds_fractional_input(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.post("/api/v1/admin/risk", headers=headers,
                               json={"values": {"R20_STOP_COOLDOWN_MINUTES": 44.9}})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["values"]["R20_STOP_COOLDOWN_MINUTES"], 45)

    # ── 校验防线 ──
    def test_out_of_range_rejected_with_reason(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.post("/api/v1/admin/risk", headers=headers,
                               json={"values": {"R20_MAX_MARGIN_EQUITY_RATIO": 5.0}})
        self.assertEqual(res.status_code, 400)
        self.assertIn("单笔保证金占比", res.json()["detail"])

    def test_unknown_key_rejected(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.post("/api/v1/admin/risk", headers=headers,
                               json={"values": {"R20_EVIL_SWITCH": 1}})
        self.assertEqual(res.status_code, 400)
        self.assertIn("未知风控参数", res.json()["detail"])

    def test_cross_field_contradiction_rejected(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.post("/api/v1/admin/risk", headers=headers, json={"values": {
            "R20_MAX_CONCURRENT_POSITIONS": 3,
            "R20_MAX_SAME_DIRECTION_POSITIONS": 6,
        }})
        self.assertEqual(res.status_code, 400)
        self.assertIn("同向持仓上限", res.json()["detail"])

    def test_empty_values_rejected(self):
        headers = self.login("admin", "InitialAdmin123456")
        self.assertEqual(self.client.post("/api/v1/admin/risk", headers=headers,
                                          json={"values": {}}).status_code, 400)

    # ── 重置 ──
    def test_reset_requires_confirmation_phrase(self):
        headers = self.login("admin", "InitialAdmin123456")
        self.client.post("/api/v1/admin/risk", headers=headers,
                         json={"values": {"R20_MIN_RISK_REWARD": 2.5}})
        bad = self.client.post("/api/v1/admin/risk/reset", headers=headers,
                               json={"confirmation": "RESET"})
        self.assertEqual(bad.status_code, 400)
        good = self.client.post("/api/v1/admin/risk/reset", headers=headers,
                                json={"confirmation": "reset risk"})
        self.assertEqual(good.status_code, 200, good.text)
        self.assertAlmostEqual(good.json()["values"]["R20_MIN_RISK_REWARD"], float(DEFAULTS["R20_MIN_RISK_REWARD"]))
        self.assertNotIn("R20_MIN_RISK_REWARD", os.environ)
        self.assertNotIn("R20_MIN_RISK_REWARD", settings_store.ENV_FILE.read_text(encoding="utf-8"))

    # ── 优质预设套件 ──
    def test_suites_exposed_and_valid(self):
        headers = self.login("admin", "InitialAdmin123456")
        body = self.client.get("/api/v1/admin/risk", headers=headers).json()
        suites = body["suites"]
        self.assertEqual({s["id"] for s in suites}, {"conservative", "balanced", "aggressive"})
        from r20_backend.risk_config import normalize
        for s in suites:
            self.assertEqual(set(s["values"]), RISK_KEYS, f"套件 {s['id']} 未覆盖全部参数")
            normalize(s["values"])  # 越界会 raise

    def test_apply_suite_writes_env_and_explicit_override_wins(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.post("/api/v1/admin/risk", headers=headers,
                               json={"suite_id": "conservative", "values": {"R20_MAX_LEVERAGE": 2.0}})
        self.assertEqual(res.status_code, 200, res.text)
        values = res.json()["values"]
        self.assertEqual(values["R20_MAX_SCALE_IN_COUNT"], 0)          # 稳健套件禁止加仓
        self.assertEqual(values["R20_MIN_ENTRY_CONFIDENCE"], 85.0)     # 稳健套件高门禁
        self.assertEqual(values["R20_MAX_LEVERAGE"], 2.0)              # 显式值覆盖套件值
        self.assertEqual(res.json()["applied_suite"], "conservative")
        self.assertIn("R20_MAX_SCALE_IN_COUNT=0", settings_store.ENV_FILE.read_text(encoding="utf-8"))

    def test_unknown_suite_rejected(self):
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.post("/api/v1/admin/risk", headers=headers, json={"suite_id": "yolo"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("未知风控预设套件", res.json()["detail"])


class PromptRiskContractTests(unittest.TestCase):
    """SYSTEM_PROMPT 与风控常量、线上布局的三重契约，防止提示词重新写死或漂移。"""

    def _load_trader(self):
        import importlib
        import risk_constants
        import ai_brain_trader
        return importlib.reload(risk_constants), importlib.reload(ai_brain_trader)

    def test_system_prompt_interpolates_live_risk_values(self):
        import ai_brain_trader
        self.assertIn(f"全系统同向持仓上限 {ai_brain_trader.MAX_SAME_DIRECTION_POSITIONS} 笔",
                      ai_brain_trader.SYSTEM_PROMPT)
        self.assertIn(f"杠杆不超过 {ai_brain_trader.MAX_LEVERAGE:g}x", ai_brain_trader.SYSTEM_PROMPT)
        self.assertIn(f"R:R < {ai_brain_trader.MIN_RISK_REWARD_RATIO:.1f}", ai_brain_trader.SYSTEM_PROMPT)

    def test_env_override_reflects_into_system_prompt(self):
        import r20_backend.config as backend_config
        os.environ["R20_MAX_SAME_DIRECTION_POSITIONS"] = "5"
        os.environ["R20_MAX_SCALE_IN_COUNT"] = "0"
        original_loader = backend_config.load_dotenv
        backend_config.load_dotenv = lambda path: None
        try:
            rc, abt = self._load_trader()
            self.assertIn("全系统同向持仓上限 5 笔", abt.SYSTEM_PROMPT)
            self.assertIn("已禁用一切金字塔加仓", abt.SYSTEM_PROMPT)  # scale=0 切换为禁令段落
        finally:
            backend_config.load_dotenv = original_loader
            del os.environ["R20_MAX_SAME_DIRECTION_POSITIONS"]
            del os.environ["R20_MAX_SCALE_IN_COUNT"]
            self._load_trader()

    def test_section_titles_match_live_layout(self):
        """标题即接口：代码分节必须与线上 trading_system 布局一一对应，否则线上会用旧快照内容。"""
        import json as _json
        import ai_brain_trader
        from prompt_library import text_to_modules
        live_library = ROOT / "data" / "prompt_library.json"
        if not live_library.exists():
            self.skipTest("无线上快照（全新部署），跳过布局对齐检查")
        lib = _json.loads(live_library.read_text(encoding="utf-8"))
        layout_titles = [m["title"] for m in lib["profiles"]["stable"]["pipelines"]["trading_system"]
                         if m.get("source") == "base"]
        code_titles = [m["title"] for m in text_to_modules(ai_brain_trader.SYSTEM_PROMPT, "base")]
        self.assertEqual(sorted(layout_titles), sorted(code_titles),
                         f"分节标题漂移: 布局缺{set(layout_titles)-set(code_titles)} 代码缺{set(code_titles)-set(layout_titles)}")
        # 线上快照内容必须与代码文本一致（快照只是缓存，不得成为第二事实源）
        live_map = {m["title"]: m["content"] for m in lib["profiles"]["stable"]["pipelines"]["trading_system"]
                    if m.get("source") == "base"}
        code_map = {m["title"]: m["content"] for m in text_to_modules(ai_brain_trader.SYSTEM_PROMPT, "base")}
        for title in layout_titles:
            self.assertEqual(live_map[title], code_map[title], f"线上快照与代码文本漂移: {title}")


class RiskExecutionWiringTests(unittest.TestCase):
    """执行层必须真的引用单一事实源，防止页面改了参数但代码没接线。"""

    def test_trader_sources_use_shared_constants(self):
        root = Path(__file__).resolve().parents[1]
        trader = (root / "scripts" / "ai_factor_trader.py").read_text(encoding="utf-8")
        self.assertIn("from risk_constants import", trader)
        for forbidden in ('os.getenv("R20_MAX_DAILY_LOSS_USDT"', 'MAX_SAME_DIRECTION_POSITIONS = 3',
                          "rem_sec = 1800", "hold_duration_sec > 28800", "ai_conf >= 80.0"):
            self.assertNotIn(forbidden, trader, f"执行层仍存在写死风控: {forbidden}")
        order_risk = (root / "scripts" / "order_risk.py").read_text(encoding="utf-8")
        self.assertIn("MIN_RISK_REWARD_RATIO", order_risk)
        brain = (root / "scripts" / "ai_brain_trader.py").read_text(encoding="utf-8")
        self.assertIn("from risk_constants import", brain)


if __name__ == "__main__":
    unittest.main()
