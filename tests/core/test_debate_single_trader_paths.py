"""席位调用的**成功/重试/降级**三态（第二百一十八刀）—— 补齐上一刀的边界。

| 状态 | 口径 |
|---|---|
| 成功 | `status=ok`；`content`/`reasoning` **去空白**；`model_used` 在无覆盖时回落**当前活跃模型**；`weight` 缺省 1.0；解析事实（requested/registered/fallback/note）**照实带出** |
| ★ 重试 | **只有**错误含 504/502/timeout **且** `timeout > 35` 才退避 1.5s 重试**一次**：强度 `high→medium`、剩余预算收紧为 `max(20, timeout-20)`、且**允许回落**（保住席位优先）|
| ★ 降级 | 重试仍失败 ⇒ `status=error`、内容写明「异常/超时降级」、`latency_ms=0`，且 **`weight=0.0`** —— **降级的席位不得参与表决权重** |

★ 那条 `weight=0.0` 是本刀最要紧的一条：委员会里一个**没答出来的席位**若仍按 1.0 计权，
会让「沉默」变成「赞成」。
"""

import unittest
from unittest.mock import patch

from r20_backend.council.debate import _call_single_trader

SPEC = {"name": "老张", "prompt": "身份", "temperature": 0.2, "weight": 1.7}


def _resolved():
    return {"model": "M1", "base_url": "U", "api_key": "K", "api_format": "F",
            "effort": "high", "requested": "M-req", "registered": True,
            "fallback": False, "reason": "已登记"}


class SeatOutcomeTest(unittest.TestCase):
    def setUp(self):
        self.p_render = patch("r20_backend.council.debate._render_seat_prompt",
                              return_value="身份")
        self.p_render.start()
        self.addCleanup(self.p_render.stop)
        self.p_sleep = patch("r20_backend.council.debate.time.sleep")
        self.sleep_mock = self.p_sleep.start()
        self.addCleanup(self.p_sleep.stop)
        self.p_runtime = patch("r20_backend.llm_manager.get_active_llm_runtime",
                               return_value={"model": "M-active"})
        self.p_runtime.start()
        self.addCleanup(self.p_runtime.stop)

    def _call(self, outcomes, spec=None, timeout=60.0, resolved=None):
        calls = []

        def _exec(**kw):
            calls.append(kw)
            out = outcomes[len(calls) - 1]
            if isinstance(out, Exception):
                raise out
            return out
        p = patch("r20_backend.llm_manager.execute_llm_request", side_effect=_exec)
        p.start()
        self.addCleanup(p.stop)
        self.calls = calls
        return _call_single_trader(lambda s: resolved or _resolved(), "trader_a",
                                   dict(spec or SPEC), "市场", "宪法", timeout=timeout,
                                   runtime_context=None)

    def test_success_strips_text_and_surfaces_the_resolution_facts(self):
        out = self._call([("  正文  ", "  思考  ", {}, 12.5)], timeout=20.0)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["content"], "正文", "去空白")
        self.assertEqual(out["reasoning"], "思考")
        self.assertEqual(out["weight"], 1.7, "权重取自角色规格")
        self.assertEqual((out["model_requested"], out["model_registered"],
                          out["model_fallback"], out["model_note"]),
                         ("M-req", True, False, "已登记"), "解析事实照实带出")
        self.assertEqual(out["model_used"], "M1")
        self.assertFalse(self.calls[0]["allow_fallback"], "首次调用不允许回落（优先登记模型）")

    def test_missing_model_falls_back_to_the_active_runtime(self):
        out = self._call([("x", "", {}, 1.0)], resolved={**_resolved(), "model": ""})
        self.assertEqual(out["model_used"], "M-active", "无覆盖 ⇒ 当前活跃模型")
        self.assertEqual(out["reasoning"], "", "空思考给空串（不是 None）")

    def test_empty_reasoning_is_normalised_to_empty_string(self):
        out = self._call([("x", None, {}, 1.0)], timeout=20.0)
        self.assertEqual(out["reasoning"], "")

    def test_short_timeout_is_not_retried(self):
        """★ `timeout <= 35` 时不重试 —— 短预算不值得翻倍消耗。"""
        out = self._call([RuntimeError("504 gateway timeout")], timeout=20.0)
        self.assertEqual(len(self.calls), 1, "只调用一次")
        self.assertEqual(out["status"], "error")
        self.assertFalse(self.sleep_mock.called)

    def test_non_gateway_errors_are_not_retried(self):
        out = self._call([RuntimeError("400 bad request")], timeout=60.0)
        self.assertEqual(len(self.calls), 1, "非 502/504/timeout ⇒ 不重试")
        self.assertIn("400 bad request", out["content"])

    def test_long_timeout_gateway_error_retries_once_with_lower_effort(self):
        """★ 退避重试的**全部参数**都要对：降强度、收紧预算、允许回落。"""
        out = self._call([RuntimeError("502 bad gateway"), ("重试正文", "", {}, 3.0)],
                         timeout=60.0)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["content"], "重试正文")
        self.assertEqual(len(self.calls), 2)
        retry = self.calls[1]
        self.assertEqual(retry["reasoning_effort"], "medium", "high ⇒ 降为 medium")
        self.assertEqual(retry["timeout"], 40.0, "剩余预算 = timeout - 20")
        self.assertTrue(retry["allow_fallback"], "重试允许回落（保住席位优先）")
        self.assertTrue(self.sleep_mock.called, "先退避")

    def test_medium_effort_is_not_downgraded_further(self):
        out = self._call([RuntimeError("504"), ("ok", "", {}, 1.0)], timeout=60.0,
                         resolved={**_resolved(), "effort": "medium"})
        self.assertEqual(self.calls[1]["reasoning_effort"], "medium", "非 high 保持原强度")

    def test_degraded_seat_has_zero_weight(self):
        """★ **降级的席位权重为 0**：没答出来的席位不得让「沉默」变成「赞成」。"""
        out = self._call([RuntimeError("boom")], timeout=20.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["weight"], 0.0)
        self.assertEqual(out["latency_ms"], 0)
        self.assertEqual(out["model_used"], "M1",
                         "有覆盖 ⇒ 降级仍报**覆盖模型**（降级不等于换模型）")
        # ⚠️ 我原以为降级一律报 unknown —— 错：判据是 `override_model or unknown`。
        no_override = self._call([RuntimeError("boom")], timeout=20.0,
                                 resolved={**_resolved(), "model": ""})
        self.assertEqual(no_override["model_used"], "unknown",
                         "**没有**覆盖时才回落为 unknown")
        self.assertIn("降级", out["content"])
        self.assertIn("boom", out["content"], "错误信息带出，便于面板/日志定位")

    def test_retry_failure_reports_the_retry_error(self):
        """⚠️ **实测边界（列待议）**：重试也失败时，上报的是**重试的**那个错误，
        原始错误被覆盖 —— 排查时可能丢掉第一次失败的线索。本刀只钉现状。"""
        out = self._call([RuntimeError("首次失败: 504"), RuntimeError("重试失败: 504")],
                         timeout=60.0)
        self.assertIn("重试失败", out["content"])
        self.assertNotIn("首次失败", out["content"], "原始错误被覆盖（现状，列待议）")


if __name__ == "__main__":
    unittest.main()
