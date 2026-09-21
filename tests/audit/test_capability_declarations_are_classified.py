"""`ExchangeCapabilities` 的每个字段必须**要么被执行、要么被登记出处**（第一百九十四刀）。

## 这一刀查的是什么

`ExchangeCapabilities` 是"能力声明表"：`signed_size`、`order_id_type`、`conditional_family`
…… 声明本身**不产生行为**。真机扫描（本刀）发现 **11 个字段在生产代码里零读取**：

    bar_case  conditional_family  has_taker_ratio  has_top_trader_ratio
    mainland_ip_restricted  order_id_type  protection_semantics  rate_limit_note
    signed_size  supports_attached_tp_sl  trigger_price_default

这本身**不一定**是缺陷——`protection_semantics`/`rate_limit_note` 就是给人读的说明，
`order_id_type` 的真实执行在**各所适配器内部**（`gate.py` 的 id_string 铁律 + 既有门）。
危险的是**没人分类**：一个未来的读者完全可能以为"声明了就等于执行了"
（本仓 doctrine：**声明≠执行**）。所以本门要求每个字段**二选一**：

1. 生产代码里**真的有读者**（属性读取 `x.<field>` 或 `getattr(x, "<field>")`）；
2. 或出现在 `UNREAD_ALLOWLIST` 里，**并写明它的行为究竟落在哪里**（文件/机构）。

## 判据的边界（如实）

- 只把"声明处"排除：`exchanges/base.py`（数据类本体）与三所适配器/沙盒（它们**声明**这些值）。
  ⇒ 若某字段的行为是**各所自己实现**的，它在本门里就是"零读者"，**必须**走登记（附出处）。
- 门不判断登记理由写得好不好，只要求"非空理由"（防腐靠人读，不靠机器）。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("scripts", "r20_backend", "r20_gateway", "plugins")
#: 零生产读者、但**行为另有出处**（或本就是人读说明）的字段 —— 每条都写明真正的落点。
UNREAD_ALLOWLIST = {
    "bar_case": "K 线周期大小写由唯一归一函数承担：`scripts/market_data_service.py::normalize_bar`"
                "（各所 `to_bar`/`_interval` 只做映射），故声明无需在运行时再被读",
    "conditional_family": "条件单族由**各所适配器自己实现**（OKX 附加腿 / Gate 独立 price_orders / "
                          "Binance 独立 algoOrder 资源族）—— 声明是给人与调研读的分类标签，非执行开关",
    "has_taker_ratio": "数据可用性说明（该所有无 taker 比值接口）：消费方直接调对应行情端点，"
                       "不做能力判断，故无运行时读者",
    "has_top_trader_ratio": "同上：数据可用性说明（该所有无大户多空比接口），非执行开关",
    "mainland_ip_restricted": "部署环境说明（Binance 受限）：落地在部署选址与文档，不在运行时判断",
    "order_id_type": "订单 id 字符串化由**各所适配器内部**强制执行：`gate.py` 的 id_string 铁律"
                     "（int/float → str）、`binance.py::list_protective_orders` 的 `str(algoId)`、"
                     "`binance.py::place_order` 回包同一处理；另有门 "
                     "`tests/venues/test_venue_capability_semantics.py` 钉住声明本身",
    "protection_semantics": "给人读的保护语义说明（各所声明注释，含「非受理即原子保护」这类纠正），"
                            "不是执行开关 —— 执行在各所 attach/verify 实现里",
    "rate_limit_note": "给人读的限频说明；真正的退避在适配器与 `market_data_service` 内部实现",
    "signed_size": "带符号张数是 **Gate 独有的载荷语义**，由 `gate.py::_normalize_order_item` 归一成 "
                   "`size_signed`（其余两所无符号张数 + side），故声明本身无运行时读者",
    "supports_attached_tp_sl": "dataclass 注明「Phase 3 用，先声明后实现」：当前不支持附带腿的所"
                               "（Gate/Binance）各自走独立资源族，行为在适配器内实现",
    "trigger_price_default": "触发价类型由**各所适配器显式传参**实现（Binance `workingType` 必传、"
                             "Gate `price_type` 必传）—— 门 `test_working_type_required_not_default` "
                             "钉住「不依赖交易所默认值」这一点",
}


def declaration_fields() -> list:
    src = (ROOT / "r20_backend/exchanges/base.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ExchangeCapabilities":
            return [n.target.id for n in node.body
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    raise AssertionError("找不到 ExchangeCapabilities（门已过期）")


def _iter_py():
    """扫全仓**除数据类本体**；适配器内部的 `self.capabilities.x` 读取**算读者**。"""
    for root in SCAN_DIRS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            if rel == "r20_backend/exchanges/base.py":
                continue
            yield rel, path


def _declaration_lines(tree) -> set:
    """**(字段, 行号)** 集合：`ExchangeCapabilities(...)` 调用里的 `arg=` 是**声明**，不是读取。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("ExchangeCapabilities"):
            for kw in node.keywords:
                if kw.arg:
                    out.add((kw.arg, kw.lineno))
    return out


