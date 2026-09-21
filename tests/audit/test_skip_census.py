"""跳过（skip）必须有理由、有账、且不能变成"静默消失的覆盖面"（第二百零五刀）。

## 为什么盯 skip

skip 是**覆盖面静默消失**的最佳藏身处：一条用例可以永远是绿的（因为压根没跑），
而套件输出的"1 skipped"没人会去追问。本刀把 skip 当账本管：

1. **每个跳过点必须给理由**（`skipTest()` 空参 = 没账）；
2. **调用点无条件**的跳过必须是登记在案的**条件式 helper**（本仓的
   `skip_if_offline_suite`：条件 `OFFLINE_SUITE_RUNNING` 在 helper **内部**，
   只有"离线守护基线"那一轮才置位；普通 `pytest tests` 不置位 ⇒ 这些用例照跑）
   或在 `UNCONDITIONAL_ALLOWLIST` 里逐条写理由 —— 否则就是"永远不跑的死用例"；
3. **已知的环境能力 skip** 要登记在 `ENV_LIMIT_SKIPS` 里（附理由），并在对应文件里
   **钉住那句说明**（理由是给人看的，不能悄悄改没）。

## 本刀实测（两条假设被推翻）

- 37 个跳过点，**没有给理由的 0 个**；
- 调用点无条件的 14 个**全部**是 `skip_if_offline_suite()`（条件在 helper 内部）⇒
  **真正"永远不跑"的用例 0 条**。
- 本容器实际只 skip 1 条：`test_self_evolution_safety.py` 的 `/dev/shm` 不可写
  （多进程信号量），属**环境能力**而非代码问题 —— 登记并钉住其说明。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 条件在**内部**的跳过 helper（调用点看着"无条件"，实际有条件）
CONDITIONAL_HELPERS = {
    "skip_if_offline_suite": "tests/config_sandbox.py：条件 `OFFLINE_SUITE_RUNNING` 在 helper 内部；"
                             "仅「离线守护基线」那一轮置位，普通 pytest 不置位",
}

#: 调用点无条件、又不在上面的 helper 表里的跳过 ⇒ 必须在下面登记理由
UNCONDITIONAL_ALLOWLIST: dict[str, str] = {}

#: 已知的**环境能力**跳过（本容器实测会发生的那类），附理由
ENV_LIMIT_SKIPS = {
    "tests/llm/test_self_evolution_safety.py": "本容器 /dev/shm 不可写（多进程信号量建不起来）⇒ "
                                               "以 spawn 为被测对象的用例如实 skip，不是代码问题",
}

#: 环境能力跳过必须保留的说明片段（防"理由被改没"）
ENV_LIMIT_REASON_MARKERS = ("Environment denies /dev/shm",)


def _parents(tree):
    pm = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            pm[child] = parent
    return pm


def skip_sites(source: str) -> list:
    """返回 [(行号, 名字, 有无理由, 调用点是否无条件)]。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    pm = _parents(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func).split(".")[-1]
        if name not in ("skipTest", "skipIf", "skipUnless", "skip_if_offline_suite"):
            continue
        # 「有理由」 = 至少给了一个参数，且不是**空字符串常量**。
        # ⚠️ 判据要容下本仓三种真实写法（初版只认字符串常量，误报 14 处）：
        #   · `skipTest("无 .env（干净检出）")`     —— 字面量
        #   · `skipTest(f"未找到 {x}")`            —— f-string（JoinedStr）
        #   · `test.skipTest(reason)` / helper 内  —— 变量（helper 自带默认理由）
        has_reason = bool(node.args) and not (
            len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
            and not str(node.args[0].value or "").strip())
        guarded = False
        cur = node
        while cur in pm:
            cur = pm[cur]
            if isinstance(cur, (ast.If, ast.ExceptHandler, ast.For, ast.While, ast.Try)):
                guarded = True
                break
        out.append((node.lineno, name, has_reason, guarded))
    return out


