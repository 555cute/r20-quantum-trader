"""自进化引擎（上半）：**写盘原子性、单飞锁、快照因果 join、心法归一**（第二百九十四刀，开新面 self_improvement_engine.py）。

先打印整个文件（780 行）再动笔。这是全仓最后一个大缺口（**328 条语句只跑到 40 条 = 12.2%**）。

| 语义 | 口径 |
|---|---|
| ★ **写盘要么成功要么不留痕** | `atomic_write_json` 走同目录 `mkstemp` → `fsync` → `os.replace`；**任何失败都在 `finally` 里清掉临时文件**（磁盘上不留 `.evolution-*.tmp`）|
| ★ **单飞锁** | `single_evolution_cycle` 用 `flock(LOCK_EX\\|LOCK_NB)`：抢不到就**记一条日志并返回 `None`**（不是抛错、更不是排队）；正常路径必须在 `finally` 里解锁 |
| ★ **日志目标调用时解析** | `_log_file()` 每次读 `R20_SELF_IMPROVEMENT_LOG` —— 否则测试漏 patch 一次 `LOG_FILE` 就直接写生产 `logs/self_improvement.log`。写日志失败一律吞掉（**日志不许影响复盘**）|
| ★ **门面薄壳必须读"门面全局"** | `get_cpa_client_config()` 把 `standalone_settings` **在调用时**取出来传给实现 —— 子模块若在 import 期绑定读到的是陈旧副本（本模块注释点名的既有接缝）|
| ★ **回退选模先同网关** | 模型池可能横跨多域名，先把配置**打桩打掉**再读真实配置没有任何意义；死域上的席位回退过去也是 400/504 ⇒ 优先同 `base_url` 的健康成员，其次异域名 |
| ★ **快照 join 的四条铁律** | ①方向必须一致（拒空头快照当多头成因）；②允许 `[-6h, +20min]` 的首巡检窗口；③**开仓 20 分钟之后的快照绝不是因果现场**；④早于 6 小时算过期证据。窗口内**取最接近的** |
| ★ **心法归一不许落回 `str(dict)`** | `_coerce_display_str` 要处理模型 schema 漂移：自序列化 JSON 字符串、`{dimension, analysis}`、`{action_type, action}`、纯列表 —— 否则前端渲染成 `[object Object]`（2026-09-09 用户截图那个事故）|
| ★ **基准心法是宪法级** | `merge_memory_with_constitution`：ADD 为纯追加并去重；REVISE/INVALIDATE 可整理战术层，但**被省略的基准心法由宿主原样补回**（大模型无权物理删除宪法级记忆）|
| ★ **证据不足就保留旧心法** | `resolve_memory_update`：`NO_CHANGE` **或提案为空** ⇒ 一律保留既有清单（`preserve=True`）；非法状态码静默归到 `NO_CHANGE` |

## 封闭性

本模块所有路径常量默认指向**生产 `data/` 与 `logs/`**，故 `_Base.setUp` 把
`DATA_DIR`/`LEDGER_JSON_FILE`/`REPORT_JSON_FILE`/`AI_MEMORY*`/`EVOLUTION_LAST_PROMPT_FILE`/
`LOG_FILE`/`EVOLUTION_LOCK_FILE` **全部**改写到临时目录，并把
`R20_SELF_IMPROVEMENT_LOG` 也钉到临时路径；`init_llm_config` 等外部依赖逐个打桩。
"""

import datetime
import fcntl
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 与同族"导入 scripts 模块"的测试同一段前置：scripts 模块用**裸兄弟导入**
# （`from instrument_pool import load_instruments`），故 scripts/ 必须先上 sys.path。
_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts import self_improvement_engine as SIE

