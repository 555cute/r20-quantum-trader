"""仪表盘载荷：**TS 契约声明的字段必须真的被后端发出**（第一百九十七刀）。

## 这一刀抓到的两个真缺陷（真机缓存为证）

`frontend/src/types/dashboard.ts::DashboardResponse` 声明了字段，而前端
`stores/dashboard.ts` 直接读**载荷根**。实测：

| 字段 | 前端读法 | 后端实际 | 后果 |
|---|---|---|---|
| `is_stale` | `data.value?.is_stale ?? false` | **从不发**（全仓 grep 为 0）| 该判断**恒为 false**；面板的陈旧分支只剩 `status === 'STALE'` 一条腿 |
| `macro_assessment` | `data.value?.macro_assessment \|\| t('common.dashboardScanning')` | 只发在 `ai_brain_history[0].macro_assessment`（真机缓存实测有真内容）| 根级永远取不到 ⇒ 面板宏观一行**永远显示"扫描中…"** |

两者都是"**声明了/读了，但那一层根本没有**"——跨层契约的静默空洞。修复：后端按契约在**根上**
发出这两个字段（`is_stale` 由**同一个**状态词表推导，`macro_assessment` 取最新脑内记录的同源别名）。

## 判据

解析 `DashboardResponse` 里**非索引签名**的字段（`[key: string]: any` 那种不算），
要求每个字段都在后端 LIVE 载荷装配器 `build_live_cache_payload` 的返回字典键里，
否则必须进 `ALLOWLIST` 并写明理由（例如"刻意由前端派生"）。

⚠️ 只钉**一个方向**（声明 ⇒ 必须发出）。反方向（后端发了但 TS 没声明）不钉：
本仓 TS 允许索引签名，多发的字段无害。
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TS_TYPES = ROOT / "frontend" / "src" / "types" / "dashboard.ts"
BUILDER = ROOT / "r20_backend" / "dashboard_payload" / "cache_payload.py"

#: 允许"TS 声明、后端不直接发"的字段（附理由）。修复后为空。
ALLOWLIST: dict[str, str] = {}


def declared_fields() -> list:
    """`DashboardResponse` 里的非索引签名字段。"""
    src = TS_TYPES.read_text(encoding="utf-8")
    m = re.search(r"export interface DashboardResponse \{(.*?)\n\}", src, re.S)
    if not m:
        raise AssertionError("找不到 DashboardResponse（门已过期）")
    body = m.group(1)
    fields, depth = [], 0
    for line in body.splitlines():
        stripped = line.strip()
        # ⚠️ 只取**顶层**成员：嵌套对象字面量（如 positions_summary 里的 total_count/items）
        # 不是载荷根字段 —— 第一版没做深度感知，把它们也当成了根字段（6 个假阳性）。
        if depth == 0:
            mm = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\??\s*:", stripped)
            if mm:
                fields.append(mm.group(1))
        depth += stripped.count("{") - stripped.count("}")
    return fields


#: 载荷对象的变量名（装配后仍会被追加字段）
PAYLOAD_VARS = {"CACHE_DATA", "stale", "payload", "out", "data"}


def emitted_root_keys() -> set:
    """真正会出现在**载荷根**上的键。

    来源有三处（只查仪表盘载荷相关文件，不把别的端点混进来）：
      1. `build_live_cache_payload` 返回字典的顶层键；
      2. 装配**之后**对载荷对象的字段赋值（真机实测：`CACHE_DATA["llm_runtime"]`、
         `CACHE_DATA["market_regime"]` 都在 `dashboard_cache.py` 里补的）；
      3. 传给载荷对象 `.update({...})` 的字典字面量键。
    """
    keys = set()
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_live_cache_payload":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    keys |= {k.value for k in sub.value.keys
                             if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if not keys:
        raise AssertionError("找不到 build_live_cache_payload 的返回字典（门已过期）")

    for path in sorted((ROOT / "r20_backend").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name != "dashboard_cache.py" and "dashboard_payload" not in path.parts:
            continue
        try:
            sub_tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(sub_tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript) and ast.unparse(target.value) in PAYLOAD_VARS:
                        sl = target.slice
                        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                            keys.add(sl.value)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "update" \
                    and ast.unparse(node.func.value) in PAYLOAD_VARS:
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        keys |= {k.value for k in arg.keys
                                 if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


class PayloadContractCrossLayerTest(unittest.TestCase):
    def test_every_declared_field_is_emitted(self):
        emitted = emitted_root_keys()
        missing = [f for f in declared_fields()
                   if f not in emitted and f not in ALLOWLIST]
        self.assertEqual(missing, [], "TS 声明了、后端根级却没发（前端读到的永远是 undefined）：\n  "
                                      + "\n  ".join(missing))

    def test_scan_is_not_vacuous(self):
        fields = declared_fields()
        emitted = emitted_root_keys()
        self.assertGreaterEqual(len(fields), 15, f"TS 只解析出 {len(fields)} 个字段 ⇒ 门与实现脱节")
        self.assertGreaterEqual(len(emitted), 20, f"装配器只解析出 {len(emitted)} 个键 ⇒ 扫描失效")
        for must in ("is_stale", "macro_assessment", "positions_summary", "data_health"):
            self.assertIn(must, fields, f"{must} 应仍声明在契约里（否则本门的动机消失了）")
            self.assertIn(must, emitted, f"{must} 必须由后端发出（本刀修的就是它）")

    def test_stale_payload_marks_itself(self):
        """陈旧分支是**拷贝** ⇒ 必须显式把 is_stale 改真（否则前端看不出这是旧数据）。"""
        src = (ROOT / "r20_backend" / "dashboard_cache.py").read_text(encoding="utf-8")
        self.assertIn('stale["is_stale"] = True', src,
                      "陈旧载荷没有显式标记 is_stale ⇒ 前端会把它当新鲜数据")

    def test_root_macro_assessment_mirrors_the_brain_history(self):
        """根级 `macro_assessment` 必须取自最新脑内记录（同源，不新算）。"""
        src = BUILDER.read_text(encoding="utf-8")
        self.assertRegex(src, r'"macro_assessment":\s*_latest_brain\.get\("macro_assessment"\)',
                         "根级 macro_assessment 不是从 ai_brain_history 取的同源别名")

    def test_teeth_on_an_unemitted_declaration(self):
        emitted = {"timestamp", "account"}
        declared = ["timestamp", "brand_new_contract_field"]
        missing = [f for f in declared if f not in emitted and f not in ALLOWLIST]
        self.assertEqual(missing, ["brand_new_contract_field"],
                         "TS 声明却没发出的字段没被抓到 ⇒ 门没有牙齿")


if __name__ == "__main__":
    unittest.main()

# ---------------------------------------------------------------------------
# 行契约（第一百九十八刀）：持仓/挂单/账户/因子等**行的字段**也要能被后端产出
#
# 与根级同理，只是"产出证据"更宽：字段可以出现在
#   ① 真机缓存样本的对应行里（`data/dashboard_last_good.json`，真数据），或
#   ② 后端/脚本代码里作为字符串字面量（生产者显式写出的键名），或
#   ③ 匹配 f-string 派生的键名模式。
# —— 三条都不满足 ⇒ 类型在承诺一个**永远不会出现**的字段（前端读到的是 undefined）。
# ---------------------------------------------------------------------------

CACHE_SAMPLE = ROOT / "data" / "dashboard_last_good.json"

#: interface → 真机样本里对应的取值路径
ROW_SAMPLE_PATHS = {
    "AccountSummary": "account",
    "PositionItem": "positions_summary.items",
    "PendingOrderItem": "pending_orders",
    "InstrumentFactor": "factors",
    "PortfolioRiskRow": "portfolio_risk",
    "LLMRuntime": "llm_runtime",
    "MarketRegimeData": "market_regime",
}

#: 允许"类型声明、后端确实不产"的行字段（附理由）。本刀修完后为空。
ROW_ALLOWLIST: dict[str, str] = {}


def _interface_fields(iface: str) -> list:
    src = TS_TYPES.read_text(encoding="utf-8")
    m = re.search(rf"export interface {iface} \{{(.*?)\n\}}", src, re.S)
    if not m:
        raise AssertionError(f"TS 里找不到 interface {iface}（门已过期）")
    fields, depth = [], 0
    for line in m.group(1).splitlines():
        stripped = line.strip()
        if depth == 0:
            mm = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\??\s*:", stripped)
            if mm:
                fields.append(mm.group(1))
        depth += stripped.count("{") - stripped.count("}")
    return fields


def _code_string_literals() -> set:
    """后端/脚本里作为字符串字面量出现的名字（生产者显式写出的键名）。"""
    names = set()
    for root in ("r20_backend", "scripts"):
        for path in (ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node.value):
                    names.add(node.value)
    return names


def _live_row(path: str) -> dict:
    """真机缓存里的样例行；样本不在（干净 clone）⇒ 返回空字典，门靠代码证据。"""
    if not CACHE_SAMPLE.exists():
        return {}
    try:
        data = json.loads(CACHE_SAMPLE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cur = data
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else {}
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(part, {})
    if isinstance(cur, list):
        cur = cur[0] if cur else {}
    return cur if isinstance(cur, dict) else {}


class RowContractCrossLayerTest(unittest.TestCase):
    def test_every_declared_row_field_is_produced(self):
        literals = _code_string_literals()
        gaps = {}
        for iface, path in ROW_SAMPLE_PATHS.items():
            row = _live_row(path)
            missing = [f for f in _interface_fields(iface)
                       if f not in row and f not in literals and f not in ROW_ALLOWLIST]
            if missing:
                gaps[iface] = missing
        self.assertEqual(gaps, {}, f"类型声明了、后端从不产出的行字段：{gaps}")

    def test_row_scan_is_not_vacuous(self):
        total = 0
        for iface in ROW_SAMPLE_PATHS:
            fields = _interface_fields(iface)
            total += len(fields)
            self.assertGreaterEqual(len(fields), 4, f"{iface} 只解析出 {len(fields)} 个字段")
        self.assertGreaterEqual(total, 80, f"行契约字段总数只有 {total} ⇒ 门与实现脱节")
        literals = _code_string_literals()
        self.assertGreaterEqual(len(literals), 500, "代码字面量集合太小 ⇒ 扫描失效")
        for must in ("instId", "upl", "posSide", "scaleOutPhase", "macro_assessment"):
            self.assertIn(must, literals, f"生产者的键名 {must} 不在字面量集合里 ⇒ 判据漏了")

    def test_teeth_on_a_field_nobody_produces(self):
        literals = {"instId", "upl"}
        declared = ["instId", "brand_new_row_field"]
        gaps = [f for f in declared if f not in literals and f not in ROW_ALLOWLIST]
        self.assertEqual(gaps, ["brand_new_row_field"], "无人产出的行字段没被抓到 ⇒ 门没有牙齿")
