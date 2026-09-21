"""`scripts/local_lock.py`：本地锁兜底的可重入化（阶段 4·B3 第四十八刀）。

## 修了什么

`scripts/prompt_library.py::_library_lock()` 与
`scripts/instrument_pool.py::_pool_lock()` 都写成同一个形状：

```python
try:
    from r20_backend.file_locks import file_lock
    return file_lock(TARGET_FILE)
except Exception:
    # …… 手写一个 contextmanager 做本地 flock ……（两处逐字相同）
```

后端 `file_lock` 是**可重入**的（审计 P2-6），而两处手写兜底是**裸 flock、
不可重入**。调用方**确实嵌套**：

```python
def mutate_instruments(mutator):
    with _pool_lock():         # 外层
        ...
        save_instruments(...)  # 内部又 with _pool_lock()
```

正常路径由层数计数兜住；一旦走兜底分支，**同线程二次 `flock` 自锁 → 永久挂死**。

**实测证据**（本文件 `DeadlockRegressionTest` 用子进程复现）：
复刻旧手写兜底并嵌套取锁 → 15 秒超时 `exit 124`（挂死）；
改用共享实现 → 正常返回 `exit 0`。

## ⚠️ 如实记录：该兜底在本仓当前是「不可达」的

`r20_backend.file_locks` 只依赖标准库，且两个脚本在本仓都由 `r20_backend`
侧导入，故 `except Exception` 分支**不会被走到**。
所以这是**潜伏**缺陷，**不是正在发生的线上故障**。

把它改成可重入是**纯行为收窄**：只在"本来会挂死"的路径上改为"正常返回"，
正常路径一行不变。故按「重构时发现原有 bug 直接修复」处理。

## 兜底语义必须与后端一致

调用方在两者之间**无条件切换**（后者 import 失败就退到本地），
任何语义差异都会让"退化路径"变成另一种行为。故本模块的层数计数机制
与 `r20_backend/file_locks.py` 的 `_STATE` **逐条对应**：

| 机制 | 后端 `file_lock` | 本模块 `local_file_lock` |
|---|---|---|
| 层数存放 | `threading.local()` | `threading.local()` |
| 已持有 | 只加层数、不再 flock | 同 |
| 归零释放 | `flock(LOCK_UN)` + `close` | 同 |
| 锁文件路径 | `.<name>.lock` 同目录 | 同 |
| 权限 | `0o600` | 同 |
| 目录创建 | `parents=True, exist_ok=True` | 同 |
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

MODULE = SCRIPTS / "local_lock.py"
FACADES = (SCRIPTS / "prompt_library.py", SCRIPTS / "instrument_pool.py")

from local_lock import local_file_lock, lock_is_held  # noqa: E402


class ReentrancyTest(unittest.TestCase):
    def test_basic_acquire_release(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "a.json"
            self.assertFalse(lock_is_held(t))
            with local_file_lock(t):
                self.assertTrue(lock_is_held(t))
            self.assertFalse(lock_is_held(t), "退出后必须释放")

    def test_nested_acquisition_does_not_deadlock(self):
        """⚠️ 本刀修的核心：同线程嵌套取锁必须不挂死。

        （旧的裸 flock 版本在这里会永久阻塞 —— 见 `DeadlockRegressionTest`。）
        """
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "b.json"
            with local_file_lock(t):
                with local_file_lock(t):
                    with local_file_lock(t):
                        self.assertTrue(lock_is_held(t))
                self.assertTrue(lock_is_held(t), "内层退出不得释放外层")

    def test_creates_lock_file_with_hidden_name(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "c.json"
            with local_file_lock(t):
                self.assertTrue((Path(d) / ".c.json.lock").exists())

    def test_lock_file_mode_is_0600(self):
        """⚠️ 负向验证暴露的**真覆盖盲区**：权限位此前完全没测。

        把 `0o600` 放宽成 `0o644` 原本**测不出来** —— 而锁文件与它所保护的
        数据同目录，权限放宽等于让同机其他用户能观察/干扰锁状态。
        这里直接断言文件 mode。
        """
        import stat as _stat
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "perm.json"
            with local_file_lock(t):
                mode = _stat.S_IMODE(os.stat(Path(d) / ".perm.json.lock").st_mode)
            self.assertEqual(mode, 0o600, "锁文件权限必须是 0o600（与后端一致）")

    def test_lock_file_mode_matches_backend(self):
        """与 `r20_backend.file_locks` 的权限保持一致（调用方无条件切换）。"""
        import stat as _stat
        from r20_backend.file_locks import file_lock
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.json", Path(d) / "b.json"
            with file_lock(a):
                backend_mode = _stat.S_IMODE(os.stat(Path(d) / ".a.json.lock").st_mode)
            with local_file_lock(b):
                local_mode = _stat.S_IMODE(os.stat(Path(d) / ".b.json.lock").st_mode)
        self.assertEqual(local_mode, backend_mode)

    def test_creates_missing_parent_dir(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "nested" / "deep" / "d.json"
            with local_file_lock(t):
                self.assertTrue(t.parent.exists())

    def test_releases_on_exception(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "e.json"
            with self.assertRaises(ValueError):
                with local_file_lock(t):
                    raise ValueError("boom")
            self.assertFalse(lock_is_held(t), "异常路径也必须释放")

    def test_held_state_is_per_target(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.json", Path(d) / "b.json"
            with local_file_lock(a):
                self.assertTrue(lock_is_held(a))
                self.assertFalse(lock_is_held(b), "不同目标文件不得互相影响")

    def test_serialises_across_threads(self):
        """跨线程必须真正互斥（同进程不同线程 → 互不共享 `threading.local`）。

        ⚠️ 我第一版在这里用了 `local_file_lock(t).__enter__()` 这种写法 ——
        它返回的是一次性 contextmanager，且参数顺序也写错了。改为显式
        `with` 结构。
        """
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "f.json"
            order = []

            def worker(blocker):
                blocker.wait(2.0)
                with local_file_lock(t):
                    order.append("acquired")

            with local_file_lock(t):
                gate = threading.Event()
                th = threading.Thread(target=worker, args=(gate,), daemon=True)
                th.start()
                gate.set()
                th.join(timeout=0.6)
                self.assertEqual(order, [], "外层持锁期间另一线程不得进入")

            # 外层已释放 → 那个线程应当能进去
            th.join(timeout=3.0)
            self.assertEqual(order, ["acquired"], "释放后另一线程应能进入")

    def test_returns_context_manager_each_time(self):
        """每次调用必须返回**新的** contextmanager。

        负向验证里「缓存单例」注入被抓住（RED）—— 但有两点须如实记录：
        ① 我第一版注入写成了无效语法，被判 BROKEN（注入后模块无法导入，
           没有测试失败汇总）；改用合法注入后才翻红；
        ② 本条**不是**可重入性的主证据 —— 旧的手写工厂同样返回新对象、
           也会通过本条。真正钉住可重入性的是
           `test_nested_acquisition_does_not_deadlock` 与
           `DeadlockRegressionTest`（子进程超时证据）。
        """
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "g.json"
            cm1, cm2 = local_file_lock(t), local_file_lock(t)
            self.assertIsNot(cm1, cm2)


class DeadlockRegressionTest(unittest.TestCase):
    """⚠️ 把"旧实现会挂死 / 新实现不会"钉成可执行证据。"""

    OLD_IMPL = '''
import fcntl, os, tempfile, sys, pathlib
from contextlib import contextmanager

def make_old(path):
    @contextmanager
    def _local_lock():
        lock_path = path.with_name('.' + path.name + '.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try: fcntl.flock(fd, fcntl.LOCK_UN)
            finally: os.close(fd)
    return _local_lock

d = tempfile.mkdtemp(); t = pathlib.Path(d) / 'x.json'
with make_old(t)():
    with make_old(t)():
        print('REACHED')
'''

    def test_new_shared_implementation_does_not_hang(self):
        # 第七十八刀：以 spawn 为被测行为，离线守护下如实 skip（守卫在 spawn 前）。
        from tests.config_sandbox import skip_if_offline_suite
        skip_if_offline_suite(self)
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import tempfile, pathlib\n"
            "from local_lock import local_file_lock\n"
            "d = tempfile.mkdtemp(); t = pathlib.Path(d) / 'x.json'\n"
            "with local_file_lock(t):\n"
            "    with local_file_lock(t):\n"
            "        print('REACHED')\n" % str(SCRIPTS)
        )
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=15, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("REACHED", r.stdout)

    def test_old_hang_is_timeout_not_exception(self):
        """把「挂死」与「抛异常」区分开 —— 两者对运维是不同故障。

        旧实现在嵌套时是**阻塞**（不抛异常、不打日志、进程不退出），
        这正是它危险的原因：没有任何错误信号。
        """
        # 第七十八刀：以 spawn 为被测行为，离线守护下如实 skip（守卫在 spawn 前）。
        from tests.config_sandbox import skip_if_offline_suite
        skip_if_offline_suite(self)
        try:
            subprocess.run([sys.executable, "-c", self.OLD_IMPL],
                           capture_output=True, text=True, timeout=8, cwd=str(ROOT))
        except subprocess.TimeoutExpired:
            # 第二百零三刀：原为 `assertTrue(True, "如期超时（阻塞）")` —— 恒真，等于没断言。
            # 真正的断言是**控制流**：走到这个 except 就说明旧实现如期阻塞（超时）了。
            return
        self.fail("旧实现没有阻塞")


class FacadeWiringTest(unittest.TestCase):
    def test_both_facades_use_shared_helper(self):
        for path in FACADES:
            src = path.read_text(encoding="utf-8")
            self.assertIn("from local_lock import local_file_lock", src,
                          f"{path.name} 未使用共享兜底")
            tree = ast.parse(src)
            for n in ast.walk(tree):
                if isinstance(n, ast.FunctionDef) and n.name == "_local_lock":
                    self.fail(f"{path.name} 仍内联着手写 _local_lock")

    def test_both_facades_still_prefer_backend_lock(self):
        """⚠️ 后端锁仍是首选 —— 本刀只替换兜底，不改变优选顺序。"""
        for path in FACADES:
            src = path.read_text(encoding="utf-8")
            self.assertIn("from r20_backend.file_locks import file_lock", src)
            self.assertIn("except Exception:", src)

    def test_both_facades_fall_back_reentrantly(self):
        """强制走兜底分支，验证两个门面的锁可嵌套。

        ⚠️⚠️ **必须在子进程 + 临时数据目录里跑**。第四十八刀我在临时验证里
        直接调了 `mutate_instruments` 并把它写到了**真实**
        `data/instrument_pool.json`（写进 `[{'name':'BTC'},{'name':'ETH'}]`）——
        实盘池被写坏，20:30 周期起 fail-safe「标的池不可信，禁止开新仓」。

        `tests/config_sandbox.isolate_config` 只重定向**大写路径常量**，
        对"调用时传入的函数/参数覆盖"无效，所以那次没被拦下。
        故这里用子进程 + `TempDirectory`，并把 `LIBRARY_FILE`/`POOL_FILE`
        一并指向临时目录 —— **绝不触碰真实 `data/`**。
        """
        # 第七十八刀：以 spawn 为被测行为，离线守护下如实 skip（守卫在 spawn 前）。
        from tests.config_sandbox import skip_if_offline_suite
        skip_if_offline_suite(self)
        probe = """