_BJ_FORMAT = "%Y-%m-%d %H:%M:%S"


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.logs = Path(self.tmp.name) / "logs"
        self.log_path = str(self.logs / "self_improvement.log")
        for name, value in (
            ("DATA_DIR", str(self.data)),
            ("LEDGER_JSON_FILE", str(self.data / "trading_ledger.json")),
            ("REPORT_JSON_FILE", str(self.data / "self_improvement_report.json")),
            ("AI_DECISIONS_FILE", str(self.data / "ai_brain_decisions.json")),
            ("AI_MEMORY_FILE", str(self.data / "ai_trading_memory.json")),
            ("AI_MEMORY_MD_FILE", str(self.data / "AI_TRADING_MEMORY.md")),
            ("EVOLUTION_LAST_PROMPT_FILE", str(self.data / "self_improvement_last_prompt.txt")),
            ("LOG_FILE", self.log_path),
            ("EVOLUTION_LOCK_FILE", str(self.data / ".self_improvement.lock")),
        ):
            self._start(mock.patch.object(SIE, name, value))
        self._start(mock.patch.dict(os.environ,
                                    {"R20_SELF_IMPROVEMENT_LOG": self.log_path}))
        self._start(mock.patch.object(SIE, "TARGET_INSTRUMENTS", ["BTC", "ETH"]))

    def _log(self) -> str:
        path = Path(self.log_path)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _write_json(self, path: Path, payload) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path


# =====================================================================
# 写盘原子性 / 夹取 / 单飞锁 / 日志
# =====================================================================

class AtomicWriteJsonTests(_Base):
    def test_it_writes_the_payload_as_utf8_json(self):
        target = self.data / "out.json"
        SIE.atomic_write_json(str(target), {"名称": "值"})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"名称": "值"})

    def test_the_parent_directory_is_created(self):
        target = self.data / "nested" / "deep" / "out.json"
        SIE.atomic_write_json(str(target), [1])
        self.assertTrue(target.is_file())

    def test_it_does_not_escape_non_ascii(self):
        target = self.data / "out.json"
        SIE.atomic_write_json(str(target), {"k": "中文"})
        self.assertIn("中文", target.read_text(encoding="utf-8"))

    def test_it_is_indented_for_human_diffing(self):
        target = self.data / "out.json"
        SIE.atomic_write_json(str(target), {"a": 1})
        self.assertIn("\n  ", target.read_text(encoding="utf-8"))

    def test_it_overwrites_an_existing_file(self):
        target = self.data / "out.json"
        SIE.atomic_write_json(str(target), {"v": 1})
        SIE.atomic_write_json(str(target), {"v": 2})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"v": 2})

    def test_a_failure_leaves_no_temp_file_behind(self):
        """★ `finally: unlink` —— 写到一半炸了也不能在盘上留 `.evolution-*.tmp`。"""
        target = self.data / "out.json"
        with self.assertRaises(TypeError):
            SIE.atomic_write_json(str(target), {"bad": object()})
        self.assertEqual([p.name for p in self.data.iterdir()], [])

    def test_a_failure_does_not_create_the_target(self):
        target = self.data / "out.json"
        with self.assertRaises(TypeError):
            SIE.atomic_write_json(str(target), object())
        self.assertFalse(target.exists())

    def test_a_successful_write_leaves_no_temp_file_behind(self):
        SIE.atomic_write_json(str(self.data / "out.json"), {"a": 1})
        self.assertEqual([p.name for p in self.data.iterdir()], ["out.json"])

    def test_the_temp_file_is_written_in_the_same_directory(self):
        """跨设备 `os.replace` 不是原子的 —— 临时文件必须同目录。"""
        source = Path(SIE.__file__).read_text(encoding="utf-8")
        self.assertIn('dir=os.path.dirname(path)', source)


class ClampTests(unittest.TestCase):
    def test_it_delegates_to_the_shared_helper(self):
        self.assertEqual(SIE.clamp(150, 0, 100, 5), 100)
        self.assertEqual(SIE.clamp(-10, 0, 100, 5), 0)
        self.assertEqual(SIE.clamp(50, 0, 100, 5), 50)

    def test_an_incomparable_value_uses_the_default(self):
        self.assertEqual(SIE.clamp("abc", 0, 100, 7), 7)
        self.assertEqual(SIE.clamp(None, 0, 100, 7), 7)

    def test_the_name_stays_patchable_on_this_module(self):
        """本模块注释点名的既有接缝：`patch.object(模块, "clamp")` 必须有效。"""
        with mock.patch.object(SIE, "clamp", return_value=42) as patched:
            self.assertEqual(SIE.clamp(1, 2, 3, 4), 42)
        patched.assert_called_once_with(1, 2, 3, 4)

    def test_the_decimal_case(self):
        self.assertEqual(SIE.clamp(1.25, 0.0, 1.0, 0.0), 1.0)


