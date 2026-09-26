"""品牌一致性门禁（2026-09-27，起因：改名后仍有 30+ 处**用户可见**的残留）。

## 事故形态（不是假设，是实测扫出来的）

2026-09 把对外品牌改成 `AstraQuant` 时，只改了"主品牌串"（版本常量、路由标题、
几个组件）。但下面这些**用户直接看得到**的地方还散着旧品牌与旧域名：

| 面 | 残留 | 用户会怎么撞上 |
|---|---|---|
| SEO 元数据 | `frontend/index.html` 的 canonical / og:* / twitter:* / JSON-LD 全指 `www.r20.cn` | 搜索引擎按 canonical 认为正主是**旧域名** —— 新站点的权重全被让出去 |
| 站点地图 | `sitemap.xml` / `robots.txt` 六条 URL 全是旧域名 | 提交给搜索引擎的就是错的 |
| 运行时 SEO 兜底 | `r20_backend/routers/dashboard.py` 里写死的域名 | 与静态资源不一致 |
| 界面文案 | `locales/*/admin/login.ts`（`R20 控制台`）、`dash/about.ts`（`关于 R20`）、`dash/matrix.ts`（剪贴板 `【R20 风控测算】`） | 顶栏、关于浮层、**用户粘贴到群里的文案** |
| 路由标题 | `router/index.ts` 里 5 处 ` · R20` | 浏览器标签页 |
| 启动输出 | `start.sh` / `start.ps1` / `deploy/docker-start.sh` | 第一次跑起来就看到 |
| 部署物料 | `deploy/r20-*.service` 的 `Description`、Grafana 面板标题、`LICENSE` 版权行 | `systemctl status` / 面板 / GitHub 仓库页 |

**手工扫一遍能扫出来，但下一次加页面、加文案时又会漂回去。** 所以立本门。

## 判据一：旧品牌串不得回潮

以下串出现在**对外面**（除 `tests/` 与显式豁免外的一切被跟踪文件）即翻红：

    R20 Quantum Trader / R20 QUANTUM TRADER / R20-Quantum-Trader
    www.r20.cn / 关于 R20 / R20 控制台 / About R20 / R20 Console

⚠️ 与**有意保留的内部标识**不冲突：`R20_*` 环境变量键、`r20_backend` /
`r20_gateway` 包名、`X-R20-Session` 会话头、`UPDATE R20` / `BACKUP R20` /
`RESTORE R20` 逐字契约、备份魔数 `R20GCM2`、Grafana UID `r20-quantum-trader`、
`/opt/r20-quantum-trader` 部署路径 —— 它们都**不含**上面这些品牌串。
判据只钉"读起来像品牌/域名"的形态，不碰"当接口名用"的形态。

## 判据二：站点 URL 必须单一来源、多处同源

`https://www.astraquant.tech` 是规范形态（**带 www**，与 DNS 实际解析一致）。
它必须在这五处完全一致：

1. `r20_backend/version.py::APP_SITE` —— 后端单一来源
2. `frontend/src/config/version.ts::OFFICIAL_SITE`
3. `frontend/index.html` 的 `rel="canonical"`
4. 同文件的 `og:url` 与 `twitter:url`
5. 同文件 JSON-LD 的 `@id` 前缀

**为什么值得钉**：canonical 与 og:url 不一致时，搜索引擎会自己挑一个 ——
而历史上挑中的那个正是**旧域名**。这就是本次事故的真实形态。

顺带钉住：`r20_backend/routers/dashboard.py` 里**不得再写死域名**（必须走 `APP_SITE`），
以及仓库 URL 不得回退到旧仓库名 `r20-quantum-trader`。
"""
from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: 旧品牌/旧域名形态（**必须消失**）。
BANNED = (
    "R20 Quantum Trader",
    "R20 QUANTUM TRADER",
    "R20-Quantum-Trader",
    "www.r20.cn",
    "关于 R20",
    "R20 控制台",
    "About R20",
    "R20 Console",
)

#: 扫描豁免：路径 → 不扫的理由（**必须写清为什么**）。
ALLOWED: "dict[str, str]" = {
    "requirements.txt":
        "依赖清单字节冻结（`tests/core/test_dependency_manifest_unchanged.py` 按 SHA-256 守它），"
        "改一个注释也会翻红；该文件的表头注释不可见给用户，不值得为它破坏那道门的语义。",
    "docs/research/r20_math_foundations_report.md":
        "有日期的历史研究报告（v1.0 / 系统 v7.6.3 / 2026-09-08）。它记录的是**当时**的系统，"
        "改写正文等于篡改历史记录；`docs/research/` 下的报告按快照对待。",
}

