"""策略快照文件锁与原子 I/O 原语（`r20_backend/policy/io.py`）残余分支收口测试 —— 第 358 刀。

本模块 77 行，是策略快照存储原子写与多进程索引互斥锁核心：
- `fcntl` 模块跨平台导入降级（Windows/受限环境 `fcntl = None` 兼容）；
- 纯线程锁降级执行（`fcntl is None` 时安全退化为纯线程锁上下文）；
- 文件锁释放与描述符关闭容错（`fcntl.flock(LOCK_UN)` 与 `os.close` 遇 `OSError` 安全 pass）。
"""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import r20_backend.policy.io as io_mod


class PolicyIOTailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="r20_policy_io_tails_")
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)

    # -------------------------------------------------------------------------
    # 1. 跨平台 fcntl 缺失与纯线程锁降级
    # -------------------------------------------------------------------------
    def test_fcntl_import_fallback(self):
        # 验证在无 fcntl 的受限环境导入时，回退 fcntl = None (line 19)
        orig_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("No module named 'fcntl'")
            return orig_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            if "r20_backend.policy.io" in sys.modules:
                del sys.modules["r20_backend.policy.io"]
            try:
                mod = importlib.import_module("r20_backend.policy.io")
                self.assertIsNone(mod.fcntl)
            finally:
                # 复原原始模块引用
                sys.modules["r20_backend.policy.io"] = io_mod

    def test_index_lock_fallback_when_fcntl_is_none(self):
        # fcntl 为 None 时降级为纯线程锁，不生成 .index.lock 文件 (lines 44-46)
        with patch.object(io_mod, "fcntl", None):
            with io_mod._index_lock(self.tmp_path):
                lock_file = self.tmp_path / ".index.lock"
                self.assertFalse(lock_file.exists())

    # -------------------------------------------------------------------------
    # 2. 解锁与描述符关闭容错
    # -------------------------------------------------------------------------
    def test_index_lock_unlock_os_error_ignored(self):
        # 解锁时 fcntl.flock 抛 OSError 被静默忽略 (lines 70-72)
        # 第一次加锁成功 (None)，第二次解锁抛 OSError
        with patch("fcntl.flock", side_effect=[None, OSError("flock unlock failed")]):
            with io_mod._index_lock(self.tmp_path):
                pass
            self.assertIsNone(getattr(io_mod._lock_tls, "fd", None))

    def test_index_lock_close_os_error_ignored(self):
        # 关闭文件描述符时 os.close 抛 OSError 被静默忽略 (lines 74-76)
        with patch("os.close", side_effect=OSError("close failed")):
            with io_mod._index_lock(self.tmp_path):
                pass
            self.assertIsNone(getattr(io_mod._lock_tls, "fd", None))


if __name__ == "__main__":
    unittest.main()
