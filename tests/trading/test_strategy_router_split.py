r"""strategy 路由拆分对拍门（结构整理 B8·第九十六刀）。

`r20_backend/routers/strategy.py`（775 行 / 35 端点）按资源拆成包
`r20_backend/routers/strategy/`（council / interceptors / policy / prompts）。

## 这类重构的唯一安全属性：**路由表一字不变**

拆 router 不动一个字符的业务代码，但只要**少一条路由、多一条、或换一次顺序**，
线上就是"某个接口 404"或"某个接口走错处理器"（子路由存在前缀包含关系：
`/admin/interceptors/{filename}` 与 `/admin/interceptors/reorder`）。

所以本门三重比对：

1. **静态**：基线文件（`git show PRE:...`）里按出现顺序抽出的
   `(路径, 方法, 处理器名)` 列表，必须与拆分后**按聚合顺序**（council →
   interceptors → policy → prompts）抽出的列表**逐项相等**；
2. **实时**：真正 import 应用取 OpenAPI 规格，比对 `(路径, 方法)` 集合
   （接口面）与 tags（`["strategy"]`，且**不得重复**）；
3. **聚合顺序**：`__init__.py` 的 include 顺序必须与文档一致。

> 本门第一版就抓到一个真 bug：`FunctionDef.lineno` 指向 `def` 行而**不含装饰器行**，
> 于是每个子模块的**首个处理器装饰器被切掉**，5 条路由静默消失。
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRE = "386f24b"                      # 本刀动工前最后提交（第九十五刀收口）
BASELINE = "r20_backend/routers/strategy.py"
PKG = ROOT / "r20_backend" / "routers" / "strategy"
INCLUDE_ORDER = ("council", "interceptors", "policy", "prompts")

# 拆分**之后**新增的路由（正常演进，不是本刀产物）：自进化配置页的读写两条，
# 落在 prompts 子模块里，路径前缀是 `/api/v1/admin/evolution`。
# 本门原本要求「路由表一字不变」，那只对**拆分那一刻**成立；此后新增路由必须
# 登记在此表，否则下面会红（少一条/多一条/换顺序/换处理器名都会红）——
# 它是登记，不是放水。
POST_SPLIT_ADDITIONS = (
    ("/api/v1/admin/evolution/config", "GET", "get_evolution_config"),
    ("/api/v1/admin/evolution/config", "PUT", "update_evolution_config"),
)


def _routes_in(node_src: str) -> list:
    """按源码顺序抽 `(路径, 方法, 处理器名)`。"""
    out = []
    for n in ast.parse(node_src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                    if d.args and isinstance(d.args[0], ast.Constant):
                        out.append((d.args[0].value, d.func.attr.upper(), n.name))
    return out


class StrategyRouterSplitTest(unittest.TestCase):
    def test_static_route_table_is_identical_and_ordered(self):
        r = subprocess.run(["git", "show", f"{PRE}:{BASELINE}"],
                           capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, f"基线取不到：{r.stderr[:200]}")
        want = _routes_in(r.stdout)
        self.assertEqual(len(want), 35, f"基线应有 35 条路由，实际 {len(want)}")

        got = []
        for name in INCLUDE_ORDER:
            got.extend(_routes_in((PKG / f"{name}.py").read_text(encoding="utf-8")))
        self.assertEqual(got[:len(want)], want,
                         "前 35 条路由表（路径/方法/处理器名/顺序）与拆分前不一致")
        self.assertEqual(tuple(got[len(want):]), POST_SPLIT_ADDITIONS,
                         "拆分后新增路由未登记（或顺序/处理器名变了）")

    def test_live_openapi_route_surface_unchanged(self):
        from r20_backend.app import app
        spec = app.openapi()
        pref = ("/api/v1/admin/council", "/api/v1/admin/interceptor",
                "/api/v1/admin/policy", "/api/v1/prompt-library", "/api/v1/admin/prompt")
        live, tags = set(), {}
        for path, ops in spec["paths"].items():
            if not any(path.startswith(p) for p in pref):
                continue
            for method, op in ops.items():
                live.add((path, method.upper()))
                t = tuple(op.get("tags") or [])
                tags[t] = tags.get(t, 0) + 1
        r = subprocess.run(["git", "show", f"{PRE}:{BASELINE}"],
                           capture_output=True, text=True, cwd=str(ROOT))
        want = {(p, m) for p, m, _ in _routes_in(r.stdout)}
        self.assertEqual(live, want, "线上接口面（路径×方法）与拆分前不一致")
        self.assertEqual(len(live), 35)
        self.assertEqual(tags, {("strategy",): 35},
                         "tags 必须恰好一处 ['strategy']（两处都加会重复）")

    def test_aggregator_include_order_is_documented_order(self):
        src = (PKG / "__init__.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        seq = []
        for n in ast.walk(tree):
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple):
                seq = [e.id for e in n.iter.elts if isinstance(e, ast.Name)]
        self.assertEqual(tuple(seq), INCLUDE_ORDER,
                         "聚合顺序必须与文档/基线出现顺序一致（顺序即匹配优先级）")
        self.assertIn("router = APIRouter()", src, "聚合器不得再加 tags")

    def test_old_module_path_still_importable(self):
        """外部 `from r20_backend.routers.strategy import router` 必须照旧可用。

        ⚠️ 本仓 FastAPI 版本的 `include_router` 是**惰性**的：聚合器的 `routes`
        里放的是 `_IncludedRouter` **句柄**（4 个子路由），真正的 35 条在应用
        规格解析时才展开 —— 故此处只断言"4 个句柄 + 可挂载"，35 条由
        `test_live_openapi_route_surface_unchanged` 从 OpenAPI 规格校验。
        """
        from r20_backend.routers.strategy import router
        self.assertTrue(hasattr(router, "routes"))
        self.assertEqual(len(router.routes), len(INCLUDE_ORDER),
                         "聚合器应有 4 个子路由句柄（惰性 include）")
        self.assertTrue(all(type(r).__name__ == "_IncludedRouter" for r in router.routes),
                        "应为 FastAPI 的惰性 include 句柄")

    def test_judgment_actually_notices_a_change(self):
        src = (PKG / "council.py").read_text(encoding="utf-8")
        got = _routes_in(src)
        self.assertTrue(got)
        broken = src.replace('@router.get("/api/v1/admin/council/config")', '', 1)
        self.assertNotEqual(_routes_in(broken), got, "自检：判据看不见缺失的路由")


if __name__ == "__main__":
    unittest.main()
