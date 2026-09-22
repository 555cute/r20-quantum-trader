"""选所路由：**硬筛淘汰必留痕、滞回只救现任、均衡哈希跨进程可复现**（第二百七十八刀，开新面 venue_router.py）。

先打印整个文件（271 行）再动笔。它是 US-002 的**选所决策核心**（不真实下单、不碰执行开关），
门面把「硬筛/评分/均衡选取/ISO 解析」再导出给 `venue_routing/selection.py`。

| 语义 | 口径 |
|---|---|
| ★ **门面是再导出而非搬空** | `_hard_filters`/`_score`/`_balanced_pick`/`_parse_iso_utc` 必须是**子模块同一个函数对象** —— `tests/audit/test_cross_process_hash_determinism.py` 在**子进程**里 `from r20_backend.venue_router import _balanced_pick`，一旦门面自建副本，跨进程确定性就名存实亡 |
| ★ **硬筛淘汰必留痕** | 每条淘汰进 `rejected` 并带 `stage`；`__FAILOPEN__`/`__NOTE__` 是**注记不淘汰**（分别进 reasons）；全灭 ⇒ `ALL_REJECTED`，无候选 ⇒ `NO_CANDIDATES` |
| ★ **滞回只救现任** | 挑战者领先比例 `(现任分-最低分)/|现任分|` **严格小于**阈值才保留现任；现任即最低分 ⇒ 不触发；现任不在评分集中 ⇒ 不触发；分母有 `1e-9` 地板防零除 |
| ★ **均衡用 sha256 而非 `hash()`** | 原因见文件内审计 D7 注释（`hash()` 受 `PYTHONHASHSEED` 随机化 ⇒ 跨进程轮入不同所）；本刀钉"同一 canonical 恒定同结果"且**加仓+现任在场时不做均衡** |
| ★ **多所分配是显式开关** | `split_enabled=False` ⇒ 永远 `None`（且**一次硬筛都不跑**）；权重按评分反比 `1/(s-floor+1)`（地板防负权）；切片低于 `min_slice_usdt` 丢弃；**不足 2 片 ⇒ 归单所** |
| ★ **预算与 size 取小** | `total = min(signal.size_usdt, budget.available)`；`total<=0` ⇒ 不拆 |
| ★ **配置三种入参** | `None`→默认 / `RouterConfig`→原样返回（同一对象）/ `dict`→**只取已知字段**（未知键静默丢弃）；其它类型 ⇒ `TypeError`（不静默降级）|

## 🐞 本刀实测到一处**崩溃级**缺陷（未擅自改生产代码）

候选 dict **缺 `venue` 键**时 `route_signal` 抛 `KeyError: 'venue'`，而不是把它记进 `rejected`：

- 第 191 行用 `cand.get("venue", "?")`（防御式），第 208 行却用 `c["venue"]`（严格式）；
- 更糟的是第 202 行的存活判定用 `str(c.get("venue"))` ⇒ `"None"`，与被淘汰记录的 `"?"` **永远配不上**
  ⇒ 即使该候选已被判淘汰，它仍会被算作存活，然后在下标取值处崩掉。

也就是说"可解释路由"遇到畸形候选会给出一个**不可解释的 KeyError**。两条路径都已写成用例钉住。
"""
import hashlib
import unittest
from unittest import mock

from r20_backend import venue_router as VR
from r20_backend.venue_routing import selection as SEL


def _cand(venue, **extra):
    return {"venue": venue, **extra}


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.signal = {"size_usdt": 1000.0, "symbol_canonical": "BTC-USDT-SWAP"}
        self.hard = self._start(mock.patch.object(VR, "_hard_filters", return_value=[]))
        self.score = self._start(mock.patch.object(VR, "_score", return_value=10.0))
        self.pick = self._start(mock.patch.object(VR, "_balanced_pick",
                                                  return_value="okx"))

    def _scores(self, mapping):
        self.score.side_effect = lambda cand, signal, cfg, reasons: mapping[str(cand["venue"])]


