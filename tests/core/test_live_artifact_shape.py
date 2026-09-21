"""生产数据产物的**形状预检**（第 48 刀）。

## 为什么要有这一类

《失败语义手册》钉的是"读不到 ≠ 没有"；而本类钉的是**"读到了、但形状不对"**——
两者危害同源：形状不对会让下游做出错误决策，或直接在**周期中途**抛异常
（其后相位整段跳过）。

生产数据文件是**可被手工编辑/被外部进程写入**的：一次手改把
`"scale_count": null` 写进去，入场循环的 `int(tracker.get("scale_count", 0))`
就会 `TypeError`；把意图文件写成 `{...}` 而非 `[...]`，`load_open_intents` 会
fail-closed（已修，但周期会停摆）。本类**只读**预检这些文件，把"形状不对"在
**开跑之前**就暴露出来。

## 判据只钉代码自己保证的不变量

避免把"业务上合理的罕见值"误判成缺陷 ⇒ 只断言结构不变量（类型/键名/单调性），
不断言业务取值。每条违规都带**后果说明**，便于直接定位下游影响。

## 空转防护

每个校验器都自带"**能抓到构造出来的违规**"的自检（永久负例），
且活体检查会记录实际校验了多少条 —— 不做"没读到 ⇒ 恒过"。
"""
from __future__ import annotations

import json
import re
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trader.data_shape import (  # noqa: E402  （单一事实源：校验器在运行时模块里）
    validate_intents,
    validate_trackers,
)

INTENTS = ROOT / "data" / "open_order_intents.json"
TRACKERS = ROOT / "data" / "position_trackers.json"

#: 追踪器键约定：`f"{instId}_{side}"`（见 factors/position_universe）






