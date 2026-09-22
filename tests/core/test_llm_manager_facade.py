"""LLM 门面：**每个薄壳都必须在调用时解析模块全局**（第二百六十七刀，开新面 llm_manager.py）。

先打印整个文件（281 行）再动笔。结构优化阶段 2/B4 把实现迁去了 `r20_backend.llm.*`，
本文件只剩**薄壳**。薄壳存在的唯一理由，就是文档里反复写的那句：
「调用时解析模块全局，测试沙箱 patch / 直接赋值必然生效」。

所以本刀钉的不是"转发对不对"，而是**转发时机**：

| 语义 | 口径 |
|---|---|
| ★ **调用时解析，不是导入时捕获** | 先在导入后 patch `LLM_CONFIG_FILE` / `init_llm_config` / `FAILOVER_EVENTS_FILE`，薄壳必须把**patch 后的**对象交给实现——若哪天有人把 `LLM_CONFIG_FILE` 当默认参数或提前绑定，测试沙箱立刻失效（这是**静默**的：功能照跑，只是测试注入无效）|
| ★ **store 组 12 个薄壳** | `init/load/get_active/resolve/activate/update_settings/upsert_model/delete_model/upsert_provider/toggle_provider/clear_provider_models/delete_provider` 逐个验「全局 + 位置参数」的完整转发 |
| ★ **call 组 4 个薄壳** | `fetch_remote_models`/`execute_llm_request`/`_lookup_api_path`/`test_llm_connection` 传的是**函数对象**（`init_llm_config` 等），不是路径——与 store 组不同，故单列一类 |
| ★ **韧性遥测绝不许炸交易链** | `record_failover_event` 是**唯一有真实逻辑**的：`ts`/`time_str` 由它自己补、新事件插到最前、**上限 200 条**、读到坏文件当空表、**任何异常一律吞掉**（写不进去也不能把下单路径带崩）|
| 兼容别名 | `init_llm_providers` / `LLM_PROVIDERS_FILE` 在**导入时**绑定 ⇒ patch 正名**不会**同步别名（`tests/llm` 里已用 `lm.LLM_PROVIDERS_FILE = lm.LLM_CONFIG_FILE` 手工同步；本刀把这条事实钉住）|
"""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

from r20_backend import llm_manager as LM


class FacadeBase(unittest.TestCase):
    def _start(self, patcher):
        """启动并返回**被替换出来的 Mock**（`patcher.start()` 的返回值），
        而不是 patcher 本身 —— 否则 `impl.assert_called_once_with` 会打在 patcher 上。"""
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-llmfacade-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # 用哨兵对象代替路径：只要薄壳转发的是「当下这个全局」，断言就能一眼看出它有没有被提前捕获
        self.sentinel = object()
        self._start(mock.patch.object(LM, "LLM_CONFIG_FILE", self.sentinel))
        self._start(mock.patch.object(LM, "FAILOVER_EVENTS_FILE",
                                      self.tmp / "llm_failover_events.json"))


class AliasTests(FacadeBase):
    def test_compatibility_aliases_are_import_time_snapshots(self):
        """⚠️ 别名在**导入时**绑定，patch 正名**不会**同步它。

        本仓已因此踩过坑：`tests/llm/test_llm_key_rotation.py` 必须写
        `lm.LLM_PROVIDERS_FILE = lm.LLM_CONFIG_FILE  # 兼容别名必须同步 patch，否则端点写真实文件`。
        这里把"别名不会跟着动"这条事实钉住，免得后人以为 patch 一次就够。
        """
        self.assertIs(LM.init_llm_providers, LM.init_llm_config,
                      "未 patch 时两个名字指向同一函数对象")
        self.assertIs(LM.LLM_CONFIG_FILE, self.sentinel, "正名已被沙箱替换")
        self.assertIsNot(LM.LLM_PROVIDERS_FILE, LM.LLM_CONFIG_FILE,
                         "别名仍是导入时那个路径（不会跟着 patch 走）")
        self.assertEqual(LM.LLM_PROVIDERS_FILE, LM.ROOT / "data" / "llm_models.json")

    def test_init_llm_config_forwards_the_current_path(self):
        impl = self._start(mock.patch.object(LM, "_store_init_llm_config",
                                             return_value={"providers": []}))
        out = LM.init_llm_config()
        impl.assert_called_once_with(self.sentinel)
        self.assertEqual(out, {"providers": []})


