"""`.env` 配置持久化：**注入防线、RMW 全程持锁、原子写、脱敏回环**（第二百八十五刀，开新面 settings_store.py）。

先打印整个文件（195 行）再动笔。它是全站配置的**唯一写入口**（密钥页/通知页/LLM 页/风控页
/策略回滚都走它）。

| 语义 | 口径 |
|---|---|
| ★ **换行=注入新键，必须拒** | 审计 P2-7：`.env` 是"每行一个 KEY=VALUE"，值里带换行就等于**追加新键**（任一输入框都能伪造 `R20_BINANCE_EXECUTION=1` 这类执行开闸键）⇒ 拒绝换行/回车/NUL，并顺带拒绝控制字符 |
| ★ **写盘用"临时文件 + fsync + replace"** | 先 `mkstemp` 同目录、`fsync` 落盘、`chmod 600`、`os.replace` 原子换、再 `chmod 600`；**失败必留 `finally` 清理**临时文件（不留 `.r20-env-*` 垃圾）|
| ★ **读-改-写整体持锁** | 审计 P0-2：旧实现无锁 ⇒ 两个并发保存各自基于同一份旧文本回写，**后写者静默覆盖先写者**，而两个接口都返回"已保存"。锁只覆盖 RMW（不覆盖无 I/O 的 `os.environ` 同步）|
| ★ **键名白名单 + 键名格式** | `update_env` 只认 `MANAGED_KEYS`；`remove_env` 只认 `^[A-Za-z_][A-Za-z0-9_]*$`。未知键**整行保留**（不误删运维手写的配置）|
| ★ **脱敏读写回环防线** | `is_masked` 识别 `mask()`/`mask_url()` 的产物：掩码串出现在写请求里一律视为「用户未改动」，绝不落盘覆盖真密钥 |
| ★ **脱敏是转发不是复制** | 第 49 刀：`mask` 与 `llm.util.mask_secret` 原先是逐字节相同的两份实现，收敛到 `redact.mask` 单一事实源；但**名字必须留在本模块**（路由与审计用例直接从这里导入）|
| ★ **先校验后落盘** | `sanitize_env_value` 在字典推导里就地求值 ⇒ 非法值**在碰文件之前**就抛，不会留下半写状态 |
| ★ **落盘之后才同步进程内环境** | `os.environ` 只在写盘成功后更新，最后 `refresh_settings()` 让 pydantic 设置对象跟上 |
"""

import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import settings_store as SS


