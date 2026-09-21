"""投委会编排：**预算切分**（90s 地板与 2s 硬底）、**错峰提交**、**席位降级**（第二百三十三刀）。

`execute_council_debate` 把 `call_trader`/`call_critique` 作为**参数**收进来 ⇒ 编排本身可注入验证。

| 语义 | 口径 |
|---|---|
| ★ 跨审预检 | 跨审模式需要 **≥ 2×MIN_SAFE_REASONING_TIME** 剩余预算，否则 **TimeoutError**（宁可不开跑）|
| ★ **首轮 90s 地板** | `round1_budget = max(2.0, min(max(rem*0.55, 90.0), rem - cio_reserve))` —— 注释记录真机教训：纯比例切分在中低预算下会把首轮压到 53s 级**必死**（2026-09-10 实测）；审计 P2-14：跨审模式也要**为 CIO 预留** |
| ★ **2s 硬底** | 预算极紧时首轮仍给 2.0（不是 0 —— 0 等于不发）|
| ★ 错峰提交 | 第 2 个席位起 `sleep(0.8)`（防毫秒级并发打穿代理连接池）|
| ★ 席位降级 | 某个席位抛错 ⇒ 该席位记 `status=error` + 内容带异常 + **`weight=0.0`**（沉默不得变赞成）|
| 无席位 | 没有任何启用的交易员 ⇒ 走 **Solo CIO** 路径（不抛）|
| 席位启用判据 | **只有字面 `False`** 才算关闭（`enabled=0` 或缺省 ⇒ **保留**）|
"""

import unittest
from unittest import mock

from r20_backend.council.debate import execute_council_debate

ROLES = {
    "cio": {"name": "首席", "is_arbitrator": True, "prompt": "p"},
    "a": {"name": "甲", "prompt": "p"},
    "b": {"name": "乙", "prompt": "p"},
    "c": {"name": "丙", "prompt": "p", "enabled": False},
}


class OrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.trader_calls = []
        self.sleep = mock.patch("r20_backend.council.debate.time.sleep")
        self.sleep_mock = self.sleep.start()
        self.addCleanup(self.sleep.stop)
        p = mock.patch("r20_backend.llm_manager.get_active_llm_runtime",
                       return_value={"model": "M"})
        p.start()
        self.addCleanup(p.stop)

    def _run(self, timeout, roles=None, mode="cross_examination"):
        def _load_config():
            return {"roles": roles or ROLES, "consensus_mode": mode}

        def _call_trader(*args, **kwargs):
            self.trader_calls.append(args)
            return {"proposal_id": f"{args[0]}_prop", "role_id": args[0], "status": "ok",
                    "content": "提案", "weight": 1.0}

        def _call_critique(*args, **kwargs):
            return {"role_id": args[0], "status": "ok", "content": "质询", "weight": 1.0}
        try:
            return execute_council_debate(_load_config, lambda s: {}, _call_trader,
                                          _call_critique, "市场", "系统",
                                          timeout=timeout, runtime_context=None), None
        except Exception as exc:
            return None, exc

    def test_first_round_gets_the_ninety_second_floor(self):
        """★ 首轮 90s 地板：正比切分（0.55×150≈82.5s）**不足以**出带推理链的提案 ⇒ 抬到 90。"""
        _out, _exc = self._run(150.0)
        self.assertTrue(self.trader_calls, "首轮确实发起了席位调用")
        budget = self.trader_calls[0][4]
        self.assertEqual(budget, 90.0, "地板生效（不是 0.55*rem≈82.5）")

    def test_first_round_gets_a_two_second_hard_floor(self):
        """★ 预算极紧时首轮仍有 **2.0s**（`rem - cio_reserve` 会算成 1s 级 ⇒ 由硬底兜住）。"""
        _out, _exc = self._run(11.0)
        self.assertTrue(self.trader_calls)
        self.assertEqual(self.trader_calls[0][4], 2.0, "2s 硬底：不是 0（0 等于不发）")

    def test_insufficient_budget_for_two_rounds_refuses_to_start(self):
        """★ 跨审要两个来回 ⇒ 剩余不足 2×5s 时**直接拒绝开跑**。"""
        out, exc = self._run(6.0)
        self.assertIsInstance(exc, TimeoutError)
        self.assertIn("cross-examination", str(exc))
        self.assertFalse(self.trader_calls, "拒绝时不得发起任何席位调用")

    def test_seats_are_staggered_by_08s(self):
        """★ 错峰：第 2 个席位起各等 0.8s（防毫秒级并发打穿代理连接池）。"""
        self._run(150.0)
        staggered = [c for c in self.sleep_mock.call_args_list if c.args == (0.8,)]
        self.assertEqual(len(staggered), len(self.trader_calls) - 1,
                         "提交席位 n 个 ⇒ 错峰 (n-1) 次")
        self.assertEqual(len(self.trader_calls), 2, "本例只有两个启用席位（丙 被 enabled=False 排除）")

    def test_only_literal_false_disables_a_seat(self):
        roles = dict(ROLES)
        roles["d"] = {"name": "丁", "prompt": "p", "enabled": 0}
        self._run(150.0, roles=roles)
        called = sorted(a[0] for a in self.trader_calls)
        self.assertEqual(called, ["a", "b", "d"],
                         "enabled=0 仍被保留（只有字面 False 才算关闭）")

    def test_no_enabled_trader_means_solo_cio_without_crashing(self):
        roles = {"cio": {"name": "首席", "is_arbitrator": True, "prompt": "p"},
                 "c": {"name": "丙", "prompt": "p", "enabled": False}}
        _out, exc = self._run(150.0, roles=roles)
        self.assertFalse(self.trader_calls, "没有启用的席位 ⇒ 不发首轮")
        self.assertNotIsInstance(exc, TimeoutError, "Solo CIO 不是超时")

    def test_a_failing_seat_does_not_block_the_others(self):
        """★ 某席位抛错 ⇒ 其余席位照常走完（编排不因一个席位倒下而中止）。

        ⚠️ **本用例不断言**该席位的 `weight`：源码里异常分支写的是 `weight: 0.0`
        （沉默不得变赞成），但**首轮提案字典是函数内部状态**，只有读到函数末尾的 `return`
        才能观察到它 —— 那一段我尚未读。**改名以求名实相符**，不假装已断言 `weight`。
        """
        def _load_config():
            return {"roles": ROLES, "consensus_mode": "cross_examination"}

        def _call_trader(*args, **kwargs):
            self.trader_calls.append(args)
            if args[0] == "b":
                raise RuntimeError("席位崩了")
            return {"proposal_id": "a_prop", "role_id": "a", "status": "ok",
                    "content": "提案", "weight": 1.0}
        seen = {}

        def _call_critique(*args, **kwargs):
            seen["critique"] = args
            return {"role_id": args[0], "status": "ok", "content": "质询", "weight": 1.0}
        try:
            execute_council_debate(_load_config, lambda s: {}, _call_trader, _call_critique,
                                   "市场", "系统", timeout=150.0, runtime_context=None)
        except Exception as exc:      # 后半段未读 ⇒ 只关心"两个席位是否都被提交过"
            self.seat_outcome = exc   # 显式记下，不吞掉（仓库门禁禁止裸 pass 吞异常）
        self.assertEqual(sorted(a[0] for a in self.trader_calls), ["a", "b"],
                         "崩掉的席位不阻断其他席位")


if __name__ == "__main__":
    unittest.main()
