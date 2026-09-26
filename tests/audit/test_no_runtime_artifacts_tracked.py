"""运行时产物不得入库（2026-09）。

## 为什么要有这道门

本仓为"机器状态混进仓库"付过两次代价：`data/prompt_library.json` 被 `git add -f`
强加进来、`.archive/` 里 7 个文件被 `-f` 强加。第三次就发生在写这道门的当天：

`data/.r20_gateway_heartbeat` 是 worker **每秒重写**的存活心跳。`.gitignore` 的
`data/` 段落是按**扩展名**列举的（`*.json` / `*.lock` / `*.pid` …），而这个文件
**没有扩展名** ⇒ 一条规则都没匹配上 ⇒ `git add -A` 把它扫进了提交。

这类文件进了仓库不会有任何测试报错（它长得就像个普通文本文件），只会在别人的
`git status` 里变成一串"莫名其妙的改动"，或者把某台机器的运行状态泄漏出去。
故在此把判据钉死：**按扩展名列举必然漏**，必须同时挡住"无扩展名的运行时文件"。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: `data/` 下**允许**被跟踪的文件（白名单，新增必须是有意为之并在此登记）。
DATA_ALLOWLIST = {
    "data/.gitkeep",              # 目录占位
    "data/prompt_library.json",   # 出厂提示词基线（只读，用户改动走 .local）
}

#: 运行时产物形态：进程/锁/日志/心跳/数据库副本等。
RUNTIME_ARTIFACT = re.compile(
    r"(\.pid|\.lock|\.log|\.db-wal|\.db-shm|\.sqlite3?|"
    r"heartbeat|\.tmp|\.bak)$"
)


def _tracked(*paths: str) -> list[str]:
    cmd = ["git", "ls-files"]
    if paths:
        cmd += ["--", *paths]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
    return [line for line in done.stdout.splitlines() if line.strip()]


def test_data_directory_holds_only_the_allowlisted_files():
    """`data/` 是运行时目录：任何新被跟踪的文件都必须是**有意**加进来的。"""
    tracked = _tracked("data")
    unexpected = sorted(set(tracked) - DATA_ALLOWLIST)
    assert not unexpected, (
        "data/ 下出现了未经登记的被跟踪文件（多半是运行时产物被 git add -A 扫进来）：\n  "
        + "\n  ".join(unexpected)
        + "\n若确实是有意入库的基线文件，请加入本门的 DATA_ALLOWLIST 并在 "
          ".gitignore 里确认它不会被误伤。")


def test_no_runtime_artifacts_are_tracked_anywhere():
    """全仓范围：进程锁/日志/心跳/数据库副本一律不该被跟踪。"""
    offenders = [p for p in _tracked() if RUNTIME_ARTIFACT.search(p)]
    assert not offenders, (
        "这些运行时产物被跟踪了 —— 它们是机器状态，不是源码：\n  " + "\n  ".join(offenders))


def test_the_extensionless_runtime_prefix_is_ignored():
    """按扩展名列举**挡不住无扩展名**的运行时文件（本门的立案原因）。

    故 `.gitignore` 必须同时有一条按前缀的兜底；这里直接问 git 要答案，
    而不是去正则匹配 .gitignore 的文本。
    """
    heartbeat = ROOT / "data" / ".r20_gateway_heartbeat"
    done = subprocess.run(["git", "check-ignore", "-q", str(heartbeat.relative_to(ROOT))],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, (
        "data/.r20_gateway_heartbeat 未被忽略 ⇒ 下一次 git add -A 又会把它扫进来")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
