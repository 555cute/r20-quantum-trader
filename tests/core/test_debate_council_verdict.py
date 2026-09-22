"""投委会**裁决出口**：`(brain_output, council_transcript)` 与 CIO 输出解析（第二百三十五刀）。

先读 `return` 再动笔（第 632 行）。实测结构：

```
council_transcript = {council_mode, council_architecture, consensus_mode, total_duration_ms,
                      arbitrator: {role_name, model_used, model_requested, model_registered,
                                   model_fallback, model_note, latency_ms, reasoning},
                      advisors: trader_proposals,
                      cross_examinations: 跨审才有，其它模式为 {} }
brain_output["council_transcript"] = council_transcript      # 同一个对象
```

| 语义 | 口径 |
|---|---|
| ★ **降级席位 weight=0.0** | 席位抛错时登记的提案 `weight=0.0` —— 本刀**终于能断言**（第 233 刀标注为"未断言"）|
| ★ 采纳轨迹 | `_normalize_cio_adopted_roles(brain_output, roles, trader_keys)` 在返回前被调用（`adopted_role` 可追溯）|
| ★ 围栏剥离 | CIO 输出被 ```json / ``` 包裹时也要能解析（去掉首尾围栏再 `json.loads`）|
| ★ 根必须是对象 | 解析结果不是 dict ⇒ **`ValueError`**（「CIO output root must be a JSON object」）|
| 裁决载荷注入 | `brain_output["council_transcript"]` **与第二个返回值是同一对象** |
| 模式差异 | 标准模式 ⇒ `cross_examinations == {}`；跨审 ⇒ 有内容 |
| 时钟 | `total_duration_ms` 是**整数毫秒**（`time.time()-t_start`）|
"""

import unittest
from unittest import mock

from r20_backend.council.debate import execute_council_debate

ROLES = {
    "cio": {"name": "首席", "is_arbitrator": True, "prompt": "p"},
    "a": {"name": "甲", "prompt": "p"},
    "b": {"name": "乙", "prompt": "p"},
}
RESOLVED = {"model": "M", "base_url": "U", "api_key": "K", "api_format": "F",
            "effort": "high", "requested": "M-req", "registered": True,
            "fallback": False, "reason": "已登记"}


class VerdictTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch("r20_backend.council.debate.time.sleep")
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch("r20_backend.llm_manager.get_active_llm_runtime",
                        return_value={"model": "M-active"})
        p2.start()
        self.addCleanup(p2.stop)
        p3 = mock.patch("r20_backend.council.debate._normalize_cio_adopted_roles")
        self.normalize = p3.start()
        self.addCleanup(p3.stop)

    def _run(self, mode="standard", cio_payload='{"decisions": []}', trader=None):
        def _load_config():
            return {"roles": ROLES, "consensus_mode": mode}

        def _call_trader(*args, **kwargs):
            if trader is not None:
                return trader(args[0], args[1])
            return {"proposal_id": f"{args[0]}_prop", "role_name": args[1].get("name"),
                    "status": "ok", "content": "提案", "weight": 1.0,
                    "model_used": "M", "model_fallback": False}

        def _call_critique(*args, **kwargs):
            return {"role_id": args[0], "role_name": "?", "status": "ok", "content": "质询"}
        p = mock.patch("r20_backend.llm_manager.execute_llm_request",
                       return_value=(cio_payload, "思考", {}, 12.5))
        p.start()
        self.addCleanup(p.stop)
        return execute_council_debate(_load_config, lambda s: dict(RESOLVED), _call_trader,
                                      _call_critique, "市场", "系统", timeout=150.0,
                                      runtime_context=None)

    def test_transcript_skeleton_and_arbitrator_facts(self):
        brain, transcript = self._run()
        self.assertIs(transcript["council_mode"], True)
        self.assertEqual(transcript["council_architecture"], "Hedge Fund Investment Committee")
        self.assertEqual(transcript["consensus_mode"], "standard")
        self.assertIsInstance(transcript["total_duration_ms"], int)
        arb = transcript["arbitrator"]
        self.assertEqual(sorted(arb), ["latency_ms", "model_fallback", "model_note",
                                       "model_registered", "model_requested", "model_used",
                                       "reasoning", "role_name"])
        self.assertEqual(arb["model_requested"], "M-req")
        self.assertEqual(arb["model_registered"], True)
        self.assertEqual(arb["latency_ms"], 12.5)
        self.assertEqual(arb["reasoning"], "思考")

    def test_the_degraded_seat_is_recorded_with_zero_weight(self):
        """★ 第 233 刀标注"未断言"的那条，现在断言：异常席位 ⇒ **`weight=0.0`**。

        （当时看不到是因为提案字典是函数内部状态；读到末尾 `return` 后，它随
        `transcript["advisors"]` 一并交出来了。）
        """
        def _trader(key, spec):
            if key == "b":
                raise RuntimeError("席位崩了")
            return {"proposal_id": "a_prop", "role_name": "甲", "status": "ok",
                    "content": "提案", "weight": 1.0}
        _brain, transcript = self._run(trader=_trader)
        advisors = transcript["advisors"]
        self.assertEqual(advisors["b"]["status"], "error")
        self.assertEqual(advisors["b"]["weight"], 0.0, "**沉默不得变赞成**")
        self.assertIn("席位崩了", advisors["b"]["content"])
        self.assertEqual(advisors["a"]["weight"], 1.0, "正常席位权重不受影响")

    def test_transcript_is_injected_into_the_brain_output_as_the_same_object(self):
        brain, transcript = self._run()
        self.assertIs(brain["council_transcript"], transcript)

    def test_adoption_traceability_normaliser_is_wired(self):
        """★ 采纳轨迹：返回前必须把 (brain_output, roles, trader_keys) 交给归一器。"""
        self._run()
        self.assertTrue(self.normalize.called)
        args = self.normalize.call_args.args
        self.assertIsInstance(args[0], dict)
        self.assertIs(args[1], ROLES)
        self.assertEqual(sorted(args[2]), ["a", "b"], "只对启用席位做采纳归一")

    def test_markdown_fences_around_the_cio_json_are_stripped(self):
        """★ CIO 输出常被 ```json 包裹 ⇒ 必须先剥围栏再解析。"""
        brain, _t = self._run(cio_payload='```json\n{"decisions": [{"action": "WAIT"}]}\n```')
        self.assertEqual(brain["decisions"], [{"action": "WAIT"}])

    def test_a_non_object_root_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self._run(cio_payload='[1, 2, 3]')
        self.assertIn("JSON object", str(ctx.exception))

    def test_standard_mode_has_no_cross_examinations_section(self):
        _b, transcript = self._run(mode="standard")
        self.assertEqual(transcript["cross_examinations"], {},
                         "标准模式不给交叉质询字段塞东西")
        _b2, cross = self._run(mode="cross_examination")
        self.assertEqual(sorted(cross["cross_examinations"]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
