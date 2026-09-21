"""配置键表里的每个键都必须**真的能被消费**（第二百刀）。

## 为什么需要这道门

本仓有三张"配置键表"，它们是**管理面能读写的全部配置面**：

| 表 | 位置 | 作用 |
|---|---|---|
| `MANAGED_KEYS` | `r20_backend/settings_store.py` | 后台设置接口可读写的键 |
| `SECRET_KEYS` | `r20_gateway/secrets.py` | 加密密钥库里允许存的键 |
| `RISK_ENV_KEYS` | `scripts/risk_constants.py` | 「风控管理页」写入、交易侧读取的键 |

表里多一个**谁都不读**的键 ⇒ 操作者在后台改了它、系统毫无反应（配置假象，比没有更坏）。

## 判据（三条通道，缺一条就会误报）

1. **直接读**：`os.environ[...]` / `environ.get` / `os.getenv` / `env.get`（dict 传参）/
   `_env_*` helper 的字面量参数；
2. **字面量**出现（表驱动的读写，如 `routers/exchanges.py` 的 `secret_map`）；
3. **解析器档位链**：凭证键是**动态**取的 ——
   `registry.py::venue_credentials` 按 `[f"{key}_DEMO", f"{key}_TESTNET", f"{key}_SANDBOX", key]`
   或 `[f"{key}_LIVE", key]` 逐档取 `f"{tier}_API_KEY"`。
   ⇒ `BINANCE_TESTNET_API_KEY`/`GATE_SANDBOX_API_KEY` 这类键**没有任何字面量读点**，
   却是真实可消费的（本刀第一版没建模这一通道，误报 18 个"死键"）。

⚠️ 表自身那一行不算"消费证据"（否则每张表都自证清白 —— 第一版就是这么假绿的）。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("scripts", "r20_backend", "r20_gateway", "plugins")
ENV_DICT_RECEIVERS = {"env", "environment", "env_vars", "_env", "env_map"}

#: 凭证解析器的档位后缀（取自 `r20_backend/exchanges/registry.py::venue_credentials`）
TIER_SUFFIXES = ("LIVE", "DEMO", "TESTNET", "SANDBOX")
CREDENTIAL_FIELDS = ("API_KEY", "SECRET_KEY", "PASSPHRASE")

#: 允许"表里有、确实没人消费"的键（附理由）。当前为空。
ALLOWLIST: dict[str, str] = {}


def _looks_like_key(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", name))


def _tables() -> dict:
    """三张表 → (键集合, 表所在文件, 表占用行区间)。"""
    tables = {}
    ss = (ROOT / "r20_backend" / "settings_store.py").read_text(encoding="utf-8")
    a = ss.index("MANAGED_KEYS = {")
    b = ss.index("}\n", a)
    tables["MANAGED_KEYS"] = (set(re.findall(r'"([A-Z][A-Z0-9_]+)"', ss[a:b])),
                              "r20_backend/settings_store.py",
                              (ss[:a].count("\n") + 1, ss[:b].count("\n") + 1))

    gw = (ROOT / "r20_gateway" / "secrets.py").read_text(encoding="utf-8")
    a2 = gw.index("SECRET_KEYS = {")
    b2 = gw.index("}\n", a2)
    tables["SECRET_KEYS"] = (set(re.findall(r'"([A-Z][A-Z0-9_]+)"', gw[a2:b2])),
                             "r20_gateway/secrets.py",
                             (gw[:a2].count("\n") + 1, gw[:b2].count("\n") + 1))

    rc = (ROOT / "scripts" / "risk_constants.py").read_text(encoding="utf-8")
    # `RISK_ENV_KEYS = tuple(DEFAULTS.keys())` ⇒ 键在 DEFAULTS 字典字面量里（AST 取，别用正则猜）
    rc_tree = ast.parse(rc)
    defaults_keys, d_lo, d_hi = set(), 0, 0
    for node in ast.walk(rc_tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets):
            if isinstance(node.value, ast.Dict):
                defaults_keys = {k.value for k in node.value.keys
                                 if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                d_lo, d_hi = node.lineno, (node.end_lineno or node.lineno)
    tables["RISK_ENV_KEYS"] = (defaults_keys, "scripts/risk_constants.py", (d_lo, d_hi))
    return tables


def _evidence() -> tuple:
    """返回 (直接读点表, 普通字面量表)。"""
    reads, literals = {}, {}
    for root in SCAN_DIRS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(ROOT))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and _looks_like_key(node.value):
                    literals.setdefault(node.value, f"{rel}:{node.lineno}")
                if isinstance(node, ast.Subscript) and ast.unparse(node.value).endswith("environ"):
                    sl = node.slice
                    if isinstance(sl, ast.Constant) and isinstance(sl.value, str) \
                            and _looks_like_key(sl.value):
                        reads.setdefault(sl.value, f"{rel}:{node.lineno}")
                if not isinstance(node, ast.Call):
                    continue
                name = ast.unparse(node.func)
                short = name.split(".")[-1]
                receiver = ast.unparse(node.func.value) if isinstance(node.func, ast.Attribute) else ""
                is_read = (name in ("os.getenv", "getenv", "os.environ.get", "environ.get")
                           or (isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                               and (receiver.endswith("environ")
                                    or receiver.split(".")[-1] in ENV_DICT_RECEIVERS))
                           or short.startswith("_env") or short.startswith("env_")
                           or short.endswith("_env"))
                if not is_read:
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                            and _looks_like_key(arg.value):
                        reads.setdefault(arg.value, f"{rel}:{node.lineno}")
                        break
    return reads, literals


#: 解析器认识的场所（`registry.py::venue_credentials` 里 key 由 venue 名大写拼出）。
#: 新增场所必须同步加进来 —— 否则它的凭证键会被本门判红（刻意的强制函数）。
VENUES = {"OKX", "BINANCE", "GATE"}

#: 凭证字段后缀（**按后缀比对**：`BINANCE_LIVE_API_KEY` 按 `_` 切分后末 token 是 "KEY"，
#: 不是 "API_KEY" —— 本门第一版就是这么错判的）
CREDENTIAL_SUFFIXES = ("_API_KEY", "_SECRET_KEY", "_PASSPHRASE")


def tier_pattern_match(key: str) -> bool:
    """凭证键是否落在解析器的档位链上。

    解析器实际取的形态（`registry.py`）：`f"{tier}_API_KEY"`，其中
    `tier ∈ {f"{venue}_LIVE", f"{venue}_DEMO", f"{venue}_TESTNET", f"{venue}_SANDBOX", venue}`。
    ⇒ 合法形态 = `{VENUE}[_{TIER}]_{FIELD}`。
    """
    for suffix in CREDENTIAL_SUFFIXES:
        if not key.endswith(suffix):
            continue
        head = key[: -len(suffix)]              # 例如 BINANCE_TESTNET / GATE / OKX_LIVE
        parts = head.split("_")
        if parts[0] not in VENUES:
            return False
        if len(parts) == 1:
            return True                          # 通用档 {VENUE}_{FIELD}
        return len(parts) == 2 and parts[1] in TIER_SUFFIXES
    return False


class ConfigTablesAreConsumedTest(unittest.TestCase):
    def test_every_table_key_is_consumable(self):
        reads, literals = _evidence()
        dead = {}
        for table, (keys, decl_file, (lo, hi)) in _tables().items():
            for key in sorted(keys):
                if key in ALLOWLIST or key in reads or tier_pattern_match(key):
                    continue
                where = literals.get(key)
                # 只出现在**声明表自己那一行**不算消费
                if where and not where.startswith(f"{decl_file}:"):
                    continue
                if where and where.startswith(f"{decl_file}:"):
                    line = int(where.rsplit(":", 1)[1])
                    if not (lo <= line <= hi):
                        continue
                dead.setdefault(table, []).append(key)
        self.assertEqual(dead, {}, "这些配置键在表里、却没有任何代码消费它 ⇒ 后台改了也不生效："
                                   f"{dead}")

    def test_scan_is_not_vacuous(self):
        tables = _tables()
        for table, (keys, _, _) in tables.items():
            self.assertGreaterEqual(len(keys), 15, f"{table} 只解析出 {len(keys)} 个键 ⇒ 门已脱节")
        reads, literals = _evidence()
        self.assertGreaterEqual(len(reads), 40, f"直接读点只有 {len(reads)} 个 ⇒ 扫描失效")
        self.assertGreaterEqual(len(literals), 300,
                                f"大写字面量集合只有 {len(literals)} 个 ⇒ 扫描失效")
        # 档位链通道必须真的在起作用：抽查两个只可能靠它通过的键
        for cred in ("BINANCE_TESTNET_API_KEY", "GATE_SANDBOX_SECRET_KEY"):
            self.assertTrue(tier_pattern_match(cred), f"{cred} 应由档位链通道判定为可消费")

    def test_tier_pattern_is_not_a_rubber_stamp(self):
        """牙齿：与凭证档位无关的键不得被档位通道放行。"""
        for not_cred in ("R20_NOTIFY_QQ_ENABLED", "R20_MAX_LEVERAGE", "LLM_MODEL",
                         "R20_GATEWAY_DB", "FOO_API_KEY", "BINANCE_LIVE_URL", "OKX_LIVE"):
            self.assertFalse(tier_pattern_match(not_cred), f"{not_cred} 不该被档位通道放行")

    def test_teeth_on_a_dead_table_key(self):
        reads, literals = {"R20_EXISTING_KNOB": "x:1"}, {"R20_EXISTING_KNOB": "x:1"}
        key = "R20_NOBODY_CONSUMES_ME"
        self.assertFalse(key in reads or tier_pattern_match(key) or key in literals,
                         "没人消费的表键必须被判为死键 ⇒ 门没有牙齿")


if __name__ == "__main__":
    unittest.main()