import sys, tempfile, pathlib
sys.path.insert(0, %r)
sys.path.insert(0, %r)
sys.modules['r20_backend.file_locks'] = None      # 逼出 ImportError → 兜底分支

d = pathlib.Path(tempfile.mkdtemp(prefix='r20-lock-probe-'))
import prompt_library, instrument_pool
for mod, const in ((prompt_library, 'LIBRARY_FILE'), (instrument_pool, 'POOL_FILE')):
    object.__setattr__(mod, const, d / (const.lower() + '.json'))
    lockfn = getattr(mod, '_library_lock', None) or getattr(mod, '_pool_lock')
    with lockfn():
        with lockfn():
            pass
print('FALLBACK-REENTRANT-OK')
assert (d / '.library_file.json.lock').exists() or (d / '.pool_file.json.lock').exists()
""" % (str(SCRIPTS), str(ROOT))
        r = subprocess.run([sys.executable, "-c", probe],
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("FALLBACK-REENTRANT-OK", r.stdout)

    def test_probe_would_have_caught_the_incident(self):
        """回归：确认"兜底分支"确实被逼出来了（否则上面的用例是空跑）。

        做法：同一探针里断言 `local_file_lock` 被真的调到 ——
        通过检查锁文件是否落在临时目录（后端锁不会创建它）。
        """
        # 第七十八刀：以 spawn 为被测行为，离线守护下如实 skip（守卫在 spawn 前）。
        from tests.config_sandbox import skip_if_offline_suite
        skip_if_offline_suite(self)
        probe = """
