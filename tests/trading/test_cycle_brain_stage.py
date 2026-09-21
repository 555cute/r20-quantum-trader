"""相位 4 前段（`scan_risk_gates_and_ai_brain`）的行为契约（第二百一十八刀）。

这一段是"模型给指令、底座把关"的交汇点，几条语义都关乎钱：

- 熔断中 ⇒ **不叫模型**（且不再走后面的池闸）；
- `execute_batch_ai_brain_cycle` 为假 ⇒ **主动不叫模型**（总开关）；
- 模型**有**产出 ⇒ 必须**重新拉一次真实仓位**再执行持仓管理：拉不到就
  append「AI持仓管理跳过」而**不拿旧快照执行**（旧快照可能已过期）；
- 模型**无**产出 ⇒ 读周期健康度：连续失败要吼（≥3 轮升级为 🔴 人工核查），
  并发跳过要写明「禁止复用旧持仓指令」；
- 模型那段**抛异常** ⇒ 只 warn，绝不打断整个周期；
- 标的池不可信 ⇒ fail-closed：**只做持仓风控接管、禁止开新仓**；
- 段内**没有 return/break**：`executed_actions` 靠原地 append 回传。
"""

import unittest

from scripts.trader.cycle_stages import scan_risk_gates_and_ai_brain


class _Rig:
    def __init__(self, *, cb=(False, ""), brain=None, brain_raises=None, refresh=None,
                 health=None, pool_trustworthy=True, margin=250.0, batch_enabled=True):
        self.actions = []
        self.saved = []
        self.managed = []
        self.merged = []
        self.queries = 0
        self.cb = cb
        self.brain = {} if brain is None else brain
        self.brain_raises = brain_raises
        self.refresh = refresh if refresh is not None else (
            True, [{"instId": "BTC-USDT-SWAP", "pos": "2"}], "")
        self.health = health or {}
        self.pool_trustworthy = pool_trustworthy
        self.margin = margin
        self.batch_enabled = batch_enabled
        self.pos_desc = None
        self.merged_into = None

    def run(self):
        def brain_call(pos_desc, active_pos_list, *, usdt_available):
            self.pos_desc = pos_desc
            self.active_pos_list = active_pos_list
            if self.brain_raises is not None:
                raise self.brain_raises
            return self.brain

        def collect(all_factors, trackers):
            return [{"instId": "BTC-USDT-SWAP", "src": "okx"}]

        def merge(active_pos_list, xv, all_factors):
            self.merged.append((active_pos_list, xv))
            active_pos_list.append({"instId": "SOL-USDT-SWAP", "src": "binance"})

        def query():
            self.queries += 1
            return self.refresh

        out = scan_risk_gates_and_ai_brain(
            _xv_total=2, active_pos_count=1, all_factors=[{"name": "BTC"}],
            executed_actions=self.actions, long_count=1, short_count=0,
            timestamp_full="2026-09-21 12:00:00", trackers={"t": 1}, usdt_available=1000.0,
            xv_positions_by_venue={"binance": [{"inst_id": "SOL"}]},
            MAX_CONCURRENT_POSITIONS=5,
            _collect_okx_position_payloads=collect,
            _merge_cross_venue_positions=merge,
            effective_single_asset_margin=lambda usdt: self.margin,
            execute_ai_position_management=lambda pos_dict, tr, ts, acts:
                self.managed.append(pos_dict),
            execute_batch_ai_brain_cycle=brain_call if self.batch_enabled else None,
            is_circuit_breaker_active=lambda usdt: self.cb,
            pool_is_trustworthy=lambda: self.pool_trustworthy,
            pool_state=lambda: {"status": "corrupt", "detail": "文件坏了"},
            query_positions=query,
            read_cycle_health=lambda: self.health,
            save_trackers=lambda tr: self.saved.append(dict(tr)))
        return out