class StoreShellTests(FacadeBase):
    """store 组：一律透传 `(LLM_CONFIG_FILE, init_llm_config, ...)`。"""

    def _shell(self, attr, call, args):
        impl = self._start(mock.patch.object(LM, attr, return_value="OK"))
        resolver = self._start(mock.patch.object(LM, "init_llm_config"))
        out = call()
        impl.assert_called_once_with(self.sentinel, resolver, *args)
        self.assertEqual(out, "OK")
        return impl

    def test_load_llm_config_resolves_the_resolver_at_call_time(self):
        impl = self._start(mock.patch.object(LM, "_store_load_llm_config",
                                             return_value={"a": 1}))
        resolver = self._start(mock.patch.object(LM, "init_llm_config",
                                                 return_value={"raw": True}))
        out = LM.load_llm_config()
        resolver.assert_called_once_with()
        impl.assert_called_once_with({"raw": True}, True)
        self.assertEqual(out, {"a": 1})

    def test_load_llm_config_forwards_mask_keys(self):
        impl = self._start(mock.patch.object(LM, "_store_load_llm_config",
                                             return_value={}))
        self._start(mock.patch.object(LM, "init_llm_config", return_value={}))
        LM.load_llm_config(mask_keys=False)
        impl.assert_called_once_with({}, False)

    def test_get_active_llm_runtime_passes_the_resolved_config_only(self):
        impl = self._start(mock.patch.object(LM, "_store_get_active_llm_runtime",
                                             return_value="OK"))
        resolver = self._start(mock.patch.object(LM, "init_llm_config",
                                                 return_value={"cfg": 1}))
        self.assertEqual(LM.get_active_llm_runtime(), "OK")
        resolver.assert_called_once_with()
        impl.assert_called_once_with({"cfg": 1})
        # 这两个薄壳传的是**已解析的配置**，不是「路径 + 解析函数」
        self.assertEqual([c.args for c in impl.call_args_list], [({"cfg": 1},)])

    def test_resolve_model_runtime_passes_the_resolved_config(self):
        impl = self._start(mock.patch.object(LM, "_store_resolve_model_runtime",
                                             return_value="OK"))
        resolver = self._start(mock.patch.object(LM, "init_llm_config",
                                                 return_value={"cfg": 2}))
        self.assertEqual(LM.resolve_model_runtime("m-1"), "OK")
        resolver.assert_called_once_with()
        impl.assert_called_once_with({"cfg": 2}, "m-1")

    def test_activate_provider_model(self):
        self._shell("_store_activate_provider_model",
                    lambda: LM.activate_provider_model("p", "m", "high", 30.0),
                    ("p", "m", "high", 30.0))

    def test_activate_provider_model_optional_args_default_to_none(self):
        impl = self._start(mock.patch.object(LM, "_store_activate_provider_model"))
        self._start(mock.patch.object(LM, "init_llm_config"))
        LM.activate_provider_model("p", "m")
        self.assertEqual(impl.call_args[0][2:], ("p", "m", None, None))

    def test_update_llm_settings(self):
        self._shell("_store_update_llm_settings",
                    lambda: LM.update_llm_settings("m", "low", 5.0, 3, ["f1"]),
                    ("m", "low", 5.0, 3, ["f1"]))

    def test_upsert_model(self):
        self._shell("_store_upsert_model",
                    lambda: LM.upsert_model("p", {"id": "m"}), ("p", {"id": "m"}))

    def test_delete_model(self):
        impl = self._start(mock.patch.object(LM, "_store_delete_model",
                                            return_value=True))
        self._start(mock.patch.object(LM, "init_llm_config"))
        self.assertTrue(LM.delete_model("p", "m"))
        impl.assert_called_once_with(self.sentinel, mock.ANY, "p", "m")

    def test_upsert_provider(self):
        self._shell("_store_upsert_provider",
                    lambda: LM.upsert_provider({"id": "p"}), ({"id": "p"},))

    def test_toggle_provider(self):
        self._shell("_store_toggle_provider",
                    lambda: LM.toggle_provider("p", False), ("p", False))

    def test_clear_provider_models(self):
        impl = self._start(mock.patch.object(LM, "_store_clear_provider_models",
                                             return_value=True))
        self._start(mock.patch.object(LM, "init_llm_config"))
        self.assertTrue(LM.clear_provider_models("p"))
        impl.assert_called_once_with(self.sentinel, mock.ANY, "p")

    def test_delete_provider(self):
        impl = self._start(mock.patch.object(LM, "_store_delete_provider",
                                             return_value=True))
        self._start(mock.patch.object(LM, "init_llm_config"))
        self.assertTrue(LM.delete_provider("p"))
        impl.assert_called_once_with(self.sentinel, mock.ANY, "p")


