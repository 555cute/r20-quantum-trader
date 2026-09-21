"""委员会开跑前的**前置检查**（第二百二十刀）。

`execute_council_debate` 很长，但它的**开跑前**几段是钱路相邻的硬口径，且都能用**早退**验证：

| 口径 | 语义 |
|---|---|
| ★ 杠杆契约 | 从 `risk_constants` 取上下限（双拼写回退 5.0/2.0），**允许环境变量覆盖**；`MIN > MAX` ⇒ `MIN = MAX`（不出现倒挂区间）|
| ★ 预算 fail-closed | `deadline = start + timeout`；剩余预算 `< MIN_SAFE_REASONING_TIME`（5s）⇒ **抛 `TimeoutError`**（**不开跑**，因为跑了也来不及）|
| 只夹上限 | 审计 P2-13：**只夹上限**（防手改配置撞调度器击杀）；**下限不夹** —— 显式传极小超时是「立即中止」的既有契约 |
| 共识模式 | 非法值 ⇒ **静默回落默认**（不抛）|

⚠️ 边界：本刀只验证**早退之前**的行为（用 `TimeoutError` 让函数停在开跑前）；
开跑之后的编排（坐席并发、时间线、CIO 仲裁）尚未逐段读，**不声称**。
"""

import os
import unittest
from unittest.mock import patch

from r20_backend.council.debate import execute_council_debate


class PreflightTest(unittest.TestCase):
    def _run(self, timeout, config=None, env=None):
        calls = {"config": 0}

        def _load_config():
            calls["config"] += 1
            return config if config is not None else {"roles": {}, "consensus_mode": "standard"}
        ctx = patch.dict(os.environ, env or {}, clear=False)
        ctx.start()
        self.addCleanup(ctx.stop)
        try:
            with patch("r20_backend.llm_manager.get_active_llm_runtime",
                       return_value={"model": "M"}):
                execute_council_debate(_load_config, lambda s: {}, lambda **k: {}, lambda **k: {},
                                       "市场", "系统", timeout=timeout, runtime_context=None)
            return "ran", calls
        except TimeoutError as exc:
            return exc, calls
        except Exception as exc:                     # 其它异常也如实带出（供断言/备案）
            return exc, calls

    def test_a_tiny_budget_fails_closed_before_running(self):
        """★ 预算不足 ⇒ **抛 `TimeoutError`**（跑了也来不及，不如明确失败）。"""
        out, calls = self._run(1.0)
        self.assertIsInstance(out, TimeoutError)
        self.assertIn("timeout", str(out).lower())
        self.assertIn("safety threshold", str(out))
        self.assertEqual(calls["config"], 1, "配置已读取（说明走到了前置检查）")

    def test_zero_and_negative_timeouts_are_also_refused(self):
        for value in (0.0, -5.0):
            with self.subTest(timeout=value):
                out, _ = self._run(value)
                self.assertIsInstance(out, TimeoutError,
                                      "下限被抬到 1.0 但仍在安全阈值之下 ⇒ 仍然拒跑")

    def test_invalid_consensus_mode_falls_back_without_raising(self):
        """★ 非法共识模式**静默回落**（不抛）—— 用「最终因预算不足而 TimeoutError」证明它没炸。"""
        out, calls = self._run(1.0, config={"roles": {}, "consensus_mode": "  不存在的模式  "})
        self.assertIsInstance(out, TimeoutError, "回落而非抛错（否则这里会是别的异常）")
        self.assertEqual(calls["config"], 1)

    def test_valid_env_overrides_do_not_break_the_preflight(self):
        out, _ = self._run(1.0, env={"R20_MAX_LEVERAGE": "7", "R20_MIN_LEVERAGE": "3"})
        self.assertIsInstance(out, TimeoutError, "合法覆盖不改变早退行为")

    def test_a_non_numeric_leverage_env_raises_value_error(self):
        """⚠️ **实测边界（列待议）**：`R20_MAX_LEVERAGE` 写成非数值 ⇒ `float()` 抛 **`ValueError`**，
        **整个委员会路径直接不可用**（没有兜底、也不是那句默认常量）。
        环境变量写错属运维常见失误 ⇒ 记为待议：应当回落到常量并留痕，而不是炸掉决策路径。
        """
        out, _ = self._run(1.0, env={"R20_MAX_LEVERAGE": "五倍"})
        self.assertIsInstance(out, ValueError)
        self.assertNotIsInstance(out, TimeoutError, "炸在读取杠杆之前，连前置检查都没走到")


if __name__ == "__main__":
    unittest.main()
