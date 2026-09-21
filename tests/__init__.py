"""测试环境统一隔离。

生产 .env 可能带有用户经后台「风控管理页」应用的套件/自定义覆盖值（R20_* 风控键），
而全部引擎测试的断言基线是代码默认值。本模块在 discover 导入任何测试模块之前：
1) 先正常 import r20_backend.config，完成真实 .env 加载（OKX/LLM/QQ 等配置是测试需要的）；
2) 禁用后续 load_dotenv 回灌（refresh_settings/update_env 每次都会调用它）；
3) 用静态键表从进程环境剥离全部风控覆盖键——必须在 import risk_constants 之前完成，
   因为其常量在 import 时一次性绑定；
4) 再首次导入 risk_constants（此时得到代码默认基线），并断言静态键表与单一事实源一致。
"""
import builtins
import json
import os
import sys
import tempfile
from pathlib import Path

# 审计卫生（2026-09-13 清积压）：audit.py / self_improvement_engine.log_msg 的
# 路径此前是模块级硬编码、不可重定向，走 TestClient 的后台测试把伪造记录直写
# 生产 logs/r20_admin_audit.jsonl（实测 2000+ 条 testclient）与
# self_improvement.log（fake "corrupt" 行）。二者已改为调用时读环境变量；这里
# 在 discover 导入任何测试模块之前把变量指到会话级临时目录，一次性隔离所有
# 此类落盘副作用（律①：测试不触生产文件）。
_TEST_SANDBOX = tempfile.mkdtemp(prefix="r20-tests-")

# ⚠️ 四个生产 sqlite 库的**环境覆盖**必须在这里就设好 —— 这是文件最早的可执行点，
# 早于任何 `r20_backend.*` import（`r20_backend/dependencies.py` 在 import 期就建
# `AdminAuthStore()`）。生产**从不设置**这四个变量 ⇒ 生产行为逐位不变；
# 测试里它们指向会话临时目录，于是"忘加沙箱"的测试也连不到生产库。
# 为什么要用 env 而不是只 patch 常量：`db_manager` 存在**双拼写**两个模块实例
# （`scripts.db_manager` 与顶层 `db_manager`），只 patch 常量会漏掉另一个
# —— 本刀实测正是它漏了 2 次生产连接（`ai_factor_trader` 走 `from db_manager import`）。
os.environ.setdefault("R20_QUANT_DB", os.path.join(_TEST_SANDBOX, "r20_quant.db"))
os.environ.setdefault("R20_RISK_RESERVATION_DB",
                      os.path.join(_TEST_SANDBOX, "risk_reservation.db"))
os.environ.setdefault("R20_ADMIN_DB", os.path.join(_TEST_SANDBOX, "r20_admin.db"))
os.environ.setdefault("R20_GATEWAY_DB", os.path.join(_TEST_SANDBOX, "r20_gateway.db"))

# 会话级沙箱必须在进程退出时清掉。
#
# 2026-09-14 实测：本会话反复运行全量套件后，/tmp（256M tmpfs）里积了 **6000+ 个**
# `r20-tests-*` 空目录，把 /tmp 用到 94%，导致 `No space left on device`，
# 进而让 `test_copytruncate_keeps_inode_for_live_writer` 这类**真的往 /tmp 写文件**
# 的测试假红（它断言的是"轮转成功"，失败信息里才看到 ENOSPC）。
#
# 危害不只是脏：tmpfs 写满会波及同机的其它进程（含网关/交易子进程的临时文件）。
# 只有 import 期的一次性 mkdtemp 是可回收的（各测试自己用 `addCleanup` 管理的
# 临时目录不归这里管），故在此注册退出清理。
def _cleanup_test_sandbox() -> None:
    import shutil
    try:
        shutil.rmtree(_TEST_SANDBOX, ignore_errors=True)
    except Exception:                                  # noqa: BLE001
        pass


import atexit as _atexit

_atexit.register(_cleanup_test_sandbox)

os.environ.setdefault("R20_AUDIT_FILE", os.path.join(_TEST_SANDBOX, "r20_admin_audit.jsonl"))
os.environ.setdefault("R20_SELF_IMPROVEMENT_LOG", os.path.join(_TEST_SANDBOX, "self_improvement.log"))
# 批E(2026-09-13)：仪表盘载荷构建在台账 >60s 未更新时会 spawn 真实台账同步子进程
# （打三所接口 + 重写 data/trading_ledger.json）。仪表盘相关测试走真实 DATA_DIR，
# 于是测试会打真网络并改写生产台账——同款隔离：默认禁用该触发点。生产不设此变量。
os.environ.setdefault("R20_LEDGER_SYNC_DISABLED", "1")