class ContractTests(unittest.TestCase):
    def test_penalty_constants(self):
        self.assertEqual(VR.DEPTH_PENALTY_MAX_BPS, 50.0)
        self.assertEqual(VR.INCUMBENT_BONUS_BPS, 5.0)

    def test_failopen_marker_matches_the_submodule(self):
        """门面第 45 行用字面量**覆盖**了第 36 行导入的同名常量；
        本用例保证两者不许漂移（否则门面里的语义注记会与子模块脱节）。"""
        self.assertEqual(VR._LISTING_FAILOPEN_MARK, SEL._LISTING_FAILOPEN_MARK)
        self.assertEqual(VR._LISTING_FAILOPEN_MARK, "跳过对账")

    def test_facade_reexports_the_very_same_objects(self):
        """再导出而不是搬空：必须是同一个函数对象。"""
        self.assertIs(VR._hard_filters, SEL._hard_filters)
        self.assertIs(VR._score, SEL._score)
        self.assertIs(VR._balanced_pick, SEL._balanced_pick)
        self.assertIs(VR._parse_iso_utc, SEL._parse_iso_utc)

    def test_balanced_pick_is_cross_process_deterministic(self):
        """同一 canonical 必须恒定同结果（sha256 摘要取模，不依赖 PYTHONHASHSEED）。"""
        venues = ["binance", "gate", "okx"]
        first = SEL._balanced_pick("BTC", list(venues))
        for _ in range(5):
            self.assertEqual(SEL._balanced_pick("BTC", list(venues)), first)
        digest = hashlib.sha256(b"BTC").hexdigest()
        self.assertEqual(first, sorted(venues)[int(digest, 16) % len(venues)])

    def test_route_decision_defaults(self):
        decision = VR.RouteDecision(venue="okx", reason_code="OK")
        self.assertEqual(decision.reasons, [])
        self.assertEqual(decision.rejected, [])
        self.assertIs(decision.hysteresis_applied, False)
        self.assertIsNone(decision.allocation)

    def test_route_decision_default_lists_are_not_shared(self):
        first = VR.RouteDecision(venue=None, reason_code="X")
        second = VR.RouteDecision(venue=None, reason_code="X")
        first.reasons.append("mutate")
        self.assertEqual(second.reasons, [], "可变默认值必须各自独立")

    def test_router_config_defaults(self):
        cfg = VR.RouterConfig()
        self.assertEqual(cfg.holding_hours, 8.0)
        self.assertEqual(cfg.funding_interval_hours, 8.0)
        self.assertEqual(cfg.hysteresis_pct, 0.15)
        self.assertIs(cfg.split_enabled, False)
        self.assertEqual(cfg.min_slice_usdt, 100.0)
        self.assertEqual(cfg.depth_penalty_max_bps, VR.DEPTH_PENALTY_MAX_BPS)
        self.assertEqual(cfg.incumbent_bonus_bps, VR.INCUMBENT_BONUS_BPS)
        self.assertEqual(cfg.health_max_age_s_default, 900.0)
        self.assertEqual(cfg.routing_mode, "auto")
        self.assertIsNone(cfg.now_utc)


class CoerceConfigTests(unittest.TestCase):
    def test_none_gives_the_defaults(self):
        self.assertEqual(VR._coerce_config(None), VR.RouterConfig())

    def test_instance_is_passed_through_by_identity(self):
        cfg = VR.RouterConfig(hysteresis_pct=0.5)
        self.assertIs(VR._coerce_config(cfg), cfg)

    def test_dict_is_filtered_to_known_fields(self):
        cfg = VR._coerce_config({"hysteresis_pct": 0.3, "split_enabled": True,
                                 "unknown_key": 1, "another": "x"})
        self.assertEqual(cfg.hysteresis_pct, 0.3)
        self.assertIs(cfg.split_enabled, True)
        self.assertFalse(hasattr(cfg, "unknown_key"))
        self.assertEqual(cfg.holding_hours, 8.0, "未提供的键走默认")

    def test_other_types_are_a_type_error(self):
        for bad in ([], "auto", 3, object()):
            with self.subTest(value=type(bad).__name__):
                with self.assertRaises(TypeError) as ctx:
                    VR._coerce_config(bad)
                self.assertIn("RouterConfig/dict/None", str(ctx.exception))


