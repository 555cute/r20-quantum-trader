"""仿真回放引擎（`r20_backend/sandbox/replay.py`）残余分支收口测试 —— 第 368 刀。

本模块 197 行，负责回测沙箱确定性历史回放驱动、逐 K 线策略调用与绩效指标计算：
- 指标结构序列化（`ReplayMetrics.to_dict()` 转为完整字典输出）；
- 空历史时间戳集快速短路（`symbols_candles` 无有效蜡烛线时间戳时直接返回初始资金指标对象）。
"""
from __future__ import annotations

import unittest

# 优先导入 exchanges 避免模块级循环引用
import r20_backend.exchanges  # noqa: F401
from r20_backend.sandbox.replay import PointInTimeReplayEngine, ReplayMetrics


class SandboxReplayTailsTests(unittest.TestCase):
    def test_replay_metrics_to_dict(self):
        # 验证指标对象序列化为字典 (line 30)
        metrics = ReplayMetrics(
            initial_equity=10000.0,
            final_equity=12500.0,
            total_return_pct=25.0,
            max_drawdown_pct=4.2,
        )
        d = metrics.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["initial_equity"], 10000.0)
        self.assertEqual(d["final_equity"], 12500.0)
        self.assertEqual(d["total_return_pct"], 25.0)

    def test_run_replay_empty_candles_returns_default_metrics(self):
        # 无任何历史蜡烛数据时短路返回默认指标 (lines 78-79)
        engine = PointInTimeReplayEngine(initial_balance=8888.0)
        res = engine.run_replay(
            symbols_candles={},
            strategy_step_fn=lambda adapter, ts: None,
            bar="15m",
        )
        self.assertIsInstance(res, ReplayMetrics)
        self.assertEqual(res.initial_equity, 8888.0)
        self.assertEqual(res.final_equity, 8888.0)
        self.assertEqual(res.equity_curve, [])


if __name__ == "__main__":
    unittest.main()