import r20_backend.config as _config

_config.load_dotenv = lambda path: None

_RISK_KEYS_STATIC = (
    "R20_PORTFOLIO_RISK_BUDGET_USDT",
    # 批4 P2-1：跨所同向敞口上限（此前只在 settings_store.MANAGED_KEYS 里，无任何读者）
    "R20_MAX_TOTAL_EXPOSURE_USDT",
    "R20_MAX_CONCURRENT_POSITIONS", "R20_MAX_SAME_DIRECTION_POSITIONS",
    "R20_MAX_MARGIN_EQUITY_RATIO", "R20_SINGLE_ASSET_EQUITY_RATIO",
    "R20_MAX_SINGLE_ASSET_MARGIN_USDT", "R20_MAX_LEVERAGE", "R20_MIN_LEVERAGE",
    "R20_RISK_PER_TRADE_RATIO", "R20_MIN_RISK_REWARD", "R20_MIN_ENTRY_CONFIDENCE",
    "R20_MAX_DAILY_LOSS_USDT", "R20_DAILY_LOSS_EQUITY_RATIO",
    "R20_TIME_STOP_HOURS", "R20_TIME_STOP_ATR_BAND", "R20_STOP_COOLDOWN_MINUTES",
    "R20_MAX_SCALE_IN_COUNT", "R20_MIN_SCALE_IN_PROFIT_RATIO", "R20_MIN_SCALE_IN_CONFIDENCE",
    "R20_SCALE_OUT_ENABLED", "R20_SCALE_OUT_RATIO", "R20_SCALE_OUT_TRIGGER_ATR",
    "R20_MAX_RISK_REWARD", "R20_STOP_LOSS_ATR_MULT", "R20_MAX_TAKE_PROFIT_ATR",
)
for _key in _RISK_KEYS_STATIC:
    os.environ.pop(_key, None)

from scripts.risk_constants import RISK_ENV_KEYS as _RISK_KEYS  # noqa: E402

assert set(_RISK_KEYS) == set(_RISK_KEYS_STATIC), (
    "tests/__init__.py 的静态风控键表与 scripts/risk_constants.RISK_ENV_KEYS 漂移，请同步")

# 律①续（批1 P0-2 配套，2026-09-13）：settings_store.ENV_FILE 默认指向仓库根 .env，
# 而 config_sandbox.isolate_config 只重定向 data/ 下的路径——于是**任何**走
# update_env/remove_env 的测试都会真实改写生产 .env（实测：test_policy_snapshot_isolated
# 的 rollback 流程在 23:19 重写了根 .env，只是值恰好与线上相同才没出事故；
# settings_store 加 flock 后还会在仓库根留下 ..env.lock）。
# 这里上硬闸：测试进程内 ENV_FILE 仍指向仓库根 .env 时，写操作直接失败，
# 逼调用方显式沙箱化（`patch.object(settings_store, "ENV_FILE", tmp)`）。
import r20_backend.settings_store as _settings_store  # noqa: E402

_REAL_ENV_FILE = _settings_store.ENV_FILE


def _sandbox_required(original, name):
    def guarded(*args, **kwargs):
        if _settings_store.ENV_FILE == _REAL_ENV_FILE:
            raise AssertionError(
                f"测试禁止写生产配置 {_REAL_ENV_FILE}（{name}）——"
                "请先把 settings_store.ENV_FILE 指向临时文件")
        return original(*args, **kwargs)
    return guarded


_settings_store.update_env = _sandbox_required(_settings_store.update_env, "update_env")
_settings_store.remove_env = _sandbox_required(_settings_store.remove_env, "remove_env")