class NowEpochTests(_Base):
    def test_injected_iso_is_parsed(self):
        cfg = VR.RouterConfig(now_utc="2026-01-02T03:04:05+00:00")
        self.assertEqual(VR._now_epoch(cfg), 1767323045.0)

    def test_unparseable_iso_falls_back_to_the_real_clock(self):
        cfg = VR.RouterConfig(now_utc="垃圾时间")
        self._start(mock.patch.object(VR.time, "time", return_value=12345.0))
        self.assertEqual(VR._now_epoch(cfg), 12345.0)

    def test_absent_iso_uses_the_real_clock(self):
        self._start(mock.patch.object(VR.time, "time", return_value=999.0))
        self.assertEqual(VR._now_epoch(VR.RouterConfig()), 999.0)

    def test_empty_iso_string_is_falsy_and_uses_the_real_clock(self):
        self._start(mock.patch.object(VR.time, "time", return_value=555.0))
        self.assertEqual(VR._now_epoch(VR.RouterConfig(now_utc="")), 555.0)


class HysteresisTests(unittest.TestCase):
    def _run(self, scores, cfg=None):
        reasons = []
        out = VR._apply_hysteresis([_cand(v) for v in scores], scores,
                                   cfg or VR.RouterConfig(), reasons)
        return out, reasons

    def test_without_an_incumbent_the_best_wins(self):
        (winner, applied), reasons = self._run({"okx": 20.0, "gate": 10.0})
        self.assertEqual(winner, "gate")
        self.assertIs(applied, False)
        self.assertEqual(reasons, [])

    def test_incumbent_being_the_best_is_not_hysteresis(self):
        (winner, applied), reasons = self._run(
            {"okx": 8.0, "gate": 10.0})
        self.assertEqual(winner, "okx")
        self.assertIs(applied, False)
        self.assertEqual(reasons, [], "现任本来就是最低成本 ⇒ 不算滞回救回")

    def test_insufficient_lead_keeps_the_incumbent(self):
        cands = [_cand("okx", current_venue=True), _cand("gate")]
        scores = {"okx": 10.0, "gate": 9.9}
        reasons = []
        winner, applied = VR._apply_hysteresis(cands, scores, VR.RouterConfig(), reasons)
        self.assertEqual(winner, "okx")
        self.assertIs(applied, True)
        self.assertEqual(len(reasons), 1)
        self.assertIn("滞回防抖", reasons[0])
        self.assertIn("保留现任所 okx", reasons[0])
        self.assertIn("1.0%", reasons[0])

    def test_sufficient_lead_switches_venue(self):
        cands = [_cand("okx", current_venue=True), _cand("gate")]
        scores = {"okx": 10.0, "gate": 5.0}
        reasons = []
        winner, applied = VR._apply_hysteresis(cands, scores, VR.RouterConfig(), reasons)
        self.assertEqual(winner, "gate")
        self.assertIs(applied, False)
        self.assertEqual(reasons, [])

    def test_exactly_at_the_threshold_switches(self):
        """判定是 `<` 阈值 ⇒ 恰好等于阈值时**不**救现任。"""
        cands = [_cand("okx", current_venue=True), _cand("gate")]
        scores = {"okx": 10.0, "gate": 8.5}     # lead = 1.5/10 = 15% == hysteresis_pct
        winner, applied = VR._apply_hysteresis(cands, scores,
                                              VR.RouterConfig(hysteresis_pct=0.15), [])
        self.assertEqual(winner, "gate")
        self.assertIs(applied, False)

    def test_zero_hysteresis_always_switches(self):
        cands = [_cand("okx", current_venue=True), _cand("gate")]
        winner, applied = VR._apply_hysteresis(cands, {"okx": 10.0, "gate": 9.999},
                                              VR.RouterConfig(hysteresis_pct=0.0), [])
        self.assertEqual(winner, "gate")
        self.assertIs(applied, False)

    def test_incumbent_absent_from_scores_is_ignored(self):
        cands = [_cand("ghost", current_venue=True), _cand("gate"), _cand("okx")]
        scores = {"gate": 12.0, "okx": 11.0}
        reasons = []
        winner, applied = VR._apply_hysteresis(cands, scores, VR.RouterConfig(), reasons)
        self.assertEqual(winner, "okx")
        self.assertIs(applied, False)
        self.assertEqual(reasons, [])

    def test_negative_scores_still_compare_by_ratio(self):
        cands = [_cand("okx", current_venue=True), _cand("gate")]
        scores = {"okx": -10.0, "gate": -10.5}
        reasons = []
        winner, applied = VR._apply_hysteresis(cands, scores, VR.RouterConfig(), reasons)
        self.assertEqual(winner, "okx")
        self.assertIs(applied, True, "分母取绝对值 ⇒ 负评分下比例仍然可算")

    def test_zero_incumbent_score_does_not_divide_by_zero(self):
        cands = [_cand("okx", current_venue=True), _cand("gate")]
        scores = {"okx": 0.0, "gate": -5.0}
        winner, applied = VR._apply_hysteresis(cands, scores, VR.RouterConfig(), [])
        self.assertEqual(winner, "gate")
        self.assertIs(applied, False, "分母地板 1e-9 ⇒ 领先比例视为极大，正常换所")

    def test_incumbent_flag_on_candidates_without_rejection(self):
        """现任所只由候选里的 `current_venue` 决定，与评分顺序无关。"""
        cands = [_cand("gate"), _cand("okx", current_venue=True)]
        scores = {"gate": 9.95, "okx": 10.0}
        reasons = []
        winner, applied = VR._apply_hysteresis(cands, scores, VR.RouterConfig(), reasons)
        self.assertEqual(winner, "okx")
        self.assertIs(applied, True)
        self.assertEqual(len(reasons), 1)


