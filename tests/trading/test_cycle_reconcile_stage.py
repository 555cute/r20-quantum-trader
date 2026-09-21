"""相位 1（`fetch_positions_and_reconcile`）的持仓汇总与「输入失败语义表」（第二百一十七刀）。

这段自带一张表（第一百二十八刀逐项实测），本刀按表造 13 项注入依赖的 harness 逐条钉：

| 输入 | 读失败行为 |
|---|---|
| OKX 持仓 `query_positions` | **整周期 abort** |
| OKX 挂单 `pending_orders` | **整周期 abort** |
| OKX 余额 `balances` | **整周期 abort** |
| 跨所持仓 `fetch_other_venue_positions` | `entries_blocked=True`（禁新开仓）+ 笔数显式「未知」 |
| 跨所挂单 `collect_pending_inst_ids` | 预留对账**不释放**（`venue_snapshot_verified=False`）；槽位少算要 warn |
| 凭证已死场所 | 跳过枚举 + **每周期明说未计入** |

外加持仓汇总本身：**只数有量的仓**、同一合约多空同时存在 ⇒ **abort**（无法表达）。
"""

import unittest

from scripts.trader.cycle_stages import fetch_positions_and_reconcile

GV = "gate"
BN = "binance"


class _Env:
    def __init__(self, mode="demo"):
        self.mode = mode


class _OkxRest:
    def __init__(self, *, pending=(), balances=None, pending_raises=None, bal_raises=None):
        self._pending = pending
        self._balances = [{"details": [{"ccy": "USDT", "availBal": "1234.5"}]}] \
            if balances is None else balances
        self._pending_raises = pending_raises
        self._bal_raises = bal_raises

    def pending_orders(self):
        if self._pending_raises is not None:
            raise self._pending_raises
        return list(self._pending)

    def balances(self):
        if self._bal_raises is not None:
            raise self._bal_raises
        return self._balances


class Rig:
    def __init__(self, *, positions=None, positions_ok=True, pending=(), xv=(True, {}, ""),
                 xv_broken=(), pending_enum_errors=(), balances=None, okx=None,
                 env_mode="demo", ready=True, pending_positions_raises=None,
                 bal_raises=None, env_raises=False, broken_raises=False):
        self.printed = []
        self.reconciles: list = []
        self.positions = positions if positions is not None else [
            {"instId": "BTC-USDT-SWAP", "pos": "2", "posSide": "long"}]
        self.positions_ok = positions_ok
        self.xv = xv
        self.okx = okx or _OkxRest(pending=pending, balances=balances,
                                   pending_raises=pending_positions_raises,
                                   bal_raises=bal_raises)
        self.ready = ready
        self.env_raises = env_raises
        self.broken_raises = broken_raises

    def run(self, *, entries_blocked=False):
        def _env():
            if self.env_raises:
                raise RuntimeError("env down")
            return _Env(self.env_mode)

        def _broken(*a, **k):
            if self.broken_raises:
                raise RuntimeError("probe boom")
            return list(self.xv_broken)

        def collect_pending_inst_ids(*, warn, **kw):
            for msg in (self._pending_errors if hasattr(self, "_pending_errors") else []):
                warn(msg)
            return (self._pending_ids, self._pending_long, self._pending_short)

        self._pending_errors, self._pending_ids, self._pending_long, self._pending_short = (
            getattr(self, "pending_enum_errors", []), {"XRP-USDT-SWAP"}, 1, 0)
        self._pending_ids_out = None
        out = fetch_positions_and_reconcile(
            entries_blocked=entries_blocked,
            _BROKEN_VENUES=("gate",),
            collect_pending_inst_ids=collect_pending_inst_ids,
            current_environment=_env,
            fetch_other_venue_positions=lambda env: self.xv,
            load_instruments=lambda: {},
            okx_rest=self.okx,
            query_positions=lambda: (
                (True, self.positions, "") if self.positions_ok else (False, [], "持仓读不动")),
            reconcile_reservation_ledger=lambda *a, **k: self.reconciles.append((a, k)),
            venue_execution_ready=lambda v, env: self.ready,
            broken_execution_venues=_broken,
            venue_registry=object())
        self._pending_ids_out = None
        return out