#: 测试夹具会**逐字**钉住历史串（抽取门的基线侧必须与老提交一致），故整树不扫。
#: 用户看不到 `tests/`，这不是可见面。
EXCLUDED_PREFIXES = ("tests/",)

#: 规范站点与仓库（判据二的期望值）。
EXPECTED_SITE = "https://www.astraquant.tech"
EXPECTED_REPO = "https://github.com/555cute/astra-quant-agent"


def _tracked_files() -> "list[str]":
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, f"git ls-files 失败：{out.stderr[:200]}"
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _read(rel: str) -> str:
    p = ROOT / rel
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""       # 二进制资源（png 等）不参与文本判据


def _grep_literal(token: str) -> "list[str]":
    """在被跟踪文件里找字面量，返回命中文件列表。

    ⚠️ **为什么走 `git grep` 而不是 Python 读文件**：本判据要扫的
    `data/prompt_library.json` 在 `tests/__init__.py` 的「生产配置读守卫」保护名单里
    （出厂预设的标题就是用户可见的品牌面，所以必须扫到它），但 Python 侧读 `data/`
    会在严格模式 `R20_TESTS_STRICT_READS=1` 下**直接抛错** ——
    而"严格模式全绿"是本仓的不变量（见 `docs/FAILURE_SEMANTICS.md`）。
    走 git 子进程能扫到同一份工作树内容，又完全不触发那条守卫。
    """
    out = subprocess.run(["git", "grep", "-F", "-l", "-e", token],
                         capture_output=True, text=True, cwd=str(ROOT))
    if out.returncode not in (0, 1):        # 1 = 无命中，不是错误
        raise AssertionError(f"git grep 失败（token={token!r}）：{out.stderr[:200]}")
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _is_scanned(rel: str) -> bool:
    return not rel.startswith(EXCLUDED_PREFIXES) and rel not in ALLOWED


class BrandStringsNotRegressedTest(unittest.TestCase):
    """判据一：旧品牌串不得回潮到对外面。"""

    def test_scan_is_not_vacuous(self):
        """闸自检：被扫文件数必须够多，否则"零命中"毫无意义。"""
        files = [f for f in _tracked_files() if _is_scanned(f)]
        self.assertGreater(len(files), 200, f"只扫到 {len(files)} 个被跟踪文件 ⇒ 扫描范围失效")

    def test_detector_actually_matches(self):
        """闸自检：**检索通道**本身要能命中。

        只断言"BANNED 里的串非空"是不够的 —— 检索一旦坏掉（cwd 错、git grep 参数错、
        范围被写死成空集），"零命中"同样是绿。故加一条**正向对照**：仓库里必然存在
        的新品牌串必须被同一通道找出来。
        """
        self.assertEqual([t for t in BANNED if not t.strip()], [], "BANNED 里有空串")
        hits = _grep_literal("AstraQuant")
        self.assertGreaterEqual(
            len(hits), 5,
            f"正向对照失败：`git grep AstraQuant` 只命中 {len(hits)} 个文件 —— 检索通道坏了，"
            "此时'没有旧品牌串'是假绿")

    def test_no_banned_brand_string_in_public_surfaces(self):
        offenders: "dict[str, list[str]]" = {}
        for tok in BANNED:
            for rel in _grep_literal(tok):
                if _is_scanned(rel):
                    offenders.setdefault(rel, []).append(tok)
        self.assertEqual(
            offenders, {},
            "对外面出现旧品牌/旧域名 —— 用户看得到的地方仍是旧名字：\n  "
            + "\n  ".join(f"{f}: {sorted(set(t))}" for f, t in sorted(offenders.items()))
            + "\n（若某文件确属历史快照，请连同理由加进本测试的 ALLOWED）")