# ── 生产配置写保护（2026-09-14 事故后加）────────────────────────────────
# 事故：批4 测试文件的 `_Base` 把 isolate_config 的返回值误当 dict → 回落到项目根，
# 于是测试直接覆盖了生产 data/venue_routing.json（多所路由配置）与
# data/instrument_pool.json（交易池），并把因子快照刷成垃圾池；两者都只能靠审计记录 +
# OKX 重建恢复。教训是"缺省必须安全"：**运维配置文件的写入一律硬失败**，
# 其余 data/ 产物（锁、缓存、快照、台账）只告警不阻断（历史测试依赖它们）。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PROTECTED_CONFIG_FILES = {
    (_PROJECT_ROOT / ".env").resolve(),
    (_PROJECT_ROOT / "data" / "venue_routing.json").resolve(),
    (_PROJECT_ROOT / "data" / "instrument_pool.json").resolve(),
    (_PROJECT_ROOT / "data" / "council_config.json").resolve(),
    (_PROJECT_ROOT / "data" / "prompt_library.json").resolve(),
    (_PROJECT_ROOT / "data" / "llm_models.json").resolve(),
    (_PROJECT_ROOT / "data" / "account_baseline.json").resolve(),
    (_PROJECT_ROOT / "data" / "position_trackers.json").resolve(),
}
_DATA_DIR = (_PROJECT_ROOT / "data").resolve()
_ALLOW_REAL_WRITES = os.environ.get("R20_TESTS_ALLOW_REAL_DATA", "") == "1"
_WARNED_PATHS: set[str] = set()


# ── 生产数据**读**保护（2026-09-21 加）────────────────────────────────────
# 起因（第二百三十一刀的个案）：一条用例断言「**线上** data/trading_ledger.json 里必须存在
# UNI/binance 的 holding 行」——仓一平/台账一更新就红，而代码一行没改。
# **断言依赖生产数据的内容＝定时炸弹**，与本仓「测试不触生产文件」的纪律冲突
# （只读也不该**断言其内容**）。当时想用静态扫描做闸，实测 41 处命中里 40 处是假阳性
# （docstring 提到 data/、临时目录恰叫 data/hello.txt、已被 patch 到临时路径的读取）
# ⇒ 静态判不可靠，故改为**运行时**守卫：读生产 `data/` 直接失败。
#
# 与之配套的两个出口：
#   ① 环境变量 `R20_TESTS_ALLOW_REAL_DATA=1`（与写守卫同一开关，整进程放开）；
#   ② `_READ_ALLOW`：**逐文件**登记 + 写明理由（要求"只读且只做结构/哈希，不得断言内容"）。
_READ_ALLOW: dict = {}

#: 运行时开关：**少数**用例的存在目的就是核对生产文件（如
#: `tests/audit/test_production_data_isolation.py`），它们在自己的作用域里显式放开。
_ALLOW_REAL_READS = False

#: 只在**用例执行中**才把"读生产运维配置"当硬错误：模块 import 期（收集阶段）读配置是本仓
#: 大量模块的正常行为（61 个文件在收集期就 ERROR 的实测教训），那种读不是"测试依赖生产数据内容"。
_IN_TEST = False
_CURRENT_TEST = ["<收集期>"]


def _install_in_test_flag() -> None:
    """把 `unittest.TestCase.run` 包一层：仅用例执行期间 `_IN_TEST=True`。"""
    import unittest as _unittest

    if getattr(_unittest.TestCase.run, "_r20_wrapped", False):
        return
    _real_run = _unittest.TestCase.run

    def _run_with_flag(self, *args, **kwargs):
        global _IN_TEST
        prev_in, prev_name = _IN_TEST, _CURRENT_TEST[0]
        _IN_TEST, _CURRENT_TEST[0] = True, f"{type(self).__module__}::{type(self).__name__}.{self._testMethodName}"
        try:
            return _real_run(self, *args, **kwargs)
        finally:
            _IN_TEST, _CURRENT_TEST[0] = prev_in, prev_name

    _run_with_flag._r20_wrapped = True  # type: ignore[attr-defined]
    _unittest.TestCase.run = _run_with_flag


class allow_real_data_reads:  # noqa: N801 - 与 contextlib 用法一致（可当装饰器/上下文）
    """显式放开生产 `data/` 的**读**（默认拒绝）。

    用法（只给"核对生产文件本身"这类用例）：

        from tests import allow_real_data_reads

        class MyGate(unittest.TestCase):
            def setUp(self):
                self._scope = allow_real_data_reads()
                self._scope.__enter__()
                self.addCleanup(self._scope.__exit__, None, None, None)
    """

    def __enter__(self):
        global _ALLOW_REAL_READS
        self._prev, _ALLOW_REAL_READS = _ALLOW_REAL_READS, True
        return self

    def __exit__(self, *exc):
        global _ALLOW_REAL_READS
        _ALLOW_REAL_READS = self._prev
        return False


