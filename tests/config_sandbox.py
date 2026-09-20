"""Per-test configuration sandbox: patch source constants AND imported path aliases."""
import importlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def skip_if_offline_suite(test, reason='本用例以 spawn 子进程/网络栈为**被测行为**，'
                                       '离线守护下无法验证（skip ≠ fail，如实反映环境能力）'):
    """离线套件（`OFFLINE_SUITE_RUNNING`）下跳过"以 spawn 为被测对象"的用例。

    第六十一刀立规矩（守卫必须在 spawn **之前**）、第七十八刀推广成共享 helper：
    护栏挡子进程是**本职**，这类用例与护栏天然冲突 —— 在离线环境它们
    "测不了"而非"测不过"，如实 skip 才不污染守护基线。
    """
    if os.environ.get("OFFLINE_SUITE_RUNNING"):
        test.skipTest(reason)


def isolate_config(test):
    temp = tempfile.TemporaryDirectory(prefix='r20-test-config-')
    test.addCleanup(temp.cleanup)
    root = Path(temp.name)
    project = Path(__file__).resolve().parents[1]
    # ⚠️ 第七十六刀：让**子进程**也被沙箱接管。
    # `run_script`（r20_backend/spawn.py）不传 env → 子进程继承父进程 os.environ。
    # 设置 R20_DATA_DIR 后，尊重它的脚本（factor_library / news_sentiment_harvester /
    # sync_full_ledger，均实测为被测试拉起的 data/ 写入者）把写入指向沙箱。
    # **生产从不设置该变量** ⇒ 行为逐位不变（见各脚本注释）。
    # 修复的是 §88/§91.6 登记的"测试经后台子进程写生产文件"泄漏。
    import os as _os
    _env_key = "R20_DATA_DIR"
    _prev = _os.environ.get(_env_key)
    _os.environ[_env_key] = str(root / "data")

    def _restore_env():
        if _prev is None:
            _os.environ.pop(_env_key, None)
        else:
            _os.environ[_env_key] = _prev
    test.addCleanup(_restore_env)
    # ⚠️⚠️ 第八十刀（顺序即 bug）：必须发生在**下面的白名单 import 之前** ——
    # `r20_backend/dashboard_cache.py` 模块**顶层末尾**就有 `start_dashboard_background_worker()`
    # （L549，实测），于是"import r20_backend.dashboard_cache"这个动作本身就点起
    # **每 2 秒跑一次 `update_cache_cycle()` 的 daemon worker**：
    #   · 非离线：worker 在**任何测试的 patch 窗口之外**真外呼
    #     www.okx.com（balances/positions/pending_orders ×每 2s）——
    #     这是 11+ 个路由测试文件"按文件扫全泄漏"的共同源头，
    #     连不碰 dashboard 的用例（如 test_config_sandbox）都被波及；
    #   · 离线：socket 守护把它拦成 fail-soft ⇒ 多年无人察觉。
    # 压制 `_fetch_json` 只盖住 patch 存活的窗口；**根治 = 关掉 worker 循环**
    # （`_BG_WORKER_RUNNING` 每轮检查，stop 后 ≤2s 线程自然退出）。
    # 生产不受影响：web 进程经 r20_backend/app.py 的 lifespan 启动它，
    # 且测试进程里这个 worker 从来不是被测对象。
    # 要真测 fetch 的文件自己再 patch.object 覆盖（mock 栈 LIFO，后装优先）。
    try:
        import r20_backend.dashboard_cache as _dash_mod
    except Exception:
        _dash_mod = None
    if _dash_mod is not None:
        try:
            _dash_mod.stop_dashboard_background_worker()
        except Exception:
            pass
        if callable(getattr(_dash_mod, "_fetch_json", None)):
            _p_fetch = patch.object(
                _dash_mod, "_fetch_json",
                lambda *a, **k: (False, None, "tests 沙箱已压制出站取数（isolate_config）"))
            _p_fetch.start(); test.addCleanup(_p_fetch.stop)
    for name in ('r20_backend.llm_manager', 'r20_backend.council_manager',
                 'r20_backend.policy_snapshot', 'r20_backend.interceptor_manager',
                 'scripts.prompt_library', 'scripts.evolution_shield',
                 'r20_gateway.secrets',
                 # `r20_backend.dashboard_cache` 的一批大写路径常量（DASHBOARD_CACHE_FILE、
                 # LOG_FILE、STATE_JSON_FILE、LEDGER_JSON_FILE…）此前**不在任何
                 # 白名单里**，于是直调 `update_cache_cycle()` 的测试会写生产
                 # `data/dashboard_last_good.json`（实测有告警但无人处理）。
                 # 它内部会调 `load_persisted_dashboard_cache()`，但那只是读一个
                 # JSON，且所有跑过仪表盘的测试本来就会 import 它。
                 'r20_backend.dashboard_cache',
                 # ---- 第七十三刀补：下面 15 个模块用内联 `ROOT / "data" / …`
                 # 拼生产路径。**沙箱只 patch 已 import 模块的大写常量**，
                 # 所以"模块不在这个白名单里"就等于"它的路径常量不受管辖"
                 # —— 无论写法多规范都一样漏（实测 `scripts/instrument_pool.py`
                 # 的 `TRADING_STATE_FILE` 提成模块级常量后，
                 # 不 import 它依然不被重定向）。
                 #
                 # 逐个确认过：15 个都能在**零副作用**下 import
                 # （无网络、无起进程、无端口绑定），与既有白名单同性质。
                 # 对应回归测试：`tests/audit/test_production_data_isolation.py`。
                 # ---- 第一百一十四刀补：风控预留库（`data/risk_reservation.db`）。
                 # 实测两处测试**只夹带渲染仪表盘**就经 `dashboard_payload.market`
                 # 的 `get_manager()` 连到生产预留库（`RiskReservationManager.__init__`
                 # 会建表）——而本模块此前既不在白名单、常量又是惰性 import 后才求值，
                 # 于是永远没人重定向它。白名单 import 保证"先 import 后重定向"的顺序。
                 'r20_backend.risk_reservation',
                 'r20_backend.account_baseline',
                 'r20_backend.admin_auth',
                 'r20_backend.backup_secrets',
                 'r20_backend.backup_store',
                 'r20_backend.exchanges.env_profiles',
                 'r20_backend.exchanges.routing_policy',
                 'r20_backend.qq_gateway_daemon',
                 'r20_backend.routers.dashboard',
                 'r20_backend.routers.strategy',
                 'r20_backend.schedule_store',
                 'scripts.archive_ledger',
                 'r20_gateway.agents',
                 'r20_gateway.publisher',
                 'r20_gateway.supervisor',
                 'r20_gateway.worker'):
        importlib.import_module(name)
    # Patch every already-bound alias, not just the defining module (law 2).
    # 白名单必须覆盖**顶层名**形式的兄弟模块：`scripts/` 在 sys.path 上，脚本以
    # `import ai_brain_trader` 引入的是与 `scripts.ai_brain_trader` 不同的模块实例，
    # 不在白名单里就完全不被重定向 → 测试会写生产 data/（如 ai_brain_last_prompt.txt、
    # system_prompt_override.txt）。批2 P1-3 回归测试就是被这条断言抓出来的。
    for name, module in list(sys.modules.items()):
        if not module or name.startswith('tests'):
            continue
        if not (name.startswith(('r20_backend.', 'r20_gateway.', 'scripts.',
                                 'dashboard.')) or
                name in ('prompt_library', 'evolution_shield', 'ai_brain_trader', 'ai_factor_trader')):
            continue
        for key, value in list(vars(module).items()):
            if not key.isupper() or not isinstance(value, (str, Path)):
                continue
            try:
                relative = Path(value).relative_to(project / 'data')
            except ValueError:
                continue
            target = root / 'data' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            replacement = str(target) if isinstance(value, str) else target
            p = patch.object(module, key, replacement)
            p.start(); test.addCleanup(p.stop)
    app = sys.modules.get("r20_backend.app")
    if app is not None:
        git_probe = patch.object(app, "git", side_effect=lambda args: (
            "0 0" if args[0] == "rev-list" else "test" if args[0] == "branch" else
            "" if args[0] in ("fetch", "status") else "abc1234"))
        git_probe.start(); test.addCleanup(git_probe.stop)
    # ⚠️ 只重定向常量还不够：`risk_reservation.get_manager()` 缓存了进程级实例，
    # 一旦某次未沙箱调用先建了实例，它会**永久指向生产库**（实测污染源）。
    # 清缓存才能让新常量真正生效。
    try:
        import r20_backend.risk_reservation as _rr
        _rr.reset_default_manager()
    except Exception:
        pass

    return root
