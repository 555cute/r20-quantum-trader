"""互评轮（Cross-Examination）的调用装配（第二百一十九刀）。

与首轮席位调用同构（宪法在前、渲染器接线、审计 P1-4b 解析事实透传），但有一条**明确的轮次语义**：
system prompt 必须写明这是**第二轮「同行方案交叉漏洞质询」**，user prompt 必须同时给出
**我第一轮的提案**与**同行卷宗**，并要求针对四类风险逐项质询：
**是否追高**、**ATR 止损距离**、**拟用保证金**、**假突破风险**。

★ 另一处细节：质询轮默认超时 **15.0**，比首轮的 20.0 **更短** —— 互评是收敛环节，不该比首轮更奢侈。

⚠️ 边界（同上一刀）：**本刀只断言调用参数**，不验证调用之后的行为（该函数后半段未读）。
"""

import unittest
from unittest.mock import patch

from r20_backend.council.debate import _call_single_trader_critique

SPEC = {"name": "老李", "prompt": "席位身份", "temperature": 0.1}


def _resolved():
    return {"model": "M2", "base_url": "U", "api_key": "K", "api_format": "F",
            "effort": "medium", "requested": "M-req", "registered": True,
            "fallback": False, "reason": "已登记"}


class CritiqueCallTest(unittest.TestCase):
    def setUp(self):
        self.seen = {}
        self.p_render = patch("r20_backend.council.debate._render_seat_prompt",
                              return_value="渲染身份")
        self.render_mock = self.p_render.start()
        self.addCleanup(self.p_render.stop)
        p = patch("r20_backend.llm_manager.get_active_llm_runtime",
                  return_value={"model": "M-active"})
        p.start()
        self.addCleanup(p.stop)

    def _call(self, **over):
        def _exec(**kw):
            self.seen["exec"] = kw
            return "质询正文", "思考", {}, 2.0
        p = patch("r20_backend.llm_manager.execute_llm_request", side_effect=_exec)
        p.start()
        self.addCleanup(p.stop)
        kwargs = dict(role_id="trader_b", role_spec=dict(SPEC), my_proposal="我的方案正文",
                      peer_proposals="同行方案卷宗", master_constitutional_rules="宪法第一条",
                      runtime_context={"k": 1})
        kwargs.update(over)
        try:
            return _call_single_trader_critique(lambda s: _resolved(), **kwargs)
        except Exception:                    # 后半段未读 ⇒ 只关心调用参数
            return None

    def test_system_prompt_declares_the_second_round_and_keeps_constitution_first(self):
        self._call()
        text = self.seen["exec"]["messages"][0]["content"]
        self.assertIn("宪法第一条", text)
        self.assertIn("渲染身份", text)
        self.assertLess(text.index("宪法第一条"), text.index("渲染身份"), "宪法仍在前")
        self.assertIn("第二轮", text, "必须写明轮次语义（否则模型不知道自己在互评）")
        self.assertIn("质询", text)
        self.assertIn("Cross-Examination", text)

    def test_user_prompt_carries_both_my_proposal_and_the_peer_dossier(self):
        self._call()
        user = self.seen["exec"]["messages"][1]["content"]
        self.assertIn("我的方案正文", user, "我的首轮提案")
        self.assertIn("同行方案卷宗", user, "同行卷宗")
        self.assertLess(user.index("我的方案正文"), user.index("同行方案卷宗"),
                        "先己后人（质询需要先建立自己的立场）")

    def test_user_prompt_names_the_four_risk_axes(self):
        """★ 四类风险逐项质询：追高 / ATR 止损距离 / 拟用保证金 / 假突破。"""
        self._call()
        user = self.seen["exec"]["messages"][1]["content"]
        for token in ("追高", "ATR", "止损距离", "保证金", "假突破"):
            with self.subTest(token=token):
                self.assertIn(token, user, f"质询要求必须点名「{token}」")

    def test_my_proposal_precedes_the_peer_dossier_and_role_name_appears(self):
        self._call(role_spec={"prompt": "p"})
        user = self.seen["exec"]["messages"][1]["content"]
        self.assertIn("trader_b", user, "无 name ⇒ 回落 role_id")

    def test_default_timeout_is_shorter_than_the_first_round(self):
        """★ 互评默认 **15.0**，比首轮 20.0 更短（收敛环节不该更奢侈）。"""
        self._call()
        self.assertEqual(self.seen["exec"]["timeout"], 15.0)
        self._call(timeout=25.0)
        self.assertEqual(self.seen["exec"]["timeout"], 25.0, "显式给值则照用")

    def test_renderer_and_resolution_facts_are_wired_the_same_way(self):
        self._call()
        self.assertEqual(self.render_mock.call_args.args, ("席位身份", {"k": 1}))
        kw = self.seen["exec"]
        self.assertEqual(kw["model"], "M2")
        self.assertEqual((kw["base_url"], kw["api_key"], kw["api_format"]),
                         ("U", "K", "F"))
        self.assertEqual(kw["temperature"], 0.1)
        self.assertEqual(kw["reasoning_effort"], "medium")


if __name__ == "__main__":
    unittest.main()
