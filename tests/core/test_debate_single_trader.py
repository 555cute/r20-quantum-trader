"""席位调用：提示词装配与**模型解析事实**的透传（第二百一十七刀）。

`_call_single_trader` 把「最高交易宪法 + 席位身份」拼成 system prompt、「市场全景 + 强制报价单格式」
拼成 user prompt，再交给 LLM 执行层。本刀钉住装配与透传的口径：

| 语义 | 口径 |
|---|---|
| ★ 审计 P1-4b | `resolve_seat(role_spec)` 的返回值**逐字**作为模型覆盖；`model` 为假值 ⇒ 传 **`None`**（不是空串），未登记模型不再**静默回落** |
| ★ 宪法在前 | system prompt 里**宪法先于身份**（顺序即语义：违反宪法框架的提案无效）|
| 渲染接线 | 席位提示词经 `_render_seat_prompt(prompt, runtime_context)`（**context 透传**，与提示词渲染器同一条链）|
| 提案标识 | `{role_id}_prop` 同时出现在 system 与 user（供 CIO 横向比对）|
| 席位名 | `role_spec["name"]` 缺省回落 `role_id` |
| 市场数据 | `market_prompt` 原文进入 user prompt |
| 强制格式 | user prompt 要求「无明确结论的标的也必须列 WAIT 行」（**不漏**）|
| 执行层入参 | `messages`（system+user）、四个覆盖项、`reasoning_effort`、`temperature`、`timeout` 原样透传 |

⚠️ 边界：**本刀不验证调用之后的行为**（该函数后半段我尚未读）——测例只断言**调用参数**。
"""

import unittest
from unittest.mock import patch

from r20_backend.council.debate import _call_single_trader

ROLE_SPEC = {"name": "老张", "prompt": "席位提示词:{{macro_4h}}", "temperature": 0.35}


class SingleTraderCallTest(unittest.TestCase):
    def setUp(self):
        self.seen = {}
        self.rv = []

        def _resolve(spec):
            self.seen["resolve_arg"] = spec
            return {"model": "", "base_url": "U", "api_key": "K", "api_format": "F",
                    "effort": "E", "registered": False, "fallback": True}
        self.resolve = _resolve

        def _exec(**kwargs):
            self.seen["exec"] = kwargs
            self.rv.append(kwargs)
            return "正文\nBTC-USDT-SWAP | WAIT | - | - | - | - | 55 | 箱体", "思考", {}, 1.5
        self.p_exec = patch("r20_backend.llm_manager.execute_llm_request", side_effect=_exec)
        self.exec_mock = self.p_exec.start()
        self.addCleanup(self.p_exec.stop)

        self.p_render = patch("r20_backend.council.debate._render_seat_prompt",
                              return_value="渲染后的身份")
        self.render_mock = self.p_render.start()
        self.addCleanup(self.p_render.stop)

    def _call(self, **over):
        kwargs = dict(role_id="trader_a", role_spec=dict(ROLE_SPEC), market_prompt="市场全景数据",
                      master_constitutional_rules="宪法第一条", timeout=7.5,
                      runtime_context={"macro_4h": "上"})
        kwargs.update(over)
        try:
            return _call_single_trader(self.resolve, **kwargs)
        except Exception:               # 后半段未读 ⇒ 只关心调用参数
            return None

    def test_model_override_uses_the_resolved_facts_verbatim(self):
        """★ 审计 P1-4b：空模型 ⇒ 传 `None`（不是空串），且四个覆盖项逐字透传。"""
        self._call()
        kw = self.seen["exec"]
        self.assertIsNone(kw["model"], "空模型必须传 None（未登记不再静默回落）")
        self.assertEqual((kw["base_url"], kw["api_key"], kw["api_format"]),
                         ("U", "K", "F"), "解析出的覆盖项逐字使用")
        self.assertEqual(kw["reasoning_effort"], "E")
        self.assertEqual(self.seen["resolve_arg"], ROLE_SPEC, "resolve_seat 收到原样的角色规格")

    def test_prompts_carry_constitution_first_then_identity(self):
        self._call()
        system = self.seen["exec"]["messages"][0]
        self.assertEqual(system["role"], "system")
        text = system["content"]
        self.assertIn("宪法第一条", text)
        self.assertIn("渲染后的身份", text)
        self.assertLess(text.index("宪法第一条"), text.index("渲染后的身份"),
                        "宪法必须排在身份之前（顺序即语义）")

    def test_seat_prompt_is_rendered_with_the_runtime_context(self):
        self._call()
        self.assertEqual(self.render_mock.call_args.args,
                         ("席位提示词:{{macro_4h}}", {"macro_4h": "上"}),
                         "席位提示词必须经同一渲染器，且 context 透传")

    def test_proposal_id_and_role_name_appear_in_the_user_prompt(self):
        self._call()
        user = self.seen["exec"]["messages"][1]
        self.assertEqual(user["role"], "user")
        self.assertIn("trader_a_prop", user["content"], "提案标识供 CIO 横向比对")
        self.assertIn("老张", user["content"], "席位名出现")
        self.assertIn("市场全景数据", user["content"], "市场数据原文进入")

    def test_output_format_demands_a_wait_row_for_every_instrument(self):
        """★ 「不漏」：没有明确结论的标的也**必须**列一行 WAIT（否则 CIO 看不到它）。"""
        self._call()
        user = self.seen["exec"]["messages"][1]["content"]
        self.assertIn("WAIT 行", user)
        self.assertIn("横向对比", user)

    def test_role_name_falls_back_to_the_role_id(self):
        self._call(role_spec={"prompt": "p", "temperature": 0.2})
        user = self.seen["exec"]["messages"][1]["content"]
        self.assertIn("trader_a", user, "无 name ⇒ 用 role_id")

    def test_models_and_timeout_reach_the_executor(self):
        self._call()
        self.assertEqual(self.seen["exec"]["timeout"], 7.5, "timeout 原样透传")

    def test_temperature_defaults_to_zero_point_two(self):
        self._call(role_spec={"prompt": "p"})
        self.assertEqual(self.seen["exec"]["temperature"], 0.2)
        self._call(role_spec={"prompt": "p", "temperature": "0.9"})
        self.assertEqual(self.seen["exec"]["temperature"], 0.9, "字符串也接受并转 float")


if __name__ == "__main__":
    unittest.main()
