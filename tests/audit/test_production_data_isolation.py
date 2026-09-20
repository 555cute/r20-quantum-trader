r"""生产 `data/` 文件在跑测试时**不被写**（结构优化阶段 4·B3 第七十三～七十五刀）。

## 这个测试在防什么（第七十二刀实测到的真事故）

第七十二刀给离线套件做基线标定时，用**对照实验**证实：
跑测试套件会覆盖生产 `data/` 里的交易状态文件。对照窗口 02:18:02 → 02:20:01
内**无任何 trader 周期**（周期在 :00/:15/:30/:45，日志亦无记录）：

| 文件 | 运行前 | 运行后 |
|---|---|---|
| `data/llm_models.json` | `bc8b1cf2…` | 未变 |
| `data/trading_state.json` | `e44a6e6c…` | **变了**（mtime 02:18:14） |
| `data/trading_ledger.json` | `e8ebb421…` | **变了**（mtime 02:19:29） |

**根因**（`scripts/instrument_pool.py`）：写入函数
`sync_instruments_state()` 里是**函数局部**拼接 ——

```python
state_file = ROOT / "data" / "trading_state.json"   # 局部变量
```

`tests/config_sandbox.isolate_config` 的重定向只对**模块级 UPPERCASE 常量**生效
（它遍历 `vars(module)`，看不见函数局部名；`ROOT` 指向仓库根，也不满足
"值在 `project/data` 之下"），于是这条路径**永远逃过沙箱**。
与第四十八刀那次事故（池文件被写成缺字段、实盘周期 fail-safe）**同一机制**。

## ⚠️ 为什么判据是"行为"而不是"静态形状"（第七十五刀的教训）

第七十三刀我先写了静态判据（正则/AST 找 `ROOT / "data"` 内联拼接），结果**连错两次**：

1. **过宽**：把 17 个**模块级** `X = ROOT / "data" / …` 报成违规 ——
   它们其实能被沙箱重定向（是大写常量且值在 `data/` 下）；
2. **判据方向错**：改判"函数内拼接=违规"并把 6 处改成模块级 `DATA_DIR` 后，
   **反而弄坏 5 个既有测试** —— `dashboard` / `strategy` / `backup_runtime` /
   `agents` / `worker` 的测试用
   `patch.object(module, "ROOT", tmp)` 隔离，函数内调用期拼 `ROOT / "data"`
   **正是它们能隔离的原因**；提成 import 期定值的 `DATA_DIR` 后 patch 失效。

⇒ **两种隔离机制（isolate_config 重定向常量 / 测试手工 patch ROOT）互相矛盾，
任何"静态形状"判据都表达不了"这条路径会不会写生产"。**
只有**行为断言**可靠：真调那个写入函数（在沙箱下），断言生产文件哈希不变。

这正是 §88.5 建议的修法（"断言跑 `sync_instruments_state()` 后生产文件 sha256 不变"）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _hash_or_absent(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        return "<absent>"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _guard_offline() -> None:
    """离线套件（装了 audit hook）下跳过 —— **必须在 spawn 之前调用**。

    与第六十一刀给 node 套件补的是同一道守卫：`subprocess` 直调会被
    audit hook 拦成 `external child process` 错，污染离线基线。
    """
    import os
    if os.environ.get("OFFLINE_SUITE_RUNNING"):
        raise unittest.SkipTest("离线套件下不 spawn 子进程（守卫在 spawn 之前）")


class SubprocessDataWritesRedirectedTest(unittest.TestCase):
    """⚠️ 第七十六刀：堵住 §91.6 登记的**后台子进程泄漏**。

    `sync_instruments_state()` 第 5 步以子进程拉起
    `factor_library.py` / `news_sentiment_harvester.py`（另有测试链路拉起
    `sync_full_ledger.py`）—— 子进程是**新解释器**，在进程沙箱
    （patch 模块常量）对它完全无效，它自己从真实 ROOT 拼路径 → 写生产。

    修法：`isolate_config` 设 `R20_DATA_DIR` 环境变量（子进程经
    `run_script` 继承），三个脚本的 `DATA_DIR` 改为
    `os.environ.get("R20_DATA_DIR") or os.path.join(WORKSPACE_DIR, "data")`。
    **生产从不设置该变量 ⇒ 行为逐位不变。**
    """

    #: 三个"会被测试经子进程拉起、且写 data/"的生产脚本
    SUBPROCESS_WRITERS = (
        "scripts/factor_library.py",
        "scripts/news_sentiment_harvester.py",
        "scripts/sync_full_ledger.py",
    )

    def test_all_writer_scripts_honor_the_env(self):
        """三个脚本必须都走"env 优先"的同款推导（静态逐个查）。"""
        for rel in self.SUBPROCESS_WRITERS:
            src = (ROOT / rel).read_text(encoding="utf-8")
            with self.subTest(script=rel):
                self.assertIn(
                    'DATA_DIR = os.environ.get("R20_DATA_DIR") or', src,
                    f"{rel} 不再尊重 R20_DATA_DIR —— 子进程写生产泄漏会复发")

    def test_fresh_interpreter_resolves_data_dir_into_sandbox(self):
        """端到端：真的**新起解释器** import factor_library，
        断言它的 DATA_DIR 落在沙箱 —— 验证整条 env 传递链
        （isolate_config → os.environ → subprocess 继承 → 脚本读取）。
        """
        import os
        import subprocess

        _guard_offline()   # 必须先于任何 spawn（含 spawn=False 的下方早退）

        from tests import config_sandbox

        config_sandbox.isolate_config(self)   # addCleanup 自动还原
        expected = os.environ.get("R20_DATA_DIR")
        self.assertTrue(expected, "isolate_config 未设置 R20_DATA_DIR")

        probe = (
            "import sys; sys.path.insert(0, 'scripts');"
            "import factor_library; print(factor_library.DATA_DIR)"
        )
        cp = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True,
                            cwd=str(ROOT), timeout=90)
        self.assertEqual(cp.returncode, 0,
                         f"子进程探针失败：{cp.stderr[-300:]}")
        self.assertEqual(cp.stdout.strip(), expected,
                         "子进程解析出的 DATA_DIR 未指向沙箱 —— "
                         "env 传递链断了（泄漏仍在）")

    def test_env_absent_outside_isolation(self):
        """⚠️ 边界钉：不进沙箱时 `R20_DATA_DIR` **必须不存在**
        —— 否则"生产从不设置该变量"的前提被破坏，脚本行为就不再等价。
        （也验证 isolate_config 的 cleanup 真的还原了。）
        """
        import os

        self.assertNotIn("R20_DATA_DIR", os.environ,
                         "沙箱之外不该有 R20_DATA_DIR（cleanup 漏了或环境脏了）")

        from tests import config_sandbox

        class _Host:
            def __init__(self):
                self.cleaners = []

            def addCleanup(self, fn):
                self.cleaners.append(fn)

        host = _Host()
        try:
            config_sandbox.isolate_config(host)
            self.assertIn("R20_DATA_DIR", os.environ, "沙箱内应设置该 env")
        finally:
            for fn in reversed(host.cleaners):
                fn()
        self.assertNotIn("R20_DATA_DIR", os.environ,
                         "还原后 R20_DATA_DIR 必须消失（与进入前一致）")

    def test_bg_thread_spawn_uses_pre_thread_env_snapshot(self):
        """⚠️ 竞态钉（第七十六刀根因的另一半）：
        后台线程 spawn 子进程时**必须用线程创建前的环境快照**。

        实测事故形状：线程还没跑到 `subprocess.run`，测试已结束、
        `isolate_config` 的 cleanup 已还原 `R20_DATA_DIR` ——
        若靠"继承"，子进程拿到的是**干净环境** ⇒ 写生产
        （03:22:41 / 03:23:11 的 mtime 就是这条竞态留下的）。

        本用例把 `run_script` 换成"记录 env 的假 spawn"，在**沙箱内**调
        `sync_instruments_state()`，然后**还原沙箱**、再等线程真正走到
        spawn —— 断言它拿到的 env **仍含沙箱 `R20_DATA_DIR`**（快照生效）。
        未修复前：还原后 env 里没有 R20_DATA_DIR → 翻红。
        """
        import threading

        from tests import config_sandbox
        import scripts.instrument_pool as pool
        from r20_backend import spawn as spawn_mod

        seen: list[dict] = []
        real_run_script = spawn_mod.run_script
        _done = threading.Event()
        #: 期望的 spawn 次数**按实际存在的脚本算**（别写死 2：脚本缺席时用例会假红）。
        _expected = sum(1 for _s in (ROOT / "scripts" / "factor_library.py",
                                     ROOT / "scripts" / "news_sentiment_harvester.py")
                        if _s.exists())

        def _fake_run_script(script, *, timeout=20, label=None, env=None):
            # 记录**实际传给子进程的 env**（None = 继承 = 竞态未修）
            seen.append(dict(env) if env is not None else None)
            if len(seen) >= _expected:
                _done.set()

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""
            return _R()

        sandbox = config_sandbox.isolate_config(self)
        p = patch.object(spawn_mod, "run_script", _fake_run_script)
        p.start()
        try:
            pool.sync_instruments_state()
            # ⚠️ 必须在**撤 patch 之前**等后台线程把 spawn 都走完 ——
            # 否则测试自己会漏出一次**真 spawn**（假想 cleanup 时序反而制造事故）。
            #
            # ⚠️ 第一百二十一刀去 flaky：原来等的是**写死 5 秒**。本用例空载、
            # 乃至 8 路 CPU 争用下都复现不了；只在**整包跑**里偶发（已两次：
            # 第 20 刀、第 25 刀），而本机同时跑着活体 `r20_gateway.worker` ——
            # 5 秒窗口被它抢走即可假红。现改成**事件驱动**（线程走完即返回），
            # 30 秒只是安全网：真超时才说明"结构变了或卡死"。
            _done.wait(30.0)
        finally:
            p.stop()
            self.doCleanups()          # 提前还原 env 与常量（幂等）

        self.assertGreaterEqual(_expected, 1, "两个脚本都不在？本用例已失去意义")
        self.assertEqual(len(seen), _expected,
                         f"后台线程没走完 spawn（只见 {len(seen)}/{_expected} 次，等满 30s）"
                         " —— 结构变了或卡死，本用例失去意义")
        bad = [i for i, env in enumerate(seen) if env is None
               or env.get("R20_DATA_DIR") != str(Path(sandbox) / "data")]
        self.assertEqual(
            bad, [],
            f"后台线程 spawn 用的不是沙箱快照（竞态复发）："
            f"{[seen[i] is None and '继承(竞态)' or 'env 缺 R20_DATA_DIR' for i in bad]}"
            " —— 修见 sync_instruments_state 的 _env_snapshot。")


class SyncInstrumentsStateNeverWritesProductionTest(unittest.TestCase):
    """⚠️ 核心回归：真调 `sync_instruments_state()`（沙箱下），
    断言写集**落在沙箱**且生产 `trading_state.json` 分毫不动。

    这是行为判据 —— 若有人把 `instrument_pool` 的模块级常量
    改回函数局部拼接，沙箱重定向失效，生产哈希会变，本用例当场翻红。

    ⚠️ 判据范围在第七十六刀**修正过一次错误**：
    第一版把 sync 的 4 个写集文件全做"生产哈希不变"断言，
    但 `factor_library_snapshot` / `dashboard_last_good` **每 ~60s 被活体
    `r20_gateway.worker` 重写**（实测 `-newermt '-3 minutes'` 命中）——
    落在我的 before→after 窗口里就会**误报成泄漏**（潜伏的 flaky，
    本轮 8 连跑没撞上纯属窗口窄）。⇒ 生产哈希断言**只保留
    `trading_state.json`**（活体 worker 不碰它，只有 15 分钟周期与 sync 写）；
    其余文件靠**正向证据**（沙箱里出现了本次写入）—— 写只有一个目标，
    落在沙箱就**在定义上**不可能同时写生产，与活体噪声无关。
    """

    def test_production_files_untouched(self):
        from tests import config_sandbox
        import scripts.instrument_pool as pool

        # 可归因的生产哈希对照：只挑活体 worker 不写的文件
        hash_guarded = ("data/trading_state.json",)
        before = {rel: _hash_or_absent(rel) for rel in hash_guarded}

        sandbox = config_sandbox.isolate_config(self)   # addCleanup 自动还原

        # 先确认沙箱确实接管了这几个常量（否则"没写到生产"只是因为根本没跑到写）
        for name in ("TRADING_STATE_FILE", "FACTOR_LIBRARY_FILE",
                     "NEWS_SENTIMENT_FILE", "DASHBOARD_CACHE_FILE"):
            value = str(getattr(pool, name, ""))
            self.assertTrue(
                value.startswith(str(sandbox)),
                f"{name} 未被重定向（实际 {value}）—— 沙箱已失效，本测试失去意义")

        # 屏蔽第 5 步后台子进程（factor_library.py / news_sentiment_harvester.py）
        # —— 本用例只测**本进程内**第 1-4 步的模块常量写入；
        # 子进程那条链由 SubprocessDataWritesRedirectedTest 单独端到端钉。
        p = patch.object(pool, "_run_captured", lambda *a, **k: None)
        p.start(); self.addCleanup(p.stop)

        # 真跑一遍写入函数（它读沙箱池、写沙箱文件）
        pool.sync_instruments_state()

        # 正向证据：本次写入**确实发生**且**落在沙箱**（写只有一个目标 ⇒
        # 生产不可能同时被写）。若常量退回函数局部，这里先红。
        sandbox_state = sandbox / "data" / "trading_state.json"
        self.assertTrue(
            sandbox_state.is_file(),
            "sync 没把 trading_state 写进沙箱 —— 写入路径没走模块常量？")
        payload = json.loads(sandbox_state.read_text(encoding="utf-8"))
        self.assertIn("instruments", payload, "沙箱里的 trading_state 形状不对")

        after = {rel: _hash_or_absent(rel) for rel in hash_guarded}
        changed = [rel for rel in hash_guarded if before[rel] != after[rel]]
        self.assertEqual(
            changed, [],
            "跑 sync_instruments_state() 覆盖了生产 " + ", ".join(changed)
            + " —— 第七十二刀实测事故复发。修法：把这些路径保持为"
              "**模块级常量**（isolate_config 才能重定向），"
              "见 scripts/instrument_pool.py 的 DATA_DIR 注释。")

    def test_all_four_paths_are_module_level_constants(self):
        """结构前提：四个写集路径必须是**模块级**常量。

        （函数局部名 `vars(module)` 看不见，沙箱就无法重定向。）
        """
        src = (ROOT / "scripts" / "instrument_pool.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        top_level = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        top_level.add(t.id)
        for name in ("DATA_DIR", "POOL_FILE", "TRADING_STATE_FILE",
                     "FACTOR_LIBRARY_FILE", "NEWS_SENTIMENT_FILE",
                     "DASHBOARD_CACHE_FILE"):
            with self.subTest(const=name):
                self.assertIn(name, top_level,
                              f"{name} 必须是**模块级**常量，否则沙箱看不到它")

    def test_old_local_names_do_not_resurrect(self):
        """第四十八/七十二刀的两个旧局部名不得复活。

        只在**代码**里查（`_strip` 掉注释与 docstring）——
        本模块与 instrument_pool 的文档里正解释着这些旧名字，
        不排除会把"解释问题的文字"当成"问题本身"（假红，§82.3 / §80.3 反复踩的坑）。
        """
        src = (ROOT / "scripts" / "instrument_pool.py").read_text(encoding="utf-8")
        code = _strip_docstrings_and_comments(src)
        for gone in ("state_file", "factor_file", "news_file", "dashboard_cache"):
            with self.subTest(gone=gone):
                self.assertNotIn(
                    gone, code,
                    f"{gone} 曾是函数内局部变量（沙箱拦不住），不应复活")

    def test_isolate_config_redirects_module_level_data_paths(self):
        """⚠️ 顺带守护机制本身：isolate_config 会重定向**已导入**模块里
        值在 `project/data` 之下的大写常量。

        这是 instrument_pool 修复生效的**前提**；若 config_sandbox 被改成
        不再遍历 `scripts.` 前缀，本用例先红，避免"修复悄悄失效"。
        """
        from tests import config_sandbox
        import scripts.instrument_pool  # 确保已进 sys.modules

        sandbox = config_sandbox.isolate_config(self)
        module = sys.modules["scripts.instrument_pool"]
        redirected = [k for k, v in vars(module).items()
                      if k.isupper() and isinstance(v, (str, Path))
                      and str(v).startswith(str(sandbox))]
        self.assertIn("TRADING_STATE_FILE", redirected,
                      "isolate_config 不再重定向 instrument_pool 的写集常量")


def _strip_docstrings_and_comments(src: str) -> str:
    """去掉 docstring 与 `#` 注释（保留换行以对齐行号）。

    用**字符扫描**而非正则剥离，避免把字符串字面量里的 `#` 误判成注释。
    """
    out, i, n = [], 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if src.startswith(('"""', "'''"), i):
            quote = src[i:i + 3]
            i += 3
            while i < n and not src.startswith(quote, i):
                out.append("\n" if src[i] == "\n" else " ")
                i += 1
            i += 3
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class ProductionDbConnectBlockedTest(unittest.TestCase):
    """第一百一十四刀：**测试连接生产 sqlite 库**必须失败（新增的一类泄漏）。

    ## 为什么单开一类

    上面那套 `_assert_not_production` 管 `open()/replace/unlink` 等**文件级**写；
    sqlite 库的连接走 `sqlite3.connect`（文件只开一次，之后全在 fd 上写）——
    完全绕开那套闸。实测一次全量 `pytest tests` 会对生产 `data/*.db` 发起
    **18 次连接**（admin 6 / quant 4 / reservation 6 / gateway 2），并且**真的**
    在生产 `data/risk_reservation.db` 里留下一行
    `environment=<MagicMock name='current_environment().mode'>` 的垃圾预留
    （id=225，created_at 2026-09-20 04:59:03）。
    """

    def test_connect_to_production_db_is_blocked(self):
        import sqlite3
        target = ROOT / "data" / "risk_reservation.db"
        with self.assertRaises(AssertionError) as ctx:
            sqlite3.connect(str(target))
        self.assertIn("禁止连接生产数据库", str(ctx.exception))

    def test_memory_and_temp_connections_still_work(self):
        """闸门只管生产目录——`:memory:` 与临时文件必须照常可用（否则测试没法活）。"""
        import sqlite3
        import tempfile
        with sqlite3.connect(":memory:") as c:
            c.execute("SELECT 1")
        with tempfile.TemporaryDirectory() as d:
            with sqlite3.connect(str(Path(d) / "x.db")) as c:
                c.execute("CREATE TABLE t(a)")

    def test_all_four_production_dbs_are_redirected_for_the_session(self):
        """会话级默认重定向：四个库都指向临时目录，且不在生产 data/ 之下。"""
        from r20_backend import admin_auth, risk_reservation
        from r20_gateway import publisher
        import scripts.db_manager as db_manager
        prod = (ROOT / "data").resolve()
        for name, value in (("admin_auth.DB_PATH", admin_auth.DB_PATH),
                            ("risk_reservation.DEFAULT_DB_PATH", risk_reservation.DEFAULT_DB_PATH),
                            ("db_manager.DB_PATH", db_manager.DB_PATH),
                            ("publisher.DB_PATH", publisher.DB_PATH)):
            with self.subTest(module=name):
                resolved = Path(value).resolve()
                self.assertFalse(str(resolved).startswith(str(prod) + os.sep),
                                 f"{name} 仍指向生产 data/：{resolved}")

    def test_default_manager_resolves_outside_production(self):
        """`get_manager()` 的**缓存实例**也必须跟着走（常量改了、缓存没清=照样连生产）。"""
        from r20_backend import risk_reservation
        mgr = risk_reservation.get_manager()
        self.assertFalse(str(Path(mgr.db_path).resolve()).startswith(
            str((ROOT / "data").resolve()) + os.sep), f"默认管理器仍钉生产库：{mgr.db_path}")

    def test_dashboard_render_does_not_touch_production_reservation_db(self):
        """行为判据（本文件的一贯做法）：渲染仪表盘 stale 注入后，生产库哈希不变。"""
        before = _hash_or_absent("data/risk_reservation.db")
        import r20_backend.dashboard_cache as dashboard
        dashboard._inject_local_data_into_stale({}, [], "2026-09-02 22:00:00 (北京时间)")
        self.assertEqual(_hash_or_absent("data/risk_reservation.db"), before,
                         "测试渲染仪表盘不得改动生产风控预留库")

    def test_admin_auth_default_arg_reads_module_constant_at_call_time(self):
        """回归：`def __init__(self, path=DB_PATH)` 的**定义期绑定**会让沙箱重定向失效。

        实测那次泄漏就是它：`r20_backend/dependencies.py:30` 在 import 期
        `AdminAuthStore()` 走的是定义期绑定的生产路径，`isolate_config` 改常量无效。
        """
        import tempfile
        from r20_backend import admin_auth
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "admin.db"
            with patch.object(admin_auth, "DB_PATH", tmp):
                store = admin_auth.AdminAuthStore()      # 不传 path ⇒ 必须用**当前**常量
                self.assertEqual(Path(store.path), tmp,
                                 "默认参数仍在定义期绑定 ⇒ 沙箱重定向对 dependencies 无效")
            self.assertTrue(tmp.exists(), "构造 store 应在（临时）路径上建表")


if __name__ == "__main__":
    unittest.main()
