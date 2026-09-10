"""Isolated producer regressions: never import trading/gateway entry points.

Compile real producer functions/expressions from their AST; dependencies are mocks,
so no project data, credentials, network, or process configuration is touched.
"""
import ast
import datetime as dt
import inspect
import json
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open

import pytest

ROOT = Path(__file__).resolve().parents[1]
BJ = dt.timezone(dt.timedelta(hours=8))
# UTC evening -> next day/year in Beijing (independent of host timezone).
EPOCH = 1767198600  # 2025-12-31 16:30:00 UTC
EXPECTED = "2026-01-01 00:30:00+08:00"


class FrozenDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        assert tz is not None, "producer must specify Beijing timezone"
        return cls.fromtimestamp(EPOCH, tz)


def tree(path):
    return ast.parse((ROOT / path).read_text())


def isolated(path, *names, **deps):
    nodes = [n for n in tree(path).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(nodes) == len(names)
    namespace = dict(datetime=FrozenDateTime, _BJ=BJ, logging=logging,
                     time=SimpleNamespace(time=lambda: EPOCH), json=json)
    namespace.update(deps)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)] + nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
    return SimpleNamespace(**namespace)


PRODUCER_PATHS = [
    "r20_backend/llm_manager.py", "r20_backend/council_manager.py",
    "r20_backend/policy_snapshot.py", "scripts/factor_library.py",
    "scripts/gate_lab_trader.py", "r20_backend/qq_gateway_daemon.py",
    "scripts/cleanup_disk.py",
]


@pytest.mark.parametrize("path", PRODUCER_PATHS)
def test_all_datetime_producer_expressions(path):
    """Exercise every changed expression, including migration/fallback branches."""
    namespace = dict(datetime=FrozenDateTime, _BJ=BJ, entry={"ts": EPOCH},
                     f=SimpleNamespace(stat=lambda: SimpleNamespace(st_mtime=EPOCH)))
    if path.endswith(("qq_gateway_daemon.py", "cleanup_disk.py")):
        namespace["datetime"] = SimpleNamespace(datetime=FrozenDateTime)
    expressions = [n for n in ast.walk(tree(path)) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute)
                   and n.func.attr in ("isoformat", "strftime")
                   and "datetime" in ast.unparse(n)]
    assert expressions
    for node in expressions:
        value = eval(compile(ast.Expression(node), path, "eval"), namespace)
        fmt = ast.unparse(node)
        if "%Y%m%d_%H%M%S" in fmt:
            assert value == "20260101_003000"
        elif "%Y-%m-%d %H:%M:%S" in fmt:
            assert path.endswith("gate_lab_trader.py")
            assert value == EXPECTED[:-6]
        else:
            assert value == EXPECTED
            assert dt.datetime.fromisoformat(value).timestamp() == EPOCH


def test_failover_epoch_preserved():
    write = MagicMock()
    mod = isolated("r20_backend/llm_manager.py", "record_failover_event",
                   FAILOVER_EVENTS_FILE=MagicMock(exists=lambda: False), _atomic_write_json=write)
    entry = {"reason": "mock failover"}
    mod.record_failover_event(entry)
    assert entry == {"reason": "mock failover", "ts": EPOCH, "time_str": EXPECTED}
    assert write.call_args.args[1] == [entry]


def test_council_save_export_and_backup_mocked():
    write = MagicMock()
    source = MagicMock()
    directory = MagicMock()
    directory.glob.return_value = []
    mod = isolated("r20_backend/council_manager.py", "save_council_config", "export_council_config", "_backup_council_config",
                   COUNCIL_CONFIG_FILE=source, DATA_DIR=directory, _atomic_write_json=write,
                   DEFAULT_CONSENSUS_MODE="standard", VALID_CONSENSUS_MODES={"standard"},
                   COUNCIL_EXPORT_FORMAT="test", COUNCIL_EXPORT_VERSION=1,
                   DEFAULT_COUNCIL_TIMEOUT=240, load_council_config=lambda: {})
    assert mod.save_council_config({"roles": {"cio": {}}})["updated_at"] == EXPECTED
    assert write.call_count == 1
    assert mod.export_council_config()["exported_at"] == EXPECTED
    mod._backup_council_config()
    directory.__truediv__.assert_called_once_with("council_config_backup_20260101_003000.json")


def test_policy_rebuild_preserves_legacy_and_converts_mtime():
    legacy = "2025-12-31 16:30:00"
    archive = MagicMock()
    file = MagicMock()
    file.name = "policy_abc.json"
    file.stat.return_value.st_mtime = EPOCH
    archive.glob.return_value = [file]
    for original, expected in [(legacy, legacy), (None, EXPECTED)]:
        mod = isolated("r20_backend/policy_snapshot.py", "_rebuild_index_from_archives",
                       open=mock_open(read_data=json.dumps({"policy_hash": "abc", "metadata": {"archived_at": original}})),
                       logger=MagicMock())
        entries = mod._rebuild_index_from_archives(archive)
        assert entries[0]["archived_at"] == expected


