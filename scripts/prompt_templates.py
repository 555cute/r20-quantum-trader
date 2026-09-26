"""提示词模板编译：文本 ⇄ 模块 ⇄ 管线布局（结构优化阶段 4·B3 第五十三刀）。

## 为什么单独成模块

`scripts/prompt_library.py`（1035 行）里有两块职责：

| 簇 | 内容 |
|---|---|
| **模板编译** | 文本↔模块互转、模块标签继承、管线布局套用与视图（本模块） |
| 配置库 CRUD | `load_library` / `save_library` / profile 增删改查 / 导入导出 / 校验 |

实测（传递纯度扫描）：**CRUD 簇全部经 `BASELINE_FILE` / `LOCAL_FILE` / `MAX_PROFILE_CHARS`
被"污染"，而模板编译簇是纯的**。故抽出本簇，
门面 `prompt_library.py` **1035 → 973 行**。

## ⚠️ 依赖处理：三项注入、一项随簇搬走

| 模块级名 | 本簇里的读点 | 处理 |
|---|---|---|
| `MAX_TEMPLATE_CHARS` | 仅 `_module` | **注入**（配置面口径，留在门面以便 patch） |
| `_BASE_TEMPLATE_SOURCES` | 仅 `base_template_text` | **注入**（它是"管线→基座来源"注册表） |
| `_BASE_TEMPLATE_CACHE` | 仅 `base_template_text` | **注入**（**可变**缓存，见下） |
| `_SECTION_RE` | 仅 `text_to_modules` | **随簇搬走**（只服务模板分节解析） |
| `BJ_TZ` | 本簇**零读点**（只有留在门面的 `_now` 读） | 不搬 |

⚠️ `_BASE_TEMPLATE_CACHE` 是**可变** dict，且门面另有
`register_base_template()` 往它里面写。若本模块 import 期绑一份，
门面登记的基座就**永远读不到**（两处状态分叉）—— 故必须由门面在调用时传入
**同一个对象**。

> ⚠️ **我在本刀的两次自我纠错**（都记下来）：
> 1. 第一版 docstring 我写"`_SECTION_RE` 在本簇里没有被读到（实测）"——
>    **是错的**。`text_to_modules` 明确用了它。我读错了自己工具的扫描输出。
>    正确处置是随簇搬走，并删掉门面那份已无读点的副本。
> 2. 第一版导入清单我是**手写的**，漏了 `hashlib` → 21 例 `NameError`。
>    改为**用 AST 求自由名**再补，才发现还漏了 `importlib` / `sys` / `copy`
>    以及那两个缓存的注入。

## ⚠️ 为什么 `_now` / `_default` **没有**跟着搬

它们看着像工具函数，但被**两个簇共用**：`_now()` 在 `_migrate` / `_revision` /
`create_profile` / `update_profile` / `rollback_profile` 里都用（CRUD 侧）；
`_default()` 在 `_migrate` / `load_library` 里用。搬过来会制造反向依赖。故留在门面。
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import re
import sys
import uuid
from typing import Any, Callable

# 分节标题解析（纯正则、无路径常量）。它**只**服务 `text_to_modules`，
# 故随簇搬来；门面里那份同名副本已因"读点随函数一起搬走"而删除。
_SECTION_RE = re.compile(r"(?m)(?=^={0,30}\s*【[^\n】]+】[^\n]*$)")


def stable_base_module_id(title: str) -> str:
    """基座模块的**确定性** id（由标题派生）。

    旧实现给每个模块随机 uuid：同一段基座文本每次重建都换 id，于是
    「同 id/同标题继承来源」只能靠标题兜底，方案库里 base 模块 id 每存一次就翻新一遍
    （P1-2/批5 同族）。基座模块内容由代码决定，id 派生自标题即可稳定。
    """
    digest = hashlib.sha1(f"r20-base-module::{title}".encode("utf-8")).hexdigest()[:10]
    return f"module-base-{digest}"

def _module(module: dict[str, Any], index: int = 0, *,
            max_template_chars: int) -> dict[str, Any]:
    title = str(module.get("title") or f"模块 {index+1}").strip()[:100]
    source = str(module.get("source") or "custom")[:40]
    fallback_id = stable_base_module_id(title) if source == "base" else f"module-{uuid.uuid4().hex[:10]}"
    return {"id":str(module.get("id") or fallback_id)[:80],"title":title,"content":str(module.get("content") or "").strip()[:max_template_chars],"enabled":bool(module.get("enabled",True)),"locked":bool(module.get("locked",False)),"source":source}

def text_to_modules(text: str, source: str = "legacy", locked: bool = False, *,
                    max_template_chars: int) -> list[dict[str, Any]]:
    chunks=[chunk.strip() for chunk in _SECTION_RE.split(str(text or "")) if chunk.strip()]
    if not chunks and str(text or "").strip(): chunks=[str(text).strip()]
    result=[]
    for i,chunk in enumerate(chunks):
        first=chunk.splitlines()[0].strip(" =") if chunk.splitlines() else f"模块 {i+1}"
        title=(re.search(r"【([^】]+)】",first).group(1) if re.search(r"【([^】]+)】",first) else first)[:100]
        result.append(_module({"title":title,"content":chunk,"locked":locked,"source":source},i,
                          max_template_chars=max_template_chars))
    return result

def compile_modules(modules: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        str(item.get("content") or "")
        for item in modules
        if isinstance(item, dict) and item.get("enabled", True) and str(item.get("content") or "").strip()
    ).strip()

def base_template_modules(text: str, pipeline: str, *,
                          max_template_chars: int,
                          base_text_resolver: Callable[[str], str]) -> list[dict[str, Any]]:
    modules = text_to_modules(text, "base", locked=False,
                  max_template_chars=max_template_chars)
    for module in modules:
        module["locked"] = False
    return modules

def base_template_text(pipeline: str, *,
                       sources: dict[str, Any],
                       cache: dict[str, str]) -> str:
    """取代码基座文本；取不到时返回空串（调用方必须退化为"不改来源"，绝不臆造）。"""
    if pipeline in cache:
        return cache[pipeline]
    entry = sources.get(pipeline)
    if not entry:
        return ""
    attribute, candidates = entry
    module = next((sys.modules[name] for name in candidates if name in sys.modules), None)
    if module is None:
        for name in candidates:
            try:
                module = importlib.import_module(name)
                break
            except Exception:
                continue
    if module is None:
        return ""
    text = str(getattr(module, attribute, "") or "")
    if text:
        cache[pipeline] = text
    return text

def align_pipeline_sources(modules: list[dict[str, Any]], pipeline: str, *,
                           max_template_chars: int,
                           base_text_resolver: Callable[[str], str]) -> list[dict[str, Any]]:
    """把重建出来的模块与代码基座对齐（只按**逐字相同**判定，避免误认亲）。"""
    base_text = base_text_resolver(pipeline)
    if not base_text or not modules:
        return modules
    base_by_title = {m["title"]: m for m in text_to_modules(base_text, "base",
                                                max_template_chars=max_template_chars)}
    aligned: list[dict[str, Any]] = []
    for module in modules:
        candidate = base_by_title.get(str(module.get("title") or ""))
        if candidate and candidate.get("content") == module.get("content"):
            aligned.append({**candidate, "enabled": bool(module.get("enabled", True))})
        else:
            aligned.append(module)
    return aligned

def _inherit_module_tags(submitted: list[Any], stored: Any) -> list[Any]:
    """提交的模块缺 source/locked 时，从同 id（或同标题）的已存模块继承。

    审计 P1-2 同族：UI 的模块视图会把 base 模块的 locked 抹平；若再丢掉 source=base
    标签，`apply_module_layout` 就会认为「这条管线没有 base 模块」→ 把整段 base 前置，
    叠加已含同样内容的模块 = 提示词翻倍。显式传入的值永远优先。
    """
    if not isinstance(stored, list) or not stored:
        return submitted
    by_id = {str(m.get("id")): m for m in stored if isinstance(m, dict) and m.get("id")}
    by_title = {str(m.get("title")): m for m in stored if isinstance(m, dict) and m.get("title")}
    out: list[Any] = []
    for item in submitted:
        if not isinstance(item, dict):
            out.append(item)
            continue
        ref = by_id.get(str(item.get("id"))) or by_title.get(str(item.get("title")))
        if isinstance(ref, dict):
            merged = dict(item)
            for field in ("source", "locked"):
                if field not in item and field in ref:
                    merged[field] = ref[field]
            out.append(merged)
        else:
            out.append(item)
    return out

def pipeline_view(base: str, profile: dict[str, Any], pipeline: str, *,
                  max_template_chars: int,
                  base_text_resolver: Callable[[str], str]) -> list[dict[str, Any]]:
    base_modules = base_template_modules(base, pipeline,
                                        max_template_chars=max_template_chars,
                                        base_text_resolver=base_text_resolver)
    current = ((profile.get("pipelines") or {}).get(pipeline) if isinstance(profile.get("pipelines"), dict) else [])
    if isinstance(current, list) and current:
        view = copy.deepcopy(current)
        for m in view:
            m["locked"] = False
        return view
    return base_modules + (text_to_modules(str(profile.get(pipeline) or ""), "custom",
                               max_template_chars=max_template_chars) if profile.get(pipeline) else [])

def append_layer(base: str, layer: str, label: str) -> str:
    layer = (layer or "").strip()
    return base if not layer else f"{base.rstrip()}\n\n======================= 【{label}】 =======================\n{layer}"