def fields_with_readers(fields) -> set:
    """真的有读者的字段：属性读取 `x.<field>` 或 `getattr(x, "<field>", …)`（声明行不算）。

    ⚠️ 必须按 **(字段, 行号)** 比对而不是"整文件出现即算声明" —— 否则同一个文件既声明又读取
    （如 `okx.py` 写 `venue="okx"` 且别处读 `capabilities.venue`）时会把真读者误删
    （本门第一版就这么错过，被自己的用例抓住）。
    """
    read = set()
    for rel, path in _iter_py():
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        declared = _declaration_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in fields:
                if (node.attr, node.lineno) not in declared:
                    read.add(node.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "getattr":
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and arg.value in fields:
                        read.add(arg.value)
    return read


class CapabilityDeclarationsClassifiedTest(unittest.TestCase):
    def test_every_declaration_is_enforced_or_documented(self):
        fields = declaration_fields()
        read = fields_with_readers(fields)
        unclassified = [f for f in fields if f not in read and f not in UNREAD_ALLOWLIST]
        self.assertEqual(unclassified, [],
                         "这些能力字段既没有生产读者、也没有登记出处（声明≠执行，"
                         "读者会误以为有护栏）：\n  " + "\n  ".join(unclassified))

    def test_scan_is_not_vacuous(self):
        fields = declaration_fields()
        self.assertGreaterEqual(len(fields), 20, f"只解析到 {len(fields)} 个字段 ⇒ 门与实现脱节")
        read = fields_with_readers(fields)
        self.assertGreaterEqual(len(read), 5, f"只找到 {len(read)} 个有读者的字段 ⇒ 扫描失效")

    def test_allowlist_entries_have_reasons_and_exist(self):
        fields = set(declaration_fields())
        for name, reason in UNREAD_ALLOWLIST.items():
            self.assertIn(name, fields, f"登记表过期：{name} 已不在数据类里")
            self.assertGreaterEqual(len(str(reason).strip()), 12, f"{name} 的出处说明太短（等于没写）")

    def test_allowlist_has_no_rot(self):
        """登记表里若某个字段**已经有读者**了，说明登记过期（应删）。"""
        fields = declaration_fields()
        read = fields_with_readers(fields)
        stale = [name for name in UNREAD_ALLOWLIST if name in read]
        self.assertEqual(stale, [], f"这些字段已有生产读者 ⇒ 登记表过期，请删除：{stale}")

    def test_teeth_on_an_unclassified_field(self):
        """牙齿：新加一个没人读、也没登记的字段必须判红。"""
        fields = ["signed_size", "brand_new_switch"]
        read = {"signed_size"}
        unclassified = [f for f in fields if f not in read and f not in UNREAD_ALLOWLIST]
        self.assertEqual(unclassified, ["brand_new_switch"])


if __name__ == "__main__":
    unittest.main()