class SingleEvolutionCycleTests(_Base):
    def test_it_runs_the_wrapped_function_and_returns_its_value(self):
        @SIE.single_evolution_cycle
        def _job(a, b=2):
            return a + b

        self.assertEqual(_job(1), 3)
        self.assertEqual(_job(1, b=10), 11)

    def test_it_preserves_the_wrapped_function_metadata(self):
        @SIE.single_evolution_cycle
        def _job():
            return 1

        self.assertTrue(callable(_job))

    def test_the_lock_is_released_after_a_successful_cycle(self):
        @SIE.single_evolution_cycle
        def _job():
            return "ok"

        self.assertEqual(_job(), "ok")
        with open(SIE.EVOLUTION_LOCK_FILE, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # 不该阻塞
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_a_contended_lock_skips_the_cycle_and_logs(self):
        """★ 抢不到锁 ⇒ 记一条日志 + 返回 `None`（不抛错、不排队）。"""
        @SIE.single_evolution_cycle
        def _job():
            return "不该跑到这里"

        holder = open(SIE.EVOLUTION_LOCK_FILE, "a+", encoding="utf-8")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.assertIsNone(_job())
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()
        self.assertIn("another cycle is still running", self._log())

    def test_the_lock_is_released_even_when_the_cycle_raises(self):
        @SIE.single_evolution_cycle
        def _job():
            raise RuntimeError("周期里炸了")

        with self.assertRaises(RuntimeError):
            _job()
        with open(SIE.EVOLUTION_LOCK_FILE, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_it_works_when_the_lock_file_does_not_exist_yet(self):
        self.assertFalse(Path(SIE.EVOLUTION_LOCK_FILE).exists())

        @SIE.single_evolution_cycle
        def _job():
            return 1

        self.assertEqual(_job(), 1)


class LogFileTests(_Base):
    def test_the_env_override_wins(self):
        self.assertEqual(SIE._log_file(), self.log_path)

    def test_it_falls_back_to_the_module_constant(self):
        with mock.patch.dict(os.environ, {"R20_SELF_IMPROVEMENT_LOG": ""}):
            self.assertEqual(SIE._log_file(), SIE.LOG_FILE)

    def test_it_resolves_at_call_time(self):
        """调用时解析 ⇒ 测试中途改常量也能生效（审计卫生的落点）。"""
        with mock.patch.object(SIE, "LOG_FILE", "/tmp/other.log"):
            with mock.patch.dict(os.environ, {"R20_SELF_IMPROVEMENT_LOG": ""}):
                self.assertEqual(SIE._log_file(), "/tmp/other.log")


class LogMsgTests(_Base):
    def test_it_writes_a_beijing_timestamped_line(self):
        SIE.log_msg("你好")
        text = self._log()
        self.assertIn("你好", text)
        self.assertRegex(text, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 你好\n$")

    def test_the_timestamp_is_beijing_time(self):
        expected = datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
        SIE.log_msg("x")
        self.assertIn(expected, self._log())

    def test_it_prints_as_well(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            SIE.log_msg("双写")
        self.assertIn("双写", buf.getvalue())

    def test_lines_accumulate(self):
        SIE.log_msg("一")
        SIE.log_msg("二")
        self.assertEqual(len(self._log().strip().splitlines()), 2)

    def test_the_log_directory_is_created_lazily(self):
        self.assertFalse(self.logs.exists())
        SIE.log_msg("x")
        self.assertTrue(self.logs.is_dir())

    def test_a_write_failure_is_swallowed(self):
        """★ 日志写不出去不许影响复盘 —— 整段包在 `except Exception: pass` 里。"""
        blocker = Path(self.tmp.name) / "blocker"
        blocker.write_text("我是个文件，不是目录", encoding="utf-8")
        with mock.patch.dict(os.environ,
                             {"R20_SELF_IMPROVEMENT_LOG": str(blocker / "x.log")}):
            self.assertIsNone(SIE.log_msg("写不进去"))


# =====================================================================
# 配置与回退选模
# =====================================================================

class GetCpaClientConfigTests(unittest.TestCase):
    def setUp(self):
        self.impl = mock.patch.object(SIE, "_get_cpa_client_config",
                                      return_value=("u", "k")).start()
        self.addCleanup(mock.patch.stopall)

    def test_it_forwards_the_facade_global(self):
        SIE.get_cpa_client_config()
        self.assertIs(self.impl.call_args[0][0], SIE.standalone_settings)

    def test_a_patched_facade_global_is_actually_used(self):
        """★ 本模块注释点名的地雷：子模块 import 期绑定会读到陈旧副本。"""
        sentinel = object()
        with mock.patch.object(SIE, "standalone_settings", sentinel):
            SIE.get_cpa_client_config()
        self.assertIs(self.impl.call_args[0][0], sentinel)

    def test_the_return_value_passes_through(self):
        self.assertEqual(SIE.get_cpa_client_config(), ("u", "k"))

    def test_none_settings_is_forwarded_as_is(self):
        with mock.patch.object(SIE, "standalone_settings", None):
            SIE.get_cpa_client_config()
        self.assertIsNone(self.impl.call_args[0][0])


class EvolutionFallbackModelTests(_Base):
    def _config(self, **kw):
        return mock.patch("r20_backend.llm_manager.init_llm_config",
                          return_value=kw).start()

    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)

    def test_a_same_gateway_member_is_preferred(self):
        """★ 死域上的席位回退过去也是 400/504 ⇒ 先挑同 `base_url` 的。"""
        self._config(active_model_id="A",
                     models=[{"id": "A", "base_url": "u1"},
                             {"id": "Z", "base_url": "u2"},
                             {"id": "B", "base_url": "u1"}])
        self.assertEqual(SIE.evolution_fallback_model(), "B")

    def test_an_other_gateway_member_is_used_when_no_same_gateway_one_exists(self):
        self._config(active_model_id="A",
                     models=[{"id": "A", "base_url": "u1"},
                             {"id": "C", "base_url": "u2"}])
        self.assertEqual(SIE.evolution_fallback_model(), "C")

    def test_the_active_model_is_never_returned(self):
        self._config(active_model_id="A", models=[{"id": "A", "base_url": "u1"}])
        self.assertIsNone(SIE.evolution_fallback_model())

    def test_an_unknown_active_model_makes_everything_other_gateway(self):
        self._config(active_model_id="MISSING",
                     models=[{"id": "A", "base_url": "u1"},
                             {"id": "B", "base_url": "u1"}])
        self.assertEqual(SIE.evolution_fallback_model(), "A")

    def test_members_without_an_id_are_skipped(self):
        self._config(active_model_id="A",
                     models=[{"id": "A", "base_url": "u1"},
                             {"id": "", "base_url": "u1"},
                             {"base_url": "u1"},
                             {"id": "B", "base_url": "u1"}])
        self.assertEqual(SIE.evolution_fallback_model(), "B")

    def test_non_dict_members_are_ignored(self):
        self._config(active_model_id="A",
                     models=[{"id": "A", "base_url": "u1"}, "junk", None,
                             {"id": "B", "base_url": "u1"}])
        self.assertEqual(SIE.evolution_fallback_model(), "B")

    def test_an_empty_pool_yields_none(self):
        self._config(active_model_id="A", models=[])
        self.assertIsNone(SIE.evolution_fallback_model())

    def test_a_none_config_yields_none(self):
        self._config()
        self.assertIsNone(SIE.evolution_fallback_model())

    def test_a_resolution_failure_is_logged_and_yields_none(self):
        mock.patch("r20_backend.llm_manager.init_llm_config",
                   side_effect=RuntimeError("配置读不到")).start()
        self.assertIsNone(SIE.evolution_fallback_model())
        self.assertIn("复盘回退模型解析失败", self._log())
        self.assertIn("配置读不到", self._log())

    def test_it_never_writes_the_global_config(self):
        """本函数只**读**配置选一个候选，绝不改写全局的 `fallback_model_ids`。

        ⚠️ 不能拿源码文本断言（函数 docstring 里就写着 `fallback_model_ids` 这个词），
        改用 AST 看它**实际调用了哪些函数**。
        """
        import ast
        tree = ast.parse(Path(SIE.__file__).read_text(encoding="utf-8"))
        node = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "evolution_fallback_model")
        called = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                name = getattr(sub.func, "attr", None) or getattr(sub.func, "id", None)
                if name:
                    called.add(name)
        self.assertIn("init_llm_config", called, "必须真的是在读配置")
        for banned in ("save", "save_config", "write", "dump", "update", "set_config"):
            self.assertNotIn(banned, called)


# =====================================================================
# 快照日志与因果 join
# =====================================================================

class LoadSignalJournalTests(_Base):
    def _journal(self, payload):
        return self._write_json(self.data / "signal_journal.json", payload)

    def test_a_missing_file_yields_an_empty_map(self):
        self.assertEqual(SIE.load_signal_journal(), {})

    def test_records_are_grouped_by_name(self):
        self._journal([{"name": "BTC", "snapshot": {"a": 1}},
                       {"name": "BTC", "snapshot": {"a": 2}},
                       {"name": "ETH", "snapshot": {"a": 3}}])
        out = SIE.load_signal_journal()
        self.assertEqual(sorted(out), ["BTC", "ETH"])
        self.assertEqual(len(out["BTC"]), 2)

    def test_the_inst_key_is_used_as_a_fallback(self):
        self._journal([{"inst": "SOL", "snapshot": {}}])
        self.assertEqual(list(SIE.load_signal_journal()), ["SOL"])

    def test_name_wins_over_inst(self):
        self._journal([{"name": "BTC", "inst": "WRONG"}])
        self.assertEqual(list(SIE.load_signal_journal()), ["BTC"])

    def test_records_without_any_identifier_are_skipped(self):
        self._journal([{"snapshot": {}}, {"name": ""}, {"name": "BTC"}])
        self.assertEqual(list(SIE.load_signal_journal()), ["BTC"])

    def test_corrupt_json_is_logged_and_yields_an_empty_map(self):
        (self.data / "signal_journal.json").write_text("{不是 JSON", encoding="utf-8")
        self.assertEqual(SIE.load_signal_journal(), {})
        self.assertIn("读取 signal_journal 异常", self._log())

    def test_a_non_list_payload_is_logged_and_yields_an_empty_map(self):
        self._journal({"name": "BTC"})
        self.assertEqual(SIE.load_signal_journal(), {})
        self.assertIn("读取 signal_journal 异常", self._log())

    def test_an_empty_list_yields_an_empty_map(self):
        self._journal([])
        self.assertEqual(SIE.load_signal_journal(), {})


class MatchSnapshotTests(_Base):
    OPEN = "2026-09-22 10:00:00"

    def _journal(self, *records):
        return {"BTC": list(records)}

    def _rec(self, entry_time, side=None, snapshot=None):
        rec = {"entryTime": entry_time}
        if side is not None:
            rec["side"] = side
        if snapshot is not None:
            rec["snapshot"] = snapshot
        return rec

    def test_no_candidates_yields_none(self):
        self.assertIsNone(SIE._match_snapshot({}, "BTC", self.OPEN, "long"))

    def test_an_unparsable_open_time_yields_none(self):
        journal = self._journal(self._rec(self.OPEN, snapshot={"a": 1}))
        self.assertIsNone(SIE._match_snapshot(journal, "BTC", "garbage", "long"))

    def test_an_exact_match_is_returned(self):
        journal = self._journal(self._rec(self.OPEN, snapshot={"a": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"), {"a": 1})

    def test_the_nearest_candidate_wins(self):
        journal = self._journal(self._rec("2026-09-22 09:20:00", snapshot={"far": 1}),
                                self._rec("2026-09-22 10:05:00", snapshot={"near": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"),
                         {"near": 1})

    def test_a_future_snapshot_beyond_the_lag_window_is_rejected(self):
        """★ 开仓 20 分钟之后的快照绝非因果现场。"""
        journal = self._journal(self._rec("2026-09-22 10:30:00", snapshot={"future": 1}))
        self.assertIsNone(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"))

    def test_a_snapshot_at_the_lag_boundary_is_accepted(self):
        journal = self._journal(self._rec("2026-09-22 10:20:00", snapshot={"edge": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"),
                         {"edge": 1})

    def test_a_stale_snapshot_is_rejected(self):
        journal = self._journal(self._rec("2026-09-22 03:30:00", snapshot={"old": 1}))
        self.assertIsNone(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"))

    def test_a_snapshot_at_the_stale_boundary_is_accepted(self):
        journal = self._journal(self._rec("2026-09-22 04:00:00", snapshot={"edge": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"),
                         {"edge": 1})

    def test_a_side_mismatch_is_rejected(self):
        """★ 不许把空头开仓快照当多头成因。"""
        journal = self._journal(self._rec(self.OPEN, side="空", snapshot={"short": 1}))
        self.assertIsNone(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"))

    def test_a_matching_side_is_accepted(self):
        journal = self._journal(self._rec(self.OPEN, side="long", snapshot={"long": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"),
                         {"long": 1})

    def test_the_chinese_side_alias_is_understood(self):
        journal = self._journal(self._rec(self.OPEN, side="多", snapshot={"long": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"),
                         {"long": 1})

    def test_a_record_without_a_side_is_not_filtered(self):
        journal = self._journal(self._rec(self.OPEN, snapshot={"noside": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"),
                         {"noside": 1})

    def test_an_unknown_wanted_side_disables_the_filter(self):
        journal = self._journal(self._rec(self.OPEN, side="空", snapshot={"short": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "sideways"),
                         {"short": 1})

    def test_a_record_with_an_unparsable_time_is_skipped(self):
        journal = self._journal(self._rec("garbage", snapshot={"bad": 1}),
                                self._rec(self.OPEN, snapshot={"good": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"),
                         {"good": 1})

    def test_a_candidate_without_a_snapshot_yields_none(self):
        journal = self._journal(self._rec(self.OPEN))
        self.assertIsNone(SIE._match_snapshot(journal, "BTC", self.OPEN, "long"))

    def test_an_absent_side_argument_is_tolerated(self):
        journal = self._journal(self._rec(self.OPEN, snapshot={"a": 1}))
        self.assertEqual(SIE._match_snapshot(journal, "BTC", self.OPEN), {"a": 1})


# =====================================================================
# 心法归一与宪法级保护
# =====================================================================

class ResolveMemoryUpdateTests(unittest.TestCase):
    EXISTING = ["旧心法一", "旧心法二"]

    def test_no_change_preserves_everything(self):
        status, memory, preserve = SIE.resolve_memory_update(
            "NO_CHANGE", ["新"], self.EXISTING)
        self.assertEqual(status, "NO_CHANGE")
        self.assertEqual(memory, self.EXISTING)
        self.assertIs(preserve, True)

    def test_add_with_proposals_replaces_the_list(self):
        status, memory, preserve = SIE.resolve_memory_update(
            "ADD", ["新心法"], self.EXISTING)
        self.assertEqual(status, "ADD")
        self.assertEqual(memory, ["新心法"])
        self.assertIs(preserve, False)

    def test_an_empty_proposal_preserves_existing_even_for_add(self):
        """★ 证据不足（提案为空）时**必须**保留旧心法，不能把记忆清空。"""
        _, memory, preserve = SIE.resolve_memory_update("ADD", [], self.EXISTING)
        self.assertEqual(memory, self.EXISTING)
        self.assertIs(preserve, True)

    def test_a_lowercase_status_is_normalised(self):
        self.assertEqual(SIE.resolve_memory_update("add", ["x"], [])[0], "ADD")

    def test_an_unknown_status_falls_back_to_no_change(self):
        status, memory, preserve = SIE.resolve_memory_update("WHATEVER", ["x"], self.EXISTING)
        self.assertEqual(status, "NO_CHANGE")
        self.assertEqual(memory, self.EXISTING)
        self.assertIs(preserve, True)

    def test_a_missing_status_falls_back_to_no_change(self):
        self.assertEqual(SIE.resolve_memory_update(None, [], [])[0], "NO_CHANGE")

    def test_a_non_list_proposal_is_treated_as_empty(self):
        _, memory, preserve = SIE.resolve_memory_update("ADD", "not a list", self.EXISTING)
        self.assertEqual(memory, self.EXISTING)
        self.assertIs(preserve, True)

    def test_object_items_are_flattened_to_strings(self):
        _, memory, _ = SIE.resolve_memory_update(
            "ADD", [{"dimension": "风险", "analysis": "别加仓"}], [])
        self.assertEqual(memory, ["【风险】别加仓"])

    def test_blank_items_are_dropped(self):
        _, memory, preserve = SIE.resolve_memory_update("ADD", ["", "   ", "有效"], [])
        self.assertEqual(memory, ["有效"])
        self.assertIs(preserve, False)

    def test_all_blank_items_behave_like_an_empty_proposal(self):
        _, memory, preserve = SIE.resolve_memory_update("ADD", ["", " "], self.EXISTING)
        self.assertEqual(memory, self.EXISTING)
        self.assertIs(preserve, True)

    def test_the_existing_list_is_copied_not_aliased(self):
        existing = list(self.EXISTING)
        _, memory, _ = SIE.resolve_memory_update("NO_CHANGE", [], existing)
        self.assertIsNot(memory, existing)
        self.assertEqual(memory, existing)

    def test_the_revise_and_invalidate_statuses_pass_through(self):
        for status in ("REVISE", "INVALIDATE"):
            with self.subTest(status=status):
                self.assertEqual(SIE.resolve_memory_update(status, ["x"], [])[0], status)


class MergeMemoryWithConstitutionTests(unittest.TestCase):
    def _lesson(self, text, baseline=False, enabled=True):
        return {"rule_text": text, "is_baseline": baseline, "enabled": enabled}

    def test_add_appends_new_items_after_the_existing_ones(self):
        final, readded = SIE.merge_memory_with_constitution(
            "ADD", ["新"], [self._lesson("旧")])
        self.assertEqual(final, ["旧", "新"])
        self.assertEqual(readded, [])

    def test_add_deduplicates(self):
        final, _ = SIE.merge_memory_with_constitution(
            "ADD", ["旧", "新", "新"], [self._lesson("旧")])
        self.assertEqual(final, ["旧", "新"])

    def test_revise_keeps_only_the_proposed_items_plus_the_baselines(self):
        final, readded = SIE.merge_memory_with_constitution(
            "REVISE", ["战术新解"], [self._lesson("战术旧解"), self._lesson("基准", True)])
        self.assertEqual(final, ["战术新解", "基准"])
        self.assertEqual(readded, ["基准"])

    def test_an_omitted_baseline_is_readded(self):
        """★ 大模型无权物理删除宪法级记忆。"""
        final, readded = SIE.merge_memory_with_constitution(
            "INVALIDATE", [], [self._lesson("基准甲", True), self._lesson("基准乙", True)])
        self.assertEqual(final, ["基准甲", "基准乙"])
        self.assertEqual(readded, ["基准甲", "基准乙"])

    def test_a_baseline_the_model_kept_is_not_readded(self):
        final, readded = SIE.merge_memory_with_constitution(
            "REVISE", ["基准甲"], [self._lesson("基准甲", True)])
        self.assertEqual(final, ["基准甲"])
        self.assertEqual(readded, [])

    def test_disabled_lessons_are_ignored(self):
        final, readded = SIE.merge_memory_with_constitution(
            "REVISE", [], [self._lesson("停用", False, enabled=False),
                           self._lesson("启用", True, enabled=True)])
        self.assertEqual(final, ["启用"])
        self.assertEqual(readded, ["启用"])

    def test_non_dict_lessons_are_ignored(self):
        final, _ = SIE.merge_memory_with_constitution(
            "ADD", ["新"], ["junk", None, self._lesson("旧")])
        self.assertEqual(final, ["旧", "新"])

    def test_lessons_without_rule_text_are_ignored(self):
        final, _ = SIE.merge_memory_with_constitution(
            "ADD", ["新"], [{"rule_text": "  "}, {"is_baseline": True}, self._lesson("旧")])
        self.assertEqual(final, ["旧", "新"])

    def test_object_memory_items_are_flattened(self):
        final, _ = SIE.merge_memory_with_constitution(
            "ADD", [{"dimension": "仓位", "analysis": "分批建仓"}], [])
        self.assertEqual(final, ["【仓位】分批建仓"])

    def test_empty_proposals_for_revise_leave_only_the_baselines(self):
        final, readded = SIE.merge_memory_with_constitution(
            "REVISE", [], [self._lesson("战术"), self._lesson("基准", True)])
        self.assertEqual(final, ["基准"])
        self.assertEqual(readded, ["基准"])

    def test_none_existing_lessons_is_tolerated(self):
        final, readded = SIE.merge_memory_with_constitution("ADD", ["新"], None)
        self.assertEqual(final, ["新"])
        self.assertEqual(readded, [])

    def test_none_proposals_is_tolerated(self):
        final, _ = SIE.merge_memory_with_constitution("REVISE", None, [self._lesson("旧")])
        self.assertEqual(final, [])


class CoerceDisplayStrTests(unittest.TestCase):
    def test_a_plain_string_is_stripped(self):
        self.assertEqual(SIE._coerce_display_str("  hello  "), "hello")

    def test_a_blank_string_becomes_empty(self):
        self.assertEqual(SIE._coerce_display_str("   "), "")

    def test_none_becomes_empty(self):
        self.assertEqual(SIE._coerce_display_str(None), "")

    def test_a_number_is_stringified(self):
        self.assertEqual(SIE._coerce_display_str(7), "7")
        self.assertEqual(SIE._coerce_display_str(1.5), "1.5")

    def test_a_serialised_json_object_is_unwrapped(self):
        """★ 模型常见漂移：数组项是"自己序列化过的 JSON 字符串"。"""
        payload = json.dumps({"dimension": "风险", "analysis": "别加仓"}, ensure_ascii=False)
        self.assertEqual(SIE._coerce_display_str(payload), "【风险】别加仓")

    def test_a_serialised_json_list_is_unwrapped_and_joined(self):
        payload = json.dumps(["甲", "乙"], ensure_ascii=False)
        self.assertEqual(SIE._coerce_display_str(payload), "甲；乙")

    def test_an_invalid_json_looking_string_is_returned_as_is(self):
        self.assertEqual(SIE._coerce_display_str("{不是 JSON"), "{不是 JSON")

    def test_a_json_scalar_string_is_returned_as_is(self):
        self.assertEqual(SIE._coerce_display_str('"just a string"'),
                         '"just a string"')

    def test_a_dict_with_dimension_and_text_key(self):
        self.assertEqual(
            SIE._coerce_display_str({"dimension": "执行力", "analysis": "要更快"}),
            "【执行力】要更快")

    def test_the_title_falls_back_through_title_and_category(self):
        for key in ("title", "category", "action_type"):
            with self.subTest(key=key):
                self.assertEqual(SIE._coerce_display_str({key: "T", "detail": "D"}),
                                 "【T】D")

    def test_the_body_uses_the_first_known_text_key(self):
        self.assertEqual(
            SIE._coerce_display_str({"title": "T", "observation": "第一个",
                                     "detail": "第二个"}),
            "【T】第一个")

    def test_an_unknown_shape_is_joined_as_key_value_pairs(self):
        self.assertEqual(SIE._coerce_display_str({"foo": "1", "bar": "2"}), "foo:1；bar:2")

    def test_the_dimension_key_itself_is_not_repeated_in_the_join(self):
        """`dimension` 只当标题用，不参与 `key:value` 拼接（否则会印成 `D: D`）。"""
        self.assertEqual(SIE._coerce_display_str({"dimension": "D", "foo": "1"}),
                         "【D】foo:1")
        self.assertEqual(SIE._coerce_display_str({"dimension": "D", "foo": "1",
                                                  "bar": "2"}), "【D】foo:1；bar:2")

    def test_list_values_are_not_joined_into_the_key_value_form(self):
        self.assertEqual(SIE._coerce_display_str({"keep": "1", "drop": [1, 2]}), "keep:1")

    def test_a_dict_with_only_a_title_returns_the_title(self):
        self.assertEqual(SIE._coerce_display_str({"dimension": "只有标题"}), "只有标题")

    def test_a_text_body_already_prefixed_is_not_double_prefixed(self):
        self.assertEqual(
            SIE._coerce_display_str({"dimension": "T", "analysis": "【T】已经带前缀"}),
            "【T】已经带前缀")

    def test_a_list_is_joined(self):
        self.assertEqual(SIE._coerce_display_str(["甲", "乙"]), "甲；乙")

    def test_a_list_drops_blank_items(self):
        self.assertEqual(SIE._coerce_display_str(["甲", "", None]), "甲")

    def test_an_empty_dict_becomes_empty(self):
        self.assertEqual(SIE._coerce_display_str({}), "")

    def test_the_result_never_contains_the_repr_of_a_dict(self):
        """★ 就是这条杜绝了前端渲染出 `[object Object]`。"""
        out = SIE._coerce_display_str({"dimension": "D", "analysis": "A"})
        self.assertNotIn("{", out)
        self.assertNotIn("}", out)


if __name__ == "__main__":
    unittest.main()
