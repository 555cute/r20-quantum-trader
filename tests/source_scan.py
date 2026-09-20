"""源码文本锚点的定位工具（结构优化阶段 2 引入）。

## 为什么需要它

本仓有一批"读源码文本做断言"的审计测试（`read_text` + `assertIn` / `count`）。
它们钉的其实是**语义**：「某段强制逻辑确实存在于运行时源码里」——例如
「两种共识模式都必须给 CIO 预留预算」「比例切分必须带 90s 地板」。

但这类测试常把语义钉死在**某一个文件**上。一旦阶段 2 的模块拆分把这段逻辑
搬到同包的其他文件（如 `council_manager.py` → `council/debate.py`），测试就会
失败 —— 而**搬家本身并不是回归**。此时正确的处理是：

  · 断言**强度不变**（同样的 needle、同样的计数下限）；
  · **定位方式升级**：从"某个文件"改成"该领域的运行时源码集合"。

这样代码再搬家也不会误报，而且覆盖面比原来更广（原来只看一个文件）。

## 防空的必要性

"在更大范围里搜"会带来一个新风险：如果路径算错、读到空内容，
`assertIn` 会失败（好事，响亮），但 `assertNotIn` 与 `count(...) == 0` 会**假通过**。
因此调用方必须同时断言"确实读到了目标领域"，例如断言合并文本里含某个必然存在的
符号、且长度超过下限。本模块提供 `assert_area_looks_real` 供直接调用。
"""
from __future__ import annotations

from pathlib import Path


def source_area(module_file: str | Path, *, pkg_name: str | None = None) -> dict[Path, str]:
    """返回「该模块文件 + 同目录下的同名包目录」的全部 .py 源码。

    例：`source_area(r20_backend/council_manager.py)`
        → {council_manager.py, council/__init__.py, council/debate.py, council/policy.py}
        `source_area("scripts/ai_factor_trader.py", pkg_name="trader")`
        → {ai_factor_trader.py, trader/__init__.py, trader/signals.py, trader/factors.py, …}

    约定：包目录名默认 = 模块文件名去掉 `_manager` 后缀与 `.py` 后缀
    （`council_manager.py` → `council/`）。当门面与子包**不同名**时（如
    `ai_factor_trader.py` 的抽取子包叫 `trader/`），用 `pkg_name` 显式指定 ——
    这样后续再往该子包搬文件时，断言面**自动覆盖**，不用回来改测试。
    找不到包目录时只返回模块文件本身，由调用方的防空断言决定是否可接受。
    """
    module = Path(module_file)
    if not module.is_absolute():
        module = Path(__file__).resolve().parents[1] / module
    sources = {module: module.read_text(encoding="utf-8")}
    stem = module.stem
    if pkg_name is None:
        pkg_name = stem[:-len("_manager")] if stem.endswith("_manager") else stem
    pkg_dir = module.parent / pkg_name
    if pkg_dir.is_dir():
        for extra in sorted(pkg_dir.glob("*.py")):
            sources[extra] = extra.read_text(encoding="utf-8")
    return sources


def combined(*module_files: str | Path, pkg_name: str | None = None) -> str:
    """把若干领域源码拼成一段文本，供 assertIn / assertNotIn / str.count 使用。

    `pkg_name` 会透传给每一个 `source_area()`（见其文档：门面与子包不同名时用）。
    """
    parts: list[str] = []
    for module_file in module_files:
        parts.extend(source_area(module_file, pkg_name=pkg_name).values())
    return "\n".join(parts)


def assert_area_looks_real(testcase, text: str, *, must_contain: str,
                           min_chars: int = 20000) -> None:
    """防空：确认合并文本确实是目标领域，避免 assertNotIn / count==0 假通过。"""
    testcase.assertIn(must_contain, text, f"未读到目标领域源码（缺少 {must_contain}）")
    testcase.assertGreater(len(text), min_chars, "目标领域源码过短，疑似定位错误")


def domain_trees(module_file: str | Path, *, pkg_name: str | None = None) -> list:
    """把「门面 + 同名字包」逐个 `ast.parse`，返回 AST 列表（按路径排序，门面在前）。

    与 `combined()` 的关系：那个拼**文本**（给 assertIn / count 用），这个保留
    **每文件独立的 AST**（给"按名字取函数节点再 exec"这类用法用）。

    为什么需要它：仓里有 3 处测试用 `ast.parse(单文件)` 再按函数名取节点
    （`test_prompt_rendering_isolated`、`test_self_evolution_safety`、
    `test_dashboard_payload_seam` 的同类写法）。一旦目标函数搬进子包，
    `next(...)` 会 `StopIteration`。改成扫整个领域后，**函数搬到哪都找得到**。
    """
    import ast
    return [ast.parse(text) for text in source_area(module_file, pkg_name=pkg_name).values()]


