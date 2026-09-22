"""`debate.py` 收口：**两条导入路径都不通**时的兜底行为（第二百三十六刀）。

这一批分支平时**永远走不到**（依赖可导入），但每条都代表一种"系统处于更差环境"时的承诺：

| 位置 | 兜底承诺 |
|---|---|
| `_render_seat_prompt` 两条导入都失败 | **返回原文** —— 宁可把占位符原样交给模型，也不要交一份**空提示词**（空提示词等于撤掉席位身份）|
| 杠杆常量两条导入都失败 | 回落到 **5.0 / 2.0**（不能让委员会因缺常量而崩）|
| `MIN > MAX` | 夹成 `MIN = MAX`（不出现倒挂区间）|
| 质询调用抛错 | 该席位记 `status='error'` + 内容带异常 + `latency_ms=0`；**注意：质询错误字典里没有 `weight`**（与首轮提案错误字典**形态不同** —— 质询不是投票）|
| CIO 输出裸 ``` 围栏 | 也要剥掉（不只有 ```json）|
"""

import sys
import unittest
from unittest import mock

from r20_backend.council import debate as D


class RenderSeatPromptFallbackTest(unittest.TestCase):
    def test_returns_the_template_verbatim_when_both_imports_fail(self):
        """★ 两条渲染器导入路径都不可用 ⇒ **返回原文**（不是空串）。"""
        with mock.patch.dict(sys.modules, {"prompt_library": None,
                                           "scripts.prompt_library": None}):
            out = D._render_seat_prompt("身份:{{macro_4h}}", {"macro_4h": "上"})
        self.assertEqual(out, "身份:{{macro_4h}}",
                         "宁可原样（预览语义），也不要空提示词")


class LeverageFallbackTest(unittest.TestCase):
    def _run(self, env=None, mode="standard"):
        def _load_config():
            return {"roles": {"cio": {"name": "首席", "is_arbitrator": True}}, "consensus_mode": mode}
        ctx = mock.patch.dict("os.environ", env or {}, clear=False)
        ctx.start()
        self.addCleanup(ctx.stop)
        try:
            D.execute_council_debate(_load_config, lambda s: {}, lambda *a, **k: {},
                                     lambda *a, **k: {}, "市场", "系统", timeout=1.0,
                                     runtime_context=None)
        except Exception as exc:
            return exc
        return None

    def test_missing_risk_constants_fall_back_without_crashing(self):
        """★ 杠杆常量两条路径都不可用 ⇒ 用**兜底 5.0/2.0**，委员会仍能走到预算预检。"""
        with mock.patch.dict(sys.modules, {"risk_constants": None,
                                           "scripts.risk_constants": None}):
            exc = self._run()
        self.assertIsInstance(exc, TimeoutError,
                              "兜底后继续执行到预算预检（不是 ImportError/NameError）")

    def test_inverted_leverage_bounds_are_clamped(self):
        """★ `MIN > MAX` ⇒ 夹成 `MIN = MAX`：走通该分支且不抛。

        ⚠️ 夹之后的值是**函数内的局部变量**，外部不可观测 —— 本用例只证明**该分支能走通**，
        **不声称**验证了夹取结果（那是读代码得到的结论）。
        """
        exc = self._run(env={"R20_MAX_LEVERAGE": "1", "R20_MIN_LEVERAGE": "9"})
        self.assertIsInstance(exc, TimeoutError, "倒挂区间不导致异常")


class CritiqueErrorShapeTest(unittest.TestCase):
    def test_critique_failure_returns_an_error_dict_without_weight(self):
        """★ 质询失败的返回字典：有 `status=error`/`latency_ms=0`，**没有 `weight`**。

        与首轮提案的错误字典（带 `weight: 0.0`）**形态不同** —— 质询不是投票，不参与权重。
        """
        def _boom(**kwargs):
            raise RuntimeError("网关 504")
        with mock.patch("r20_backend.llm_manager.execute_llm_request", side_effect=_boom):
            out = D._call_single_trader_critique(lambda s: {"model": "M", "base_url": "U",
                                                            "api_key": "K", "api_format": "F",
                                                            "effort": "high"},
                                                 "b", {"name": "乙", "prompt": "p"},
                                                 "我的提案", "同行卷宗", "宪法",
                                                 timeout=15.0, runtime_context=None)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["role_id"], "b")
        self.assertIn("504", out["content"])
        self.assertEqual(out["latency_ms"], 0)
        self.assertNotIn("weight", out, "质询不是投票 ⇒ 不带权重")


