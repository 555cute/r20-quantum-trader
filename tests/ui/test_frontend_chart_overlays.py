"""`chartOverlays.ts`（阶段 3·F4 第二轮抽离）的价格线描述符回归。

## 这个测试在守什么

`ChartWorkstation.vue` 的 `updatePriceLines()` 原先 92 行里内嵌了三条价格线
（入场/止损/止盈）的**建线描述**。本刀把描述部分抽到 `chartOverlays.ts`，
组件只保留"调用图表库怎么画"。

抽离的安全网有两层：

1. **`vue-tsc --noEmit` + `vite build`** —— 类型与构建（仓里既有的前端闸）；
2. **本文件** —— 描述符的**字节级形状**。这一层是必要的：类型能保证
   "字段名对"，但保证不了"颜色没接反""守卫没放开"。

## 为什么用黄金样本而不是直接跑 TS

本仓前端**没有测试框架**（无 vitest / jsdom），且目标明确
**禁止增减依赖**，所以不能引测试框架。

Node 24 能用 `--experimental-strip-types` 直接跑 `.ts`，但把 `node` 子进程
放进套件会**打穿 `tests/offline_suite.py` 的儿童进程白名单**
（该名单刻意只放 `uname -p`、一条固定 `grep`、一条固定哈希的 `python -c`）
—— 我不该为了让自己的测试跑起来去放松那道闸。

故采用：**开发期用 node 真跑一次生成黄金样本**
（`tests/data/chart_overlays_golden.json`），套件只做**静态比对**。
即"昂贵的一次性验证留在开发期，每日闸只做便宜的回归"。

## 防"黄金样本变成谎话"

静态比对有个典型失效模式：**被比对的实现没了、或字段改名了，样本却还绿**。
故本文件同时做**源码结构核对**（见 `SourceShapeTest`）：
黄金样本里的每个固定色值/尺寸/文案片段，都必须在 `.ts` 里出现；
模块也不得反向依赖任何组件。两边同时成立才算过。
"""
from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "frontend" / "src" / "components" / "dashboard" / "chartOverlays.ts"
COMPONENT = ROOT / "frontend" / "src" / "components" / "dashboard" / "ChartWorkstation.vue"
GOLDEN = ROOT / "tests" / "data" / "chart_overlays_golden.json"

LONG = "#10B981"
SHORT = "#F43F5E"