class SaveConfigTests(FacadeBase):
    def test_save_takes_the_file_lock_and_writes_atomically(self):
        import contextlib
        lock = self._start(mock.patch.object(LM, "file_lock",
                                             return_value=contextlib.nullcontext()))
        writer = self._start(mock.patch.object(LM, "_atomic_write_json"))
        LM.save_llm_config({"providers": []})
        lock.assert_called_once_with(self.sentinel)
        writer.assert_called_once_with(self.sentinel, {"providers": []})

    def test_save_resolves_the_path_at_call_time(self):
        import contextlib
        self._start(mock.patch.object(LM, "file_lock",
                                      return_value=contextlib.nullcontext()))
        writer = self._start(mock.patch.object(LM, "_atomic_write_json"))
        replacement = self.tmp / "other.json"
        with mock.patch.object(LM, "LLM_CONFIG_FILE", replacement):
            LM.save_llm_config({"a": 1})
        self.assertEqual(writer.call_args[0][0], replacement,
                         "写入口必须读当下的全局，否则沙箱 patch 会静默失效")


class CallShellTests(FacadeBase):
    """call 组：透传的是**函数对象**，不是路径。"""

    def test_fetch_remote_models_passes_function_handles(self):
        impl = self._start(mock.patch.object(LM, "_core_fetch_remote_models",
                                             return_value={"models": []}))
        resolver = self._start(mock.patch.object(LM, "init_llm_config"))
        active = self._start(mock.patch.object(LM, "get_active_llm_runtime"))
        out = LM.fetch_remote_models("https://api", "k", "p", 3.0)
        impl.assert_called_once_with(resolver, active, "https://api", "k", "p", 3.0)
        self.assertEqual(out, {"models": []})

    def test_execute_llm_request_passes_all_three_hooks(self):
        impl = self._start(mock.patch.object(LM, "_core_execute_llm_request",
                                             return_value=("t", "c", {}, 200)))
        active = self._start(mock.patch.object(LM, "get_active_llm_runtime"))
        resolve = self._start(mock.patch.object(LM, "resolve_model_runtime"))
        recorder = self._start(mock.patch.object(LM, "record_failover_event"))
        out = LM.execute_llm_request([{"role": "user", "content": "hi"}],
                                     model="m", allow_fallback=False)
        impl.assert_called_once_with(active, resolve, recorder,
                                     [{"role": "user", "content": "hi"}],
                                     "m", None, None, None, None, 0.2, None, None, False)
        self.assertEqual(out, ("t", "c", {}, 200))

    def test_lookup_api_path_passes_the_resolver(self):
        impl = self._start(mock.patch.object(LM, "_core__lookup_api_path",
                                             return_value="/v1/chat"))
        resolver = self._start(mock.patch.object(LM, "init_llm_config"))
        self.assertEqual(LM._lookup_api_path("https://api", "m"), "/v1/chat")
        impl.assert_called_once_with(resolver, "https://api", "m")

    def test_test_llm_connection_passes_the_resolver(self):
        impl = self._start(mock.patch.object(LM, "_core_test_llm_connection",
                                             return_value={"ok": True}))
        resolver = self._start(mock.patch.object(LM, "init_llm_config"))
        out = LM.test_llm_connection("https://api", "k", "m", "openai_chat")
        impl.assert_called_once_with(resolver, "https://api", "k", "m",
                                     "openai_chat", "auto", "auto", 15.0, "")
        self.assertTrue(out["ok"])

    def test_recent_failover_events_passes_the_current_events_file(self):
        impl = self._start(mock.patch.object(LM, "_core_recent_failover_events",
                                             return_value=[]))
        LM.recent_failover_events(5)
        impl.assert_called_once_with(LM.FAILOVER_EVENTS_FILE, 5)

    def test_recent_failover_events_default_limit(self):
        impl = self._start(mock.patch.object(LM, "_core_recent_failover_events",
                                             return_value=[]))
        LM.recent_failover_events()
        self.assertEqual(impl.call_args[0][1], 30)