class SkipCensusTest(unittest.TestCase):
    def _all_sites(self):
        sites = {}
        for path in sorted((ROOT / "tests").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            for lineno, name, has_reason, guarded in skip_sites(path.read_text(encoding="utf-8")):
                sites.setdefault(rel, []).append((lineno, name, has_reason, guarded))
        return sites

    def test_every_skip_has_a_reason(self):
        bad = {rel: [(ln, nm) for ln, nm, has_reason, _ in rows if not has_reason]
               for rel, rows in self._all_sites().items()}
        bad = {k: v for k, v in bad.items() if v}
        self.assertEqual(bad, {}, f"跳过没给理由（等于没账）：{bad}")

    def test_unconditional_skips_are_registered(self):
        bad = {}
        for rel, rows in self._all_sites().items():
            for lineno, name, _has_reason, guarded in rows:
                if guarded or name in CONDITIONAL_HELPERS or name == "skipTest":
                    # skipTest 在守卫内由 test_every_skip_has_a_reason 管理由；
                    # 无条件的 skipTest 由下面的登记表管
                    if not guarded and name == "skipTest" and f"{rel}:{lineno}" not in UNCONDITIONAL_ALLOWLIST:
                        bad.setdefault(rel, []).append(f"L{lineno} {name}()")
                    continue
                if f"{rel}:{lineno}" not in UNCONDITIONAL_ALLOWLIST:
                    bad.setdefault(rel, []).append(f"L{lineno} {name}()")
        self.assertEqual(bad, {}, "调用点无条件的跳过（= 永远不跑的死用例；确认有意请登记）："
                                  f"{bad}")

    def test_conditional_helpers_are_documented(self):
        """登记的条件式 helper 必须真的**自带条件**（否则就是伪装成 helper 的死用例）。"""
        for helper, reason in CONDITIONAL_HELPERS.items():
            self.assertGreaterEqual(len(reason), 20, f"{helper} 的理由太短")
            src = (ROOT / "tests" / "config_sandbox.py").read_text(encoding="utf-8")
            self.assertIn(f"def {helper}", src, f"{helper} 不存在 ⇒ 登记表过期")
            self.assertIn("os.environ.get(", src, f"{helper} 里没看到环境条件 ⇒ 可能变成无条件跳过")

    def test_env_limit_skips_keep_their_explanation(self):
        for path, reason in ENV_LIMIT_SKIPS.items():
            self.assertTrue((ROOT / path).exists(), f"登记表过期：{path} 不存在")
            self.assertGreaterEqual(len(reason), 20, f"{path} 的理由太短")
            src = (ROOT / path).read_text(encoding="utf-8")
            for marker in ENV_LIMIT_REASON_MARKERS:
                # 用 assertTrue + 短消息：assertIn 失败时 pytest 会把整份文件打出来（几百行噪音）
                self.assertTrue(marker in src,
                                f"{path} 里那句环境说明被改没了（skip 就成了无解释的绿）：缺 {marker!r}")

    def test_scan_is_not_vacuous(self):
        sites = self._all_sites()
        total = sum(len(v) for v in sites.values())
        self.assertGreaterEqual(total, 30, f"只扫到 {total} 个跳过点 ⇒ 判据失效")
        self.assertGreaterEqual(len(sites), 15, f"只涉及 {len(sites)} 个文件 ⇒ 判据失效")

    def test_teeth_on_an_unconditional_dead_skip(self):
        src = ("class T(unittest.TestCase):\n"
               "    def test_x(self):\n"
               "        self.skipTest('永远跳过')  # 调用点无条件 ⇒ 死用例\n")
        site = skip_sites(src)[0]
        self.assertFalse(site[3], "无条件的跳过点必须被识别为'未守卫'")

    def test_no_false_positive_on_guarded_skip(self):
        src = ("class T(unittest.TestCase):\n"
               "    def test_x(self):\n"
               "        if not os.path.exists('node'):\n"
               "            self.skipTest('未找到 node')\n")
        self.assertTrue(skip_sites(src)[0][3], "守卫内的跳过被误判为无条件")


if __name__ == "__main__":
    unittest.main()
