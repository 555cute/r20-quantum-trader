"""跨所同向敞口闸门的**跨所**语义门（第一百一十一刀）。

## 真事故：一个名叫"跨所"、实际只算一所的活闸门

`r20_backend/execution_router.py` 里敞口闸门的 `positions_reader` 一直是
`ad.positions()` —— **只读被下单的那一个场所**。而闸门文档写的是
"跨所**同向名义额合计**超限则拒开"。

关键：这**不是死代码**。`.env` 里 `R20_MAX_TOTAL_EXPOSURE_USDT=3000.0` 是真的配了的
（`scripts/risk_constants.py` 在 cron/手动路径下显式加载 `.env`；本机实测
`TOTAL_EXPOSURE_CAP == 3000.0`）。三所平权后，每所各自只算自己那份 ⇒
**合计上限最多可被突破到 3 倍**（如 binance 2500U + gate 2500U 各自"没超 3000U"）。

⚠️ 更隐蔽的是：本该提醒"闸门首次生效"的那道护栏
（`tests/extraction/...::ProductionCapStillDisabledTest`）断言的是 `risk_constants`
里的值，而 pytest 做了环境隔离 ⇒ 它**看不见生产 `.env`**，于是这道护栏一直"安全通过"。
本门把该护栏改成**直接读 `.env`** 的诚实版本。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from r20_backend.execution.risk_gates import check_total_exposure  # noqa: E402


_READ_SCOPE = None


def setUpModule():
    """显式声明生产读（第二百三十七刀）：
    本文件对照**线上 `.env`** 的跨所敞口上限配置，核对闸门语义与线上配置一致
    —— 有意的线上守卫。

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


def _fail(stage, detail, venue="gate", **extra):
    return {"stage": stage, "detail": detail, "venue": venue, **extra}


class CrossVenueSumTest(unittest.TestCase):
    def _run(self, rows, *, cap=3000.0, action="SELL_SHORT", margin=300.0, leverage=5.0):
        return check_total_exposure(
            venue="gate", asset="BTC", action=action, margin=margin, leverage=leverage,
            total_exposure_cap=cap, all_positions=rows,
            positions_reader=lambda: rows, fail_factory=_fail)

    def test_same_side_exposure_sums_across_venues(self):
        """**本刀的核心回归**：别所的 2000U 必须计入，本单 1500U ⇒ 3500U > 3000U 拒开。

        修前：只读下单场所 ⇒ same_side=0 ⇒ 1500U < 3000U ⇒ **放行**（上限被突破）。
        """
        rows = [{"base": "BTC", "side": "short", "size_signed": -1.0,
                 "mark_price": 2000.0, "venue": "binance"}]
        r = self._run(rows)
        self.assertIsNotNone(r, "别所同向敞口必须计入合计")
        self.assertEqual(r["stage"], "exposure")
        self.assertEqual(r["contributing_venues"], ["binance"],
                         "拒开理由必须说清同向敞口来自哪个场所")
        self.assertIn("binance", r["detail"])

    def test_three_venues_can_break_a_single_venue_cap(self):
        """三所各 1100U 同向（各自"没超"3000U）⇒ 合计 3300U + 本单 ⇒ 必须拒。"""
        rows = [{"base": "BTC", "side": "short", "size_signed": -1.0, "mark_price": 1100.0,
                 "venue": v} for v in ("binance", "gate", "okx")]
        r = self._run(rows, margin=0.0, leverage=0.0)
        self.assertIsNotNone(r)
        self.assertEqual(sorted(r["contributing_venues"]), ["binance", "gate", "okx"])

    def test_other_asset_and_opposite_side_are_not_counted(self):
        rows = [{"base": "ETH", "side": "short", "size_signed": -10.0, "mark_price": 5000.0,
                 "venue": "binance"},
                {"base": "BTC", "side": "long", "size_signed": 10.0, "mark_price": 5000.0,
                 "venue": "gate"}]
        self.assertIsNone(self._run(rows), "别币/反向都不该计入同向敞口")

    def test_disabled_cap_never_reads_positions(self):
        """闸门停用（cap=0）时**不得**产生任何取数（本刀新增跨所读取的代价边界）。"""
        def _boom():
            raise AssertionError("cap=0 时不该读持仓（会白增网络调用与失败面）")
        self.assertIsNone(check_total_exposure(
            venue="gate", asset="BTC", action="SELL_SHORT", margin=1.0, leverage=1.0,
            total_exposure_cap=0.0, all_positions=None, positions_reader=_boom,
            fail_factory=_fail))

    def test_read_failure_is_fail_closed(self):
        r = check_total_exposure(
            venue="gate", asset="BTC", action="SELL_SHORT", margin=1.0, leverage=1.0,
            total_exposure_cap=3000.0, all_positions=None,
            positions_reader=lambda: (_ for _ in ()).throw(RuntimeError("binance 凭证未配置")),
            fail_factory=_fail)
        self.assertIsNotNone(r)
        self.assertEqual(r["stage"], "exposure")
        self.assertIn("无法读取持仓", r["detail"])


