"""LLM 模块接缝纪律回归闸（结构优化阶段 2 / B4）。

## 背景：为什么需要这道闸

阶段 2 把 `r20_backend/llm_manager.py` 从 2105 行拆成「门面薄壳 + 9 个核心模块」。
薄壳能成立的唯一前提是**测试注入接缝**：测试用

    llm_manager.LLM_CONFIG_FILE = tmp          # 直接赋值
    patch.object(llm, "LLM_CONFIG_FILE", tmp)  # 或 patch
    patch("r20_backend.llm_manager.init_llm_config", return_value=cfg)

来把配置读写重定向到沙箱。这些注入**只对「在门面模块命名空间里解析该名字」的代码生效**。

已实证的失效模式（最小实验）：

    patch.object(mod_a, "CONST", "SANDBOX/...")
      函数留在原模块（调用时读自身全局）→ 补丁生效
      函数搬到子模块（导入时绑定副本）  → 补丁**静默失效**，读到真实路径

后者正是「测试写进生产 `data/`」的事故类型，仓库 sandbox（tests/config_sandbox.py）
就是为防它而建。本闸钉住三件事：

1. **公开面完整**：门面仍导出全部既有名字（含 `_` 私有名，测试会直接访问）；
2. **接缝仍通**：patch / 直接赋值门面的路径常量与 `init_llm_config`，仍能重定向核心；
3. **无循环依赖**：核心模块不得反向 import `llm_manager`（薄壳靠注入拿门面对象）。

以及两条被测试按**文件位置**钉住的约束（动文件前请先改对应测试）：
`record_failover_event` 必须仍定义在 `llm_manager.py`（`isolated()` 按 AST 取），
且 `save_llm_config` 必须仍含 `with file_lock(LLM_CONFIG_FILE):`（P4 文本锚点）。
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import r20_backend.llm_manager as lm  # noqa: E402

LLM_DIR = ROOT / "r20_backend" / "llm"
PROD_CONFIG = ROOT / "data" / "llm_models.json"

# 门面必须继续导出的名字（来自全仓 `from r20_backend.llm_manager import X`
# 与 `llm_manager.X` 属性访问的实测清单）
PUBLIC_SURFACE = [
    # 常量
    "LLM_CONFIG_FILE", "LEGACY_PROVIDERS_FILE", "LLM_PROVIDERS_FILE", "FAILOVER_EVENTS_FILE",
    "DEFAULT_PROVIDERS", "SUPPORTED_API_FORMATS", "STANDARD_REASONING_EFFORTS",
    "DEFAULT_REQUEST_ATTEMPTS", "MIN_REQUEST_ATTEMPTS", "MAX_REQUEST_ATTEMPTS",
    "MAX_FALLBACK_MODELS", "FAILOVER_MAX_TOTAL_WAIT", "TRANSIENT_MARKERS",
    # 配置读写
    "init_llm_config", "load_llm_config", "save_llm_config", "get_active_llm_runtime",
    "resolve_model_runtime", "init_llm_providers",
    # 配置库 CRUD
    "activate_provider_model", "update_llm_settings", "upsert_model", "delete_model",
    "upsert_provider", "toggle_provider", "clear_provider_models", "delete_provider",
    # 调用链
    "execute_llm_request", "fetch_remote_models", "test_llm_connection",
    # 回退事件
    "record_failover_event", "recent_failover_events",
    # 私有名（测试直接访问）
    "_detect_capabilities", "_detect_reasoning_type", "_detect_api_format",
    "_join_api_path", "_url_path_of", "_resolve_active_provider_id",
    "_provider_holds_active_model", "_model_holder_pids",
    "build_request_spec", "build_chat_payload", "_attempt_llm_call",
    "_is_transient_http", "_parse_llm_response", "_atomic_write_json", "mask_secret",
    "_lookup_api_path",
]


_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（第二百三十六刀）：
    本文件对照**线上** llm_models.json 的字节/mtime，证明写入落在沙箱、**没碰生产**
    —— 不读生产就无法证明这一点，属有意的线上守卫。

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


def _make_config(active_model_id: str = "m1") -> dict:
    """构造**自洽**配置：active_model_id 必须同时登记在 providers[].models 与顶层 models 里。

    否则 init_llm_config 的归一化会把它重置回已登记的模型 —— 这是它的正常职责
    （防「主脑指向未登记模型」），构造夹具时必须自己保持一致。
    """
    model = {"id": active_model_id, "name": active_model_id.upper(),
             "base_url": "https://p.example/v1", "api_key": "sk-p"}
    return {
        "version": "3.2", "defaults_seeded": True,
        "active_model_id": active_model_id, "active_provider_id": "p",
        "active_reasoning_effort": "high", "request_attempts": 3, "fallback_model_ids": [],
        "providers": [{"id": "p", "name": "P", "base_url": "https://p.example/v1",
                       "api_key": "sk-p", "enabled": True, "models": [dict(model)]}],
        "models": [dict(model, provider_id="p")],
    }


class SeamDisciplineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r20-llm-seam-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.cfg_file = self.tmp / "llm_models.json"
        self.cfg_file.write_text(json.dumps(_make_config(), ensure_ascii=False), encoding="utf-8")
        self._orig = {n: getattr(lm, n) for n in
                      ("LLM_CONFIG_FILE", "FAILOVER_EVENTS_FILE", "LLM_PROVIDERS_FILE", "LEGACY_PROVIDERS_FILE")}
        for k in self._orig:
            self.addCleanup(setattr, lm, k, self._orig[k])
        lm.LLM_CONFIG_FILE = self.cfg_file
        lm.LLM_PROVIDERS_FILE = self.cfg_file
        lm.LEGACY_PROVIDERS_FILE = self.tmp / "nonexistent_legacy.json"
        lm.FAILOVER_EVENTS_FILE = self.tmp / "llm_failover_events.json"

    # ── 1. 公开面 ──────────────────────────────────────────────
    def test_public_surface_is_intact(self):
        missing = [n for n in PUBLIC_SURFACE if not hasattr(lm, n)]
        self.assertEqual(missing, [], f"门面缺少这些名字（既有调用方/测试会 AttributeError）: {missing}")

    # ── 2. 路径常量接缝：直接赋值与 patch 都必须生效 ──────────────
    def test_config_file_assignment_redirects_reads(self):
        cfg = lm.init_llm_config()
        self.assertEqual(cfg.get("active_model_id"), "m1", "未读到临时配置里的 active_model_id")

    def test_config_file_patch_redirects_reads(self):
        other = self.tmp / "other.json"
        other.write_text(json.dumps(_make_config("m9"), ensure_ascii=False), encoding="utf-8")
        with patch.object(lm, "LLM_CONFIG_FILE", other):
            self.assertEqual(lm.init_llm_config().get("active_model_id"), "m9",
                             "patch.object 未重定向 init_llm_config")

    def test_config_write_goes_to_sandbox_not_production(self):
        """最关键的一条：写入必须落在沙箱，绝不能碰生产 data/llm_models.json。"""
        before = PROD_CONFIG.read_bytes() if PROD_CONFIG.exists() else None
        before_mtime = PROD_CONFIG.stat().st_mtime if PROD_CONFIG.exists() else None
        lm.save_llm_config(_make_config("mz"))
        self.assertEqual(lm.init_llm_config().get("active_model_id"), "mz",
                         "写入未落到沙箱配置文件")
        if before is not None:
            self.assertEqual(PROD_CONFIG.read_bytes(), before, "生产配置文件内容被改写")
            self.assertEqual(PROD_CONFIG.stat().st_mtime, before_mtime, "生产配置文件被写入（mtime 变化）")

    # ── 3. 函数接缝：patch 门面的函数必须传导到核心 ────────────────
    def test_init_llm_config_patch_reaches_core_users(self):
        fake = {"version": "9.9", "active_model_id": "patched", "active_provider_id": "pp",
                "active_reasoning_effort": "low", "request_attempts": 1, "fallback_model_ids": [],
                "providers": [{"id": "pp", "models": [{"id": "patched", "base_url": "https://x/v1",
                                                       "api_path": "/patched-path"}]}],
                "models": [{"id": "patched", "provider_id": "pp", "base_url": "https://x/v1",
                            "api_key": "k", "api_path": "/patched-path"}]}
        with patch("r20_backend.llm_manager.init_llm_config", return_value=fake):
            self.assertEqual(lm.load_llm_config().get("version"), "9.9",
                             "load_llm_config 未走补丁的 init_llm_config")
            self.assertEqual(lm.get_active_llm_runtime().get("model"), "patched",
                             "get_active_llm_runtime 未走补丁的 init_llm_config")
            self.assertIsNotNone(lm.resolve_model_runtime("patched"),
                                 "resolve_model_runtime 未走补丁的 init_llm_config")
            self.assertEqual(lm._lookup_api_path("https://x/v1", "patched"), "/patched-path",
                             "_lookup_api_path 未走补丁的 init_llm_config")

    def test_get_active_llm_runtime_patch_reaches_execute_llm_request(self):
        """execute_llm_request 必须用门面注入的 runtime，而不是自己重新加载。"""
        seen = {}
        def fake_runtime():
            seen["called"] = True
            return {"model": "seam-probe-model", "base_url": "http://127.0.0.1:9/v1",
                    "api_key": "k", "api_format": "openai_chat", "reasoning_effort": "high"}
        with patch("r20_backend.llm_manager.get_active_llm_runtime", side_effect=fake_runtime), \
             patch("r20_backend.llm_manager.resolve_model_runtime", return_value=None), \
             patch("r20_backend.llm_manager.init_llm_config",
                   return_value={"request_attempts": 1, "fallback_model_ids": [], "models": []}):
            with self.assertRaises(Exception) as ctx:
                lm.execute_llm_request([{"role": "user", "content": "hi"}], timeout=3.0)
        self.assertTrue(seen.get("called"), "execute_llm_request 未调用门面的 get_active_llm_runtime")
        self.assertIn("seam-probe-model", str(ctx.exception),
                      "execute_llm_request 未采用注入的 runtime（异常未提及探针模型名）")

    # ── 4. 结构约束：无循环依赖 + 两条文件位置锚点 ─────────────────
    def test_core_modules_do_not_import_facade(self):
        offenders = []
        for path in sorted(LLM_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                # import r20_backend.llm_manager [as X]
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == "r20_backend.llm_manager":
                            offenders.append(f"{path.name}:{node.lineno} import {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    # from r20_backend.llm_manager import X
                    if node.module and "llm_manager" in node.module:
                        offenders.append(f"{path.name}:{node.lineno} from {node.module} import ...")
                    # from r20_backend import llm_manager   ← 初版漏掉这一形态（负向验证抓到）
                    elif node.module in ("r20_backend", "..", "."):
                        for a in node.names:
                            if a.name == "llm_manager":
                                offenders.append(
                                    f"{path.name}:{node.lineno} from {node.module} import llm_manager")
        self.assertEqual(offenders, [],
                         "核心模块反向 import 了门面（会造成循环依赖；应为薄壳注入）:\n  "
                         + "\n  ".join(offenders))

    def test_record_failover_event_stays_in_facade_and_is_self_contained(self):
        """tests/core/test_beijing_time_producers.py 的 isolated() 按 AST 从本文件取同名函数，
        且用裸名注入 FAILOVER_EVENTS_FILE / _atomic_write_json / _BJ。"""
        source = (ROOT / "r20_backend" / "llm_manager.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("record_failover_event", names, "record_failover_event 已不在 llm_manager.py")
        fn = next(n for n in tree.body if getattr(n, "name", None) == "record_failover_event")
        body_names = {x.id for x in ast.walk(fn) if isinstance(x, ast.Name)}
        for required in ("FAILOVER_EVENTS_FILE", "_atomic_write_json"):
            self.assertIn(required, body_names, f"record_failover_event 不再以裸名引用 {required}")

    def test_save_llm_config_keeps_file_lock_anchor(self):
        """tests/audit/test_audit_config_p4_cleanup.py 断言本文件含该字符串（配置写互斥）。"""
        source = (ROOT / "r20_backend" / "llm_manager.py").read_text(encoding="utf-8")
        self.assertIn("with file_lock(LLM_CONFIG_FILE):", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