def count_name_references(module_file: str | Path, name: str, *,
                          pkg_name: str | None = None) -> dict:
    """在「门面 + 同名字包」领域里统计标识符 `name` 的**定义数**与**引用数**。

    为什么需要它（而不是 `text.count("name(")`）：**文本计数会被文档污染**。
    实测 `order_margin_gate` 在领域文本里出现 7 次，但实际只有 1 处定义 +
    2 处调用 —— 其余 4 次全在 `gates.py` / `__init__.py` 的注释与 docstring 里
    （它们解释的正是这条锚点本身）。用文本数字当断言，等于把"文档写得多细"
    变成了测试条件。

    改为走 AST：注释与 docstring **天然不计入**，留下的就是真正的代码结构。

    返回 `{"defs": n, "refs": m}`：
    - `defs`：顶层 `def name`（门面壳与子包实现会各算一次，这是有意的）；
    - `refs`：所有 `ast.Name` 读取（`Load` 语境）的出现次数，调用点即在此列。
    """
    import ast
    defs = 0
    refs = 0
    for text in source_area(module_file, pkg_name=pkg_name).values():
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                defs += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
                refs += 1
    return {"defs": defs, "refs": refs}


def find_function_node(module_file: str | Path, name: str, *, pkg_name: str | None = None,
                       node_only: bool = False):
    """在「门面 + 同名字包」全领域里按名字找顶层函数节点。

    找不到直接抛错。返回 `(node, 该节点所在文件路径)`，便于报错时指明位置。

    ## 关于「同名两份」

    本仓的抽取手法是**门面保留同名薄壳**（`return _xxx_impl(...)`）+ 子包放实现，
    所以领域内同名函数**合理地出现两次**。此时：

    - 默认（`node_only=False`）：返回**实现体**那一份 —— 即二者中源码更长的那个。
      这是"要执行的真实逻辑"，也正是这类测试想要的。
    - 域内同名多份且**源码完全相同**：判为搬家留下的拷贝（危险），抛错。
    """
    import ast
    hits: list[tuple] = []
    for path, text in source_area(module_file, pkg_name=pkg_name).items():
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                hits.append((node, path, ast.get_source_segment(text, node) or ""))
    if not hits:
        raise LookupError(f"领域内找不到顶层函数 {name!r}（{module_file}）")
    if len(hits) == 1:
        return hits[0][0], hits[0][1]

    bodies = [body for _, _, body in hits]
    if len(set(bodies)) < len(bodies):
        raise LookupError(
            f"领域内 {name!r} 有 {len(bodies)} 份**完全相同**的实现，疑似搬家留下的拷贝："
            + ", ".join(str(p) for _, p, _ in hits))
    # 门面壳 vs 子包实现：取实现体（源码更长者）
    node, path, _ = max(hits, key=lambda h: len(h[2]))
    return node, path


def count_keyword_argument(module_file, function_name, keyword, *,
                           value_must_contain=None, pkg_name=None):
    """数 `function_name(...)` 调用里出现 `keyword=` 的次数（AST，不受换行影响）。

    ## 为什么需要它

    既有的门面锚点用 `facade.count('"max_margin_usdt": equity_margin_cap(usdt_available)')`
    这类**整行字面量**做断言。它脆得离谱：抽取时我只是把该行拆成两行
    （`max_margin_usdt=` 与 `equity_margin_cap(...)` 分行），计数就从 2 变 0 ——
    **代码行为完全没变，断言却翻了红**。这类"排版一变就失灵"的锚点会逼着后人
    别去格式化代码，与重构目标直接冲突。

    改成 AST 计数后，换行、缩进、尾随注释都不影响；而"漏一条路径""多加一处"
    仍然抓得住。`value_must_contain` 用来进一步钉住实参内容（例如必须是
    `equity_margin_cap(usdt_available)` 这个调用，而不是别的值）。

    返回该关键字在全部匹配调用中出现的次数。
    """
    import ast
    from pathlib import Path

    files = [Path(module_file)]
    if pkg_name:
        pkg_dir = Path(module_file).parent / pkg_name
        if pkg_dir.is_dir():
            files.extend(sorted(pkg_dir.rglob("*.py")))

    hits = 0
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == function_name):
                continue
            for kw in node.keywords:
                if kw.arg != keyword:
                    continue
                if value_must_contain is not None:
                    rendered = ast.unparse(kw.value)
                    if value_must_contain not in rendered:
                        continue
                hits += 1
    return hits