import sys, tempfile, pathlib
sys.path.insert(0, %r)
sys.path.insert(0, %r)
sys.modules['r20_backend.file_locks'] = None
d = pathlib.Path(tempfile.mkdtemp(prefix='r20-lock-probe2-'))
import instrument_pool
instrument_pool.POOL_FILE = d / 'pool.json'
with instrument_pool._pool_lock():
    pass
created = sorted(p.name for p in d.iterdir())
print('CREATED=' + ','.join(created))
assert created == ['.pool.json.lock'], created
""" % (str(SCRIPTS), str(ROOT))
        r = subprocess.run([sys.executable, "-c", probe],
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("CREATED=.pool.json.lock", r.stdout)

    def test_dual_import_under_both_path_layouts(self):
        """⚠️ `local_lock` 的导入必须能在**两种**布局下成功。

        两个门面既可能以**裸名**导入（`scripts/` 在 `sys.path`，
        此时 `from local_lock import ...` 生效），也可能以
        **包名** `scripts.xxx` 导入（repo 根在 `sys.path`，
        此时改成 `from scripts.local_lock import ...`）。
        单写一种就会在另一种布局下 `ModuleNotFoundError`。

        ⚠️ 我第一版只写了裸名导入，被
        `tests/test_dashboard_bills_extraction` 当场抓成
        `ModuleNotFoundError: No module named 'local_lock'`（3 例翻红）。
        本用例在**子进程**里分别验证两种布局。
        """
        # 第七十八刀：以 spawn 为被测行为，离线守护下如实 skip（守卫在 spawn 前）。
        from tests.config_sandbox import skip_if_offline_suite
        skip_if_offline_suite(self)
        bare = ("import sys\n"
                "sys.path.insert(0, %r)\n"
                "import prompt_library, instrument_pool\n"
                "print('OK')\n" % str(SCRIPTS))
        pkg = ("import sys\n"
               "sys.path.insert(0, %r)\n"
               "import scripts.prompt_library, scripts.instrument_pool\n"
               "print('OK')\n" % str(ROOT))
        for label, code in (("bare", bare), ("pkg", pkg)):
            r = subprocess.run([sys.executable, "-c", code],
                               capture_output=True, text=True, timeout=60, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0,
                             f"布局 {label} 失败:\n{r.stderr[-1200:]}")
            self.assertIn("OK", r.stdout, f"布局 {label}")

    def test_facades_use_try_except_dual_import(self):
        for path in FACADES:
            src = path.read_text(encoding="utf-8")
            self.assertIn("from scripts.local_lock import local_file_lock", src,
                          f"{path.name} 缺少包模式导入分支")
            self.assertIn("from local_lock import local_file_lock", src,
                          f"{path.name} 缺少裸名导入分支")
            self.assertIn("except ImportError:", src)

    def test_new_module_uses_only_stdlib(self):
        """⚠️ 兜底模块必须只依赖标准库 —— 否则它无法在
        「后端不在 sys.path」时被导入，兜底就没意义了。"""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imported |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                imported.add((n.module or "").split(".")[0])
        for banned in ("r20_backend", "scripts"):
            self.assertNotIn(banned, imported, f"兜底模块不得 import {banned}")
        self.assertTrue(imported <= {"fcntl", "os", "threading", "contextlib",
                                     "pathlib", "typing", "__future__", "ast",
                                     "subprocess", "sys", "tempfile", "unittest"},
                        f"出现非标准库依赖: {imported}")

    def test_new_module_has_no_module_level_side_effects(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        bare = [n for n in tree.body
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)]
        self.assertEqual(bare, [], "模块层不应有裸调用")


class BackendParityTest(unittest.TestCase):
    """⚠️ 兜底语义必须与后端一致（调用方在两者间无条件切换）。"""

    def test_lock_path_formula_matches_backend(self):
        from r20_backend.file_locks import _lock_path as backend_lock_path
        from local_lock import _lock_path as local_lock_path
        for name in ("a.json", "instrument_pool.json", "no-ext"):
            t = Path("/tmp/dir") / name
            self.assertEqual(local_lock_path(t), backend_lock_path(t)[1],
                             f"{name} 的锁文件路径公式与后端不一致")

    def test_reentrancy_semantics_match_backend(self):
        """两者都必须在嵌套时「内层退出不释放外层」。"""
        from r20_backend.file_locks import file_lock, lock_is_held as backend_held
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "p.json"
            with file_lock(t):
                with file_lock(t):
                    pass
                self.assertTrue(backend_held(t), "后端语义：内层退出仍持锁")
            with local_file_lock(t):
                with local_file_lock(t):
                    pass
                self.assertTrue(lock_is_held(t), "兜底语义：内层退出仍持锁")


if __name__ == "__main__":
    unittest.main()