class RecordFailoverEventTests(FacadeBase):
    def setUp(self):
        super().setUp()
        self.file = LM.FAILOVER_EVENTS_FILE
        self.writer = self._start(mock.patch.object(LM, "_atomic_write_json"))

    def test_stamps_time_and_prepends_the_event(self):
        entry = {"kind": "retry_exhausted", "model": "m"}
        LM.record_failover_event(entry)
        self.assertIsInstance(entry["ts"], int)
        self.assertIn("time_str", entry)
        stamped = datetime.fromtimestamp(entry["ts"],
                                         timezone(timedelta(hours=8)))
        self.assertTrue(entry["time_str"].startswith(stamped.strftime("%Y-%m-%d")))
        written_path, written_events = self.writer.call_args[0]
        self.assertEqual(written_path, self.file)
        self.assertEqual(written_events[0]["kind"], "retry_exhausted")

    def test_new_events_go_to_the_front_and_duplicates_are_kept(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps([{"kind": "old"}]), encoding="utf-8")
        LM.record_failover_event({"kind": "new"})
        events = self.writer.call_args[0][1]
        self.assertEqual([e["kind"] for e in events], ["new", "old"])

    def test_history_is_capped_at_two_hundred(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps([{"kind": f"e{i}"} for i in range(250)]),
                             encoding="utf-8")
        LM.record_failover_event({"kind": "newest"})
        events = self.writer.call_args[0][1]
        self.assertEqual(len(events), 200)
        self.assertEqual(events[0]["kind"], "newest")
        self.assertEqual(events[-1]["kind"], "e198", "从头截断，保留最新的 200 条")

    def test_corrupt_or_non_list_history_is_treated_as_empty(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text("{not json", encoding="utf-8")
        LM.record_failover_event({"kind": "a"})
        self.assertEqual([e["kind"] for e in self.writer.call_args[0][1]], ["a"])

        self.file.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        LM.record_failover_event({"kind": "b"})
        self.assertEqual([e["kind"] for e in self.writer.call_args[0][1]], ["b"])

    def test_absent_file_is_not_an_error(self):
        self.assertFalse(self.file.exists())
        LM.record_failover_event({"kind": "a"})
        self.assertEqual([e["kind"] for e in self.writer.call_args[0][1]], ["a"])

    def test_write_failure_is_swallowed_so_trading_never_breaks(self):
        self.writer.side_effect = OSError("磁盘满了")
        LM.record_failover_event({"kind": "a"})   # 不许抛
        self.assertEqual(self.writer.call_count, 1)

    def test_unreadable_history_is_swallowed(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text("[]", encoding="utf-8")
        with mock.patch("builtins.open", side_effect=PermissionError("拒绝访问")):
            LM.record_failover_event({"kind": "a"})   # 读历史失败 ⇒ 当空表继续
        self.assertEqual([e["kind"] for e in self.writer.call_args[0][1]], ["a"])


if __name__ == "__main__":
    unittest.main()