def names_defined_at_call(fn_node, call_node, *, module_tree):
    """返回在 `call_node` 处**沿唯一到达路径**已定义的名字集合。

    ## 用途

    抽取大函数里的代码块时，调用点会引用一批局部量。若某个局部量没在**这一支**
    里定义，实盘走到该分支就 `NameError` —— 而这类缺陷**常规测试抓不到**：
    只有当那条分支真的被执行时才暴露（本会话在 pyramiding 抽取时实际踩过，
    做空分支丢了 3 行定义，当时全量 1466 个测试仍是全绿）。

    ## 判据（反复迭代才写对，故集中在此统一复用）

    1. **只沿到达该调用的唯一路径**收集定义：从函数体逐层下沉，每层只看
       「该层内、位于通往调用那条语句之前」的兄弟语句。
       用 `ast.walk` 遍历祖先块是错的 —— 祖先块若是
       `if action == "SELL_SHORT":`，`walk` 会**下钻进开多分支**，
       于是开多分支的同名局部量把开空的缺口盖住（本会话连漏两次）。
    2. **按词法作用域逐层收集**：`lambda` 参数、推导式（comprehension）目标变量
       只在各自的嵌套作用域内可见。若把它们混进外层，会把 `sum(v for v in ...)`
       里的 `v` 误判成"外层已定义"；反之若完全不管，又会误报未定义。
    3. 模块级名字（import / 赋值 / `def`）也算。注意两种易漏写法：
       - **带注解赋值** `_BROKEN_VENUES: set = set()` 是 `ast.AnnAssign`，
         不是 `ast.Assign`；
       - 赋值可能落在模块级 `try:` 体内。
    4. 内建名（`print` / `float` / `len` 等）不能算未定义。

    返回的集合可直接与调用点实参里出现的名字求差。
    """
    import ast
    import builtins

    def bindings_in(stmt):
        """**该语句自身在当前位置**产生的绑定（不下钻、不前瞻）。

        关键：只认"执行到这一句时已经生效"的绑定点 ——
        `x = ...`（含注解/自增）的目标、`for x in ...` 的目标、
        `with ... as x`、`except ... as x`、推导式/`lambda` 的自有变量。

        **不**递归进语句内部找所有 `Store`：那样会让"调用之后才赋值"和
        "兄弟分支里赋值"被误判成已定义（`ast.walk` 不看顺序、不看分支）。
        本会话三次踩到这个坑，故实现按绑定点逐个取出。
        """
        out = set()

        def target_of(t):
            for nm in ast.walk(t):
                if isinstance(nm, ast.Name):
                    out.add(nm.id)

        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                target_of(t)
        elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
            target_of(stmt.target)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            target_of(stmt.target)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                if item.optional_vars is not None:
                    target_of(item.optional_vars)
        elif isinstance(stmt, ast.ExceptHandler):
            if stmt.name:
                out.add(stmt.name)
        elif isinstance(stmt, (ast.ListComp, ast.SetComp, ast.DictComp,
                              ast.GeneratorExp)):
            for gen in stmt.generators:
                target_of(gen.target)
        elif isinstance(stmt, ast.Lambda):
            for a in (stmt.args.args + stmt.args.kwonlyargs + stmt.args.posonlyargs):
                out.add(a.arg)
            if stmt.args.vararg:
                out.add(stmt.args.vararg.arg)
            if stmt.args.kwarg:
                out.add(stmt.args.kwarg.arg)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.ClassDef)):
            out.add(stmt.name)
        elif isinstance(stmt, ast.Try):
            # try/except/finally 里**必有一条执行**（无异常走 body/no-exception，
            # 有异常走某个 handler），故其内层绑定在 try 之后一定存在。
            # 这也覆盖了模块级 `try: X = ... except: X = ...` 这种常见写法。
            # 注意 `if` 不能这样处理 —— 分支可能都不走。
            for sub in (list(stmt.body) + list(stmt.orelse) + list(stmt.finalbody)
                        + list(stmt.handlers)):
                out |= bindings_in(sub)
        return out

    def seq_and_branches(owner):
        """把 `owner` 的子语句拆成 `(必过序列, [分支列表, ...])`。

        - **必过序列**：真正按顺序执行的语句（函数体、`for` 体、`try` 的
          `body`+`finally` 等）。
        - **分支列表**：`if/elif/else` 这类**互斥**分支，每组里只会走一条。
          路径若走的是第 2 组，就绝不能被第 1 组里的绑定"喂饱"。

        `elif` 是挂在 `orelse` 里的嵌套 `If`，所以 `if` 的分支组是
        `[body, orelse]`，而不是"按源码顺序的平铺列表" —— 这一点没搞清
        就会写出"从 body 拿了绑定、却从 orelse 往下走"的错（本轮实际踩到）。
        """
        seq, branch_groups = [], []
        if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seq = list(owner.body)
        elif isinstance(owner, ast.If):
            branch_groups = [list(owner.body), list(owner.orelse)]
        elif isinstance(owner, (ast.For, ast.AsyncFor, ast.While)):
            seq = list(owner.body)
            if owner.orelse:
                branch_groups.append(list(owner.orelse))
        elif isinstance(owner, ast.Try):
            # body 与 finally 必过；handlers 互斥
            seq = list(owner.body) + list(owner.finalbody)
            if owner.handlers:
                branch_groups.append(list(owner.handlers))
        elif isinstance(owner, (ast.With, ast.AsyncWith)):
            seq = list(owner.body)
        return seq, branch_groups

    def collect_along(target):
        """收集到达 `target` 的**唯一路径**上、先于它的绑定。

        逐层下沉：每层沿「必过序列」收集路径之前的兄弟语句；若路径进入某个
        **互斥分支组**，只在该组内继续，绝不回头看别的分支组。
        """
        par_of = {}
        for n in ast.walk(fn_node):
            for ch in ast.iter_child_nodes(n):
                par_of[ch] = n

        def walk(owner, node, acc):
            """把 `node` 在 `owner` 内的前置绑定累加进 `acc`。"""
            seq, groups = seq_and_branches(owner)
            for stmt in seq:
                if stmt is node:
                    return True
                acc |= bindings_in(stmt)
            for group in groups:
                if node in group:
                    for stmt in group:
                        if stmt is node:
                            return True
                        acc |= bindings_in(stmt)
                    return True
                # 路径不在这组，整组忽略（互斥）
            return False

        out = set()
        cur = target
        while True:
            par = par_of.get(cur)
            if par is None:
                break
            # 找到 cur 所属的父块（跳过 Return/Call 之类的中转节点）
            walk(par, cur, out)
            # 本层的绑定点（块开始即生效）
            if isinstance(par, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith,
                                ast.ExceptHandler)):
                out |= bindings_in(par)
            cur = par

        # 嵌套块里的绑定也必须收进来：`all_factors` 是绑在
        # `with ThreadPoolExecutor(...):` 体里的，只在最外层看直接子语句
        # 会把它漏掉（本轮实际踩到）。这里在不越过路径的前提下做一次补收：
        # 对函数体内路径之前的语句，递归取其"必过绑定"（Skip 互斥 if 分支）。
        def always_bound(stmt, seen=None):
            """该语句在自身执行后**必然**产生的绑定（不下钻互斥分支）。"""
            got = bindings_in(stmt)
            if isinstance(stmt, (ast.Try, ast.With, ast.AsyncWith,
                                 ast.For, ast.AsyncFor, ast.While)):
                for sub in (list(getattr(stmt, "body", []))
                            + list(getattr(stmt, "orelse", []))
                            + list(getattr(stmt, "finalbody", []))):
                    got |= always_bound(sub)
                for h in getattr(stmt, "handlers", []):
                    got |= always_bound(h)
            return got

        cur = target
        while True:
            par = par_of.get(cur)
            if par is None:
                break
            seq, groups = seq_and_branches(par)
            on_path = None
            for group in ([seq] + groups):
                if cur in group:
                    on_path = group
                    break
            if on_path is not None:
                for stmt in on_path:
                    if stmt is cur:
                        break
                    out |= always_bound(stmt)
            cur = par

        return out

    def scope_names(scope_owner):
        """该作用域内由 lambda 参数 / 推导式目标引入的名字。"""
        out = set()
        for n in ast.walk(scope_owner):
            if isinstance(n, ast.Lambda):
                out |= {a.arg for a in n.args.args + n.args.kwonlyargs}
                if n.args.vararg:
                    out.add(n.args.vararg.arg)
                if n.args.kwarg:
                    out.add(n.args.kwarg.arg)
            elif isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp,
                                ast.GeneratorExp)):
                for gen in n.generators:
                    for nm in ast.walk(gen.target):
                        if isinstance(nm, ast.Name):
                            out.add(nm.id)
        return out

    defined = {a.arg for a in fn_node.args.args + fn_node.args.kwonlyargs}
    if fn_node.args.vararg:
        defined.add(fn_node.args.vararg.arg)
    if fn_node.args.kwarg:
        defined.add(fn_node.args.kwarg.arg)
    defined |= set(dir(builtins))
    defined |= scope_names(fn_node)

    for node in module_tree.body:
        defined |= bindings_in(node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for al in node.names:
                defined.add(al.asname or al.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
    defined |= scope_names(module_tree)

    parent = {}
    for n in ast.walk(fn_node):
        for child in ast.iter_child_nodes(n):
            parent[child] = n

    defined |= collect_along(call_node)

    return defined


def missing_names_at_helper_calls(module_file, function_name, helper_names, *,
                                  pkg_name=None):
    """检查 `function_name` 里每处对 `helper_names` 的调用，实参名是否都已定义。

    返回 `{helper: [{"line": L, "missing": [名字...]}, ...]}`；空 dict 表示全部安全。

    ## 为什么要有这个函数

    这是抽取大函数时的**主要守卫**：搬走一段代码后，调用点会引用一批局部量；
    若某个局部量没在**这一支**里定义，实盘走到该分支就 `NameError`，
    而常规测试只在分支真正执行时才暴露（本会话实际踩过：做空分支丢 3 行定义，
    当时全量 1466 个测试仍全绿）。

    之前每个抽取测试文件各自复制了一段"收集实参 → 求差"的循环，
    结果**四处都有同一个取值 bug**：从 `ast.walk` 取调用时拿到的是"第一个匹配"，
    而 `ast.walk` 是宽度优先、顺序不稳定，于是同一份判据在不同测试里
    给出不同结论。集中到一处后只需修一次。
    """
    import ast
    from pathlib import Path

    files = [Path(module_file)]
    if pkg_name:
        pkg_dir = Path(module_file).parent / pkg_name
        if pkg_dir.is_dir():
            files.extend(sorted(pkg_dir.rglob("*.py")))

    result = {}
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        func = next((n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == function_name), None)
        if func is None:
            continue

        # 按行号排序，保证结论与遍历顺序无关
        calls = sorted(
            (n for n in ast.walk(func)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in helper_names),
            key=lambda n: n.lineno)

        for call in calls:
            passed = set()
            for arg in call.args:
                for nm in ast.walk(arg):
                    if isinstance(nm, ast.Name):
                        passed.add(nm.id)
            for kw in call.keywords:
                for nm in ast.walk(kw.value):
                    if isinstance(nm, ast.Name):
                        passed.add(nm.id)
            available = names_defined_at_call(func, call, module_tree=tree)
            missing = sorted(n for n in passed if n not in available)
            if missing:
                result.setdefault(call.func.id, []).append(
                    {"line": call.lineno, "missing": missing, "file": str(path)})
    return result


def router_domain_source(router_name: str = "strategy", *, root=None) -> str:
    """返回某路由域的**全部源码文本**：`routers/<name>.py` 或包 `routers/<name>/*.py`。

    ## 为什么需要它

    路由域可以从单模块**拆成包**（第九十六刀：`routers/strategy.py` →
    `routers/strategy/{council,interceptors,policy,prompts}.py`）。按旧路径
    `read_text()` 的判据会在拆分后直接抛 `FileNotFoundError` —— 那是"锚点绑死了
    文件位置"，不是"行为回归"。凡"某段代码必须在路由域里"这类判据，一律用本函数
    取域全文（与 `combined()` 同思路：判据绑**域**，不绑**文件**）。
    """
    from pathlib import Path as _P
    base = _P(root) if root else _P(__file__).resolve().parents[1]
    d = base / "r20_backend" / "routers"
    single = d / f"{router_name}.py"
    if single.exists():
        return single.read_text(encoding="utf-8")
    pkg = d / router_name
    if pkg.is_dir():
        return "\n".join(sorted(f.read_text(encoding="utf-8")
                                for f in pkg.glob("*.py")))
    raise AssertionError(f"路由域不存在：{single} 或 {pkg}/")

def load_subscripts(module_file, function_name, var_name, *, pkg_name=None) -> set:
    """收集函数体内 `var_name[<字符串常量>]` 的**读取**下标键集合。

    只取 `ast.Load`（排除函数自己写入的 `f["ai_thought"] = ...` 之类 Store）。

    ## 为什么必须补一次文本扫描（本仓实测踩过）

    本仓 Python 版本是 **3.11**：**f-string 内的表达式不是 AST 节点**（3.12 起才是）。
    审计里最典型的三个渲染点（跨所保护报告、预演输出）恰好把下标写在 f-string 里，
    只扫 AST 会**一个都抓不到** ⇒ 后续"需要的键 ⊆ 提供的键"断言**空集恒过**。
    故这里 AST + 文本（正则）双扫，调用方仍应自带"确实抓到了预期键"的自检。
    """
    import ast
    import re

    node, path = find_function_node(module_file, function_name, pkg_name=pkg_name)
    text = Path(path).read_text(encoding="utf-8")
    keys = set()
    for n in ast.walk(node):
        if (isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load)
                and isinstance(n.value, ast.Name) and n.value.id == var_name
                and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)):
            keys.add(n.slice.value)
    written = {n.slice.value for n in ast.walk(node)
               if isinstance(n, ast.Subscript) and isinstance(n.ctx, (ast.Store, ast.Del))
               and isinstance(n.value, ast.Name) and n.value.id == var_name
               and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)}
    seg = ast.get_source_segment(text, node) or ""
    keys |= {m.group(1) for m in re.finditer(
        rf"\b{re.escape(var_name)}\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]", seg)}
    # ⚠️ 坑 2：文本扫描**分不清读/写**（`f["ai_reason"] = ...` 也会命中）⇒ 必须减掉
    # 函数**自己写入**的键，否则会把"消费方自产的键"误判成"生产方必须提供"
    # （本仓实测：入场循环自己写 `f["ai_thought"]` 等 5 个键，误红过一次）。
    return keys - written