class FenceStrippingTest(unittest.TestCase):
    def _run(self, payload):
        def _load_config():
            return {"roles": {"cio": {"name": "首席", "is_arbitrator": True, "prompt": "p"},
                              "a": {"name": "甲", "prompt": "p"}}, "consensus_mode": "standard"}
        p = mock.patch("r20_backend.llm_manager.execute_llm_request",
                       return_value=(payload, "", {}, 1.0))
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch("r20_backend.llm_manager.get_active_llm_runtime",
                        return_value={"model": "M"})
        p2.start()
        self.addCleanup(p2.stop)
        p3 = mock.patch.object(D, "_normalize_cio_adopted_roles")
        p3.start()
        self.addCleanup(p3.stop)
        brain, _t = D.execute_council_debate(
            _load_config, lambda s: {"model": "M", "base_url": "U", "api_key": "K",
                                     "api_format": "F", "effort": "high", "requested": "r",
                                     "registered": True, "fallback": False, "reason": "n"},
            lambda *a, **k: {"proposal_id": "a_prop", "content": "提案", "weight": 1.0},
            lambda *a, **k: {}, "市场", "系统", timeout=150.0, runtime_context=None)
        return brain

    def test_a_bare_fence_is_stripped_too(self):
        """★ 不只 ```json：**裸 ```** 围栏同样要剥（否则 `json.loads` 直接失败）。"""
        brain = self._run("```\n{\"decisions\": [{\"action\": \"HOLD\"}]}\n```")
        self.assertEqual(brain["decisions"], [{"action": "HOLD"}])


class CritiqueErrorInOrchestrationTest(unittest.TestCase):
    """★ 编排层自己的质询异常分支（第 433 行）：与**函数层**那条是两处不同的兜底。

    （我上一刀只测了函数层 `_call_single_trader_critique` 的异常返回，编排层的这条没覆盖到 ——
    覆盖探针把差额指了出来。）
    """

    def test_a_failing_critique_lands_in_the_transcript_as_error(self):
        def _load_config():
            return {"roles": {"cio": {"name": "首席", "is_arbitrator": True, "prompt": "p"},
                              "a": {"name": "甲", "prompt": "p"},
                              "b": {"name": "乙", "prompt": "p"}},
                    "consensus_mode": "cross_examination"}

        def _boom(*a, **k):
            raise RuntimeError("质询崩了")
        for name, value in (("r20_backend.llm_manager.execute_llm_request", None),
                            ("r20_backend.llm_manager.get_active_llm_runtime",
                             {"model": "M"}),
                            ("r20_backend.council.debate._normalize_cio_adopted_roles", None)):
            target, ret = name, value
            if ret is None and target.endswith("execute_llm_request"):
                patcher = mock.patch(target, return_value=('{"decisions": []}', "", {}, 1.0))
            elif ret is None:
                patcher = mock.patch(target)
            else:
                patcher = mock.patch(target, return_value=ret)
            patcher.start()
            self.addCleanup(patcher.stop)
        p_sleep = mock.patch("r20_backend.council.debate.time.sleep")
        p_sleep.start()
        self.addCleanup(p_sleep.stop)
        _brain, transcript = D.execute_council_debate(
            _load_config,
            lambda s: {"model": "M", "base_url": "U", "api_key": "K", "api_format": "F",
                       "effort": "high", "requested": "r", "registered": True,
                       "fallback": False, "reason": "n"},
            lambda *a, **k: {"proposal_id": f"{a[0]}_prop", "role_name": a[1].get("name"),
                             "status": "ok", "content": "提案", "weight": 1.0},
            _boom, "市场", "系统", timeout=150.0, runtime_context=None)
        cross = transcript["cross_examinations"]
        self.assertEqual(sorted(cross), ["a", "b"], "两个席位都记下来了")
        self.assertEqual(cross["a"]["status"], "error")
        self.assertIn("质询崩了", cross["a"]["content"])
        self.assertEqual(cross["a"]["latency_ms"], 0)
        self.assertNotIn("weight", cross["a"], "质询不是投票 ⇒ 不带权重")


if __name__ == "__main__":
    unittest.main()