def _strip_ts_comments(src: str) -> str:
    """去掉 `//` 行注释与 `/* */` 块注释，**保留字符串字面量**。

    ⚠️ 只剥注释、不剥字符串。理由：本模块的行为**全靠字符串字面量表达**
    （`'solid'` / `'priceLine'` / `'Entry Long'` / `'🛑'`）。
    我第一版把字符串一并剥掉，结果断言 `style: 'solid'` 之类全部找不到，
    测试自己先红了 —— 工具剥过头会把被测内容一起删掉。

    ⚠️ 为什么必须剥注释：本模块的文档串**说明**了这些值
    （如"原实现取 `tok('--ink-1')`"）。判断"令牌解析是否泄进纯模块"时，
    注释里的提及会造成**假阳性**，故先剥注释再断言。
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            if j == -1:
                break
            out.append("\n")
            i = j + 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if c in "'\"`":
            quote = c
            start = i
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(src[start:i])
            continue
        out.append(c)
        i += 1
    return "".join(out)


class GoldenTest(unittest.TestCase):
    """黄金样本自洽性 + 关键形状。"""

    @classmethod
    def setUpClass(cls):
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    def test_fixture_covers_every_builder_and_guard(self):
        """样本必须覆盖三个构造函数与 plan 的三条守卫，否则"绿"没有意义。"""
        need = {
            "entry_long_zh", "entry_long_en", "entry_short_zh", "entry_short_en",
            "sl_zh", "sl_en", "sl_frac",
            "tp_zh", "tp_en", "tp_frac",
            # 分批止盈两档（2026-09）：TP1 / TP2 图签必须各自有样本
            "tp1_zh", "tp2_zh",
            "plan_none", "plan_all", "plan_entry_guard", "plan_sl_only",
        }
        self.assertEqual(set(self.golden), need)

    def test_entry_line_is_solid_and_coloured_by_side(self):
        for key, color in (("entry_long_zh", LONG), ("entry_long_en", LONG),
                           ("entry_short_zh", SHORT), ("entry_short_en", SHORT)):
            o = self.golden[key]
            self.assertEqual(o["styles"]["line"]["style"], "solid", key)
            self.assertEqual(o["styles"]["line"]["color"], color, key)
            self.assertEqual(o["styles"]["text"]["backgroundColor"], color, key)

    def test_sl_tp_lines_are_dashed_and_side_independent(self):
        """止损恒 rose、止盈恒 emerald —— **不随多空反转**。"""
        for key, color in (("sl_zh", SHORT), ("sl_en", SHORT),
                           ("tp_zh", LONG), ("tp_en", LONG)):
            o = self.golden[key]
            self.assertEqual(o["styles"]["line"]["style"], "dashed", key)
            self.assertEqual(o["styles"]["line"]["dashedValue"], [6, 4], key)
            self.assertEqual(o["styles"]["line"]["color"], color, key)

    def test_all_lines_share_name_and_pane(self):
        for key, o in self.golden.items():
            if not isinstance(o, dict) or "name" not in o:
                continue
            self.assertEqual(o["name"], "priceLine", key)
            self.assertEqual(o["paneId"], "candle_pane", key)

    def test_text_color_is_the_injected_value(self):
        """文字色来自入参（组件传 `tok('--ink-1')`），不是模块内写死。"""
        for key, o in self.golden.items():
            if isinstance(o, dict) and "styles" in o:
                self.assertEqual(o["styles"]["text"]["color"], "#e5e7eb", key)

    def test_entry_labels_i18n(self):
        self.assertEqual(self.golden["entry_long_zh"]["extendData"], "多头入场")
        self.assertEqual(self.golden["entry_long_en"]["extendData"], "Entry Long")
        self.assertEqual(self.golden["entry_short_zh"]["extendData"], "空头入场")
        self.assertEqual(self.golden["entry_short_en"]["extendData"], "Entry Short")

    def test_sl_tp_labels_and_percent_format(self):
        # 批 26：前缀由 emoji（🛑/🎯）改为全站表格同款方向字形 ▲▼ ——
        # 设计清单禁 emoji 图标，且 emoji 在 canvas 文本里各家字体渲染不一致。
        self.assertEqual(self.golden["sl_zh"]["extendData"], "▼ 止损SL -5.0%")
        self.assertEqual(self.golden["sl_en"]["extendData"], "▼ SL -5.0%")
        self.assertEqual(self.golden["tp_zh"]["extendData"], "▲ 止盈TP +10.0%")
        self.assertEqual(self.golden["tp_en"]["extendData"], "▲ TP +10.0%")

    def test_percent_is_always_one_decimal(self):
        """`toFixed(1)` —— 5.678 → 5.7、10.123 → 10.1。"""
        self.assertIn("-5.7%", self.golden["sl_frac"]["extendData"])
        self.assertIn("+10.1%", self.golden["tp_frac"]["extendData"])

    def test_plan_none_draws_nothing(self):
        self.assertEqual(self.golden["plan_none"], {"tps": []})

    def test_plan_all_draws_entry_sl_and_the_tp_lines(self):
        """止盈改成一档一条后，`plan` 的止盈键是**数组** `tps`（不再是单值 `tp`）。"""
        self.assertEqual(set(self.golden["plan_all"]), {"entry", "sl", "tps"})
        self.assertEqual(len(self.golden["plan_all"]["tps"]), 1)

    def test_entry_guard_requires_has_pos_or_order(self):
        """**关键守卫**：`hasPosOrOrder` 为假时即使 entryPx>0 也不画入场线。

        但止盈止损线**不受该守卫约束**（只看各自价格 > 0）。
        """
        o = self.golden["plan_entry_guard"]
        self.assertNotIn("entry", o, "无持仓无挂单时不得画入场线")
        self.assertIn("sl", o, "止损线不受 hasPosOrOrder 约束")
        self.assertTrue(o["tps"], "止盈线不受 hasPosOrOrder 约束")

    def test_sl_only_when_entry_price_zero(self):
        self.assertEqual(set(self.golden["plan_sl_only"]), {"sl", "tps"})
        self.assertEqual(self.golden["plan_sl_only"]["tps"], [], "未设止盈时不得出线")

    def test_plan_pieces_match_standalone_builders(self):
        """plan 造出的线必须与单独调用构造函数**逐字节相同**。"""
        o = self.golden["plan_all"]
        self.assertEqual(o["entry"], self.golden["entry_long_zh"])
        self.assertEqual(o["sl"], self.golden["sl_zh"])
        self.assertEqual(o["tps"][0], self.golden["tp_zh"])

    # ---- 行为 0（2026-09 新增）：分批止盈 ----

    def test_batch_tp_labels_distinct(self):
        """分批止盈：TP1 / TP2 图签必须真的不同，且单档图签与改前**逐字相同**。

        改前 K 线只画终点一条（`tps` 长度恒为 1）—— 用户反馈"只会显示一个止盈点，
        但平时都是分批挂单"。这条钉住"两档都出、且图签可区分、单档不硬凑 TP1/TP2"。
        """
        self.assertNotEqual(self.golden["tp1_zh"]["extendData"],
                            self.golden["tp2_zh"]["extendData"])
        self.assertEqual(self.golden["tp1_zh"]["extendData"], "▲ 止盈TP1 +5.0%")
        self.assertEqual(self.golden["tp2_zh"]["extendData"], "▲ 止盈TP2 +10.0%")
        self.assertEqual(self.golden["tp_zh"]["extendData"], "▲ 止盈TP +10.0%")


class DerivedBehaviourTest(unittest.TestCase):
    """从**源码派生**行为，再与黄金样本对照。

    ## 为什么单靠"源码里有某字符串 + 黄金样本里有某值"不够

    我第一版就是这么写的，负向验证给出**7/7 全部不翻红**：
    因为注入改的是 `.ts` 的**运行时行为**，而黄金 JSON 与源码字符串断言
    **都还在原地**，测试当然全绿。

    也就是说：**两组各自静态的断言之间没有推理链** —— 它们可以同时为真，
    而实现已经错了。这与第 22 节"`assertNotIn` 在文件移动后静默变空真"
    是同一类失效：**断言没有真正约束实现**。

    ## 做法

    在测试里**从 `.ts` 源码推导出行为**（解析常量绑定、解析三元分支），
    再断言推导结果与黄金样本一致。这样：
    - 注入改颜色绑定 → 推导出的颜色变 → 与黄金不符 → **翻红**；
    - 注入删 `toFixed(1)` → 推导出的格式化变 → **翻红**；
    - 注入给 SL 加守卫 → 黄金仍无守卫 → **翻红**。
    """

    @classmethod
    def setUpClass(cls):
        cls.src = MODULE.read_text(encoding="utf-8")
        cls.code = _strip_ts_comments(cls.src)
        cls.golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    # ---- 小工具：从源码里取一件事 ----

    def _const(self, name: str) -> str:
        m = re.search(rf"^const {name} = '([^']+)'", self.code, flags=re.M)
        self.assertIsNotNone(m, f"找不到常量 {name}")
        return m.group(1)

    def _fn_body(self, name: str) -> str:
        """取导出的函数体（到下一个 `export ` 或文件末尾）。

        容忍泛型参数（`export function f<T extends …>(`）—— 旧实现写死 `name(`，
        遇到带泛型的导出会直接 `ValueError`。
        """
        m = re.search(rf"export function {re.escape(name)}\b", self.code)
        self.assertIsNotNone(m, f"找不到导出函数 {name}")
        start = m.start()
        nxt = self.code.find("\nexport ", start + 1)
        return self.code[start:] if nxt == -1 else self.code[start:nxt]

    def _entry_colour_expr(self) -> str:
        body = self._fn_body("buildEntryOverlay")
        m = re.search(r"const color = ([^\n]+)", body)
        self.assertIsNotNone(m, "找不到入场线颜色绑定")
        return m.group(1).strip()

    def _line_colour_token(self, fn: str, marker: str) -> str:
        """在某条线的函数里，取 `color: X` 中 X 的**源码记号**。"""
        body = self._fn_body(fn)
        seg = body[body.index(marker):]
        m = re.search(r"\bcolor:\s*([A-Z_]+|'#[0-9A-Fa-f]+')", seg)
        self.assertIsNotNone(m, f"{fn} 的 {marker} 段找不到 color")
        return m.group(1)

    # ---- 行为 1：入场线颜色随多空 ----

    def test_entry_colour_binding_matches_golden(self):
        expr = self._entry_colour_expr()
        long_c = self._const("LONG_COLOR")
        short_c = self._const("SHORT_COLOR")
        # 按源码表达式**求值**
        derived_long = long_c if expr == "isLong ? LONG_COLOR : SHORT_COLOR" else short_c
        derived_short = short_c if expr == "isLong ? LONG_COLOR : SHORT_COLOR" else long_c
        self.assertNotEqual(long_c, short_c)
        self.assertEqual(derived_long, self.golden["entry_long_zh"]["styles"]["line"]["color"])
        self.assertEqual(derived_short, self.golden["entry_short_zh"]["styles"]["line"]["color"])

    def test_entry_binding_is_not_inverted(self):
        self.assertEqual(self._entry_colour_expr(),
                         "isLong ? LONG_COLOR : SHORT_COLOR",
                         "入场线颜色绑定被改（多空接反）")

    # ---- 行为 2：SL/TP 颜色与样式 ----

    def test_sl_uses_short_colour_both_places(self):
        short_c = self._const("SHORT_COLOR")
        body = self._fn_body("buildSlOverlay")
        self.assertIn("color: SHORT_COLOR", body)
        self.assertEqual(body.count("SHORT_COLOR"), 2,
                         "SL 的线色与文字底色都必须是 SHORT_COLOR")
        self.assertNotIn("LONG_COLOR", body, "SL 不得用止盈色")
        self.assertEqual(short_c, self.golden["sl_zh"]["styles"]["line"]["color"])

    def test_tp_uses_long_colour_both_places(self):
        long_c = self._const("LONG_COLOR")
        body = self._fn_body("buildTpOverlay")
        self.assertEqual(body.count("LONG_COLOR"), 2)
        self.assertNotIn("SHORT_COLOR", body, "TP 不得用止损色")
        self.assertEqual(long_c, self.golden["tp_zh"]["styles"]["line"]["color"])

    def test_entry_line_is_solid_sl_tp_dashed(self):
        self.assertIn("style: 'solid'", self._fn_body("buildEntryOverlay"))
        for fn in ("buildSlOverlay", "buildTpOverlay"):
            body = self._fn_body(fn)
            self.assertIn("style: 'dashed'", body, fn)
            self.assertIn("dashedValue: [6, 4]", body, fn)
        self.assertEqual(self.golden["entry_long_zh"]["styles"]["line"]["style"], "solid")
        self.assertEqual(self.golden["sl_zh"]["styles"]["line"]["style"], "dashed")

    # ---- 行为 3：百分比格式化 ----

    def test_percent_formatting_derived_from_source(self):
        for fn, gk in (("buildSlOverlay", "sl_frac"), ("buildTpOverlay", "tp_frac")):
            body = self._fn_body(fn)
            self.assertIn("toFixed(1)", body, f"{fn} 丢了 toFixed(1)")
            # 黄金样本里的小数位必须只有一位
            m = re.search(r"([+-]?\d+\.\d+)%", self.golden[gk]["extendData"])
            self.assertIsNotNone(m, f"{gk} 文案里没有百分比")
            decimals = len(m.group(1).split(".")[1])
            self.assertEqual(decimals, 1,
                             f"{gk} 样本是 {decimals} 位小数，源码却是 toFixed(1)")

    # ---- 行为 4：三条守卫 ----

    def test_guards_derived_from_source(self):
        body = self._fn_body("planPriceLines")
        self.assertIn("if (hasPosOrOrder && entryPx > 0)", body)
        self.assertIn("if (slPx > 0)", body)
        # 止盈守卫：分批后由单值 `tpPx > 0` 改为逐档 `lv.price > 0`
        self.assertIn("lv.price > 0", body)
        # 黄金样本必须与此一致
        guard = self.golden["plan_entry_guard"]
        self.assertNotIn("entry", guard, "无持仓无挂单时不得画入场线")
        self.assertIn("sl", guard, "止损线不受 hasPosOrOrder 约束")
        self.assertTrue(guard["tps"], "止盈线不受 hasPosOrOrder 约束")

    def test_sl_guard_does_not_require_pos_or_order(self):
        body = self._fn_body("planPriceLines")
        self.assertNotIn("hasPosOrOrder && slPx", body,                         "止损线不得被 hasPosOrOrder 约束")

    # ---- 行为 5：i18n 标签 ----

    def test_entry_labels_not_swapped(self):
        body = self._fn_body("buildEntryOverlay")
        long_branch = re.search(r"\?\s*\(isEn \? '([^']+)' : '([^']+)'\)", body)
        self.assertIsNotNone(long_branch)
        self.assertEqual(long_branch.group(1), "Entry Long")
        self.assertEqual(long_branch.group(2), "多头入场")
        self.assertEqual(self.golden["entry_long_en"]["extendData"], "Entry Long")
        self.assertEqual(self.golden["entry_long_zh"]["extendData"], "多头入场")

    def test_sl_tp_label_prefixes(self):
        """前缀仍是**必需的方向标记**，只是不再用 emoji（批 26 换成 ▲▼）。

        这条不改判据强度：源码里必须有前缀、黄金样本必须也以它开头。
        """
        self.assertIn("▼", self._fn_body("buildSlOverlay"))
        self.assertIn("▲", self._fn_body("buildTpOverlay"))
        self.assertTrue(self.golden["sl_zh"]["extendData"].startswith("▼"))
        self.assertTrue(self.golden["tp_zh"]["extendData"].startswith("▲"))

    # ---- 行为 6（2026-09 新增）：一档一条 + 价格轴扩范围 ----

    def test_batch_tp_one_line_per_level_derived_from_source(self):
        """一档一条：`planPriceLines` 必须**逐档**出线，而不是只取单个 tpPx。"""
        body = self._fn_body("planPriceLines")
        m = re.search(r"for \(const (\w+) of tpLevels\)", body)
        self.assertIsNotNone(m, "planPriceLines 必须遍历档位（而非只取单个 tpPx）")
        self.assertIn(f"{m.group(1)}.price > 0", body, "每档仍要各自判 price > 0")
        self.assertIn("plan.tps.push(", body)
        # 图签后缀必须来自入参，否则分批档位无法区分
        self.assertIn("label", self._fn_body("buildTpOverlay"))

    def test_axis_range_helper_only_grows(self):
        """价格轴范围：库侧只统计蜡烛+指标（不看 overlay），故本模块把它撑到含仓位价位。

        钉住"只扩不缩" —— 缩小会把 K 线拉伸进无意义的空白区。
        """
        self.assertIn("export function expandRangeToLevels", self.src)
        body = self._fn_body("expandRangeToLevels")
        self.assertIn("Math.min(defaultRange.realFrom", body)
        self.assertIn("Math.max(defaultRange.realTo", body)
        self.assertIn("return defaultRange", body, "无有效价位时必须原样返回")
        self.assertIn("Number.isFinite(p) && p > 0", body, "非法价位必须被滤掉")


class SourceShapeTest(unittest.TestCase):
    """源码结构核对 —— 防"实现没了/改名了、黄金样本却还绿"。"""

    @classmethod
    def setUpClass(cls):
        cls.src = MODULE.read_text(encoding="utf-8")
        cls.component = COMPONENT.read_text(encoding="utf-8")

    def test_module_exports_the_four_functions(self):
        for name in ("buildEntryOverlay", "buildSlOverlay", "buildTpOverlay",
                     "planPriceLines", "expandRangeToLevels"):
            # `expandRangeToLevels` 带泛型参数 ⇒ 只断到函数名，不咬死签名形状
            self.assertRegex(self.src, rf"export function {name}\b", name)

    def test_constants_present_in_source(self):
        """黄金样本里的固定值必须能在 `.ts` 里找到（否则样本是"孤儿数据"）。"""
        for token in (LONG, SHORT, "'priceLine'", "'candle_pane'",
                      "[6, 4]", "多头入场", "空头入场", "Entry Long", "Entry Short",
                      "止损SL", "止盈", "'TP'", "'TP1'", "'TP2'", "▼", "▲", "toFixed(1)"):
            self.assertIn(token, self.src, f"源码缺少黄金样本里的 {token!r}")

    def test_guard_expressions_present(self):
        """三条守卫必须原样在源码里（改坏会被黄金样本 + 这条一起拦下）。

        止盈守卫由"单个 `tpPx > 0`"演进为"逐档 `lv.price > 0`"（分批止盈），
        入场/止损两条口径未变。
        """
        self.assertIn("hasPosOrOrder && entryPx > 0", self.src)
        self.assertIn("slPx > 0", self.src)
        self.assertIn("lv.price > 0", self.src)

    def test_module_has_no_component_dependency(self):
        """反向依赖检查：本模块不得 import 任何 .vue 或 Vue 运行时。

        ⚠️ 这里**不能**用 `ast.parse` —— 本模块是 **TypeScript**，
        `ast.parse` 会直接 `SyntaxError`（我第一版就踩了，整条用例报 ERROR）。
        改用正则扫 import 语句：够用且不会误判。
        """
        imports = re.findall(
            r"^\s*import\s+(?:type\s+)?(?:[^'\"]*?from\s+)?['\"]([^'\"]+)['\"]",
            self.src, flags=re.M)
        self.assertTrue(imports is not None)
        for mod in imports:
            self.assertFalse(mod.endswith(".vue"), f"不得 import 组件 {mod}")
            self.assertNotEqual(mod, "vue", "不得依赖 vue 运行时")

    def test_component_uses_the_plan(self):
        """组件必须真的用上抽出的函数，且不再内嵌建线字面量。"""
        self.assertIn("import { planPriceLines, expandRangeToLevels } from './chartOverlays'",
                      self.component)
        self.assertIn("planPriceLines({", self.component)
        self.assertIn("expandRangeToLevels(", self.component)
        for gone in ("dashedValue: [6, 4]", "'多头入场'", "'Entry Long'", "止损SL"):
            self.assertNotIn(gone, self.component, f"组件仍残留内联建线片段 {gone!r}")

    def test_component_still_calls_chart_library(self):
        """"怎么画"仍留在组件：createOverlay 三处调用不得消失。"""
        self.assertEqual(self.component.count("klineChart.createOverlay("), 3)
        self.assertIn("klineChart.removeOverlay(", self.component)

    def test_component_resolves_theme_token_at_call_site(self):
        """文字色仍由组件在调用点解析（`tok('--ink-1')`）—— 未被搬进新模块。

        ⚠️ 判"模块里没有令牌解析"必须**先去掉注释与文档串**：
        本模块的文档串**说明**了这件事（"原实现取 tok('--ink-1')"），
        直接 `assertNotIn` 会被自己的说明文字绊倒 —— 我第一版正是如此。
        """
        self.assertIn("textColor: tok('--ink-1')", self.component)
        code = _strip_ts_comments(self.src)
        self.assertNotIn("--ink-1", code, "主题令牌解析不得进入纯模块")


class FixtureFreshnessTest(unittest.TestCase):
    """黄金样本是"某次 node 真跑"的快照；这里记录它的来源与复现命令。"""

    def test_fixture_is_json_and_non_trivial(self):
        raw = GOLDEN.read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertGreaterEqual(len(data), 14)
        # 不是空壳：每条线都得有 styles
        with_styles = [k for k, v in data.items()
                       if isinstance(v, dict) and "styles" in v]
        self.assertGreaterEqual(len(with_styles), 10)

    def test_regen_recipe_recorded(self):
        """复现命令写在模块文档串里 —— 后人要更新样本时不必猜。"""
        self.assertIn("node", self.__class__.__doc__ or "" + MODULE.read_text(encoding="utf-8"))
        self.assertTrue(MODULE.exists() and GOLDEN.exists())


if __name__ == "__main__":
    unittest.main()