def _reader_frame() -> str:
    """找出"是谁在读"（第一条不在本文件里的帧）——提示里带上它，清理才可机械执行。"""
    import inspect

    me = __file__
    for fr in inspect.stack()[2:]:
        fn = fr.filename
        if fn == me or "/site-packages/" in fn or "/_pytest/" in fn:
            continue
        if "python3.11/" in fn or fn.endswith("pathlib.py") or fn.endswith("<frozen importlib._bootstrap>"):
            continue          # 标准库/导入机制的中转帧不算"读取点"
        if fn:
            return f"{fn}:{fr.lineno}"
    return "<unknown>"


def _assert_not_reading_production(path: object) -> None:
    """测试读生产 `data/` 直接失败（缺省必须安全）。"""
    if _ALLOW_REAL_WRITES or _ALLOW_REAL_READS:
        return
    try:
        resolved = Path(os.fspath(path)).resolve()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return
    if resolved in _READ_ALLOW:
        return
    # 与**写**守卫同一套policy（缺省必须安全，但不搞一刀切）：
    # ① 运维配置文件（venue_routing/instrument_pool/llm_models/…）的**内容会直接塑造决策**，
    #    读它＝测试依赖生产配置 ⇒ **硬失败**；
    # ② 其余 data/ 产物（台账、状态、快照…）历史上被大量测试读到，一律硬失败会掀翻整套，
    #    而它们的危害形态是"断言依赖其内容"⇒ 每个路径**提示一次**，逼人看见。
    if resolved in _PROTECTED_CONFIG_FILES and _IN_TEST:
        # 实测（第二百三十二刀）：一旦硬失败，`tests/venues` 里立刻有 21 个用例红（3 个文件）——
        # 也就是说**测试套件确实在依赖线上运维配置的内容**。这是真问题，但一次掀翻 21 个用例
        # 不叫修好；缺省改为**可见的提示**（每个「路径@用例」一次），并留一个**严格模式**
        # （`R20_TESTS_STRICT_READS=1`）供逐个清理时当闸用。清理清单见台账第 131 刀。
        key = f"readcfg:{resolved}@{_CURRENT_TEST[0]}"
        if key not in _WARNED_PATHS:
            _WARNED_PATHS.add(key)
            msg = (f"[tests] ⚠️ 生产配置依赖：{_CURRENT_TEST[0]} 读了线上 {resolved.name}"
                   f"（读取点 {_reader_frame()}）—— "
                   f"它的内容会塑造决策 ⇒ 结果随线上配置漂移。请 patch 到沙箱/临时文件"
                   f"（`tests.config_sandbox.isolate_config` 或 `patch.object(模块, \"XXX_FILE\", tmp)`）")
            # ⚠️ 严格模式**先打印再抛**：生产代码里大量 fail-soft（`except Exception: pass`）
            # 会把这里抛出的异常**吞掉**，于是测试只看到"下游断言莫名其妙地变了"
            # （实测 `test_gate_execution_router.py` 13 个用例表现为 `venue_dry_run != protective`
            # 这类 stage 漂移，读取点信息整个丢失）。打印后即便被吞也能在输出里找到真凶。
            print(msg)
            if os.environ.get("R20_TESTS_STRICT_READS", "") == "1":
                raise AssertionError(msg + "（当前为严格模式 R20_TESTS_STRICT_READS=1）")
            print(msg)
    if resolved == _DATA_DIR or str(resolved).startswith(str(_DATA_DIR) + os.sep):
        key = "read:" + str(resolved)
        if key not in _WARNED_PATHS:
            _WARNED_PATHS.add(key)
            print(f"[tests] 提示：测试正在**读**生产 data/ 下的 {resolved.name}"
                  f"（非运维配置，仅提示）—— 若据此**断言其内容**，平台一变就红，请改沙箱")


def _assert_not_production(path: object, action: str = "写入") -> None:
    """运维配置文件禁止测试写入；其余生产路径仅提示（每个路径一次）。"""
    if _ALLOW_REAL_WRITES:
        return
    try:
        resolved = Path(os.fspath(path)).resolve()  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return
    if resolved in _PROTECTED_CONFIG_FILES:
        raise AssertionError(
            f"测试禁止{action}生产配置文件 {resolved}——请用 tests.config_sandbox.isolate_config "
            f"的沙箱根（其返回值就是临时根 Path），不要回落到项目根")
    if resolved == _DATA_DIR or str(resolved).startswith(str(_DATA_DIR) + os.sep):
        key = str(resolved)
        if key not in _WARNED_PATHS:
            _WARNED_PATHS.add(key)
            print(f"[tests] 提示：测试正在{action}生产 data/ 下的 {resolved.name}"
                  f"（非运维配置，仅提示；建议改用沙箱）")