def dict_literal_keys(module_file, function_name, *, pkg_name=None, var_name=None) -> set:
    """收集函数内字典**字面量**的字符串键（可限定 `var_name = {...}` 那次赋值）。"""
    import ast

    node, _path = find_function_node(module_file, function_name, pkg_name=pkg_name)
    keys = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Dict):
            continue
        if var_name is not None:
            owner = getattr(n, "_owner_name", None)
            if owner is not None and owner != var_name:
                continue
        keys |= {k.value for k in n.keys if isinstance(k, ast.Constant)
                 and isinstance(k.value, str)}
    return keys


def dict_assign_keys_by_name(module_file, function_name, *, pkg_name=None) -> dict:
    """收集 `name = {...}` 字面量赋值：`{name: [(lineno, {keys})]}`（按行号可查最近一次）。"""
    import ast

    node, _path = find_function_node(module_file, function_name, pkg_name=pkg_name)
    out: dict = {}
    for n in ast.walk(node):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)):
            keys = {k.value for k in n.value.keys if isinstance(k, ast.Constant)
                    and isinstance(k.value, str)}
            out.setdefault(n.targets[0].id, []).append((n.lineno, keys))
    for name in out:
        out[name].sort()
    return out

def subscript_assign_keys(module_file, function_name, var_name, key, *, pkg_name=None) -> set:
    """收集 `var_name["key"] = {...}` 这类**下标赋值**的字面量键。

    用途：本仓的"形状契约"里，有的形状不是 `name = {...}` 而是 `f["position"] = {...}`
    （因子快照里的仓位块就是如此）⇒ 需要专门取这一处的字面量键，不能用"函数内所有
    字典字面量"糊过去（那会把蜡烛/基础字典的键也算进来，判据失真）。
    """
    import ast

    node, _path = find_function_node(module_file, function_name, pkg_name=pkg_name)
    keys = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Assign) or not isinstance(n.value, ast.Dict):
            continue
        for tgt in n.targets:
            if (isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == var_name
                    and isinstance(tgt.slice, ast.Constant) and tgt.slice.value == key):
                keys |= {k.value for k in n.value.keys if isinstance(k, ast.Constant)
                         and isinstance(k.value, str)}
    return keys