class VenueSelectionTest(unittest.TestCase):
    """场所选择：按**凭证齐备（本部署参与）**纳入，并把未计场所如实报出。"""

    def test_uncredentialed_venue_is_skipped_not_fatal(self):
        """凭证未配置的所不得让下单路径整体崩掉（本刀第一版就踩了这个）。

        实测形状：读未配凭证的所抛 `ExchangeCapabilityError` ⇒ 若按 fail-closed
        处理，**所有**单都被拒。正确语义：本部署不在这里交易 ⇒ 不计入，但留痕。
        """
        from r20_backend import execution_router as er
        with patch("r20_backend.exchanges.registry.registered_venues",
                   lambda: ["okx", "binance", "gate"]), \
             patch("r20_backend.exchanges.registry.venue_credentials",
                   lambda v, e=None: ("", "") if v == "binance" else ("k", "s")):
            counted, skipped = er._exposure_venues("gate", "demo")
        self.assertEqual(counted, ["gate", "okx"])
        self.assertEqual(skipped, ["binance(凭证未配置)"], "未计场所必须留痕")

    def test_okx_is_included_even_though_execution_open_is_false(self):
        """⚠️ 本刀实测的第二处坑：OKX 直签链路 ⇒ `execution_open("okx")` 恒 False，
        但它持仓最多。按开闸判会把 OKX **整个漏掉** ⇒ 判据必须是凭证而非开闸。"""
        from r20_backend import execution_router as er
        with patch("r20_backend.exchanges.registry.registered_venues",
                   lambda: ["okx", "binance", "gate"]), \
             patch("r20_backend.exchanges.registry.execution_open",
                   lambda v, e=None: False), \
             patch("r20_backend.exchanges.registry.venue_credentials",
                   lambda v, e=None: ("k", "s")):
            counted, _ = er._exposure_venues("gate", "demo")
        self.assertIn("okx", counted,
                      "OKX 走直签链路、execution_open 恒 False，但必须在跨所统计内")

    def test_entering_venue_is_always_counted(self):
        from r20_backend import execution_router as er
        with patch("r20_backend.exchanges.registry.registered_venues", lambda: []):
            counted, skipped = er._exposure_venues("gate", "demo")
        self.assertEqual(counted, ["gate"], "本次场所恒计入（哪怕枚举失败）")
        self.assertEqual(skipped, [])


class ProductionEnvGuardTest(unittest.TestCase):
    """诚实版护栏：**直接读 `.env`**，而不是读被 pytest 隔离过的 `os.environ`。"""

    def test_env_cap_and_gate_semantics_are_consistent(self):
        env_file = ROOT / ".env"
        if not env_file.exists():
            self.skipTest("无 .env（干净检出）")
        cap = None
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("R20_MAX_TOTAL_EXPOSURE_USDT="):
                try:
                    cap = float(line.split("=", 1)[1].strip().strip('"').strip("'"))
                except ValueError:
                    cap = None
        if cap in (None, 0.0):
            self.skipTest("生产未启用敞口上限 → 本闸门停用")
        # 生产**已启用**上限 ⇒ 必须确认闸门真的是"跨所"口径，否则上限形同虚设。
        src = (ROOT / "r20_backend" / "execution_router.py").read_text(encoding="utf-8")
        self.assertIn("_exposure_venues(", src,
                      f"生产 .env 已配置 R20_MAX_TOTAL_EXPOSURE_USDT={cap} —— "
                      "敞口闸门处于**生效**状态，必须是跨所口径（否则上限最多被突破到 N 倍）")
        self.assertIn("registered_venues", src, "跨所场所必须注册表驱动，不得硬编码")


class RealCoverageTest(unittest.TestCase):
    """只读核验：真实部署下这个"跨"到底跨到哪几所（环境相关，缺配置即跳过）。"""

    def test_counted_venues_are_reported_for_reals(self):
        from r20_backend import execution_router as er
        counted, skipped = er._exposure_venues("gate", "demo")
        self.assertIn("gate", counted)
        for v in skipped:
            self.assertTrue(v.startswith(tuple(["okx", "binance", "gate"])), skipped)
        if len(counted) == 1 and skipped:
            self.skipTest(f"当前仅参与 {counted}，未计 {skipped}（覆盖率已如实留痕）")


if __name__ == "__main__":
    unittest.main()