class ScanRiskGatesAndBrainTest(unittest.TestCase):
    def test_circuit_breaker_skips_the_model_and_the_pool_gate(self):
        rig = _Rig(cb=(True, "🚨 断崖"), pool_trustworthy=False)
        cap, cache, cb_active, reason = rig.run()
        self.assertTrue(cb_active)
        self.assertIn("断崖", reason)
        self.assertIsNone(rig.pos_desc, "熔断中绝不许叫模型")
        self.assertEqual(rig.actions, [], "熔断时连池闸都不该走（先止损，别再谈开仓）")

    def test_batch_switch_off_skips_the_model(self):
        rig = _Rig(batch_enabled=False)
        rig.run()
        self.assertIsNone(rig.pos_desc, "总开关关掉 ⇒ 一个模型调用都不发")
        self.assertEqual(rig.queries, 0)

    def test_asset_margin_cap_comes_from_the_adaptive_helper(self):
        cap, _, _, _ = _Rig(margin=777.0).run()
        self.assertEqual(cap, 777.0, "单标的保证金上限必须走自适应函数（与提示词同口径）")

    def test_brain_output_triggers_a_fresh_position_refresh(self):
        rig = _Rig(brain={"BTC": {"action": "HOLD"}})
        _, cache, _, _ = rig.run()
        self.assertEqual(cache, {"BTC": {"action": "HOLD"}})
        self.assertEqual(rig.queries, 1, "有产出 ⇒ 必须重新拉一次真实仓位")
        self.assertEqual(len(rig.managed), 1, "刷新成功才执行持仓管理")
        self.assertEqual(rig.saved, [{"t": 1}], "执行完必须落盘 trackers")

    def test_refresh_failure_skips_management_instead_of_using_stale_snapshot(self):
        rig = _Rig(brain={"BTC": {}}, refresh=(False, [], "接口抖动"))
        rig.run()
        self.assertEqual(rig.managed, [], "拉不到真实仓位 ⇒ 不许拿旧快照执行")
        self.assertTrue(any("AI持仓管理跳过" in a for a in rig.actions), rig.actions)

    def test_refresh_keeps_only_positions_with_size(self):
        rig = _Rig(brain={"BTC": {}}, refresh=(True, [
            {"instId": "BTC-USDT-SWAP", "pos": "2"},
            {"instId": "ETH-USDT-SWAP", "pos": "0"}], ""))
        rig.run()
        self.assertEqual(list(rig.managed[0]), ["BTC-USDT-SWAP"],
                         "数量为 0 的行不是持仓（旧快照里可能有已平掉的标的）")

    def test_empty_brain_output_reads_health_and_refuses_to_reuse_old_orders(self):
        rig = _Rig(brain={}, health={"last_status": "ok"})
        _, cache, _, _ = rig.run()
        self.assertEqual(cache, {})
        self.assertTrue(any("并发跳过" in a and "禁止复用旧持仓指令" in a for a in rig.actions),
                        rig.actions)
        self.assertEqual(rig.managed, [])

    def test_consecutive_failures_upgrade_to_a_human_review_alarm(self):
        rig = _Rig(brain={}, health={"last_status": "failed", "consecutive_failures": 4,
                                     "last_error": "额度不足"})
        rig.run()
        joined = "\n".join(rig.actions)
        self.assertIn("连续4轮", joined)
        self.assertIn("人工核查", joined, "连续失败 ≥3 轮必须升级成人看的告警")
        self.assertIn("额度不足", joined, "失败原因要带出来（否则没法查）")

    def test_brain_exception_only_warns(self):
        rig = _Rig(brain_raises=RuntimeError("模型超时"))
        cap, cache, cb_active, _ = rig.run()
        self.assertEqual(cache, {}, "异常 ⇒ 空缓存，不许留半成品")
        self.assertIsNotNone(cap, "异常不该打断本段（后续阶段照常）")

    def test_untrustworthy_pool_blocks_new_entries(self):
        rig = _Rig(brain={"BTC": {}}, pool_trustworthy=False)
        rig.run()
        self.assertTrue(any("标的池不可信" in a and "禁止开新仓" in a for a in rig.actions),
                        f"池不可信必须 fail-closed：{rig.actions}")
        self.assertEqual(len(rig.managed), 1, "但持仓风控接管照常执行")

    def test_trustworthy_pool_adds_no_warning(self):
        rig = _Rig(brain={"BTC": {}}, pool_trustworthy=True)
        rig.run()
        self.assertFalse(any("标的池不可信" in a for a in rig.actions))
        self.assertEqual(rig.actions, [], "没动作时不该制造噪音")

    def test_cross_venue_positions_are_merged_into_the_panorama(self):
        rig = _Rig(brain={"BTC": {}})
        rig.run()
        self.assertTrue(rig.merged, "必须汇入外所在管持仓（三所平权全景）")
        self.assertIn("SOL-USDT-SWAP", [p["instId"] for p in rig.active_pos_list],
                      "外所持仓要进全景，否则模型看不到它")

    def test_position_description_discloses_counts_and_cross_venue(self):
        rig = _Rig(brain={"BTC": {}})
        rig.run()
        self.assertIn("1/5", rig.pos_desc)
        self.assertIn("2", rig.pos_desc, "跨所笔数要写进描述")

    def test_unknown_cross_venue_count_is_spelled_out(self):
        rig = _Rig(brain={"BTC": {}})
        rig._xv = None
        # 直接改调用参数：用 _xv_total=None 再跑一次
        rig_kwargs = dict(_xv_total=None)
        import scripts.trader.cycle_stages as cs
        captured = {}
        out = cs.scan_risk_gates_and_ai_brain(
            _xv_total=None, active_pos_count=1, all_factors=[], executed_actions=[],
            long_count=1, short_count=0, timestamp_full="t", trackers={}, usdt_available=1.0,
            xv_positions_by_venue={}, MAX_CONCURRENT_POSITIONS=5,
            _collect_okx_position_payloads=lambda a, t: [],
            _merge_cross_venue_positions=lambda l, x, a: None,
            effective_single_asset_margin=lambda u: 1.0,
            execute_ai_position_management=lambda *a: None,
            execute_batch_ai_brain_cycle=lambda desc, lst, *, usdt_available: captured.update(
                {"desc": desc}) or {"x": 1},
            is_circuit_breaker_active=lambda u: (False, ""),
            pool_is_trustworthy=lambda: True, pool_state=lambda: {},
            query_positions=lambda: (True, [], ""), read_cycle_health=lambda: {},
            save_trackers=lambda t: None)
        self.assertIn("未知", captured["desc"], "跨所笔数拿不到时必须写「未知」，绝不装 0")


if __name__ == "__main__":
    unittest.main()
