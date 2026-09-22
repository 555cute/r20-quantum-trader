"""载荷瘦身：**省略必须显式留痕，缺失不得代填**（第二百四十刀）。

`r20_backend/dashboard_payload/slim.py` 是**纯函数**（不改入参、不读文件、不碰全局）。

| 语义 | 口径 |
|---|---|
| ★ 纯函数 | 顶层浅拷贝 ⇒ **入参不被修改** |
| ★ 历史裁剪 | 前 5 条完整；第 6 条起删 `top_opportunities`/`position_management`/`policy_snapshot`，并在**行内**写 `_trimmed=[被裁键名]`；非 dict 条目原样保留 |
| ★ **提示词去重** | 行内 `ai_last_prompt` 与顶层**同文**（前 200 字符相同且长度 ≥2000）⇒ 删掉并写 `ai_last_prompt_chars` + `ai_last_prompt_ref="top_level"`；**短存根与不同文原样保留** |
| ★ trades 上限 | `SLIM_TRADES == LEDGER_TRADES_MAX`（**与台账视图同一事实源**）—— 见下方事故记录 |
| 裁最近 | `trades[-N:]`、`logs[-20:]`（保留**最近**的，不是最早的）|
| 未超限 | 既不裁剪也**不留痕**（`omitted` 里不出现该键）|
| ★ 幂等 | 对**已瘦身**载荷再跑 ⇒ 上一轮 `_meta.omitted` **保留**（否则留痕消失 ⇒ 前端把被裁数据当完整 ⇒ "UI 说谎"）|
| ★ `_meta` 自述 | `slim=True` + `full_payload="/api/all?full=1"` + note「省略项均已显式留痕；**缺失一律不用 0 或空值代填**」|

## ★ 事故记录（写在常量注释里，本刀用断言把它钉住）

2026-09-16 修：`SLIM_TRADES` 原为 **20**，而台账视图上限是 **60** ⇒ 默认瘦身把 34 笔已平仓台账砍到
最近 20 笔（`_meta.omitted.trades` 有留痕，但**前端从不读**）⇒ 台账页少 14 行，且
「累计平仓 / 胜率 / 净盈亏 / 手续费」**全在被砍的切片上聚合**。
结论：**瘦身不得比视图本身更紧**。代价量化：34 行 16.4KB vs 20 行 9.6KB（+6.8KB ≈ 瘦身载荷的 +2.9%）。
"""

import copy
import unittest

from r20_backend.dashboard_payload import slim as S
from r20_backend.dashboard_payload.ledger_view import LEDGER_TRADES_MAX


def _history(n, prompt=None):
    rows = []
    for i in range(n):
        row = {"index": i, "top_opportunities": [1], "position_management": {"a": 1},
               "policy_snapshot": {"b": 2}}
        if prompt is not None:
            row["ai_last_prompt"] = prompt
        rows.append(row)
    return rows


