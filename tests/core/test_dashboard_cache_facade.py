"""看板缓存门面的「未就绪 ≠ 出错」与路径注入（第一百八十九刀）。

`dashboard_cache.py` 是看板数据的**门面**：路径常量在模块级，真正实现放在
`dashboard_payload/*`；门面函数是**薄壳**，调用时才解析门面全局 —— 于是测试才能
既钉住「传的是哪个路径」又钉住「读不到时页面看到什么」。

| 语义 | 纪律 |
|---|---|
| `_fetch_json` | 成功 ⇒ `(True, 值, "")`；**凭证未配置** ⇒ `(False, None, 人话文案)`；其他异常 ⇒ `(False, None, 类型: 消息)` —— **异常绝不冒泡**，错误文本直接给页面 `data_health.errors` |
| 薄壳 | `load_trading_memory_md` / `_memory_freshness_note` 只负责把**模块级路径常量**传下去 |
"""

import unittest
from unittest.mock import patch

import r20_backend.dashboard_cache as dc
import scripts.okx_rest as okx_rest


class FacadeShellTest(unittest.TestCase):
    def test_trading_memory_shell_passes_both_paths(self):
        with patch.object(dc, "_core_load_trading_memory_md", return_value="MEM") as m:
            self.assertEqual(dc.load_trading_memory_md(), "MEM")
        self.assertEqual(m.call_args.args, (dc.AI_MEMORY_MD_FILE, dc.DATA_DIR),
                         "薄壳要把模块级路径常量传下去（不是让实现在别处重新猜路径）")

    def test_freshness_note_shell_passes_data_dir(self):
        with patch.object(dc, "_core__memory_freshness_note", return_value="note") as m:
            self.assertEqual(dc._memory_freshness_note(), "note")
        self.assertEqual(m.call_args.args, (dc.DATA_DIR,))


class FetchJsonTest(unittest.TestCase):
    def test_success_returns_value_and_empty_error(self):
        self.assertEqual(dc._fetch_json(lambda a, b=2: a + b, 1, b=3), (True, 4, ""))

    def test_not_configured_is_a_human_sentence_not_a_traceback(self):
        """★ 凭证未配置 ⇒ **NOT READY 人话文案**（不是 traceback，也不是空错误）。"""
        def _boom():
            raise okx_rest.OKXNotConfigured("no key")
        ok, data, err = dc._fetch_json(_boom)
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertEqual(err, dc._NOT_READY_TEXT)
        self.assertIn("NOT READY", err)
        self.assertNotIn("Traceback", err)

    def test_other_errors_are_surfaced_with_their_type_and_never_escape(self):
        """★ 其他异常 ⇒ **绝不冒泡**，类型名 + 消息透传给页面。"""
        def _boom():
            raise ValueError("坏数据")
        ok, data, err = dc._fetch_json(_boom)
        self.assertFalse(ok)
        self.assertIsNone(data)
        self.assertEqual(err, "ValueError: 坏数据")
        self.assertTrue(err.startswith(type(ValueError()).__name__), "类型名打头，便于页面归类")


if __name__ == "__main__":
    unittest.main()
