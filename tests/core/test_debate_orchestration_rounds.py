"""投委会两轮编排：**同行卷宗**、**缺席文案**、预算耗尽时的**安全降级**（第二百三十四刀）。

| 语义 | 口径 |
|---|---|
| 标准模式 | **只一轮**提案 ⇒ 直接进 CIO 裁决（`call_critique` 不被调用）|
| 跨审模式 | 两轮：每个席位都要质询**其他所有**席位的提案 ⇒ `call_critique` 每席位一次 |
| ★ 同行卷宗 | 每条卷宗带「**名字 + 提案标识**」，且**不含自己**；没有同行时给「（无其他同行提案）」|
| ★ 缺席有文案 | 自己的提案缺失 ⇒「（该交易员第一轮未提交有效提案）」；同行缺失 ⇒「（未提交）」——**缺席写成缺席**，不静默留空 |
| ★ **安全降级** | 第二轮前若剩余预算 `< 7.0s` ⇒ 质询记 `status='skipped'`，文案写明「**时间预算紧缺…安全降级跳过交叉质询以确保 CIO 终审**」⇒ `call_critique` **一次都不发** |
| ★ CIO 预留（审计 P2-14） | 注释记录：CIO 是**唯一会被执行的输出**，旧实现只留 5s ⇒ 四席思考型模型吃满后 CIO「只能在几秒内草率定稿」|
| 标准模式预检 | 剩余 `< MIN_SAFE + 2.0s` ⇒ **TimeoutError** |
"""

import unittest
from unittest import mock

from r20_backend.council.debate import execute_council_debate

ROLES = {
    "cio": {"name": "首席", "is_arbitrator": True, "prompt": "p"},
    "a": {"name": "甲", "prompt": "p"},
    "b": {"name": "乙", "prompt": "p"},
}


class RoundsTest(unittest.TestCase):
    def setUp(self):
        self.trader_calls = []
        self.critique_calls = []
        p = mock.patch("r20_backend.council.debate.time.sleep")
        self.sleep_mock = p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch("r20_backend.llm_manager.get_active_llm_runtime",
                        return_value={"model": "M"})
        p2.start()
        self.addCleanup(p2.stop)

    def _run(self, timeout, mode="cross_examination", proposal_for=None, time_seq=None):
        def _load_config():
            return {"roles": ROLES, "consensus_mode": mode}

        def _call_trader(*args, **kwargs):
            self.trader_calls.append(args)
            key = args[0]
            if proposal_for is not None:
                return proposal_for(key)
            return {"proposal_id": f"{key}_prop", "role_name": args[1].get("name", key),
                    "status": "ok", "content": f"{key} 的提案", "weight": 1.0}

        def _call_critique(*args, **kwargs):
            self.critique_calls.append(args)
            return {"role_id": args[0], "status": "ok", "content": "质询", "weight": 1.0}

        patches = []
        if time_seq is not None:
            ticks = iter(time_seq)
            patches.append(mock.patch("r20_backend.council.debate.time.time",
                                      side_effect=lambda: next(ticks, 999.0)))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        try:
            return execute_council_debate(_load_config, lambda s: {}, _call_trader,
                                          _call_critique, "市场", "系统",
                                          timeout=timeout, runtime_context=None), None
        except Exception as exc:
            return None, exc

    def test_standard_mode_runs_a_single_round(self):
        self._run(150.0, mode="standard")
        self.assertTrue(self.trader_calls, "标准模式仍有首轮提案")
        self.assertFalse(self.critique_calls, "标准模式**不**做交叉质询")
        self.assertEqual(self.trader_calls[0][4], 90.0, "首轮同样享受 90s 地板")

    def test_standard_mode_refuses_when_budget_is_below_the_floor(self):
        _out, exc = self._run(6.0, mode="standard")
        self.assertIsInstance(exc, TimeoutError)
        self.assertIn("standard deliberation", str(exc))
        self.assertFalse(self.trader_calls, "拒绝时不得发起任何席位调用")

    def test_critiques_carry_every_peer_but_never_the_reviewer(self):
        """★ 同行卷宗：**含其他所有席位、不含自己**，且每条都带提案标识。"""
        self._run(150.0)
        self.assertEqual(len(self.critique_calls), 2, "两个席位各质询一次")
        by_key = {args[0]: args for args in self.critique_calls}
        peers_of_a = by_key["a"][3]
        self.assertIn("b_prop", peers_of_a, "卷宗里带同行的提案标识")
        self.assertIn("b 的提案", peers_of_a)
        self.assertNotIn("a 的提案", peers_of_a, "**不得把自己塞进同行卷宗**")

    def test_absent_proposals_are_worded_not_left_blank(self):
        """★ 缺席有明确文案：自己没提案 / 同行没提案，两种都有话说。"""
        def _proposal(key):
            if key == "a":
                return {"proposal_id": "a_prop", "role_name": "甲", "status": "ok",
                        "content": "甲 的提案"}
            return {"proposal_id": "b_prop", "role_name": "乙", "status": "ok"}   # 无 content
        self._run(150.0, proposal_for=_proposal)
        by_key = {args[0]: args for args in self.critique_calls}
        self.assertEqual(by_key["b"][2], "（该交易员第一轮未提交有效提案）",
                         "自己缺提案 ⇒ 明确文案（不是空串）")
        self.assertIn("（未提交）", by_key["a"][3], "同行缺提案 ⇒ 卷宗里写明未提交")

    def test_my_own_proposal_is_handed_to_my_critique(self):
        self._run(150.0)
        by_key = {args[0]: args for args in self.critique_calls}
        self.assertEqual(by_key["a"][2], "a 的提案", "质询看的是**自己**的提案")
        self.assertEqual(by_key["b"][2], "b 的提案")

    def test_critiques_are_skipped_when_the_budget_is_exhausted(self):
        """★ **安全降级**：第二轮前预算不足 ⇒ 跳过交叉质询以**确保 CIO 终审**（一次都不发）。

        用假时钟驱动：跨审预检要求 ≥10s，故初始给 15s；每次取时前进 3s ⇒ 走到第二轮时已 <7s。
        """
        # 第 1 次取时=t_start；第 2 次=预算预检(rem 15≥5)；第 3 次=跨审预检(rem 11≥10 通过)；
        # 第 4 次=第二轮检查(rem 6 < 7 ⇒ 跳过)。其后一律 999（若调用次数更多，仍是"跳过硬降级"）。
        seq = [0.0, 0.0, 4.0, 9.0]
        _out, _exc = self._run(15.0, time_seq=seq)
        self.assertTrue(self.trader_calls, "首轮照常进行")
        self.assertFalse(self.critique_calls,
                         "预算耗尽 ⇒ 质询全部记为 skipped 且**不发**调用")


if __name__ == "__main__":
    unittest.main()