_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（第二百三十七刀）：
    本文件的名字就是它的目的：核对**线上产物**（data/*.json）的**形状**是否符合契约
    （只查结构，不断言具体内容/标的）—— 有意的线上守卫。

    只读、不改；声明在此把「依赖线上配置内容」从**静默**变成**可审计**
    （未声明时 `R20_TESTS_STRICT_READS=1` 会报错）。
    """
    global _READ_SCOPE
    from tests import allow_real_data_reads
    _READ_SCOPE = allow_real_data_reads()
    _READ_SCOPE.__enter__()


def tearDownModule():
    global _READ_SCOPE
    if _READ_SCOPE is not None:
        _READ_SCOPE.__exit__(None, None, None)
        _READ_SCOPE = None


class LiveArtifactShapeTest(unittest.TestCase):
    """活体预检（只读）。文件不存在时跳过 —— 但校验器的自检始终执行。"""

    def _load(self, path: Path):
        if not path.exists():
            self.skipTest(f"{path.name} 不存在（全新环境）")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_live_intents_shape(self):
        bad, checked = validate_intents(self._load(INTENTS))
        self.assertEqual(bad, [], "活意图文件形状不合规：\n" + "\n".join(bad))
        self.assertGreaterEqual(checked, 0)

    def test_live_trackers_shape(self):
        bad, checked = validate_trackers(self._load(TRACKERS))
        self.assertEqual(bad, [], "活追踪文件形状不合规：\n" + "\n".join(bad))
        self.assertGreaterEqual(checked, 0)


class ValidatorSelfCheckTest(unittest.TestCase):
    """永久负例：校验器必须能抓到**构造出来**的违规（防空转 / 防判据坏死）。"""

    def test_intents_validator_catches_structural_violations(self):
        cases = {
            "非 list 顶层": {"instId": "BTC"},
            "缺 instId": [{"side": "buy", "ts": 1}],
            "ts 非整数": [{"instId": "A", "side": "buy", "ts": "now"}],
            "ts 在未来": [{"instId": "A", "side": "buy", "ts": int(time.time() * 1000) + 10**7}],
            "重复对": [{"instId": "A", "side": "buy", "ts": 1},
                       {"instId": "A", "side": "buy", "ts": 2}],
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                bad, _ = validate_intents(payload)
                self.assertTrue(bad, f"校验器漏掉了违规：{name}")

    def test_intents_validator_accepts_a_well_formed_file(self):
        good = [{"instId": "XRP-USDT-SWAP", "side": "sell", "ts": int(time.time() * 1000) - 1000}]
        bad, checked = validate_intents(good)
        self.assertEqual(bad, [])
        self.assertEqual(checked, 1)

    def test_trackers_validator_catches_the_typeerror_landmine(self):
        """最关键的一条：`scale_count: null` 会让入场循环 TypeError（周期中途中断）。"""
        bad, _ = validate_trackers({"BTC-USDT-SWAP_long": {"scale_count": None}})
        self.assertTrue(any("scale_count" in b for b in bad), bad)
        bad, _ = validate_trackers({"BTC-USDT-SWAP_long": {"trailingStopPx": "80000"}})
        self.assertTrue(any("trailingStopPx" in b for b in bad), bad)
        bad, _ = validate_trackers({"BTC_USDT_long": {}})   # 下划线拼写（非本仓约定）
        self.assertTrue(any("约定" in b for b in bad), bad)
        good = {"BTC-USDT-SWAP_long": {"scale_count": 1, "trailingStopPx": 80000.0,
                                       "entryTs": 1789000000}}
        self.assertEqual(validate_trackers(good)[0], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

class RuntimePreflightStageTest(unittest.TestCase):
    """运行时预检阶段：**只警告、不阻断、不写盘**（第 49 刀）。

    与加载侧的分工：加载侧负责行为（fail-closed / 拒绝覆盖），本阶段负责**可见性**
    —— 把"读得到却会被静默忽略"的形状问题指名道姓（典型：追踪器键名拼写不合约定
    ⇒ 水位查找落空、状态静默丢失，却不抛任何异常）。
    """

    def setUp(self):
        import tempfile
        from tests.config_sandbox import isolate_config
        isolate_config(self)
        self.tmp = tempfile.TemporaryDirectory(prefix="shape-preflight-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _stage(self, intents=None, trackers=None):
        import io
        from contextlib import redirect_stdout
        from scripts.trader.cycle_stages import data_shape_preflight_stage
        from scripts.trader.data_shape import (validate_intents_file,
                                               validate_trackers_file)
        ip, tp = self.base / "intents.json", self.base / "trackers.json"
        for path, payload in ((ip, intents), (tp, trackers)):
            if payload is not None:
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = data_shape_preflight_stage(
                intents_path=ip, trackers_path=tp,
                validate_intents_file=validate_intents_file,
                validate_trackers_file=validate_trackers_file)
        return out, buf.getvalue()

    def test_conforming_files_are_reported_as_clean(self):
        out, text = self._stage(intents=[{"instId": "A", "side": "buy", "ts": 1}],
                                trackers={"A_long": {"scale_count": 0}})
        self.assertEqual(out, [])
        self.assertIn("合规", text)

    def test_bad_shape_warns_with_consequence_and_does_not_raise(self):
        """关键契约：**只警告不阻断**（预检不得变成新的单点故障）。"""
        out, text = self._stage(intents={"oops": "not a list"},
                                trackers={"A_long": {"scale_count": None}})
        self.assertTrue(any("意图" in line for line in out))
        self.assertTrue(any("scale_count" in line for line in out))
        self.assertIn("TypeError", text, "违规必须带**下游后果**，否则读者不知道为什么要管")
        self.assertTrue(all(line.startswith("[数据形状预检]") for line in out))

    def test_unreadable_file_is_reported_not_swallowed(self):
        (self.base / "intents.json").write_text("{ 半截 JSON", encoding="utf-8")
        out, _ = self._stage(intents=None, trackers={})
        self.assertTrue(any("读不出来" in line for line in out),
                        "读不出来必须报（§手册：读不到 ≠ 没有）")

    def test_missing_files_are_a_legit_empty_state(self):
        out, _ = self._stage(intents=None, trackers=None)
        self.assertEqual(out, [], "文件不存在是合法空态，不得报警")

    def test_stage_never_writes_anything(self):
        (self.base / "trackers.json").write_text("{}", encoding="utf-8")
        before = (self.base / "trackers.json").read_bytes()
        self._stage(intents={"bad": 1}, trackers={})
        self.assertEqual((self.base / "trackers.json").read_bytes(), before,
                         "预检不得写盘（它是报告器，不是判定器）")