class PositionSummaryTest(unittest.TestCase):
    def setUp(self):
        self.env_mode = GV
        self.xv_broken = ()
        self.pending_enum_errors = []
        self.ready = True

    def _run(self, **kw):
        rig = Rig(env_mode=self.env_mode, xv_broken=self.xv_broken, ready=self.ready)
        rig.env_mode = self.env_mode
        rig.xv_broken = self.xv_broken
        rig.pending_enum_errors = self.pending_enum_errors
        rig.ready = self.ready
        for k, v in kw.items():
            setattr(rig, k, v)
        return rig, rig.run()

    def test_long_and_short_are_counted_separately(self):
        rig, out = self._run(positions=[
            {"instId": "BTC-USDT-SWAP", "pos": "2", "posSide": "long"},
            {"instId": "ETH-USDT-SWAP", "pos": "3", "posSide": "short"}])
        self.assertIsNotNone(out)
        self.assertEqual(out[4], 1, "多头计数")
        self.assertEqual(out[10], 1, "空头计数")
        self.assertEqual(out[1], 2, "活跃仓位数")
        self.assertEqual(sorted(out[6]), ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

    def test_zero_size_positions_are_not_counted(self):
        """数量为 0 的仓位行是「没有仓」，不许计入（否则槽位被虚占）。"""
        _, out = self._run(positions=[{"instId": "BTC-USDT-SWAP", "pos": "0", "posSide": "long"}])
        self.assertEqual(out[1], 0)
        self.assertEqual(out[4], 0)
        self.assertEqual(out[6], {})

    def test_simultaneous_long_and_short_aborts_the_cycle(self):
        """同一合约多空同时存在 ⇒ 本周期中止（系统无法表达这种仓位，硬猜会算错）。"""
        _, out = self._run(positions=[
            {"instId": "BTC-USDT-SWAP", "pos": "2", "posSide": "long"},
            {"instId": "BTC-USDT-SWAP", "pos": "1", "posSide": "short"}])
        self.assertIsNone(out, "同合约多空并存必须 abort，而不是挑一边记账")

    def test_net_side_position_counts_as_neither_long_nor_short(self):
        _, out = self._run(positions=[{"instId": "BTC-USDT-SWAP", "pos": "2", "posSide": "net"}])
        self.assertEqual(out[1], 1, "净持仓也要计入活跃仓位")
        self.assertEqual((out[4], out[10]), (0, 0), "net 既不是 long 也不是 short（不许硬归一边）")


class InputFailureSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.env_mode = GV
        self.xv_broken = ()
        self.pending_enum_errors = []
        self.ready = True

    def _rig(self, **kw):
        rig = Rig(**kw)
        rig.env_mode = self.env_mode
        rig.xv_broken = self.xv_broken
        rig.pending_enum_errors = self.pending_enum_errors
        rig.ready = self.ready
        return rig

    def test_positions_read_failure_aborts_the_cycle(self):
        out = self._rig(positions_ok=False).run()
        self.assertIsNone(out, "持仓读不到 ⇒ 整周期 abort（读不到 ≠ 没有仓）")

    def test_pending_orders_read_failure_aborts_the_cycle(self):
        out = self._rig(pending_positions_raises=RuntimeError("pending down")).run()
        self.assertIsNone(out, "挂单读不到 ⇒ 整周期 abort")

    def test_balance_read_failure_aborts_the_cycle(self):
        out = self._rig(bal_raises=RuntimeError("bal down")).run()
        self.assertIsNone(out, "余额读不到 ⇒ 整周期 abort（不许拿 0 余额硬算仓位）")

    def test_cross_venue_position_failure_blocks_entries_and_says_unknown(self):
        out = self._rig(xv=(False, {}, "binance 读失败")).run()
        self.assertIsNotNone(out)
        self.assertTrue(out[3], "跨所持仓读失败 ⇒ fail-closed 禁本轮新开仓")
        self.assertIsNone(out[0], "跨所笔数必须显示「未知」，绝不装 0")

    def test_cross_venue_positions_enter_the_quota(self):
        out = self._rig(xv=(True, {BN: [{"inst_id": "SOL", "side": "long", "size_signed": 3}]}, "")).run()
        self.assertIsNotNone(out)
        self.assertEqual(out[0], 1, "跨所笔数")
        # 同向配额 = OKX 多头(1) + 在途挂单多头(桩给 1) + 外所多头(1) = 3
        self.assertEqual(out[7], out[4] + 2, "外所多头与前两者一起进同向配额")
        self.assertEqual(out[9], out[1] + 1 + 1, "槽位要含外所持仓与在途挂单")

    def test_cross_venue_short_enters_the_short_quota(self):
        """外所**空头**要进同向（空）配额 —— 与多头同一张尺子，不许只算一半。"""
        out = self._rig(xv=(True, {BN: [{"inst_id": "SOL", "side": "short", "size_signed": -3}]},
                            "")).run()
        self.assertEqual(out[8], out[10] + 1, "外所空头进空向配额")
        self.assertEqual(out[7], out[4] + 1, "外所空头不该动多头配额")
        self.assertEqual(out[9], out[1] + 2, "槽位仍要含外所持仓与在途挂单")

    def test_pending_enumeration_failure_makes_the_snapshot_unverified(self):
        """跨所挂单枚举失败 ⇒ 预留对账**不许释放**（`venue_snapshot_verified=False`）。"""
        rig = self._rig()
        rig.pending_enum_errors = ["binance 挂单枚举失败"]
        out = rig.run()
        self.assertIsNotNone(out)
        self.assertTrue(rig.reconciles, "对账器必须被调用")
        self.assertFalse(rig.reconciles[0][1].get("venue_snapshot_verified"),
                         "挂单侧没核验成功 ⇒ 快照不算核验过（否则会误释放活单的预留）")

    def test_snapshot_verified_when_both_sides_ok(self):
        rig = self._rig()
        rig.run()
        self.assertTrue(rig.reconciles[0][1].get("venue_snapshot_verified"),
                        "持仓与挂单都读成功 ⇒ 快照才算核验过")

    def test_broken_credential_venues_are_disclosed_every_cycle(self):
        rig = self._rig()
        rig.xv_broken = [BN]
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rig.run()
        self.assertIn("凭证已死", buf.getvalue(), "凭证死的所必须每周期明说「未计入」")
        self.assertIn("未计入", buf.getvalue())

    def test_usdt_available_is_read_from_the_balance_payload(self):
        out = self._rig().run()
        self.assertEqual(out[11], 1234.5, "USDT 可用余额要真的从 balances 里读出来")



    def test_pending_orders_are_counted_and_non_live_orders_skipped(self):
        """在途挂单：只数 live/partially_filled；已撤/已成的不算在场（否则槽位虚占）。"""
        rig = Rig(env_mode=self.env_mode, pending=[
            {"instId": "XRP-USDT-SWAP", "state": "live", "posSide": "long"},
            {"instId": "SOL-USDT-SWAP", "state": "partially_filled", "posSide": "short"},
            {"instId": "DOGE-USDT-SWAP", "state": "canceled", "posSide": "long"},
            {"instId": "ADA-USDT-SWAP", "state": "filled", "posSide": "long"},
            {"state": "live", "posSide": "short"},          # 无 instId：不进行程集合，但仍算同向
        ])
        rig.env_mode = self.env_mode
        rig.xv_broken = []
        rig.pending_enum_errors = []
        rig.ready = self.ready
        out = rig.run()
        # OKX 在途：2 个 instId（XRP/SOL）⇒ 与外所桩给的 XRP **去重**后仍是 2
        self.assertEqual(out[9], out[1] + 2,
                         "槽位 = 持仓 + 在途挂单去重后的 instId 数（OKX 的在途单必须算进去）")
        self.assertEqual(out[7], out[4] + 2, "多头 = OKX 持仓 + OKX 在途多头(1) + 外所在途多头(1)")
        self.assertEqual(out[8], out[10] + 2,
                         "空头 = OKX 持仓 + OKX 在途空头(partially_filled + 无 instId 各 1)")

    def test_environment_read_failure_is_treated_as_untrustworthy(self):
        """环境轴两次都读不到 ⇒ 一律按不可信处理（不是"当 demo 继续"）。"""
        out = self._no_broken(env_raises=True, xv=(False, {}, "读不到"))
        self.assertIsNotNone(out)
        self.assertTrue(out[3], "环境不可得 + 跨所读失败 ⇒ fail-closed 禁新开仓")

    def test_broken_venue_probe_failure_is_only_warned(self):
        out = self._no_broken(broken_raises=True)
        self.assertIsNotNone(out, "坏所探测异常不该中断周期（只 warn）")

    def _no_broken(self, **kw):
        rig = Rig(**kw)
        rig.env_mode = self.env_mode
        rig.xv_broken = []
        rig.pending_enum_errors = []
        rig.ready = self.ready
        return rig.run()


if __name__ == "__main__":
    unittest.main()
