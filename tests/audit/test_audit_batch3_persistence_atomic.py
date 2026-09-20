"""批3 持久化原子化（审计 2026-09-13）回归钉。

方法论：原子性用「中途爆炸」证明——patch json.dump 在写临时文件时抛异常，
断言目标文件字节级原样（旧 open("w") 写法此刻已被截断）。
覆盖：
- ③#1 trader record_trade（台账）原子化
- ③#2 stop_cooldown 单文件统一 + 损坏 fail-closed + 拒覆盖现场
- ③#3 venue_health / ③#4 trading_state / instrument_pool 扇出 原子化
- ③#5 harvester 熔断原子化 + 损坏自愈（不再无限期停摆）
- ③#6 read_json 损坏≠缺失（日志吼）；daily briefing 拒发假 0
- ③#7 备份 prune 按 job 隔离；staging 不误删在途
- ③#12 backup_methods 损坏 → 写面熔断
- ③#9 decisions flock（trader RMW 与 brain 整写互斥）
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _explode(*a, **k):
    raise RuntimeError("mid-write explosion")


class TestAtomicHelpers(unittest.TestCase):
    def test_trader_atomic_write_json_rollback_safety(self):
        import scripts.ai_factor_trader as aft
        d = tempfile.mkdtemp(prefix="r20-b3-")
        target = os.path.join(d, "x.json")
        with open(target, "w") as f:
            f.write('{"old": true}')
        original = open(target).read()
        with patch("json.dump", side_effect=_explode):
            with self.assertRaises(RuntimeError):
                aft._atomic_write_json(target, {"new": 1})
        self.assertEqual(open(target).read(), original)         # 旧文件原样
        self.assertEqual([p for p in os.listdir(d) if p.startswith(".")], [])  # 无 .tmp 残留
        aft._atomic_write_json(target, {"new": 1})
        self.assertEqual(json.load(open(target)), {"new": 1})   # 正常路径

    def test_harvester_and_pool_helpers_exist(self):
        import news_sentiment_harvester as nh
        import instrument_pool as ip
        self.assertTrue(callable(nh._atomic_write_json))
        self.assertTrue(callable(ip._write_json_atomic))


class TestLedgerRecordTradeAtomic(unittest.TestCase):
    def test_record_trade_never_truncates_ledger(self):
        import scripts.ai_factor_trader as aft
        d = tempfile.mkdtemp(prefix="r20-b3-ledger-")
        ledger = os.path.join(d, "trading_ledger.json")
        with open(ledger, "w") as f:
            json.dump([{"status": "closed", "pnl": 1.0}], f)
        original = open(ledger).read()
        with patch.object(aft, "LEDGER_JSON_FILE", ledger), \
             patch("scripts.ai_factor_trader._atomic_write_json", side_effect=_explode), \
             redirect_stdout(io.StringIO()):
            aft.record_trade({"status": "closed", "pnl": 2.0})   # 内部吞异常仅打印
        self.assertEqual(open(ledger).read(), original)          # 事故=本笔未入账，绝不撕裂
        with patch.object(aft, "LEDGER_JSON_FILE", ledger), redirect_stdout(io.StringIO()):
            aft.record_trade({"status": "closed", "pnl": 2.0})
        self.assertEqual(len(json.load(open(ledger))), 2)


class TestStopCooldownUnification(unittest.TestCase):
    def test_file_names_unified(self):
        import scripts.ai_factor_trader as aft
        import r20_backend.execution.circuit_breaker as cb
        self.assertEqual(os.path.basename(aft.STOP_COOLDOWN_FILE),
                         Path(cb.STOP_COOLDOWN_FILE).name)      # 单数活文件归一
        self.assertEqual(Path(cb.STOP_COOLDOWN_FILE).name, "stop_cooldown.json")

    def _sandbox(self, mod, tmp, as_path=False):
        f = Path(os.path.join(tmp, "stop_cooldown.json")) if as_path else os.path.join(tmp, "stop_cooldown.json")
        return patch.object(mod, "STOP_COOLDOWN_FILE", f)

    def test_corrupt_means_in_cooldown_fail_closed(self):
        import scripts.ai_factor_trader as aft
        import r20_backend.execution.circuit_breaker as cb
        for mod, as_path in ((aft, False), (cb, True)):
            tmp = tempfile.mkdtemp(prefix="r20-b3-cd-")
            with self._sandbox(mod, tmp, as_path):
                f = mod.STOP_COOLDOWN_FILE
                with open(f, "w") as h:
                    h.write("{half-corrupt")
                self.assertTrue(mod.is_in_stop_cooldown("BTC-USDT-SWAP", "long"),
                                f"{mod.__name__}: 损坏必须按『仍在冷却』而非『无冷却』")

    def test_add_refuses_overwrite_corrupt_scene(self):
        import scripts.ai_factor_trader as aft
        tmp = tempfile.mkdtemp(prefix="r20-b3-cd2-")
        with self._sandbox(aft, tmp), redirect_stdout(io.StringIO()) as buf:
            f = str(aft.STOP_COOLDOWN_FILE)
            with open(f, "w") as h:
                h.write("{half")
            aft.add_stop_cooldown("BTC-USDT-SWAP", "long")
            self.assertEqual(open(f).read(), "{half")           # 现场保全
            self.assertIn("CRITICAL", buf.getvalue())            # 必吼

    def test_live_roundtrip_and_cross_visibility(self):
        import scripts.ai_factor_trader as aft
        import r20_backend.execution.circuit_breaker as cb
        tmp = tempfile.mkdtemp(prefix="r20-b3-cd3-")
        f = os.path.join(tmp, "stop_cooldown.json")
        with patch.object(aft, "STOP_COOLDOWN_FILE", f), \
             patch.object(cb, "STOP_COOLDOWN_FILE", Path(f)):
            aft.add_stop_cooldown("ETH-USDT-SWAP", "short", "测试冷却")
            self.assertTrue(cb.is_in_stop_cooldown("ETH-USDT-SWAP", "short"),
                            "trader 写的冷却后端必须可见（同文件同 schema）")
            cb.add_stop_cooldown("SOL-USDT-SWAP", "long")
            self.assertTrue(aft.is_in_stop_cooldown("SOL-USDT-SWAP", "long"), "反向同理")
            self.assertFalse(aft.is_in_stop_cooldown("BTC-USDT-SWAP", "long"))


class TestHarvesterBreakerSelfHeal(unittest.TestCase):
    def test_trigger_is_atomic_and_readable(self):
        import news_sentiment_harvester as nh
        tmp = tempfile.mkdtemp(prefix="r20-b3-cb-")
        f = os.path.join(tmp, "circuit_breaker.json")
        with patch.object(nh, "CIRCUIT_BREAKER_FILE", f):
            nh.trigger_circuit_breaker("BTC 交易所跑路", "exchange-collapse")
            data = json.load(open(f))
            self.assertTrue(data["active"])
            self.assertEqual([p for p in os.listdir(tmp) if p.startswith(".")], [])

    def test_corrupt_file_self_heals_not_deadlock(self):
        # 审计③#5 的核心事故：损坏 → 读者每轮误停，清除路径 except:pass 永不自愈
        import news_sentiment_harvester as nh
        tmp = tempfile.mkdtemp(prefix="r20-b3-cb2-")
        f = os.path.join(tmp, "circuit_breaker.json")
        with open(f, "w") as h:
            h.write("[trunc")
        # 直接驱动清除段语义（等价复刻主循环 else 分支的自愈重写）
        try:
            json.load(open(f))
            healed = False
        except Exception:
            nh._atomic_write_json(f, {"active": False, "self_healed_at": 1, "note": "x"})
            healed = True
        data = json.load(open(f))
        self.assertTrue(healed and data["active"] is False)
        src = Path(ROOT / "scripts" / "news_sentiment_harvester.py").read_text(encoding="utf-8")
        self.assertIn("熔断自愈", src)  # 主循环清除路径的自愈分支在场
        self.assertNotIn('cb_data["active"] = False\n                    with open(CIRCUIT_BREAKER_FILE, "w"', src)


class TestReadJsonCorruptVsMissing(unittest.TestCase):
    def test_decode_error_logs_critically(self):
        from r20_backend import dependencies as dep
        tmp = tempfile.mkdtemp(prefix="r20-b3-rj-")
        (Path(tmp) / "broken.json").write_text("{nope", encoding="utf-8")
        with patch.object(dep, "DATA_DIR", Path(tmp)), patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(dep.read_json("broken.json", []), [])
        self.assertIn("CRITICAL", err.getvalue())
        self.assertIn("损坏", err.getvalue())
        with patch.object(dep, "DATA_DIR", Path(tmp)), patch("sys.stderr", new_callable=io.StringIO) as err2:
            self.assertEqual(dep.read_json("never_existed.json", []), [])   # 缺失
        self.assertNotIn("CRITICAL", err2.getvalue())                       # 静默合法


class TestDailyBriefingNoFakeZero(unittest.TestCase):
    def _brief(self, ledger_content):
        import daily_summary_and_backup as ds
        tmp = tempfile.mkdtemp(prefix="r20-b3-ds-")
        ledger = os.path.join(tmp, "trading_ledger.json")
        with open(ledger, "w") as f:
            f.write(ledger_content)
        pushed = []
        with patch.object(ds, "LEDGER_JSON_FILE", ledger), \
             patch.object(ds, "notify_daily_summary", lambda t: pushed.append(t)), \
             patch.object(ds, "subprocess", type("S", (), {"run": staticmethod(lambda *a, **k: None)})), \
             redirect_stdout(io.StringIO()):
            text = ds.generate_daily_briefing_and_backup()
        return text, pushed

    def test_corrupt_ledger_pushes_fault_not_zero(self):
        text, pushed = self._brief("{trunc")
        self.assertIn("台账文件损坏", text)
        self.assertIn("台账文件损坏", pushed[0])

    def test_healthy_empty_ledger_still_reports_normally(self):
        text, pushed = self._brief("[]")
        self.assertNotIn("台账文件损坏", text)
        self.assertIn("今日平仓战绩", text)


class TestBackupPruneJobIsolation(unittest.TestCase):
    def test_other_jobs_archives_survive(self):
        import backup_runtime as br
        root = Path(tempfile.mkdtemp(prefix="r20-b3-bk-"))
        (root / "backups").mkdir()
        dest = root / "backups" / "local"
        dest.mkdir()
        staging = root / "backups" / "staging"
        staging.mkdir()
        a = dest / "r20_backup_jobA_20260913_010000.tar.gz"
        b = staging / "r20_backup_jobB_20260913_020000.tar.gz"  # 真实流：源在 staging
        a.write_bytes(b"x")
        b.write_bytes(b"y")
        import os as _os
        _os.utime(a, (1_000_000_000, 1_000_000_000))  # A 更新（mtime 更大）
        _os.utime(b, (999_999_900, 999_999_900))
        with patch.object(br, "ROOT", root):          # 守卫目录重定向到沙箱
            br.retain_local_archive(b, retention=0, destination_dir=dest)
        # 旧行为：retention=0 会把目录里所有 r20_backup_* 全删（包括 A 刚生成的）
        self.assertTrue(a.exists(), "任务 B 的 prune 不得触碰任务 A 的归档")
        self.assertFalse((dest / b.name).exists(), "本任务超额归档照旧被裁")

    def test_staging_keeps_fresh_inflight(self):
        import backup_runtime as br
        root = tempfile.mkdtemp(prefix="r20-b3-st-")
        staging = Path(root) / "staging"
        staging.mkdir()
        inflight = staging / "r20_backup_jobX_20260913_030000.tar.gz"
        inflight.write_bytes(b"")                     # 并发备份刚 mkstemp（0 字节）
        stale = staging / "r20_backup_jobY_20200101_000000.tar.gz"
        stale.write_bytes(b"old")
        import os as _os
        _os.utime(stale, (1_000, 1_000))
        old_backups = br.BACKUPS
        br.BACKUPS = Path(root)
        try:
            cleaned = br.clean_stale_staging()
        finally:
            br.BACKUPS = old_backups
        self.assertTrue(inflight.exists(), "0 字节在途归档不得当垃圾秒删")
        self.assertFalse(stale.exists(), "过期残留照旧回收")


class TestBackupConfigCorruptCircuit(unittest.TestCase):
    def test_corrupt_blocks_default_overwrite(self):
        import r20_backend.backup_store as bs
        tmp = Path(tempfile.mkdtemp(prefix="r20-b3-bm-"))
        cfg = tmp / "backup_methods.json"
        cfg.write_text("{corrupt", encoding="utf-8")
        with patch.object(bs, "CONFIG_FILE", cfg), patch("sys.stderr", new_callable=io.StringIO) as err:
            loaded = bs.load_backup_config()
        self.assertIn("CRITICAL", err.getvalue())
        with self.assertRaises(ValueError):
            bs.save_backup_config(loaded)                      # 拒以默认档为底覆盖
        cfg.write_text('{"version":2,"jobs":[]}', encoding="utf-8")
        bs.load_backup_config()                                # 可解 → 熔断解除
        self.assertFalse(bs._LOAD_WAS_CORRUPT)


class TestDecisionsFlock(unittest.TestCase):
    def test_both_writers_take_lock(self):
        import inspect
        import scripts.ai_factor_trader as aft
        import scripts.ai_brain_trader as abr
        # 第八十二刀起 persist 真实现住 `scripts/trader/venue_evidence.py`
        # （门面只剩调用期转发壳）。断言对象跟随搬家，意图不变：
        # **写路径的 flock 包裹必须真实存在** —— 门面壳文本必须不含它（防虚 Hits）。
        import scripts.trader.venue_evidence as _ve
        self.assertIn("file_lock(AI_DECISION_CACHE_FILE)",
                      inspect.getsource(_ve.persist_venue_decision))
        self.assertNotIn("file_lock(AI_DECISION_CACHE_FILE)",
                         inspect.getsource(aft.persist_venue_decision),
                         "门面壳里出现 flock 文本会虚 Hits 上面断言")
        # 第九十八刀：该 flock 随"派发+落盘"尾块迁入 scripts/brain/dispatch.py
        # （判定对象随实现迁移；原意不变：写路径必须真的被 flock 包裹）
        import scripts.brain.dispatch as _bd
        self.assertIn("file_lock(AI_DECISION_CACHE_FILE)",
                      inspect.getsource(_bd.dispatch_llm_and_persist_decisions))
        self.assertNotIn("file_lock(AI_DECISION_CACHE_FILE)",
                         inspect.getsource(abr.execute_batch_ai_brain_cycle),
                         "门面函数里出现 flock 文本会虚 Hits 上面断言")

    def test_lock_mutual_exclusion_semantics(self):
        from r20_backend.file_locks import file_lock
        import fcntl
        target = os.path.join(tempfile.mkdtemp(prefix="r20-b3-fl-"), "d.json")
        with open(target, "w") as f:
            f.write("{}")
        with file_lock(target):
            # 持锁期间对同一 .lock 文件 NB 试探必须失败
            lp = os.path.join(os.path.dirname(target), "." + os.path.basename(target) + ".lock")
            fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)


class TestVenueHealthAndStateAtomicSource(unittest.TestCase):
    def test_no_raw_open_w_on_decisions_family(self):
        # 阶段4·B3 把跨所健康度落盘搬进了 scripts/brain/xvenue.py（门面只留薄壳）。
        # 原来只读门面单文件的断言在搬走后会**空转通过**（assertNotIn 在搬走的文件上
        # 必然为真）。改为按「主脑域运行时源码集」定位：断言强度不变（同一个 needle），
        # 覆盖面反而更广 —— 门面或子包任一处置回裸 open 都会响。
        from tests import source_scan
        brain = source_scan.combined("scripts/ai_brain_trader.py", pkg_name="brain")
        source_scan.assert_area_looks_real(
            self, brain, must_contain="def construct_full_market_prompt", min_chars=60000)
        self.assertNotIn('open(VENUE_HEALTH_FILE, "w"', brain)   # ③#3 改道 atomic_write_json
        # 领域定位：执行层源码已按 B3 拆进 scripts/trader/，单文件定位会在搬家后假红。
        from tests.source_scan import combined
        trader = combined("scripts/ai_factor_trader.py", pkg_name="trader")
        self.assertIn('_atomic_write_json(os.path.join(DATA_DIR, "trading_state.json")', trader)


class TraderSlotGuardAtomicityTest(unittest.TestCase):
    """同槽去重守卫：**原子写 + 读不到要吼**（第一百三十六刀）。

    背景：`single_trader_cycle` 的守卫此前内联在装饰器里，两处语义无法单独测试：
    ①写用非原子 `open(..., "w")`（先截断再写）⇒ 写崩留 0 字节/半截 JSON；
    ②读分支 `except Exception: pass` **静默**吞掉损坏 ⇒ 同槽去重**静默失效**
    （同一 15 分钟槽可能跑两轮、重复处理同一批信号）。现抽成
    `_slot_guard_should_skip` 并用本模块既有的 `_atomic_write_json`。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="slot-guard-")
        self.addCleanup(self.tmp.cleanup)
        self.slot = os.path.join(self.tmp.name, "slot.json")

    def _guard(self, now_slot):
        import scripts.ai_factor_trader as aft
        with patch.object(aft, "TRADER_SLOT_FILE", self.slot):
            buf = io.StringIO()
            with redirect_stdout(buf):
                skip = aft._slot_guard_should_skip(now_slot)
        return skip, buf.getvalue()

    def _write(self, text):
        with open(self.slot, "w", encoding="utf-8") as f:
            f.write(text)

    def test_first_sight_allows_and_records(self):
        skip, out = self._guard(1000)
        self.assertFalse(skip)
        with open(self.slot, encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["slot"], 1000)
        self.assertEqual(out, "", "正常路径不该刷告警")

    def test_same_slot_recent_trigger_is_skipped(self):
        self._guard(1000)
        skip, out = self._guard(1000)
        self.assertTrue(skip, "同槽 + 刚启动 ⇒ 必须判为重复触发")
        self.assertIn("duplicate trigger", out)

    def test_unreadable_state_warns_loudly_and_still_runs(self):
        """读不到 ⇒ **仍放行**（不因一个状态文件停实盘），但绝不静默。"""
        self._write("{ 这不是 JSON")
        skip, out = self._guard(2000)
        self.assertFalse(skip, "不因状态文件损坏而停交易（可用性优先，但要吼）")
        self.assertIn("同槽去重状态不可读", out, "损坏必须吼出来，不得静默 pass")
        with open(self.slot, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["slot"], 2000, "随后应原子覆写成合法状态（自愈）")

    def test_write_crash_preserves_previous_state(self):
        """写崩 ⇒ 旧状态**字节级原样**（非原子直写会先截断，守卫就此失效）。"""
        good = json.dumps({"slot": 7, "started_at": 1, "pid": 1})
        self._write(good)
        import scripts.ai_factor_trader as aft
        with patch.object(aft, "TRADER_SLOT_FILE", self.slot), \
                patch("os.replace", side_effect=OSError("模拟写崩")), \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                aft._slot_guard_should_skip(3000)
        with open(self.slot, encoding="utf-8") as f:
            self.assertEqual(f.read(), good, "写崩必须保全旧状态（绝不截断）")
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n != "slot.json"], [],
                         "失败路径不得残留临时文件")


if __name__ == "__main__":
    unittest.main(verbosity=2)