class SlimPayloadTest(unittest.TestCase):
    def test_trims_only_after_the_kept_full_entries(self):
        data = {"ai_brain_history": _history(7)}
        out = S.slim_payload(data)
        rows = out["ai_brain_history"]
        self.assertEqual([r["index"] for r in rows], list(range(7)), "顺序不变")
        for row in rows[:S.SLIM_HISTORY_FULL_ENTRIES]:
            self.assertIn("top_opportunities", row, "前 5 条完整")
            self.assertNotIn("_trimmed", row)
        for row in rows[S.SLIM_HISTORY_FULL_ENTRIES:]:
            self.assertNotIn("top_opportunities", row)
            self.assertEqual(row["_trimmed"], list(S.SLIM_HISTORY_DROP_KEYS),
                             "行内必须写明**被裁了哪些键**")
        meta = out["_meta"]["omitted"]["ai_brain_history"]
        self.assertEqual(meta["kept_full"], S.SLIM_HISTORY_FULL_ENTRIES)
        self.assertEqual((meta["total"], meta["trimmed_entries"]), (7, 2))
        self.assertEqual(meta["dropped_fields"], list(S.SLIM_HISTORY_DROP_KEYS))
        self.assertEqual(meta["full"], "/api/v1/cache/brain-history")

    def test_the_input_payload_is_not_mutated(self):
        data = {"ai_brain_history": _history(7)}
        snapshot = copy.deepcopy(data)
        S.slim_payload(data)
        self.assertEqual(data, snapshot, "纯函数：入参一个字节都不许改")

    def test_non_dict_history_entries_survive_untouched(self):
        data = {"ai_brain_history": _history(6) + ["裸字符串"]}
        out = S.slim_payload(data)
        self.assertEqual(out["ai_brain_history"][-1], "裸字符串")

    def test_identical_long_prompts_are_elided_with_a_reference(self):
        long_prompt = "X" * 3000
        data = {"ai_last_prompt": long_prompt, "ai_brain_history": _history(6, long_prompt)}
        out = S.slim_payload(data)
        row = out["ai_brain_history"][5]
        self.assertNotIn("ai_last_prompt", row, "同文的整段提示词不再重复下发")
        self.assertEqual(row["ai_last_prompt_chars"], 3000, "只留字数")
        self.assertEqual(row["ai_last_prompt_ref"], "top_level", "并指向顶层那份")
        self.assertIn("ai_brain_history.prompt_text", out["_meta"]["omitted"])
        self.assertEqual(out["ai_last_prompt"], long_prompt, "顶层那份**仍完整**（白盒承诺）")

    def test_short_stubs_and_different_prompts_are_kept(self):
        """★ 只对"同文的长提示词"下手：210 字存根与不同文的都原样保留。"""
        short = "S" * 210
        different = "Y" * 3000
        data = {"ai_last_prompt": "Z" * 3000, "ai_brain_history": _history(6)}
        data["ai_brain_history"][5]["ai_last_prompt"] = short
        data["ai_brain_history"][4]["ai_last_prompt"] = different
        out = S.slim_payload(data)
        self.assertEqual(out["ai_brain_history"][5]["ai_last_prompt"], short, "短存根保留")
        self.assertEqual(out["ai_brain_history"][4]["ai_last_prompt"], different, "不同文保留")

    def test_long_but_different_prompts_are_not_elided(self):
        """★ 第 66 行的分支：**够长但不同文**（前 200 字符就不同）⇒ 原样保留。

        （上一刀只覆盖了"短存根"与"同文"两条，这一条是探针指出来的最后一行。）
        """
        top = "T" * 3000
        mine = "U" * 3000
        data = {"ai_last_prompt": top, "ai_brain_history": _history(6)}
        data["ai_brain_history"][5]["ai_last_prompt"] = mine
        out = S.slim_payload(data)
        row = out["ai_brain_history"][5]
        self.assertEqual(row["ai_last_prompt"], mine, "不同文 ⇒ 不去重")
        self.assertNotIn("ai_last_prompt_elided", row)
        self.assertNotIn("ai_brain_history.prompt_text", out["_meta"]["omitted"],
                         "没有任何一条被去重 ⇒ 不留痕")

    def test_non_dict_rows_inside_the_prompt_elision_loop(self):
        """★ 第 66 行：**顶层有提示词**时，去重循环会遇到非 dict 行 ⇒ 必须跳过而不是炸。

        ⚠️ 我上一刀把第 66 行说成"够长但不同文"的分支 —— **错**（那是第 68 行），
        所以用例加了、探针却仍标 66 未命中。这次先**打印**那一行再动笔。
        """
        data = {"ai_last_prompt": "abc", "ai_brain_history": _history(6) + ["裸字符串"]}
        out = S.slim_payload(data)
        self.assertEqual(out["ai_brain_history"][-1], "裸字符串", "非 dict 行原样保留")
        self.assertNotIn("ai_brain_history.prompt_text", out["_meta"]["omitted"],
                         "没有一条被去重 ⇒ 不留痕")

    def test_review_prompt_is_deduplicated_against_the_top_level_copy(self):
        long_prompt = "P" * 4000
        data = {"ai_last_prompt": long_prompt, "review": {"ai_last_prompt": long_prompt,
                                                          "comment": "ok"}}
        out = S.slim_payload(data)
        self.assertNotIn("ai_last_prompt", out["review"])
        self.assertEqual(out["review"]["ai_last_prompt_chars"], 4000)
        self.assertEqual(out["review"]["ai_last_prompt_ref"], "top_level")
        self.assertEqual(out["review"]["comment"], "ok", "同字典其它键保留")
        self.assertIn("review.ai_last_prompt", out["_meta"]["omitted"])

    def test_trades_limit_matches_the_ledger_view(self):
        """★ 瘦身**不得比视图本身更紧**（2026-09-16 事故的修复点）。"""
        self.assertEqual(S.SLIM_TRADES, LEDGER_TRADES_MAX,
                         "同一事实源：瘦身上限 = 台账视图上限")
        rows = [{"i": i} for i in range(LEDGER_TRADES_MAX + 5)]
        out = S.slim_payload({"trades": rows})
        self.assertEqual(len(out["trades"]), LEDGER_TRADES_MAX)
        self.assertEqual(out["trades"][-1]["i"], LEDGER_TRADES_MAX + 4, "保留**最近**的")
        self.assertEqual(out["_meta"]["omitted"]["trades"],
                         {"kept": LEDGER_TRADES_MAX, "total": LEDGER_TRADES_MAX + 5,
                          "full": "/api/v1/cache/ledger"})

    def test_untrimmed_sections_leave_no_trace(self):
        out = S.slim_payload({"trades": [{"i": 1}], "logs": ["a"]})
        self.assertNotIn("trades", out["_meta"]["omitted"], "未超限就不留痕")
        self.assertNotIn("logs", out["_meta"]["omitted"])

    def test_logs_are_capped_and_traced_without_a_full_pointer(self):
        logs = [f"l{i}" for i in range(S.SLIM_LOGS + 3)]
        out = S.slim_payload({"logs": logs})
        self.assertEqual(out["logs"], logs[-S.SLIM_LOGS:])
        self.assertEqual(out["_meta"]["omitted"]["logs"],
                         {"kept": S.SLIM_LOGS, "total": S.SLIM_LOGS + 3})

    def test_meta_block_self_describes_and_is_idempotent(self):
        """★ `_meta` 自述 + **幂等**：二次瘦身必须**保留**上一轮的省略留痕。"""
        out = S.slim_payload({"ai_brain_history": _history(8)})
        self.assertIs(out["_meta"]["slim"], True)
        self.assertEqual(out["_meta"]["full_payload"], "/api/all?full=1")
        self.assertIn("不用 0 或空值代填", out["_meta"]["note"])
        again = S.slim_payload(out)
        self.assertEqual(again["_meta"]["omitted"], out["_meta"]["omitted"],
                         "留痕不得因为再过一遍而消失")
        self.assertEqual(again["_meta"]["omitted"]["ai_brain_history"]["total"], 8)

    def test_empty_payload_still_gets_the_meta_block(self):
        out = S.slim_payload({})
        self.assertIs(out["_meta"]["slim"], True)
        self.assertEqual(out["_meta"]["omitted"], {})


if __name__ == "__main__":
    unittest.main()
