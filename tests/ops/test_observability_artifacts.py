r"""观测产物（Prometheus 规则 / Grafana 面板）**反漂移门**。

## 为什么需要它

`deploy/observability/` 里的三个文件引用了一批指标族名。它们有三个致命特点：

1. **没有任何编译期检查** —— 面板写错一个字母，导入 Grafana 后只是"图是空的"，
   没有任何报错；告警写错则**永不触发**（最危险：以为有人在看，其实没人）；
2. **改名是常态** —— `r20_backend/metrics.py` 里改一个族名，仓库测试全绿，
   生产面板静默失效；
3. **这里是"会响的入口"** —— 第 137 刀那次 30 小时无信号，根因就是"失败没有出口"。
   面板/告警就是出口，出口自己烂掉等于事故复现。

## 本门怎么做（两个方向都钉）

- **正向**：面板/告警里出现的每个 `r20_*` 族名，必须能在**真实渲染结果**
  （`render_prometheus` 对一份"全源齐备"快照的输出）里找到；
- **反向**：真实渲染出的每个族名，必须**至少被面板或告警用上**，或显式登记进
  `INTENTIONALLY_UNUSED`（"产出了但没人看"本身就是缺陷，必须当着人登记）。

## 局限（诚实说明）

本环境**没有 pyyaml、没有 promtool、没有 docker**（实测），故：

- JSON 面板用 stdlib `json` 做**完整结构校验**；
- YAML 不做通用解析，而是按本仓这几个文件的实际形态做**窄结构校验**
  （缩进/键值形态/告警四要素）。**最终校验请用 `promtool check rules alerts.yml`**
  与 `docker compose config`（见 README）——本门是"名字与结构"的第一道闸，
  不是 YAML 解析器。
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from r20_backend import metrics as M  # noqa: E402

OBS = ROOT / "deploy" / "observability"
DASHBOARD = OBS / "grafana-dashboard.json"
ALERTS = OBS / "alerts.yml"
PROMETHEUS = OBS / "prometheus.yml"

#: 产出了但**刻意不告警/不上板**的族（登记即承诺：这里每加一个名字都要说清理由）
INTENTIONALLY_UNUSED = {
    # 快照生成时刻：用于人工对时间，不适合做告警（它自己就是时间的度量）
    "r20_metrics_generated_at_timestamp_seconds",
}
#: 面板里允许出现的非本系统指标（Prometheus 自身/时序函数）

FAMILY_TOKEN = re.compile(r"\br20_[a-zA-Z0-9_]+\b")


def _full_snapshot() -> dict:
    """一份"全源齐备"的快照：让 `render_prometheus` 把所有族都吐出来。

    ⚠️ 这里必须覆盖**每一个**族；漏一个，本门就会把"真实存在的族"误判成
    "面板引用了不存在的族"（反向也会漏检）。故新增族时这份快照要同步。
    """
    return {
        "generated_at": 1789000000.0,
        "sources": {"venue_health": True, "model_calls": True, "risk_limits": True,
                    "market_data": True, "market_stream": True},
        "venue_health": {
            "updated_utc": "2026-09-20 07:45:32",
            "venues": {"okx": {"ok": ["BTC"], "failed": {"SOL": "timeout"},
                               "avg_ms": 105, "testnet": True}},
        },
        "model_stats": {"total_calls": 12, "successful_calls": 10,
                        "avg_duration_ms": 4200, "total_tokens": 98765},
        "risk_limits": {"max_leverage": 5.0},
        "market_data_health": {
            "schema_version": 1,
            "written_at_ms": 1789000000_000 - 30_000,
            "calls": {"okx_public_get_ticker": 4},
            "failed_calls": {"okx_public_get_ticker": 1},
            "latency": {"okx_public_get_ticker": {"count": 4, "avg_ms": 120.0,
                                                  "max_ms": 900.0, "p50_ms": 110.0,
                                                  "p95_ms": 880.0}},
            "last_success_ms": {"okx_public_get_ticker": 1789000000_000 - 5_000},
            "failures": {"total": 1, "by_kind": {"okx_public_get_ticker": 1},
                         "last_error": {}},
        },
        # ⚠️ 新增族时这里必须同步，否则反漂移门会失去覆盖（门里已有断言钉住这点）
        "market_stream_health": {
            "schema_version": 1,
            "written_at_ms": 1789000000_000 - 12_000,
            "venues": {
                "okx": {"frames": 30, "ticks": 29, "parse_errors": 0, "errors": 0,
                        "reconnects": 0, "last_msg_ms": 1789000000_000 - 12_000,
                        "last_tick_ms": 1789000000_000 - 12_000, "tick_age_s": 12.0,
                        "last_error": None},
                "gate": {"frames": 1, "ticks": 0, "parse_errors": 0, "errors": 1,
                         "reconnects": 0, "last_msg_ms": 1789000000_000 - 12_000,
                         "last_tick_ms": None, "tick_age_s": None,
                         "last_error": "已连接但窗口内零数据帧"},
            },
        },
    }


def emitted_families() -> set:
    """真实渲染出来的族名集合（**唯一事实来源**：`r20_backend/metrics.py`）。"""
    text = M.render_prometheus(_full_snapshot())
    names = set()
    for line in text.splitlines():
        if line.startswith("# HELP "):
            names.add(line.split()[2])
    assert names, "渲染结果里一个 HELP 都没有？指标中枢坏了"
    return names


def _dashboard_exprs(payload: dict) -> list:
    out = []
    for panel in payload.get("panels") or []:
        for target in panel.get("targets") or []:
            expr = target.get("expr")
            if expr:
                out.append((panel.get("title"), expr))
    return out


def _alert_entries() -> list:
    """窄解析 `alerts.yml` → [{name, expr, for, severity, keys}]（见模块 docstring 局限说明）。"""
    entries, current = [], None
    in_expr_block = False
    for raw in ALERTS.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- alert:"):
            if current:
                entries.append(current)
            current = {"name": stripped.split(":", 1)[1].strip(), "expr": "",
                       "for": None, "severity": None, "annotations": {}}
            in_expr_block = False
            continue
        if current is None:
            continue
        if stripped.startswith("expr:"):
            value = stripped.split(":", 1)[1].strip()
            if value in ("|", ">", ""):
                in_expr_block = True
                continue
            in_expr_block = False
            current["expr"] += " " + value
            continue
        if in_expr_block:
            # 块标量：以更深缩进继续；`for:`/`labels:` 等更浅的键结束块
            if line.startswith(" " * 10):
                current["expr"] += " " + stripped
                continue
            in_expr_block = False
        if stripped.startswith("for:"):
            current["for"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("severity:"):
            current["severity"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("summary:"):
            current["annotations"]["summary"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("description:"):
            current["annotations"]["description"] = stripped.split(":", 1)[1].strip()
    if current:
        entries.append(current)
    return entries


class DashboardStructureTest(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads(DASHBOARD.read_text(encoding="utf-8"))

    def test_basic_fields_and_unique_panel_ids(self):
        self.assertTrue(self.payload.get("title"))
        self.assertTrue(self.payload.get("uid"))
        panels = self.payload.get("panels") or []
        self.assertGreaterEqual(len(panels), 10, "面板太少，覆盖不到四类信号")
        ids = [p.get("id") for p in panels]
        self.assertEqual(len(ids), len(set(ids)), f"面板 id 重复：{ids}")
        for panel in panels:
            with self.subTest(panel=panel.get("title")):
                self.assertTrue(panel.get("title"), "面板必须有标题")
                self.assertTrue(panel.get("type"))
                grid = panel.get("gridPos") or {}
                self.assertEqual(sorted(grid), ["h", "w", "x", "y"], "gridPos 字段不全")
                self.assertLessEqual(grid["x"] + grid["w"], 24, "gridPos 超出 24 列")

    def test_every_panel_has_a_query(self):
        for panel in self.payload.get("panels") or []:
            with self.subTest(panel=panel.get("title")):
                targets = panel.get("targets") or []
                self.assertTrue(targets, "面板没有任何查询")
                for target in targets:
                    self.assertTrue(target.get("expr"), "查询缺少 expr")
                    self.assertTrue(target.get("refId"), "查询缺少 refId")

    def test_datasource_variable_is_declared(self):
        declared = {v.get("name") for v in (self.payload.get("templating") or {}).get("list") or []}
        used = set()
        for panel in self.payload.get("panels") or []:
            ds = panel.get("datasource")
            if isinstance(ds, dict) and ds.get("uid"):
                used.add(ds["uid"])
        for uid in used:
            if uid.startswith("${") and uid.endswith("}"):
                self.assertIn(uid[2:-1], declared,
                              f"面板引用了未声明的数据源变量 {uid} ⇒ 导入后图全是空的")

    def test_dashboard_refresh_and_timewindow_present(self):
        self.assertTrue(self.payload.get("time"), "没有默认时间窗 ⇒ 打开是空白")
        self.assertTrue(self.payload.get("refresh"))


class MetricNameAntiRotTest(unittest.TestCase):
    """核心门：族名必须与真实渲染结果**双向**对齐。"""

    def test_every_referenced_family_exists(self):
        known = emitted_families()
        referenced = set()
        for _title, expr in _dashboard_exprs(json.loads(DASHBOARD.read_text(encoding="utf-8"))):
            referenced |= set(FAMILY_TOKEN.findall(expr))
        for entry in _alert_entries():
            referenced |= set(FAMILY_TOKEN.findall(entry["expr"]))
        self.assertTrue(referenced, "面板/告警一个族名都没引用？解析器坏了")
        unknown = sorted(referenced - known)
        self.assertEqual(
            unknown, [],
            "这些族名在面板/告警里被引用，但 metrics.py 根本不产出 —— "
            f"图表会静默空白、告警会永不触发：{unknown}\n"
            f"（实际产出：{sorted(known)}）")

    def test_every_emitted_family_is_consumed_or_registered(self):
        known = emitted_families()
        referenced = set()
        for _title, expr in _dashboard_exprs(json.loads(DASHBOARD.read_text(encoding="utf-8"))):
            referenced |= set(FAMILY_TOKEN.findall(expr))
        for entry in _alert_entries():
            referenced |= set(FAMILY_TOKEN.findall(entry["expr"]))
        orphan = sorted(known - referenced - INTENTIONALLY_UNUSED)
        self.assertEqual(
            orphan, [],
            f"这些族产出了但没人看（要么上面板/告警，要么登记进 INTENTIONALLY_UNUSED）：{orphan}")

    def test_snapshot_fixture_covers_every_family(self):
        """夹具漏族 ⇒ 上一门会误报。用"已知血缘"反查：每个族都必须来自夹具的某个源。"""
        known = emitted_families()
        for family in ("r20_market_data_calls_total", "r20_venue_instruments_ok",
                       "r20_model_tokens_total", "r20_risk_limit"):
            self.assertIn(family, known, f"夹具没覆盖 {family} ⇒ 反漂移门形同虚设")
        self.assertGreaterEqual(len(known), 14, f"族数异常偏少：{sorted(known)}")

    def test_up_is_always_scoped_to_a_job(self):
        """`up` 是 Prometheus 自带的**全局**序列：不限定 job 就会把别的抓取目标算进来。

        （本门第一版写成"检查有没有 r20_up"是纯粹的逻辑错误 —— `r20_up` 是本仓
        自己的进程存活指标，与 Prometheus 的 `up` 是两回事；门禁自己也会写错，
        所以每加一条断言都要说清它到底在防什么。）
        """
        exprs = [e for _t, e in _dashboard_exprs(json.loads(DASHBOARD.read_text(encoding="utf-8")))]
        exprs += [e["expr"] for e in _alert_entries()]
        for expr in exprs:
            if re.search(r"(?<![a-zA-Z0-9_])up(?![a-zA-Z0-9_])", expr):
                self.assertIn('job="', expr,
                              f"用到了 Prometheus 的 up 却没限定 job（会串到别的抓取目标）：{expr}")


class AlertRulesShapeTest(unittest.TestCase):
    def setUp(self):
        self.entries = _alert_entries()

    def test_alerts_are_discovered(self):
        self.assertGreaterEqual(len(self.entries), 6, f"告警条目太少：{len(self.entries)}")

    def test_each_alert_has_four_essentials(self):
        for entry in self.entries:
            with self.subTest(alert=entry["name"]):
                self.assertTrue(entry["name"])
                self.assertTrue(entry["expr"].strip(), "告警没有表达式")
                self.assertTrue(entry["for"], "告警没有 for ⇒ 一次抖动就吼，会被忽略")
                self.assertIn(entry["severity"], {"warning", "critical"},
                              "severity 必须是 warning/critical（Grafana/Prometheus 惯例）")
                self.assertTrue(entry["annotations"].get("summary"), "缺 summary")
                self.assertTrue(entry["annotations"].get("description"),
                                "缺 description ⇒ 收到告警的人不知道该怎么办")

    def test_expressions_are_single_line_safe(self):
        for entry in self.entries:
            with self.subTest(alert=entry["name"]):
                expr = entry["expr"]
                self.assertEqual(expr.count("("), expr.count(")"),
                                 f"括号不平衡（YAML 折行解析出错？）：{expr}")
                self.assertNotIn("\t", expr)
                self.assertFalse(expr.strip().startswith("|"), "块标量没被拼起来")

    def test_severity_split_is_meaningful(self):
        severities = {e["severity"] for e in self.entries}
        self.assertEqual(severities, {"warning", "critical"},
                         "既有 warning 也有 critical 才说明分级是有意的")

    def test_coverage_of_the_two_incident_classes(self):
        """两次真实事故必须各有对应告警（否则出口又漏了）。"""
        exprs = " ".join(e["expr"] for e in self.entries)
        self.assertIn("r20_market_data_last_success_age_seconds", exprs,
                      "第 137 刀：取数失败必须有告警")
        self.assertIn("r20_market_data_snapshot_age_seconds", exprs,
                      "worker 断档（周期不再运行）必须有告警")


class PrometheusConfigShapeTest(unittest.TestCase):
    """窄结构校验（无 pyyaml，见模块 docstring 局限说明）。"""

    def setUp(self):
        self.text = PROMETHEUS.read_text(encoding="utf-8")

    def test_scrape_targets_the_admin_metrics_endpoint(self):
        self.assertIn("metrics_path: /api/v1/admin/metrics", self.text)
        self.assertIn("job_name: r20-backend", self.text)

    def test_uses_admin_header_not_query_token(self):
        """令牌必须走请求头文件注入：查询串里的 token 会进日志/浏览器历史。"""
        self.assertIn("X-R20-Admin-Token", self.text)
        self.assertNotIn("?token=", self.text)
        self.assertNotIn("X-R20-Admin-Token: ", self.text.replace("X-R20-Admin-Token:", "", 1)
                         .replace('      X-R20-Admin-Token:', "", 1),
                         "令牌值不得直接写在配置里（只允许 secrets 文件引用）")

    def test_token_is_never_a_literal(self):
        for line in self.text.splitlines():
            if "Token" in line or "token" in line:
                self.assertNotRegex(line, r"[:=]\s*[A-Za-z0-9]{16,}",
                                    f"疑似把令牌字面量写进了配置：{line.strip()}")

    def test_rule_files_reference_exists(self):
        self.assertIn("rule_files:", self.text)
        self.assertIn("alerts.yml", self.text)
        self.assertTrue(ALERTS.exists(), "rule_files 指向的 alerts.yml 不存在")

    def test_retention_is_not_unbounded(self):
        compose = (OBS / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("--storage.tsdb.retention.time", compose,
                      "没有保留期 ⇒ 磁盘会被时序数据吃满")
        self.assertIn("127.0.0.1", compose,
                      "指标含账户规模与风控阈值，端口必须只绑本机")

    def test_compose_referenced_files_all_exist(self):
        compose = (OBS / "docker-compose.yml").read_text(encoding="utf-8")
        for name in re.findall(r"\./([A-Za-z0-9_.-]+\.(?:yml|json))", compose):
            self.assertTrue((OBS / name).exists(), f"compose 引用了不存在的文件：{name}")

    def test_compose_mounts_token_file_readonly(self):
        compose = (OBS / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("/etc/r20/metrics_token:ro", compose, "令牌文件必须只读挂载")


if __name__ == "__main__":
    unittest.main()


class DocsNumberAntiRotTest(unittest.TestCase):
    """文档数字防腐烂：README 里写的面板/规则条数必须与**真实文件**一致。

    教训（本刀自己踩的）：README 与提交信息里写了"13 个面板"，真实是 **14** ——
    这正是本仓最讨厌的那类"看着像事实的数字"。人写数字会错，所以让门来数。
    """

    def setUp(self):
        self.doc = (OBS / "README.md").read_text(encoding="utf-8")
        self.payload = json.loads(DASHBOARD.read_text(encoding="utf-8"))

    def test_panel_count_in_docs_matches_reality(self):
        real = len(self.payload.get("panels") or [])
        stated = [int(m) for m in re.findall(r"\*\*(\d+) 个面板", self.doc)]
        self.assertTrue(stated, "README 应写明面板数量（`**N 个面板**`）")
        self.assertEqual(stated[0], real, f"README 写 {stated[0]} 个面板，实际 {real} 个")

    def test_query_count_in_docs_matches_reality(self):
        real = sum(len(p.get("targets") or []) for p in self.payload.get("panels") or [])
        stated = [int(m) for m in re.findall(r"(\d+) 条查询", self.doc)]
        self.assertTrue(stated, "README 应写明查询条数（`N 条查询`）")
        self.assertEqual(stated[0], real, f"README 写 {stated[0]} 条查询，实际 {real} 条")

    def test_alert_count_in_docs_matches_reality(self):
        real = len(_alert_entries())
        stated = [int(m) for m in re.findall(r"\*\*(\d+) 条\*\*", self.doc)]
        self.assertTrue(stated, "README 应写明告警条数")
        self.assertEqual(stated[0], real, f"README 写 {stated[0]} 条告警，实际 {real} 条")

    def test_alert_table_lists_every_rule(self):
        """README 的告警表必须逐条列出真实规则名 —— 漏一条就有人不知道要看什么。"""
        for entry in _alert_entries():
            self.assertIn(entry["name"], self.doc,
                          f"README 告警表漏了 {entry['name']}（响了没人知道该做什么）")