class SiteUrlIsSingleSourceTest(unittest.TestCase):
    """判据二：规范站点 URL 必须单一来源、多处同源。"""

    def _py_app_site(self) -> str:
        m = re.search(r'^APP_SITE\s*=\s*"([^"]+)"', _read("r20_backend/version.py"), re.M)
        self.assertIsNotNone(m, "r20_backend/version.py 缺少 APP_SITE 常量")
        return m.group(1)

    def _ts_official_site(self) -> str:
        m = re.search(r"OFFICIAL_SITE\s*=\s*'([^']+)'",
                      _read("frontend/src/config/version.ts"))
        self.assertIsNotNone(m, "frontend/src/config/version.ts 缺少 OFFICIAL_SITE 常量")
        return m.group(1)

    def test_python_and_typescript_brand_sources_agree(self):
        self.assertEqual(self._py_app_site(), EXPECTED_SITE,
                         "后端 APP_SITE 不是规范形态（注意：**带 www**）")
        self.assertEqual(self._ts_official_site(), EXPECTED_SITE,
                         "前端 OFFICIAL_SITE 不是规范形态（注意：**带 www**）")

    def test_index_html_seo_metadata_is_same_origin(self):
        html = _read("frontend/index.html")
        self.assertTrue(html, "frontend/index.html 读不到")
        got = {
            "rel=canonical": re.search(r'rel="canonical" href="([^"]+)"', html),
            "og:url": re.search(r'property="og:url" content="([^"]+)"', html),
            "twitter:url": re.search(r'name="twitter:url" content="([^"]+)"', html),
        }
        for label, m in got.items():
            self.assertIsNotNone(m, f"index.html 缺少 {label}（会被搜索引擎当成无主页面）")
        origins = {m.group(1).rstrip("/") for m in got.values()}
        self.assertEqual(len(origins), 1,
                         f"canonical / og:url / twitter:url 不同源：{sorted(origins)} "
                         "⇒ 搜索引擎会自己挑一个，历史教训是它挑中了旧域名")
        self.assertEqual(origins.pop(), EXPECTED_SITE, "SEO 元数据指向的不是规范站点")

    def test_jsonld_ids_point_at_the_canonical_site(self):
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            _read("frontend/index.html"), re.S)
        self.assertEqual(len(blocks), 1, "index.html 应恰好有一段 JSON-LD")
        graph = json.loads(blocks[0])["@graph"]
        self.assertTrue(graph, "JSON-LD @graph 为空")
        for node in graph:
            self.assertTrue(node.get("@id", "").startswith(EXPECTED_SITE),
                            f"JSON-LD @id 不在规范站点下：{node.get('@id')!r}")
            self.assertTrue(str(node.get("url", "")).startswith(EXPECTED_SITE),
                            f"JSON-LD url 不在规范站点下：{node.get('url')!r}")

    def test_robots_and_sitemap_use_the_canonical_origin(self):
        """⚠️ 只判**站点自身的 URL**：sitemap 的 XML 命名空间（w3.org / sitemaps.org）
        是协议要求的固定常量，把它们算进来只会制造噪音。"""
        robots = _read("frontend/public/robots.txt")
        self.assertTrue(robots, "frontend/public/robots.txt 读不到")
        robots_urls = re.findall(r"^(?:Host|Sitemap):\s*(\S+)\s*$", robots, re.M)
        self.assertTrue(robots_urls, "robots.txt 里没有 Host/Sitemap 行 ⇒ 判据失效")
        bad = sorted({u for u in robots_urls if not u.startswith(EXPECTED_SITE)})
        self.assertEqual(bad, [], f"robots.txt 指向了非规范站点：{bad}")

        sitemap = _read("frontend/public/sitemap.xml")
        self.assertTrue(sitemap, "frontend/public/sitemap.xml 读不到")
        locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
        self.assertTrue(locs, "sitemap.xml 里没有 <loc> ⇒ 判据失效")
        bad_loc = sorted({u for u in locs if not u.startswith(EXPECTED_SITE)})
        self.assertEqual(bad_loc, [], f"sitemap.xml 的 <loc> 指向了非规范站点：{bad_loc}")

    def test_runtime_seo_endpoints_have_no_hardcoded_domain(self):
        """运行期兜底（`/robots.txt` `/sitemap.xml`）必须走单一来源，不得再写死域名。"""
        src = _read("r20_backend/routers/dashboard.py")
        self.assertTrue(src, "r20_backend/routers/dashboard.py 读不到")
        self.assertIn("from r20_backend.version import APP_SITE", src,
                      "运行期 SEO 兜底应引用 APP_SITE 单一来源")
        for hard in ("www.r20.cn", "astraquant.tech"):
            self.assertNotIn(hard, src,
                             f"routers/dashboard.py 又写死了域名 {hard!r} —— 请改用 APP_SITE")

    def test_repository_url_is_the_renamed_repo(self):
        """⚠️ 只判**仓库 URL**形态。裸串 `r20-quantum-trader` 仍会合法出现 ——
        它是有意保留的部署路径 / Grafana UID（见 README「品牌与内部代号」），
        一刀切会让那条有意为之的说明反而过不了门。"""
        stale_url = "github.com/555cute/r20-quantum-trader"
        for rel in ("README.md", "README.zh-CN.md", "frontend/src/config/version.ts",
                    "r20_backend/routers/system.py", "frontend/src/views/docs/DocsView.vue"):
            self.assertNotIn(stale_url, _read(rel),
                             f"{rel} 里还有旧仓库 URL（改名后是 404 链接）")
        self.assertIn(EXPECTED_REPO, _read("frontend/src/config/version.ts"))
        self.assertIn(EXPECTED_REPO, _read("r20_backend/routers/system.py"))


if __name__ == "__main__":
    unittest.main()
