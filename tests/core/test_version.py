"""版本号唯一真源：**`get_version()` 会去读自己的源码文件**（第二百九十刀，开新面 version.py）。

★ 为什么值得单独开一刀：探针显示 `version.py` 的 `get_version()` **整整 10 行从未被执行过**
（`5/14 = 35.7%`）。原因是全仓唯一碰它的用例

    tests/core/test_router_system.py:602
    mock.patch.object(A, "get_version", return_value="9.9.9")

把它**打桩打掉了** —— 于是 `/api/v1/system/*` 的版本字段永远在测"假版本"，
而这个函数真正的行为（**每次调用都重新读盘**，好让展示层不用重启就能感知版本变更）
从来没被验证过。

| 语义 | 口径 |
|---|---|
| ★ **每次调用都重新读盘** | 这正是它的存在理由：常驻进程不重启也要能感知本地版本变更。故用「把模块常量改成过期值，`get_version()` 仍须返回文件里的真值」钉死 |
| ★ **解析规则** | 逐行找 `startswith("__version__")` **且含 `=`** 的行；取 `=` 右侧并剥掉空白与**单/双引号**；**第一行胜出** |
| ★ **任何异常都回落静态 `__version__`** | 读不到文件、路径解析失败、文件里压根没有那一行 —— 一律退回导入期常量（不许抛到展示层）|
"""

import re
import unittest
from pathlib import Path
from unittest import mock

from r20_backend import version as V


class VersionConstantTests(unittest.TestCase):
    def test_the_version_looks_like_a_semver(self):
        self.assertRegex(V.__version__, r"^\d+\.\d+\.\d+")

    def test_the_display_version_is_prefixed_with_v(self):
        self.assertEqual(V.APP_VERSION, f"v{V.__version__}")

    def test_the_branding_names(self):
        self.assertEqual(V.APP_NAME, "R20量子交易系统")
        self.assertEqual(V.APP_NAME_EN, "R20 Quantum Trading System")

    def test_the_module_file_exists_and_is_named_version(self):
        self.assertEqual(Path(V.__file__).name, "version.py")
        self.assertTrue(Path(V.__file__).is_file())


class GetVersionTests(unittest.TestCase):
    def test_it_reads_the_declared_version_from_disk(self):
        self.assertEqual(V.get_version(), V.__version__)

    def test_it_agrees_with_the_literal_in_the_source_file(self):
        source = Path(V.__file__).read_text(encoding="utf-8")
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.M)
        self.assertIsNotNone(match, "源码里应当有一行 __version__ 字面量")
        self.assertEqual(V.get_version(), match.group(1))

    def test_a_stale_module_constant_does_not_win(self):
        """★ 核心语义：**以磁盘为准**，不是返回模块常量。"""
        with mock.patch.object(V, "__version__", "0.0.0-stale"):
            self.assertNotEqual(V.get_version(), "0.0.0-stale")
            self.assertEqual(V.get_version(), V.APP_VERSION.lstrip("v"))

    def test_it_falls_back_when_the_source_cannot_be_read(self):
        with mock.patch.object(V.Path, "read_text", side_effect=OSError("读不到")):
            with mock.patch.object(V, "__version__", "1.2.3"):
                self.assertEqual(V.get_version(), "1.2.3")

    def test_it_falls_back_when_there_is_no_version_line(self):
        with mock.patch.object(V.Path, "read_text", return_value="# 没有版本行\n"):
            with mock.patch.object(V, "__version__", "9.9.9"):
                self.assertEqual(V.get_version(), "9.9.9")

    def test_a_line_that_merely_starts_with_version_is_skipped(self):
        """`__version__` 开头但**没有 `=`** 的行必须跳过，继续往下找。"""
        text = "__version__ 说明文字\n__version__ = \"5.5.5\"\n"
        with mock.patch.object(V.Path, "read_text", return_value=text):
            self.assertEqual(V.get_version(), "5.5.5")

    def test_surrounding_whitespace_and_quotes_are_stripped(self):
        with mock.patch.object(V.Path, "read_text",
                               return_value='__version__ =   "7.7.7"  \n'):
            self.assertEqual(V.get_version(), "7.7.7")

    def test_single_quotes_are_stripped_too(self):
        with mock.patch.object(V.Path, "read_text",
                               return_value="__version__ = '6.6.6'\n"):
            self.assertEqual(V.get_version(), "6.6.6")

    def test_an_unquoted_value_is_returned_as_is(self):
        with mock.patch.object(V.Path, "read_text", return_value="__version__ = 3.3.3\n"):
            self.assertEqual(V.get_version(), "3.3.3")

    def test_the_first_match_wins(self):
        text = '__version__ = "1.1.1"\n__version__ = "2.2.2"\n'
        with mock.patch.object(V.Path, "read_text", return_value=text):
            self.assertEqual(V.get_version(), "1.1.1")

    def test_leading_whitespace_on_the_line_is_tolerated(self):
        with mock.patch.object(V.Path, "read_text",
                               return_value='    __version__ = "4.4.4"\n'):
            self.assertEqual(V.get_version(), "4.4.4")

    def test_it_does_not_cache_the_result(self):
        """两次调用都各自读盘 —— 文件改了不用重启就能看见。"""
        with mock.patch.object(V.Path, "read_text",
                               side_effect=['__version__ = "1.0.0"\n',
                                            '__version__ = "2.0.0"\n']) as read:
            self.assertEqual(V.get_version(), "1.0.0")
            self.assertEqual(V.get_version(), "2.0.0")
        self.assertEqual(read.call_count, 2)

    def test_the_path_is_resolved_through_this_module(self):
        seen = {}
        real_read_text = Path.read_text

        def _read(self_, *a, **k):
            seen["path"] = self_
            return real_read_text(self_, *a, **k)

        with mock.patch.object(V.Path, "read_text", _read):
            V.get_version()
        self.assertEqual(Path(seen["path"]).resolve(), Path(V.__file__).resolve())


if __name__ == "__main__":
    unittest.main()