if not _ALLOW_REAL_WRITES:
    _real_write_text = Path.write_text
    _real_write_bytes = Path.write_bytes
    _real_path_open = Path.open
    _real_open = open
    _real_replace = os.replace
    _real_remove = os.remove

    def _guarded_write_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        _assert_not_production(self)
        return _real_write_text(self, *args, **kwargs)

    def _guarded_write_bytes(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        _assert_not_production(self)
        return _real_write_bytes(self, *args, **kwargs)

    def _guarded_path_open(self, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            _assert_not_production(self)
        else:
            _assert_not_reading_production(self)
        return _real_path_open(self, mode, *args, **kwargs)

    def _guarded_open(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            _assert_not_production(file)
        else:
            _assert_not_reading_production(file)
        return _real_open(file, mode, *args, **kwargs)

    def _guarded_replace(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
        _assert_not_production(dst, "替换")
        return _real_replace(src, dst, *args, **kwargs)

    def _guarded_remove(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        # dir_fd 形式（shutil.rmtree 的 _rmtree_safe_fd 会用）里的 path 是相对名，
        # 不能拿 CWD 解析 —— 否则临时目录清理会被误判成"删除生产 .env"。
        if kwargs.get("dir_fd") is None:
            _assert_not_production(path, "删除")
        return _real_remove(path, *args, **kwargs)

    Path.write_text = _guarded_write_text
    Path.write_bytes = _guarded_write_bytes
    Path.open = _guarded_path_open
    builtins.open = _guarded_open
    os.replace = _guarded_replace
    os.remove = _guarded_remove
    os.unlink = _guarded_remove
    _install_in_test_flag()


# ── 会话级配置沙箱（第二百三十四刀）────────────────────────────────────────
# 严格模式普查（`R20_TESTS_STRICT_READS=1 pytest tests`）实测 108 处用例读线上配置，
# 按**读取点**排序：`scripts/okx_runtime.py` 43（读 `ROOT/.env`）、
# `scripts/instrument_pool.py` 19（读池）、`scripts/prompt_library.py` 16（读提示词库）。
# 这三处是"缺省不安全"的源头 ⇒ 本刀把前两处**会话级**钉进沙箱（其余仍逐处处理）。
#
# ⚠️ 双拼写铁律：本仓 `scripts/x.py` 与 `x.py`（sys.path 含 scripts/）是**两个模块对象**
# （实测 `instrument_pool is not scripts.instrument_pool`）——只 patch 一个，另一个照旧读生产。
# 这正是上一刀"夹具池已装却仍读线上池"的真因。
_SESSION_SANDBOX: list = []


def session_sandbox_roots() -> list:
    """会话级配置沙箱的根目录（供 `tests.config_sandbox.isolate_config` 接管）。"""
    return [Path(t.name) for t in _SESSION_SANDBOX]


class _ConfigSandboxFinder:
    """给 `okx_runtime` / `instrument_pool` 的**两种拼写**在 import 时自动钉沙箱路径。

    为什么不用简单的 `setattr`：① 裸拼写（`import okx_runtime`）在会话开始时往往还没导入；
    ② `importlib.reload` 会**重新执行模块顶层**（`ROOT = Path(__file__)...`）把 setattr 冲掉
    —— 实测过"单跑绿、全量红"。这里包住 loader 的 `exec_module`：任何一次导入/重载之后，
    路径都会被重新钉到沙箱（`__spec__.loader` 就是本包装器 ⇒ reload 也走它）。
    """

    def __init__(self, targets):
        # 模块名 -> (属性名, 沙箱值, 是否让位给 isolate_config)
        self._targets = targets

    def find_spec(self, name, path=None, target=None):
        if name not in self._targets:
            return None
        import importlib.machinery as _machinery

        spec = _machinery.PathFinder.find_spec(name, path)
        if spec is None or spec.loader is None:
            return None
        attr, value, defer = self._targets[name]
        inner = spec.loader

        class _SandboxedLoader:
            def create_module(self, spec):
                create = getattr(inner, "create_module", None)
                return create(spec) if create else None

            def exec_module(self, module):
                inner.exec_module(module)
                # `tests.config_sandbox.isolate_config` 会设 `R20_DATA_DIR` 并把
                # 它白名单里的模块（含 prompt_library）重载进**更具体的**沙箱；
                # 那种情况下让位（否则会把它的沙箱路径顶掉 —— 实测
                # `test_config_sandbox.py::test_nested_policy_paths_share_one_sandbox` 就是这么红的）。
                if defer and os.environ.get("R20_DATA_DIR"):
                    return
                setattr(module, attr, value)

        spec.loader = _SandboxedLoader()
        return spec


def _install_session_config_sandbox() -> None:
    """把 `okx_runtime.ROOT` 与 `instrument_pool.POOL_FILE` 钉到临时沙箱（缺省安全）。"""
    if _ALLOW_REAL_WRITES:          # R20_TESTS_ALLOW_REAL_DATA=1：整进程放开，不沙箱
        return
    import tempfile

    tmp = tempfile.TemporaryDirectory(prefix="r20_tests_config_")
    _SESSION_SANDBOX.append(tmp)    # 保持引用，别被 GC 掉
    root = Path(tmp.name)
    # 池夹具：**同形且字段齐全**（少字段会把路由打进 `venue_pool` 分支），7 条 ≥ 本仓基线 6。
    # ⚠️ `ctVal/precision/tickSz/minSz` 用**真实静态值**（抄自线上池一次，之后固定，**不是**读取）：
    # 合约面值直接进保证金/名义额算式，写错会让断言按 10×/100× 漂移
    # （实测 `test_dashboard_cache.py` 的 ETH 断言 711.6 → 7116.0 就是这么红的，
    # 与 `REAL_HOLDING_UNI` 同一手法：**把形状/常量抄成固定夹具**，而不是每次去读生产）。
    _meta = {   # name: (ctVal, precision, tickSz, minSz)
        "BTC": (0.01, 1, "0.1", "0.01"), "ETH": (0.1, 2, "0.01", "0.01"),
        "SOL": (1.0, 2, "0.01", "0.01"), "DOGE": (1000.0, 5, "0.00001", "0.01"),
        "SUI": (1.0, 4, "0.0001", "1"), "ADA": (100.0, 4, "0.0001", "0.1"),
        "XRP": (100.0, 4, "0.0001", "0.01"),
        # UNI/ARB 也在路由夹具的 assets 里（池与路由要同形）
        "UNI": (1.0, 1, "0.001", "0.1"), "ARB": (1.0, 1, "0.0001", "0.1"),
    }
    names = tuple(_meta)
    insts = [{"instId": f"{n}-USDT-SWAP", "name": n, "type": "crypto", "ccy": n,
              "tier": "tier_1_bluechip" if n in ("BTC", "ETH") else "tier_2_momentum",
              "max_leverage": 5 if n in ("BTC", "ETH") else 3,
              "sl_atr_mult": 2.0 if n in ("BTC", "ETH") else 2.2,
              "base_sz": 1, "precision": _meta[n][1], "ctVal": _meta[n][0],
              "tickSz": _meta[n][2], "minSz": _meta[n][3],
              "risk_per_trade_usd": 20.0 if n in ("BTC", "ETH") else 15.0}
             for n in names]
    pool = root / "instrument_pool.json"
    pool.write_text(json.dumps({"version": 2, "instruments": insts}), encoding="utf-8")
    # 注意：**故意不写** `.env` ⇒ `okx_runtime._load_dotenv()` / `config.load_dotenv()`
    # 都直接返回（不读生产、不覆盖 os.environ；基线由 pin_baseline_risk_env 提供）。

    targets = {}
    for name in ("okx_runtime", "scripts.okx_runtime"):
        targets[name] = ("ROOT", root, False)
    for name in ("instrument_pool", "scripts.instrument_pool"):
        targets[name] = ("POOL_FILE", pool, False)
    # `r20_backend/notifications.py` 用**内联** `ROOT / ".env"` 读配置（同 okx_runtime 型）
    for name in ("r20_backend.notifications",):
        targets[name] = ("ROOT", root, False)
    # 场所路由：`routing_policy.py` 读 `data/venue_routing.json`（读取点 121，由守卫指出）。
    # ⚠️ 缺键时 **gate 默认 dry_run=True** ⇒ 不钉住就会让"实盘闸"的用例漂到 `venue_dry_run`
    # （实测 `test_gate_execution_router.py` 13 例：`venue_dry_run != protective/leverage/sizing`）。
    # 夹具用**抄自线上一次的静态值**（含两所 assets 与 dry_run=false 的实盘姿态）。
    routing = root / "venue_routing.json"
    routing.write_text(json.dumps({
        "preferred_venue": "auto", "routing_mode": "balanced",
        "gate": {"assets": ["ADA", "BTC", "DOGE", "ETH", "SOL", "SUI", "UNI", "XRP"],
                 "dry_run": False, "margin_per_trade_usdt": 500.0, "max_open": 5,
                 "min_confidence": 72.0},
        "binance": {"assets": ["ADA", "ARB", "BTC", "DOGE", "ETH", "SOL", "SUI", "UNI", "XRP"],
                    "dry_run": False, "margin_per_trade_usdt": 500.0, "max_open": 5,
                    "min_confidence": 72.0},
    }, ensure_ascii=False), encoding="utf-8")
    for name in ("r20_backend.exchanges.routing_policy",):
        targets[name] = ("ROUTING_FILE", routing, False)
    # 提示词库：指向沙箱里**不存在**的路径 ⇒ `load_library()` 走 `_default()` 确定性回退
    # （不读生产、也不随线上模板漂移）。要断言"线上模板内容"的用例必须自带夹具。
    for name in ("prompt_library", "scripts.prompt_library"):
        targets[name] = ("LIBRARY_FILE", root / "prompt_library.json", True)

    # 已经导入过的：就地钉一次；之后所有（含 reload）由 finder 兜住
    for name, (attr, value, defer) in targets.items():
        mod = sys.modules.get(name)
        if mod is not None and not (defer and os.environ.get("R20_DATA_DIR")):
            setattr(mod, attr, value)
    sys.meta_path.insert(0, _ConfigSandboxFinder(targets))
    print(f"[tests] 会话级配置沙箱已启用：okx_runtime.ROOT / instrument_pool.POOL_FILE → {root}"
          f"（要读真实配置：R20_TESTS_ALLOW_REAL_DATA=1）")


_install_session_config_sandbox()

# ⚠️ 第八十刀：import 时机静默 dashboard 的 **2 秒缓存外呼循环**。
# `r20_backend/dashboard_cache.py` 模块**顶层末尾**就 `start_dashboard_background_worker()`
# （web_shell 降格为纯库的历史残留，但删不得——`r20_backend/static/`（原 （已归档的 dashboard/start.sh），已归档） 的
# `uvicorn r20_backend.dashboard_cache:app` 独立部署模式全靠它）。后果：任何 import 过
# r20_backend.dashboard_cache 的测试进程里，都有一个 daemon 线程**每 2s 真外呼
# www.okx.com**（balances/positions/pending_orders）——11+ 个路由测试文件
# 的共同泄漏源，连完全不碰 dashboard 的用例都被波及（探针逐文件实测）。
# 测试进程不是 web 宿主：这里（tests 包最早加载点）先 import 再立即 stop。
# 模块顶层只执行一次；此后唯一重启者是 `with TestClient` 的 lifespan
# （r20_backend/app.py:139），那种用例必在 isolate_config 窗口内，
# 外呼已被 `_fetch_json` 压制（见 config_sandbox）。
try:
    import r20_backend.dashboard_cache as _dashboard_app
    _dashboard_app.stop_dashboard_background_worker()
    del _dashboard_app
except Exception:      # pragma: no cover — 导入失败不阻断测试收集
    pass


# ── 生产 sqlite 库的**连接**硬闸（第一百一十四刀，2026-09-20）──────────────
# 为什么单独有这条：上面那套 `_assert_not_production` 管的是 `open()/replace/unlink`
# 这类**文件级**写操作，而 sqlite 库的连接走 `sqlite3.connect`（文件只开一次，
# 之后全在 fd 上写）——**完全绕开**了那套闸。
#
# 实测证据（本刀用 `sys.addaudithook` 全量扫了一遍）：一次完整 `pytest tests`
# 会对**生产** `data/*.db` 发起 **18 次连接**：
#
# | 库 | 次数 | 入口 |
# |---|---|---|
# | `r20_admin.db` | 6 | `r20_backend/dependencies.py` **import 期**建 `AdminAuthStore()` |
# | `r20_quant.db` | 4 | `aft.record_trade` → `ledger_writer` → `db_manager.init_database()` |
# | `risk_reservation.db` | 6 | 仪表盘 stale 注入 → `dashboard_payload.market.get_manager()` |
# | `r20_gateway.db` | 2 | `metrics.build_snapshot` → `GatewayStore(DB_PATH)` |
#
# 后果不是理论：生产 `data/risk_reservation.db` 里**真的**留下一行
# `environment=<MagicMock name='current_environment().mode'>` 的垃圾预留
# （id=225，created_at 2026-09-20 04:59:03）——某个测试把 MagicMock 当环境
# 写进了生产风控台账。
#
# 所以缺省必须安全：**测试进程内连接生产 data/*.db 直接失败**，逼调用方沙箱化
# （`tests.config_sandbox.isolate_config`，或把库路径 patch 到 tmp）。
# 逃生口与上面一致：`R20_TESTS_ALLOW_REAL_DATA=1`。
if not _ALLOW_REAL_WRITES:
    import sqlite3 as _sqlite3
    import traceback as _traceback

    _PROD_DB_DIR = str(_DATA_DIR) + os.sep

    def _guard_production_sqlite(event, args):
        if event != "sqlite3.connect":
            return
        target = args[0] if args else ""
        try:
            text = os.path.abspath(os.fspath(target))
        except (TypeError, ValueError):
            return
        if not text.startswith(_PROD_DB_DIR):
            return
        stack = "".join(_traceback.format_stack()[-7:-1])
        raise AssertionError(
            f"测试禁止连接生产数据库 {text}\n"
            "请用 tests.config_sandbox.isolate_config 的沙箱根，或把该库路径 "
            "patch 到临时文件（临时目录不受管辖）。\n"
            f"调用栈：\n{stack}")

    import sys as _sys
    _sys.addaudithook(_guard_production_sqlite)


# ── 四个生产 sqlite 库：测试会话级**默认**重定向（第一百一十四刀）──────────
# 上面那条 `sqlite3.connect` 硬闸负责"发现"，这里负责"默认就该是安全的"：
# 会话开始就把四个库指向临时目录，此后任何测试（哪怕忘了 `isolate_config`）
# 都不会连到生产库；需要特定库内容的测试照旧自己 patch 到临时文件。
#
# 覆盖到的四个（全量实测的 18 次连接全部来自它们）：
#   `r20_admin.db`（`dependencies` import 期建 AdminAuthStore）、
#   `r20_quant.db`（`db_manager.get_db`）、
#   `risk_reservation.db`（`get_manager`）、
#   `r20_gateway.db`（`publisher.DB_PATH`，走 `R20_GATEWAY_DB` 环境变量）。
#
# ⚠️ 必须在**任何** `r20_backend.dependencies` import 之前完成 ——
# 它 import 期就 `AdminAuthStore()` 建表（本刀实测的 6 次连接来源）。
if not _ALLOW_REAL_WRITES:
    import tempfile as _tempfile
    _DB_SANDBOX = _tempfile.mkdtemp(prefix="r20-tests-dbs-")
    os.environ.setdefault("R20_GATEWAY_DB", os.path.join(_DB_SANDBOX, "r20_gateway.db"))

    def _redirect_db_paths():
        """把这四个库的模块常量指到会话临时目录（调用期读取，故改常量即生效）。

        `db_manager` **两种拼写都要补**（律②：patch 每个已绑定别名）——
        `scripts.db_manager` 与顶层 `db_manager` 是两个模块实例。
        """
        try:
            import r20_backend.admin_auth as _aa
            _aa.DB_PATH = Path(os.environ["R20_ADMIN_DB"])
        except Exception:
            pass
        try:
            import r20_backend.risk_reservation as _rr
            _rr.DEFAULT_DB_PATH = os.environ["R20_RISK_RESERVATION_DB"]
            _rr.reset_default_manager()
        except Exception:
            pass
        for _name in ("scripts.db_manager", "db_manager"):
            try:
                _mod = __import__(_name, fromlist=["DB_PATH"])
                _mod.DB_PATH = os.environ["R20_QUANT_DB"]
            except Exception:
                pass
        try:
            import r20_gateway.publisher as _pub
            _pub.DB_PATH = Path(os.environ["R20_GATEWAY_DB"])
        except Exception:
            pass

    _redirect_db_paths()