class StageOfTests(unittest.TestCase):
    def test_stage_mapping(self):
        cases = {
            "执行开闸关闭": "executable",
            "listing 对账失败": "listing",
            "不在准入币种清单内": "venue_pool",
            "名义额低于最小量": "precision",
            "步进精度不匹配": "precision",
            "行情新鲜度不足": "freshness",
            "超出可用预算": "budget",
            "完全不认识的原因": "unknown",
            "": "unknown",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(VR._stage_of(reason), expected)

    def test_first_matching_keyword_wins(self):
        self.assertEqual(VR._stage_of("listing 且执行开闸关闭"), "executable")
        self.assertEqual(VR._stage_of("listing 且 准入币种清单"), "listing")


class SplitAllocationTests(_Base):
    def _split(self, signal=None, candidates=None, budget=None, config=None, **kw):
        return VR.split_allocation(signal if signal is not None else self.signal,
                                   candidates if candidates is not None else
                                   [_cand("okx"), _cand("gate")],
                                   budget, config, **kw)

    def test_disabled_by_default_and_runs_no_hard_filters(self):
        self.assertIsNone(self._split())
        self.hard.assert_not_called()
        self.assertEqual(self.score.call_count, 0)

    def test_explicitly_disabled_also_returns_none(self):
        self.assertIsNone(self._split(config=VR.RouterConfig(split_enabled=False)))
        self.hard.assert_not_called()

    def test_pre_alive_path_skips_the_listing_recheck(self):
        cfg = VR.RouterConfig(split_enabled=True)
        self._scores({"okx": 0.0, "gate": 1.0})
        out = self._split(config=cfg, pre_alive=[_cand("okx"), _cand("gate")])
        self.hard.assert_not_called()
        self.assertEqual([s["venue"] for s in out], ["okx", "gate"])

    def test_empty_pre_alive_returns_none(self):
        self.assertIsNone(self._split(config=VR.RouterConfig(split_enabled=True),
                                      pre_alive=[]))

    def test_independent_path_applies_hard_filters(self):
        cfg = VR.RouterConfig(split_enabled=True)
        self._scores({"okx": 0.0, "gate": 1.0})
        self.hard.side_effect = lambda s, c, cfg_, b, n: (
            [] if c["venue"] == "okx" else ["超出可用预算"])
        out = self._split(config=cfg)
        self.assertIsNone(out, "只剩一个合格候选 ⇒ 拆不出 2 片 ⇒ 归单所")
        self.assertEqual(self.hard.call_count, 2, "独立路径确实逐个跑了硬筛")

    def test_failopen_and_note_markers_do_not_disqualify(self):
        """独立路径只过滤 `__` 前缀 ⇒ 只有注记的候选**依然合格**。"""
        cfg = VR.RouterConfig(split_enabled=True)
        self._scores({"okx": 0.0, "gate": 1.0})
        self.hard.side_effect = lambda s, c, cfg_, b, n: (
            ["__FAILOPEN__跳过对账"] if c["venue"] == "okx" else ["__NOTE__提示"])
        out = self._split(config=cfg)
        self.assertEqual(sorted(s["venue"] for s in out), ["gate", "okx"])

    def test_no_eligible_candidate_returns_none(self):
        self.hard.return_value = ["执行开闸关闭"]
        self.assertIsNone(self._split(config=VR.RouterConfig(split_enabled=True)))

    def test_non_positive_total_returns_none(self):
        cfg = VR.RouterConfig(split_enabled=True)
        for size in (0, None, -5, ""):
            with self.subTest(size=size):
                self.assertIsNone(self._split(signal={"size_usdt": size}, config=cfg))

    def test_budget_available_clamps_the_total(self):
        cfg = VR.RouterConfig(split_enabled=True, min_slice_usdt=1.0)
        self._scores({"okx": 0.0, "gate": 0.0})
        budget = mock.Mock(available=100.0)
        out = self._split(signal={"size_usdt": 1000.0}, budget=budget, config=cfg)
        self.assertEqual(sum(s["amount_usdt"] for s in out), 100.0)

    def test_budget_without_available_attribute_does_not_clamp(self):
        cfg = VR.RouterConfig(split_enabled=True, min_slice_usdt=1.0)
        self._scores({"okx": 0.0, "gate": 0.0})
        budget = mock.Mock(spec=[])          # 没有 available
        out = self._split(signal={"size_usdt": 1000.0}, budget=budget, config=cfg)
        self.assertEqual(sum(s["amount_usdt"] for s in out), 1000.0)

    def test_weights_are_inverse_to_score(self):
        cfg = VR.RouterConfig(split_enabled=True, min_slice_usdt=1.0)
        self._scores({"okx": 0.0, "gate": 1.0})
        out = self._split(signal={"size_usdt": 1500.0}, config=cfg)
        by_venue = {s["venue"]: s["amount_usdt"] for s in out}
        self.assertEqual(by_venue, {"okx": 1000.0, "gate": 500.0},
                         "1/(0-0+1)=1 与 1/(1-0+1)=0.5 ⇒ 2:1")

    def test_equal_scores_split_evenly(self):
        cfg = VR.RouterConfig(split_enabled=True, min_slice_usdt=1.0)
        self._scores({"okx": 5.0, "gate": 5.0})
        out = self._split(signal={"size_usdt": 1000.0}, config=cfg)
        self.assertEqual([s["amount_usdt"] for s in out], [500.0, 500.0])

    def test_slices_below_the_minimum_are_dropped(self):
        cfg = VR.RouterConfig(split_enabled=True, min_slice_usdt=400.0)
        self._scores({"okx": 0.0, "gate": 1.0, "binance": 100.0})
        out = self._split(signal={"size_usdt": 1000.0}, config=cfg)
        self.assertIsNone(out, "只有 okx 一片过线 ⇒ 不足 2 片 ⇒ 归单所")

    def test_two_surviving_slices_are_returned(self):
        cfg = VR.RouterConfig(split_enabled=True, min_slice_usdt=100.0)
        self._scores({"okx": 0.0, "gate": 1.0})
        out = self._split(signal={"size_usdt": 1000.0}, config=cfg)
        self.assertEqual(len(out), 2)
        for slice_ in out:
            self.assertEqual(set(slice_), {"venue", "amount_usdt"})
            self.assertGreaterEqual(slice_["amount_usdt"], 100.0)

    def test_dict_config_is_accepted(self):
        self._scores({"okx": 0.0, "gate": 0.0})
        out = self._split(signal={"size_usdt": 400.0},
                          config={"split_enabled": True, "min_slice_usdt": 100.0})
        self.assertEqual(len(out), 2)


class RouteSignalTests(_Base):
    def test_no_candidates_short_circuits(self):
        decision = VR.route_signal(self.signal, [])
        self.assertIsNone(decision.venue)
        self.assertEqual(decision.reason_code, "NO_CANDIDATES")
        self.assertEqual(decision.reasons, ["无候选所"])
        self.assertEqual(decision.rejected, [])
        self.hard.assert_not_called()

    def test_all_rejected_is_reported_with_stages(self):
        self.hard.side_effect = lambda s, c, cfg, b, n: (
            ["行情新鲜度不足"] if c["venue"] == "okx" else ["超出可用预算"])
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")])
        self.assertIsNone(decision.venue)
        self.assertEqual(decision.reason_code, "ALL_REJECTED")
        self.assertEqual(decision.reasons, ["所有候选所均被硬筛淘汰"])
        self.assertEqual(decision.rejected,
                         [{"venue": "okx", "stage": "freshness", "reason": "行情新鲜度不足"},
                          {"venue": "gate", "stage": "budget", "reason": "超出可用预算"}])
        self.assertEqual(self.score.call_count, 0, "全灭时不评分")

    def test_failopen_is_a_note_not_a_rejection(self):
        self.hard.side_effect = lambda s, c, cfg, b, n: (
            ["__FAILOPEN__跳过对账：接口超时"] if c["venue"] == "okx" else [])
        self._scores({"okx": 5.0, "gate": 9.0})
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")])
        self.assertEqual(decision.venue, "okx")
        self.assertEqual(decision.rejected, [])
        self.assertTrue(any("listing fail-open" in r for r in decision.reasons))
        self.assertTrue(any("不淘汰" in r for r in decision.reasons))

    def test_plain_note_marker_is_recorded(self):
        self.hard.side_effect = lambda s, c, cfg, b, n: (
            ["__NOTE__行情缓存偏旧"] if c["venue"] == "okx" else [])
        self._scores({"okx": 5.0, "gate": 9.0})
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")])
        self.assertEqual(decision.rejected, [])
        self.assertTrue(any("[okx] 行情缓存偏旧" == r for r in decision.reasons))

    def test_note_and_rejection_can_coexist_per_candidate(self):
        self.hard.side_effect = lambda s, c, cfg, b, n: (
            ["__NOTE__提示", "超出可用预算"] if c["venue"] == "gate" else [])
        self._scores({"okx": 5.0})
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")])
        self.assertEqual(decision.venue, "okx")
        self.assertEqual([r["venue"] for r in decision.rejected], ["gate"])
        self.assertTrue(any("提示" in r for r in decision.reasons))

    def test_ok_decision_shape(self):
        self._scores({"okx": 3.0, "gate": 9.0})
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")])
        self.assertEqual(decision.venue, "okx")
        self.assertEqual(decision.reason_code, "OK")
        self.assertIs(decision.hysteresis_applied, False)
        self.assertIsNone(decision.allocation)
        self.assertTrue(any("选中 okx" in r for r in decision.reasons))

    def test_hysteresis_decision_code(self):
        self._scores({"okx": 10.0, "gate": 9.9})
        decision = VR.route_signal(self.signal,
                                   [_cand("okx", current_venue=True), _cand("gate")])
        self.assertEqual(decision.venue, "okx")
        self.assertEqual(decision.reason_code, "OK_HYSTERESIS")
        self.assertIs(decision.hysteresis_applied, True)
        self.assertTrue(any("经滞回保留现任" in r for r in decision.reasons))

    def test_only_the_alive_candidates_are_scored(self):
        self.hard.side_effect = lambda s, c, cfg, b, n: (
            ["执行开闸关闭"] if c["venue"] == "gate" else [])
        self._scores({"okx": 5.0})
        VR.route_signal(self.signal, [_cand("okx"), _cand("gate")])
        scored = [call[0][0]["venue"] for call in self.score.call_args_list]
        self.assertEqual(scored, ["okx"])

    def test_allocation_is_appended_to_the_reasons(self):
        self._scores({"okx": 0.0, "gate": 1.0})
        cfg = VR.RouterConfig(split_enabled=True, min_slice_usdt=100.0)
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")],
                                   config=cfg)
        self.assertEqual(decision.venue, "okx")
        self.assertIsNotNone(decision.allocation)
        self.assertEqual(len(decision.allocation), 2)
        self.assertTrue(any("多所分配开启" in r for r in decision.reasons))

    def test_budget_view_is_forwarded_to_the_hard_filters(self):
        budget = mock.Mock()
        VR.route_signal(self.signal, [_cand("okx")], budget_view=budget)
        self.assertEqual(self.hard.call_args[0][3], budget)

    def test_configured_now_utc_is_forwarded_as_an_epoch(self):
        cfg = VR.RouterConfig(now_utc="2026-01-02T03:04:05+00:00")
        VR.route_signal(self.signal, [_cand("okx")], config=cfg)
        self.assertEqual(self.hard.call_args[0][4], 1767323045.0)