def test_new_policy_archive_keeps_hash_and_writes_offset():
    package = {"policy_hash": "abc12345", "policy_version": "v-test@abc12345", "summary": "mock"}
    write = MagicMock()
    index_write = MagicMock()
    mod = isolated("r20_backend/policy_snapshot.py", "archive_current_policy",
                   _index_lock=MagicMock(),
                   capture_full_strategy_package=MagicMock(return_value=package),
                   _atomic_write_json=write, load_archive_index=lambda **kw: [],
                   save_archive_index=index_write)
    result = mod.archive_current_policy("mock archive", archive_dir=MagicMock())
    assert result["archived_at"] == EXPECTED
    assert result["policy_hash"] == "abc12345"
    assert result["policy_version"] == "v-test@abc12345"
    assert write.call_args.args[1]["metadata"]["archived_at"] == EXPECTED
    assert index_write.call_args.args[0] == [result]


def test_factor_snapshot_mocked():
    executor = MagicMock()
    executor.return_value.__enter__.return_value.map.return_value = []
    mod = isolated("scripts/factor_library.py", "update_factor_library",
                   subprocess=MagicMock(), ThreadPoolExecutor=executor,
                   TARGET_INSTRUMENTS=[], os=MagicMock(), DATA_DIR="unused",
                   FACTOR_LIB_CACHE_FILE="unused.json", open=mock_open())
    snap = mod.update_factor_library()
    assert snap["timestamp"] == EPOCH
    assert snap["time_str"] == EXPECTED


def test_gate_trade_uses_beijing_without_changing_ledger_format(monkeypatch):
    db = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "db_manager", db)
    mod = isolated("scripts/gate_lab_trader.py", "_record_main_ledger")
    mod._record_main_ledger({"asset": "BTC", "entry_ts": EPOCH, "mode": "live"})
    trade = db.record_trade_sqlite.call_args.args[0]
    assert trade["time"] == EXPECTED[:-6]
    assert trade["bill_id"] == f"gatelib-live-BTC-{EPOCH}"


def test_qq_log_mocked():
    log_file = MagicMock()
    mod = isolated("r20_backend/qq_gateway_daemon.py", "log", LOG_FILE=log_file,
                   datetime=SimpleNamespace(datetime=FrozenDateTime), print=MagicMock())
    mod.log("mock")
    log_file.open.return_value.__enter__.return_value.write.assert_called_once_with(f"[{EXPECTED}] mock\n")


def test_cleanup_no_real_cleanup():
    mod = isolated("scripts/cleanup_disk.py", "run_cleanup_and_check",
                   datetime=SimpleNamespace(datetime=FrozenDateTime),
                   get_disk_status=lambda: {"free_gb": 10}, clean_logs=MagicMock(return_value=[]),
                   clean_system_caches=MagicMock())
    assert mod.run_cleanup_and_check()["timestamp"] == EXPECTED
    mod.clean_system_caches.assert_not_called()


def test_scheduler_record_created_and_handler_scope():
    original = logging.Formatter.converter
    logger = logging.Logger("isolated-beijing-test")
    handler = logging.StreamHandler()
    mocked_logging = SimpleNamespace(Formatter=logging.Formatter, INFO=logging.INFO,
                                    FileHandler=MagicMock(return_value=handler))
    mod = isolated("r20_backend/scheduler.py", "BeijingFormatter", "configure_logging",
                   logging=mocked_logging, logger=logger, LOGS=MagicMock())
    mod.configure_logging()
    record = logging.LogRecord("test", logging.INFO, "test", 1, "delayed", (), None)
    record.created = EPOCH
    record.msecs = 0
    assert handler.format(record) == "2026-01-01 00:30:00,000 +08:00 INFO delayed"
    assert logging.Formatter.converter is original
    mod.configure_logging()
    assert len(logger.handlers) == 1
    assert not logger.propagate


def test_watchdog_date_explicit_timezone():
    text = (ROOT / "scripts/r20_watchdog.sh").read_text()
    assert "$(TZ=Asia/Shanghai date '+%F %T +08:00')" in text


def load_tests(loader, standard_tests, pattern):
    """Bridge pytest-style functions into bare unittest runs.

    These are the Beijing-time contract regressions; the official suite runner
    (``python3 -m unittest discover`` / offline_suite) must not silently execute
    zero of them just because they are written pytest-style. Cases are built
    inside this hook (not at module level), so pytest still collects only the
    original functions and does not double-run them.
    """
    suite = unittest.TestSuite()
    for name in sorted(n for n in globals() if n.startswith("test_")):
        fn = globals()[name]
        if not callable(fn):
            continue
        params = inspect.signature(fn).parameters
        arg_sets = [{"path": p} for p in PRODUCER_PATHS] if "path" in params else [{}]
        for kwargs in arg_sets:
            def run(fn=fn, kwargs=kwargs, params=params):
                mp = pytest.MonkeyPatch() if "monkeypatch" in params else None
                try:
                    if mp is not None:
                        fn(monkeypatch=mp, **kwargs)
                    else:
                        fn(**kwargs)
                finally:
                    if mp is not None:
                        mp.undo()
            suffix = ("_" + kwargs["path"].replace("/", "_").replace(".py", "")) if "path" in kwargs else ""
            cls = type("producer_" + name + suffix, (unittest.TestCase,),
                       {"runTest": lambda self, _r=run: _r()})
            suite.addTest(cls("runTest"))
    return suite
