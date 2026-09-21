"""周期开跑前的只读形状预检（`data_shape_preflight_stage`，第 49 刀）。

这一格的定位很窄，也很重要：**只给可见性，不给行为**。

| 侧 | 负责 |
|---|---|
| 加载侧（`load_open_intents` / `load_trackers`）| 行为：读不出来 ⇒ fail-closed / 拒绝覆盖 |
| 本阶段 | 可见性：读得到但形状不合规 ⇒ 打印并指出下游后果 |

它要抓的是那些"读得到却会被静默忽略"的形状问题（比如追踪器键名拼写不合约定，
水位查找落空、状态静默丢失，而不触发任何异常）。所以本刀钉住三件事：

1. 两个校验器**各自拿到自己的路径**（接线错位会让预检看错文件）；
2. 违规行必须**带来源前缀**（意图/追踪器）——否则读者不知道去改哪个文件；
3. 无违规时也要**明确说"合规"**（沉默会与"没跑预检"混淆）；
4. 本阶段**只警告、不阻断、不写盘**：校验器抛错它不吞（照实上抛 ⇒ 周期停在预检阶段），
   但不产生任何副作用。
"""

import io
import unittest
from contextlib import redirect_stdout

from scripts.trader.cycle_stages import data_shape_preflight_stage


class ShapePreflightTest(unittest.TestCase):
    def _run(self, intents_lines=(), trackers_lines=(), *, raises=None):
        seen = []

        def validate_intents_file(path):
            seen.append(("意图", path))
            if raises == "intents":
                raise RuntimeError("checker boom")
            return list(intents_lines)

        def validate_trackers_file(path):
            seen.append(("追踪器", path))
            if raises == "trackers":
                raise RuntimeError("checker boom")
            return list(trackers_lines)

        buf = io.StringIO()
        with redirect_stdout(buf):
            out = data_shape_preflight_stage(intents_path="/tmp/intents.json",
                                             trackers_path="/tmp/trackers.json",
                                             validate_intents_file=validate_intents_file,
                                             validate_trackers_file=validate_trackers_file)
        return out, seen, buf.getvalue()

    def test_each_checker_receives_its_own_path(self):
        _, seen, _ = self._run()
        self.assertEqual(seen, [("意图", "/tmp/intents.json"), ("追踪器", "/tmp/trackers.json")],
                         f"接线错位会让预检看错文件：{seen}")

    def test_violations_carry_their_source_prefix_and_are_returned(self):
        out, _, printed = self._run(intents_lines=["缺 action"], trackers_lines=["键名不合约定"])
        self.assertEqual(out, ["[数据形状预检] 意图: 缺 action",
                              "[数据形状预检] 追踪器: 键名不合约定"])
        self.assertIn("意图: 缺 action", printed, "违规行要印出来（可见性靠它）")
        self.assertIn("追踪器: 键名不合约定", printed)

    def test_multiple_violations_from_one_checker_are_all_kept(self):
        out, _, _ = self._run(trackers_lines=["a", "b", "c"])
        self.assertEqual(len(out), 3, "一个校验器报多条时不许只留一条")

    def test_clean_data_says_so_explicitly(self):
        out, _, printed = self._run()
        self.assertEqual(out, [])
        self.assertIn("形状合规", printed,
                      "没违规也要明说：沉默会与「预检没跑」混淆")

    def test_checker_exception_is_not_swallowed(self):
        """本阶段不吞校验器的异常（照实上抛 ⇒ 周期停在预检阶段），且不产生副作用。"""
        with self.assertRaises(RuntimeError):
            self._run(raises="trackers")


if __name__ == "__main__":
    unittest.main()
