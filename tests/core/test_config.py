"""环境配置装配：**两条 fail-soft 兜底**（第二百九十三刀，开新面 config.py）。

先打印整个文件（89 行）再动笔，未命中的 3 行全是**兜底分支**（正常路径早被既有用例覆盖）：

| 行 | 分支 | 口径 |
|---|---|---|
| 14 | `load_encrypted_secrets()` 的 `except: pass` | 密钥库注入失败**不许让后端起不来** —— 缺密钥的后果是"该所未配置"，不该是进程崩溃 |
| 19 | `load_dotenv(path)` 的 `if not path.exists(): return` | `.env` 不存在是**正常态**（全新部署/容器里只用环境变量），静默返回 |
| 71 | `refresh_settings()` 里 `load_secrets()` 的 `except: secret_values = {}` | 密钥库解密失败时，已配置判定**退回只看 `os.environ`**，绝不让整个装配抛出去 |

★ 另一个必须记下的沙箱事实：`tests/__init__.py:70` 会把 `config.load_dotenv`
**换成 `lambda path: None`**（防止 `.env` 回灌进 `os.environ`）⇒ 想测这段真实现，
只能像下面这样把函数节点从源码里摘出来单独 exec。

★ 本刀最要紧的一条是**别把线上的东西读进来**：`refresh_settings()` 原实现会
`load_dotenv(ROOT/".env")` + `load_encrypted_secrets()`（读 `data/r20_secrets.enc`），
所以用例把这两个函数 patch 掉，只测"装配逻辑"本身，绝不触碰生产配置与密钥库。
`settings` 是模块级单例，逐用例**整份快照/还原**其 `__dict__`，不留跨用例污染。
"""

import ast
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from r20_backend import config as CFG
from r20_gateway import secrets as SEC


class LoadEncryptedSecretsTests(unittest.TestCase):
    def test_a_successful_injection_is_silent(self):
        with mock.patch.object(SEC, "inject_into_environment") as inject:
            self.assertIsNone(CFG.load_encrypted_secrets())
        inject.assert_called_once()

    def test_an_injection_failure_is_swallowed(self):
        """密钥库坏了只该导致"该所未配置"，不该让后端进程起不来。"""
        with mock.patch.object(SEC, "inject_into_environment",
                               side_effect=RuntimeError("密钥库坏了")):
            self.assertIsNone(CFG.load_encrypted_secrets())

    def test_an_import_failure_is_also_swallowed(self):
        with mock.patch.dict("sys.modules", {"r20_gateway.secrets": None}):
            self.assertIsNone(CFG.load_encrypted_secrets())