class BalancedModeTests(_Base):
    def setUp(self):
        super().setUp()
        self.cfg = VR.RouterConfig(routing_mode="balanced")

    def test_balanced_mode_picks_via_the_stable_hash(self):
        self._scores({"okx": 10.0, "gate": 11.0})
        self.pick.return_value = "gate"
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")],
                                   config=self.cfg)
        self.assertEqual(decision.venue, "gate")
        self.assertEqual(decision.reason_code, "OK_BALANCED")
        self.assertIs(decision.hysteresis_applied, False)
        self.pick.assert_called_once_with("BTC", ["gate", "okx"])
        self.assertTrue(any("均衡模式生效" in r for r in decision.reasons))

    def test_score_gap_beyond_tolerance_falls_back_to_cost(self):
        self._scores({"okx": 10.0, "gate": 40.0})     # 40 > 10 + 15
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")],
                                   config=self.cfg)
        self.assertEqual(decision.venue, "okx")
        self.assertEqual(decision.reason_code, "OK")
        self.pick.assert_not_called()

    def test_single_survivor_is_not_balanced(self):
        self.hard.side_effect = lambda s, c, cfg, b, n: (
            ["执行开闸关闭"] if c["venue"] == "gate" else [])
        self._scores({"okx": 10.0})
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")],
                                   config=self.cfg)
        self.assertEqual(decision.reason_code, "OK")
        self.pick.assert_not_called()

    def test_scale_in_with_an_incumbent_stays_with_the_incumbent(self):
        signal = {**self.signal, "is_scale_in": True}
        self._scores({"okx": 10.0, "gate": 11.0})
        decision = VR.route_signal(signal,
                                   [_cand("okx", current_venue=True), _cand("gate")],
                                   config=self.cfg)
        self.assertEqual(decision.venue, "okx")
        self.assertEqual(decision.reason_code, "OK")
        self.pick.assert_not_called()

    def test_new_position_balances_even_with_an_incumbent(self):
        self._scores({"okx": 10.0, "gate": 11.0})
        decision = VR.route_signal(self.signal,
                                   [_cand("okx", current_venue=True), _cand("gate")],
                                   config=self.cfg)
        self.assertEqual(decision.reason_code, "OK_BALANCED")

    def test_scale_in_without_an_incumbent_still_balances(self):
        signal = {**self.signal, "is_scale_in": True}
        self._scores({"okx": 10.0, "gate": 11.0})
        decision = VR.route_signal(signal, [_cand("okx"), _cand("gate")],
                                   config=self.cfg)
        self.assertEqual(decision.reason_code, "OK_BALANCED")

    def test_canonical_falls_back_to_inst_id_then_empty(self):
        self._scores({"okx": 10.0, "gate": 11.0})
        VR.route_signal({"size_usdt": 100, "inst_id": "eth-usdt-swap"},
                        [_cand("okx"), _cand("gate")], config=self.cfg)
        self.assertEqual(self.pick.call_args[0][0], "ETH")

        self.pick.reset_mock()
        VR.route_signal({"size_usdt": 100}, [_cand("okx"), _cand("gate")],
                        config=self.cfg)
        self.assertEqual(self.pick.call_args[0][0], "")

    def test_balanced_mode_splits_the_allocation(self):
        self._scores({"okx": 0.0, "gate": 1.0})
        cfg = VR.RouterConfig(routing_mode="balanced", split_enabled=True,
                              min_slice_usdt=100.0)
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")], config=cfg)
        self.assertIsNotNone(decision.allocation)
        self.assertEqual(len(decision.allocation), 2)

    def test_balanced_mode_reasons_are_explainable(self):
        self._scores({"okx": 10.0, "gate": 11.0})
        self.pick.return_value = "okx"
        decision = VR.route_signal(self.signal, [_cand("okx"), _cand("gate")],
                                   config=self.cfg)
        joined = " ".join(decision.reasons)
        self.assertIn("均衡轮动分发至 okx", joined)
        self.assertIn("标的 BTC", joined)


class MalformedCandidateTests(_Base):
    """🐞 缺 `venue` 键的候选会让 route_signal 崩掉（本刀实测，按实际行为钉住）。"""

    def test_missing_venue_key_raises_keyerror_when_it_survives(self):
        with self.assertRaises(KeyError) as ctx:
            VR.route_signal(self.signal, [{"inst_id": "BTC-USDT-SWAP"}])
        self.assertEqual(ctx.exception.args[0], "venue")

    def test_missing_venue_key_raises_even_when_it_was_rejected(self):
        self.hard.return_value = ["执行开闸关闭"]
        with self.assertRaises(KeyError) as ctx:
            VR.route_signal(self.signal, [{"inst_id": "X"}, _cand("okx")])
        self.assertEqual(ctx.exception.args[0], "venue",
                         "淘汰记录用的是 '?'，而存活判定用 str(None)='None' ⇒ 二者永不相等，"
                         "于是被判淘汰的候选仍被当成存活，随后下标取值崩溃")

    def test_venue_is_stringified_for_normal_candidates(self):
        self._scores({"7": 1.0})
        decision = VR.route_signal(self.signal, [_cand(7)])
        self.assertEqual(decision.venue, "7")


if __name__ == "__main__":
    unittest.main()