class _Base(unittest.TestCase):
    """所有用例都把 ENV_FILE 指到临时目录 —— **绝不碰生产 `.env`**。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_file = Path(self.tmp.name) / ".env"
        self._start(mock.patch.object(SS, "ENV_FILE", self.env_file))
        self._start(mock.patch.dict(os.environ, {}, clear=False))
        self._start(mock.patch.object(SS, "refresh_settings"))

    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def _write(self, text):
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text(text, encoding="utf-8")

    def _read(self):
        return self.env_file.read_text(encoding="utf-8")

    def _temps(self):
        return sorted(p.name for p in self.env_file.parent.glob(".r20-env-*"))


class MaskForwardingTests(unittest.TestCase):
    def test_it_forwards_to_the_single_source_of_truth(self):
        spy = self._spy = mock.Mock(return_value="掩码")
        with mock.patch.object(SS, "_redact_mask", spy):
            self.assertEqual(SS.mask("supersecret"), "掩码")
        spy.assert_called_once_with("supersecret", 4)

    def test_the_visible_argument_is_forwardable(self):
        spy = mock.Mock(return_value="x")
        with mock.patch.object(SS, "_redact_mask", spy):
            SS.mask("supersecret", 7)
        spy.assert_called_once_with("supersecret", 7)

    def test_the_name_stays_in_this_module(self):
        """docstring 契约：路由与审计用例直接 `from r20_backend.settings_store import mask`。"""
        self.assertTrue(callable(SS.mask))
        self.assertEqual(SS.mask.__module__, "r20_backend.settings_store")


class IsMaskedTests(unittest.TestCase):
    def test_empty_and_none_are_not_masked(self):
        self.assertIs(SS.is_masked(""), False)
        self.assertIs(SS.is_masked(None), False)

    def test_a_run_of_eight_stars_is_masked(self):
        self.assertIs(SS.is_masked("abc********def"), True)

    def test_a_pure_star_string_is_masked(self):
        for width in (1, 2, 3, 7, 8, 20):
            with self.subTest(width=width):
                self.assertIs(SS.is_masked("*" * width), True,
                              "纯星串一律视为掩码（不要求凑满 8 颗）")

    def test_seven_stars_alone_are_not_masked(self):
        self.assertIs(SS.is_masked("abc*******def"), False)

    def test_surrounding_whitespace_is_stripped_first(self):
        self.assertIs(SS.is_masked("  ********  "), True)

    def test_a_normal_credential_is_not_masked(self):
        for value in ("sk-abcdef123456", "abc*def", "***abc***"):
            with self.subTest(value=value):
                self.assertIs(SS.is_masked(value), False)

    def test_the_mask_url_product_is_recognised(self):
        """脱敏读写回环：`mask_url` 的产物写回来时必须被认出来。"""
        masked = SS.mask_url("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef123456")
        self.assertIs(SS.is_masked(masked), True)


class MaskUrlTests(unittest.TestCase):
    def test_an_empty_url_is_returned_as_is(self):
        self.assertEqual(SS.mask_url(""), "")
        self.assertEqual(SS.mask_url(None), "")

    def test_a_url_with_a_short_query_keeps_the_base_and_hides_all(self):
        out = SS.mask_url("https://x.test/hook?abc")
        self.assertEqual(out, "https://x.test/hook?********")

    def test_a_url_with_a_long_query_keeps_the_tail(self):
        out = SS.mask_url("https://x.test/hook?key=abcdef123456")
        self.assertEqual(out, "https://x.test/hook?key=********123456")

    def test_the_visible_tail_is_configurable(self):
        out = SS.mask_url("https://x.test/hook?key=abcdef123456", visible_tail=2)
        self.assertEqual(out, "https://x.test/hook?key=********56")

    def test_a_short_url_without_a_query_becomes_all_stars(self):
        self.assertEqual(SS.mask_url("short"), "*****")
        self.assertEqual(SS.mask_url("x" * 12), "*" * 12)

    def test_a_long_url_without_a_query_keeps_head_and_tail(self):
        url = "abcdefghijkl" + "m" * 10 + "nopqrs"
        out = SS.mask_url(url)
        self.assertEqual(out, "abcdefghijkl" + "*" * 8 + "nopqrs")

    def test_the_query_is_split_once_and_the_tail_kept(self):
        """`split("?", 1)` ⇒ 后续 `&` 不再拆；尾部露出的是**整个 query** 的最后 6 个字符。"""
        out = SS.mask_url("https://x.test/a?b=1&c=2&d=3")
        self.assertEqual(out, "https://x.test/a?key=********=2&d=3")

    def test_thirteen_chars_is_already_long(self):
        """13 > 12 ⇒ 走"头 12 + 8 星 + 尾 6"；尾 6 是**最后 6 个字符**（与头 12 有重叠）。"""
        out = SS.mask_url("abcdefghijklm")
        self.assertEqual(out, "abcdefghijkl" + "*" * 8 + "hijklm")


class SanitizeEnvValueTests(unittest.TestCase):
    def test_none_becomes_an_empty_string(self):
        self.assertEqual(SS.sanitize_env_value("K", None), "")

    def test_values_are_coerced_to_text(self):
        self.assertEqual(SS.sanitize_env_value("K", 123), "123")
        self.assertEqual(SS.sanitize_env_value("K", True), "True")

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(SS.sanitize_env_value("K", "  v  "), "v")

    def test_a_plain_value_passes_through(self):
        self.assertEqual(SS.sanitize_env_value("OKX_API_KEY", "sk-abc"), "sk-abc")

    def test_newlines_are_rejected(self):
        for bad in ("a\nb", "a\rb", "a\0b"):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(SS.EnvValueError) as ctx:
                    SS.sanitize_env_value("OKX_API_KEY", bad)
                self.assertIn("OKX_API_KEY", str(ctx.exception))

    def test_the_newline_error_names_the_injection_risk(self):
        with self.assertRaises(SS.EnvValueError) as ctx:
            SS.sanitize_env_value("K", "v\nR20_BINANCE_EXECUTION=1")
        self.assertIn("防止注入新配置键", str(ctx.exception))

    def test_control_characters_are_rejected(self):
        for bad in ("a\x01b", "a\tb", "a\x1fb"):
            with self.subTest(value=repr(bad)):
                with self.assertRaises(SS.EnvValueError) as ctx:
                    SS.sanitize_env_value("K", bad)
                self.assertIn("控制字符", str(ctx.exception))

    def test_a_trailing_newline_is_also_rejected(self):
        with self.assertRaises(SS.EnvValueError):
            SS.sanitize_env_value("K", "v\n")

    def test_the_error_is_a_value_error(self):
        """路由层按 ValueError 映射 400。"""
        self.assertTrue(issubclass(SS.EnvValueError, ValueError))


class RemoveEnvTests(_Base):
    def test_it_drops_the_target_line_and_keeps_everything_else(self):
        self._write("# 注释\nOKX_API_KEY=old\nLLM_MODEL=gpt\n\nOTHER=1\n")
        SS.remove_env(["OKX_API_KEY"])
        self.assertEqual(self._read(), "# 注释\nLLM_MODEL=gpt\n\nOTHER=1\n")

    def test_only_exact_key_names_match(self):
        self._write("OKX_API_KEY=a\nOKX_API_KEY_2=b\nXOKX_API_KEY=c\n")
        SS.remove_env(["OKX_API_KEY"])
        self.assertEqual(self._read(), "OKX_API_KEY_2=b\nXOKX_API_KEY=c\n")

    def test_a_comment_mentioning_the_key_survives(self):
        self._write("# OKX_API_KEY 说明\nOKX_API_KEY=a\n")
        SS.remove_env(["OKX_API_KEY"])
        self.assertEqual(self._read(), "# OKX_API_KEY 说明\n")

    def test_lines_without_an_equals_sign_survive(self):
        self._write("bare-line\nOKX_API_KEY=a\n")
        SS.remove_env(["OKX_API_KEY"])
        self.assertEqual(self._read(), "bare-line\n")

    def test_whitespace_around_the_key_is_tolerated(self):
        self._write("  OKX_API_KEY = a  \nKEEP=1\n")
        SS.remove_env(["OKX_API_KEY"])
        self.assertEqual(self._read(), "KEEP=1\n")

    def test_a_missing_file_yields_a_single_newline(self):
        self.assertFalse(self.env_file.exists())
        SS.remove_env(["OKX_API_KEY"])
        self.assertEqual(self._read(), "\n")

    def test_the_parent_directory_is_created(self):
        nested = Path(self.tmp.name) / "a" / "b" / ".env"
        with mock.patch.object(SS, "ENV_FILE", nested):
            SS.remove_env(["K"])
        self.assertTrue(nested.parent.is_dir())

    def test_an_invalid_key_name_is_rejected_before_touching_the_file(self):
        self._write("GOOD=1\n")
        with self.assertRaises(SS.EnvValueError) as ctx:
            SS.remove_env(["BAD KEY"])
        self.assertIn("非法的环境变量键名", str(ctx.exception))
        self.assertEqual(self._read(), "GOOD=1\n", "校验失败不得改动文件")

    def test_a_key_starting_with_a_digit_is_invalid(self):
        with self.assertRaises(SS.EnvValueError):
            SS.remove_env(["1BAD"])

    def test_a_key_with_a_dash_is_invalid(self):
        with self.assertRaises(SS.EnvValueError):
            SS.remove_env(["BAD-KEY"])

    def test_an_underscore_and_digits_are_fine(self):
        self._write("K_1=a\n")
        SS.remove_env(["K_1"])
        self.assertEqual(self._read(), "\n")

    def test_the_file_mode_is_six_hundred(self):
        self._write("K=1\n")
        SS.remove_env(["K"])
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)

    def test_environment_entries_are_dropped(self):
        with mock.patch.dict(os.environ, {"OKX_API_KEY": "live", "KEEP": "1"}):
            self._write("OKX_API_KEY=a\n")
            SS.remove_env(["OKX_API_KEY"])
            self.assertNotIn("OKX_API_KEY", os.environ)
            self.assertEqual(os.environ["KEEP"], "1")

    def test_removing_a_key_that_is_absent_leaves_the_file_alone(self):
        self._write("A=1\nB=2\n")
        SS.remove_env(["ZZZ"])
        self.assertEqual(self._read(), "A=1\nB=2\n")

    def test_it_accepts_sets_lists_and_tuples(self):
        for shape in ({"A"}, ["A"], ("A",)):
            with self.subTest(shape=type(shape).__name__):
                self._write("A=1\nB=2\n")
                SS.remove_env(shape)
                self.assertEqual(self._read(), "B=2\n")

    def test_keys_are_coerced_to_strings(self):
        self._write("A=1\n")
        SS.remove_env([mock.Mock(__str__=lambda self: "A")])
        self.assertEqual(self._read(), "\n")

    def test_no_temp_file_is_left_behind(self):
        self._write("A=1\n")
        SS.remove_env(["A"])
        self.assertEqual(self._temps(), [])

    def test_the_temp_file_is_cleaned_up_when_the_replace_fails(self):
        self._write("A=1\n")
        with mock.patch.object(SS.os, "replace", side_effect=OSError("跨设备")):
            with self.assertRaises(OSError):
                SS.remove_env(["A"])
        self.assertEqual(self._temps(), [], "失败也必须清掉 .r20-env-* 临时文件")

    def test_the_rewrite_is_locked(self):
        spy = mock.Mock(wraps=SS.file_lock)
        with mock.patch.object(SS, "file_lock", spy):
            self._write("A=1\n")
            SS.remove_env(["A"])
        spy.assert_called_once_with(self.env_file)

    def test_it_returns_none(self):
        self._write("A=1\n")
        self.assertIsNone(SS.remove_env(["A"]))


class UpdateEnvTests(_Base):
    def test_it_appends_a_new_managed_key(self):
        self._write("LLM_MODEL=gpt\n")
        SS.update_env({"R20_ADMIN_TOKEN": "tok"})
        self.assertEqual(self._read(), "LLM_MODEL=gpt\n\nR20_ADMIN_TOKEN=tok\n")

    def test_it_replaces_an_existing_key_in_place(self):
        self._write("A=1\nLLM_MODEL=old\nB=2\n")
        SS.update_env({"LLM_MODEL": "new"})
        self.assertEqual(self._read(), "A=1\nLLM_MODEL=new\nB=2\n")

    def test_no_blank_separator_when_the_file_already_ends_blank(self):
        self._write("LLM_MODEL=gpt\n\n")
        SS.update_env({"R20_ADMIN_TOKEN": "tok"})
        # splitlines 得到 ["LLM_MODEL=gpt", ""] ⇒ result[-1] 为空 ⇒ **不再**补分隔空行
        self.assertEqual(self._read(), "LLM_MODEL=gpt\n\nR20_ADMIN_TOKEN=tok\n")

    def test_no_blank_separator_when_the_file_was_empty(self):
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        SS.update_env({"R20_ADMIN_TOKEN": "tok"})
        self.assertEqual(self._read(), "R20_ADMIN_TOKEN=tok\n")

    def test_unmanaged_keys_are_ignored(self):
        self._write("A=1\n")
        SS.update_env({"TOTALLY_UNMANAGED": "x"})
        self.assertEqual(self._read(), "A=1\n")

    def test_none_values_are_ignored(self):
        self._write("R20_ADMIN_TOKEN=keep\n")
        SS.update_env({"R20_ADMIN_TOKEN": None})
        self.assertEqual(self._read(), "R20_ADMIN_TOKEN=keep\n")

    def test_comments_and_blank_lines_survive(self):
        self._write("# 头注释\n\nR20_ADMIN_TOKEN=old\n")
        SS.update_env({"R20_ADMIN_TOKEN": "new"})
        self.assertEqual(self._read(), "# 头注释\n\nR20_ADMIN_TOKEN=new\n")

    def test_values_are_sanitized(self):
        self._write("")
        SS.update_env({"LLM_MODEL": "  gpt-4  "})
        self.assertEqual(self._read(), "LLM_MODEL=gpt-4\n")

    def test_booleans_become_python_text(self):
        self._write("")
        SS.update_env({"R20_GATE_EXECUTION": True})
        self.assertIn("R20_GATE_EXECUTION=True", self._read())

    def test_an_injection_attempt_is_rejected_before_any_write(self):
        self._write("LLM_MODEL=gpt\n")
        with self.assertRaises(SS.EnvValueError):
            SS.update_env({"LLM_MODEL": "gpt\nR20_BINANCE_EXECUTION=1"})
        self.assertEqual(self._read(), "LLM_MODEL=gpt\n", "失败不得留下半写状态")

    def test_several_keys_are_written_together(self):
        self._write("")
        SS.update_env({"LLM_MODEL": "a", "LLM_BASE_URL": "https://x"})
        content = self._read()
        self.assertIn("LLM_MODEL=a", content)
        self.assertIn("LLM_BASE_URL=https://x", content)

    def test_written_keys_reach_the_process_environment(self):
        self._write("")
        SS.update_env({"LLM_MODEL": "gpt-4"})
        self.assertEqual(os.environ["LLM_MODEL"], "gpt-4")

    def test_unmanaged_keys_never_reach_the_environment(self):
        self._write("")
        SS.update_env({"TOTALLY_UNMANAGED": "x"})
        self.assertNotIn("TOTALLY_UNMANAGED", os.environ)

    def test_none_values_never_reach_the_environment(self):
        self._write("")
        os.environ.pop("LLM_MODEL", None)
        SS.update_env({"LLM_MODEL": None})
        self.assertNotIn("LLM_MODEL", os.environ)

    def test_settings_are_refreshed_after_a_successful_write(self):
        self._write("")
        SS.update_env({"LLM_MODEL": "gpt-4"})
        SS.refresh_settings.assert_called_once()

    def test_settings_are_not_refreshed_when_the_write_fails(self):
        self._write("")
        with mock.patch.object(SS.os, "replace", side_effect=OSError("炸了")):
            with self.assertRaises(OSError):
                SS.update_env({"LLM_MODEL": "gpt-4"})
        SS.refresh_settings.assert_not_called()

    def test_the_file_mode_is_six_hundred(self):
        self._write("")
        SS.update_env({"LLM_MODEL": "gpt-4"})
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)

    def test_no_temp_file_is_left_behind(self):
        self._write("")
        SS.update_env({"LLM_MODEL": "gpt-4"})
        self.assertEqual(self._temps(), [])

    def test_the_temp_file_is_cleaned_up_on_a_write_failure(self):
        self._write("")

        def _boom(path, data):
            raise OSError("磁盘满")

        with mock.patch.object(SS.os, "replace", side_effect=OSError("磁盘满")):
            with self.assertRaises(OSError):
                SS.update_env({"LLM_MODEL": "gpt-4"})
        self.assertEqual(self._temps(), [])

    def test_the_rewrite_is_locked(self):
        self._write("")
        spy = mock.Mock(wraps=SS.file_lock)
        with mock.patch.object(SS, "file_lock", spy):
            SS.update_env({"LLM_MODEL": "gpt-4"})
        spy.assert_called_once_with(self.env_file)

    def test_it_returns_none(self):
        self._write("")
        self.assertIsNone(SS.update_env({"LLM_MODEL": "gpt-4"}))

    def test_a_credential_key_is_managed(self):
        self._write("")
        SS.update_env({"OKX_API_KEY": "sk-live"})
        self.assertIn("OKX_API_KEY=sk-live", self._read())

    def test_ebusy_fallback_for_docker_bind_mount(self):
        import errno
        self._write("LLM_MODEL=old-model\n")
        err = OSError("Device or resource busy")
        err.errno = errno.EBUSY
        with mock.patch.object(SS.os, "replace", side_effect=err):
            SS.update_env({"LLM_MODEL": "new-docker-model"})
        self.assertIn("LLM_MODEL=new-docker-model", self._read())
        self.assertEqual(self._temps(), [])

    def test_a_non_string_key_is_not_managed(self):
        self._write("A=1\n")
        SS.update_env({123: "x"})
        self.assertEqual(self._read(), "A=1\n")


class RiskEnvKeysFallbackTests(unittest.TestCase):
    def test_the_import_fallback_runs_in_an_isolated_namespace(self):
        """`scripts/risk_constants` 拿不到时走 `except Exception: pass`（第 62–63 行）。

        这一行在本部署里**结构性不可达**（该模块一直可导入），所以把模块**源码**在
        **全新命名空间**里编译执行（同一 `__file__` ⇒ 覆盖率按同一文件计），真正跑一遍
        兜底分支，同时**不触碰**线上模块对象。
        """
        import sys
        src = Path(SS.__file__).read_text(encoding="utf-8")
        ns = {"__name__": "r20_settings_store_fallback_probe",
              "__file__": SS.__file__, "__package__": "r20_backend"}
        with mock.patch.dict(sys.modules, {"scripts.risk_constants": None}):
            exec(compile(src, SS.__file__, "exec"), ns)
        self.assertIn("R20_MAX_LEVERAGE", SS.MANAGED_KEYS, "线上模块不受影响")
        self.assertNotIn("R20_MAX_LEVERAGE", ns["MANAGED_KEYS"],
                         "风控键表拿不到 ⇒ 兜底静默跳过，只剩字面白名单")
        self.assertIn("OKX_API_KEY", ns["MANAGED_KEYS"], "字面白名单照常生效")
        self.assertIn("R20_MAX_TOTAL_EXPOSURE_USDT", ns["MANAGED_KEYS"],
                      "该键同时在字面白名单里（所以它不随兜底消失）")


class ManagedKeysTests(unittest.TestCase):
    def test_the_execution_toggles_are_managed(self):
        """真实下单权限的开关必须在白名单里，否则后台改不动（也不该能改）。"""
        for key in ("R20_GATE_EXECUTION", "R20_GATE_DEMO_EXECUTION",
                    "R20_BINANCE_EXECUTION", "R20_BINANCE_DEMO_EXECUTION",
                    "R20_MAX_TOTAL_EXPOSURE_USDT", "R20_MANUAL_CLOSE_ENABLED"):
            self.assertIn(key, SS.MANAGED_KEYS)

    def test_the_six_account_credential_families_are_managed(self):
        for venue in ("OKX", "BINANCE", "GATE"):
            for env in ("LIVE", "DEMO"):
                self.assertIn(f"{venue}_{env}_API_KEY", SS.MANAGED_KEYS)
                self.assertIn(f"{venue}_{env}_SECRET_KEY", SS.MANAGED_KEYS)

    def test_every_managed_key_is_a_valid_env_name(self):
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
        for key in SS.MANAGED_KEYS:
            with self.subTest(key=key):
                self.assertRegex(key, pattern)

    def test_risk_env_keys_are_merged_in(self):
        """风控参数由 `scripts/risk_constants.RISK_ENV_KEYS` 提供，必须被并进来。"""
        import scripts.risk_constants as RC
        self.assertTrue(set(RC.RISK_ENV_KEYS) <= SS.MANAGED_KEYS)

    def test_the_env_file_points_at_the_repo_root(self):
        from r20_backend.config import ROOT
        self.assertEqual(SS.ENV_FILE, ROOT / ".env")


if __name__ == "__main__":
    unittest.main()