def _extract(function_name: str):
    """从源码里单独取出一个函数的 AST 再 exec，拿到**真实现**。

    ⚠️ 两条都不能走：
    1. 直接调 `CFG.load_dotenv` —— `tests/__init__.py:70` 会把它换成
       `lambda path: None`（会话级沙箱要挡住 `.env` 回灌到 `os.environ`），
       于是真实现根本不会被调用；
    2. 整个 `exec` 一遍 `config.py` —— 它在文件末尾就 `settings = Settings();
       refresh_settings()`，那会去读**生产 `.env` 与 `data/r20_secrets.enc`**。

    故只把目标函数节点摘出来执行（本仓既有的"单函数 AST + exec"手法）。
    """
    source = Path(CFG.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            namespace = {"os": os, "Path": Path}
            exec(compile(module, CFG.__file__, "exec"), namespace)  # noqa: S102
            return namespace[function_name]
    raise AssertionError(f"config.py 里没有 {function_name}")


class LoadDotenvTests(unittest.TestCase):
    def setUp(self):
        self.load_dotenv = _extract("load_dotenv")
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = dict(os.environ)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def _write(self, text):
        path = Path(self.tmp.name) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_missing_file_is_a_silent_no_op(self):
        """★ `.env` 不存在是正常态（容器里通常只用环境变量），不是错误。"""
        missing = Path(self.tmp.name) / "definitely-missing.env"
        before = dict(os.environ)
        self.assertIsNone(CFG.load_dotenv(missing))
        self.assertEqual(dict(os.environ), before)

    def test_a_directory_instead_of_a_file_is_still_treated_as_missing(self):
        """`exists()` 对目录也返回真……于是这里会抛 —— 如实钉住该边界。"""
        directory = Path(self.tmp.name) / "a_dir"
        directory.mkdir()
        with self.assertRaises(IsADirectoryError):
            self.load_dotenv(directory)

    def test_a_present_file_is_loaded(self):
        os.environ.pop("R20_TEST_DOTENV_KEY", None)
        self.load_dotenv(self._write("R20_TEST_DOTENV_KEY=hello\n"))
        self.assertEqual(os.environ["R20_TEST_DOTENV_KEY"], "hello")

    def test_comments_blanks_and_lines_without_equals_are_skipped(self):
        for key in ("A_KEY", "B_KEY"):
            os.environ.pop(key, None)
        self.load_dotenv(self._write("# 注释\n\n没有等号\nA_KEY=1\n  B_KEY = 2  \n"))
        self.assertEqual(os.environ["A_KEY"], "1")
        self.assertEqual(os.environ["B_KEY"], "2")
        self.assertNotIn("没有等号", os.environ)

    def test_quotes_around_the_value_are_stripped(self):
        self.load_dotenv(self._write('C_KEY="quoted"\nD_KEY=\'single\'\n'))
        self.assertEqual(os.environ["C_KEY"], "quoted")
        self.assertEqual(os.environ["D_KEY"], "single")

    def test_only_the_first_equals_sign_splits(self):
        self.load_dotenv(self._write("E_KEY=a=b=c\n"))
        self.assertEqual(os.environ["E_KEY"], "a=b=c")

    def test_the_key_is_stripped(self):
        self.load_dotenv(self._write("  F_KEY  =v\n"))
        self.assertEqual(os.environ["F_KEY"], "v")


class RefreshSettingsFallbackTests(unittest.TestCase):
    """`refresh_settings()` 的第 68-71 行：密钥库读不到时退回只看 `os.environ`。"""

    def setUp(self):
        self._snapshot = dict(CFG.settings.__dict__)
        self.addCleanup(self._restore)

    def _restore(self):
        CFG.settings.__dict__.clear()
        CFG.settings.__dict__.update(self._snapshot)

    def _refresh(self, **kw):
        # ★ 必须同时挡住两条"读线上"的路径：生产 .env 与 data/r20_secrets.enc
        with mock.patch.object(CFG, "load_dotenv"), \
                mock.patch.object(CFG, "load_encrypted_secrets"), \
                mock.patch.object(SEC, "load_secrets", **kw):
            return CFG.refresh_settings()

    def test_a_load_failure_falls_back_to_the_environment_only(self):
        out = self._refresh(side_effect=RuntimeError("解密失败"))
        self.assertIs(out, CFG.settings)
        expected = bool(os.environ.get("OKX_LIVE_API_KEY")
                        and os.environ.get("OKX_LIVE_SECRET_KEY")
                        and os.environ.get("OKX_LIVE_PASSPHRASE"))
        self.assertEqual(CFG.settings.okx_live_configured, expected)

    def test_a_secret_only_key_does_not_survive_the_failure(self):
        """反证：正常路径下"只在密钥库里"的键**会**让判定为真，失败路径下**不会**。"""
        secrets = {"OKX_LIVE_API_KEY": "k", "OKX_LIVE_SECRET_KEY": "s",
                   "OKX_LIVE_PASSPHRASE": "p"}
        self.assertTrue(self._refresh(return_value=secrets).okx_live_configured)
        self._restore()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(
                self._refresh(side_effect=RuntimeError("解密失败")).okx_live_configured)

    def test_a_load_failure_still_assembles_the_rest(self):
        out = self._refresh(side_effect=RuntimeError("解密失败"))
        self.assertIsInstance(out.port, int)
        self.assertIsInstance(out.host, str)
        self.assertIsInstance(out.llm_model, str)
        self.assertTrue(out.llm_base_url.startswith("http"))

    def test_a_load_failure_does_not_wipe_an_existing_true(self):
        """兜底是"退回环境变量"，不是"一律置假" —— 环境里本就有全套时仍须为真。"""
        env = {"OKX_LIVE_API_KEY": "k", "OKX_LIVE_SECRET_KEY": "s",
               "OKX_LIVE_PASSPHRASE": "p"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertTrue(
                self._refresh(side_effect=RuntimeError("解密失败")).okx_live_configured)

    def test_the_secret_store_is_consulted_on_the_normal_path(self):
        """⚠️ 不能断言 `assert_called_once`：`selected_environment()` 内部
        （`scripts/okx_runtime.py` 的 `_load_dotenv`）**也会**调一次 `load_secrets`，
        实测共 2 次。这里只断言"被咨询过"、"结果进了 effective"。"""
        with mock.patch.object(CFG, "load_dotenv"), \
                mock.patch.object(CFG, "load_encrypted_secrets"), \
                mock.patch.object(SEC, "load_secrets", return_value={}) as load:
            CFG.refresh_settings()
        self.assertTrue(load.called)

    def test_both_callers_of_load_secrets_are_expected(self):
        """把上面那条观察钉成断言：调用方是 2 个（一个来自 okx_runtime，一个来自本函数）。"""
        import scripts.okx_runtime as RT
        with mock.patch.object(CFG, "load_dotenv"), \
                mock.patch.object(CFG, "load_encrypted_secrets"), \
                mock.patch.object(SEC, "load_secrets", return_value={}) as load:
            CFG.refresh_settings()
        self.assertGreaterEqual(load.call_count, 2)
        self.assertTrue(hasattr(RT, "_load_dotenv"))

    def test_the_env_file_and_secret_store_are_both_loaded_first(self):
        with mock.patch.object(CFG, "load_dotenv") as dotenv, \
                mock.patch.object(CFG, "load_encrypted_secrets") as enc, \
                mock.patch.object(SEC, "load_secrets", return_value={}):
            CFG.refresh_settings()
        dotenv.assert_called_once_with(CFG.ROOT / ".env")
        enc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
