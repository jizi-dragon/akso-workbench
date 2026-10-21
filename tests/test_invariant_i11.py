r"""不变量 **I11**：同步阻塞调用一律 `asyncio.to_thread`（`CORE-ARCHITECTURE.md` §5）。

## ★ 为什么这条不变量此前**一条机器守卫都没有**

2026-09-24 的完成度核对里，把 §5 的 18 条不变量逐条映射到「判据」与「反证」，
结果 **6 条没有反证**（I4 / I5 / I7 / I9 / I11 / I14）。其中五条**有判据**，
只有 **I11 连判据都没有** —— 而 §5 给它的后果写的是：

> **整个服务卡住**（不是报错）

★ 那正是本仓最防的一类失败：**不报错、别的判据也不红**，只表现为"服务没反应"，
而排查会从"是不是模型慢"开始。

## 两条规则（都是**精确**的）

### 规则 A：`async def` 里不得**直接**调用已知阻塞的名字

`time.sleep` / `requests.*` / `subprocess.*` / `sqlite3.connect` / `urlopen`
＋ ★ **本项目自己的**同步入口（§5 逐字点名了 `egmp.client` / `proc` / `sandbox.run`）。

### ★★ 规则 S（结构）：`async def` 里的嵌套同步 `def` 不得被**直接调用**

★★ **这一条是实测逼出来的**：规则 A 的两版**都没抓到**那个反证 ——
我把 `await asyncio.to_thread(run)` 改成 `run()`，判据照样绿，
因为 `run` 体内调的是**项目自己的**同步函数，而那个名字**列不全**
（列全就等于"所有函数都可能阻塞"，判据会变成噪音源）。

⇒ 换成**结构性**判据，它才是这条纪律的实质：

> 一个在 `async def` 里定义的**嵌套同步函数**，存在的理由**就是被挪走**
> （`await asyncio.to_thread(call)`）。**直接调用它**就等于在事件循环上跑同步代码。
> 而"作为实参传给别的调用"（含 `to_thread`，也含当回调）**不算**直接调用。

实测：全仓今天 **0 处**命中 ⇒ **没有误报面**；而那个反证**当场变红**。

## 为什么不建全图调用图

「某个**模块级**同步函数最终会不会被 async 路径调到」要建全图调用图，
而本仓大量使用"把函数当值传"的写法，全图分析会给出大量假阳性 ——
而一个整天误报的判据会把真缺陷埋掉（`tools/check_doc_links.py` 第一版报 789 处、
真的只有 3 处，PITFALLS #108 / #109）。⇒ **宁可窄而准**。
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKBENCH = ROOT / "workbench"

#: 规则 A 的名单。**只能往准里加** —— 加错了会让判据变成噪音源。
BLOCKING_EXACT = {
    "time.sleep",
    "requests.get", "requests.post", "requests.put", "requests.delete", "requests.request",
    "subprocess.run", "subprocess.check_output", "subprocess.call", "subprocess.Popen",
    "sqlite3.connect",
    "urlopen",
    "sandbox.run",
}

#: 前缀匹配（本项目自己的同步模块）
BLOCKING_PREFIXES = ("egmp_client.", "proc.")

#: **豁免**：键是 `相对路径:函数名`，值是理由（必须写清"为什么不需要挪"）。
EXEMPT: dict[str, str] = {}


def _dotted(node: ast.AST) -> str:
    """`a.b.c(...)` → `"a.b.c"`；取不到名字就返回空串。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _walks(node: ast.AST):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walks(child)


def _is_blocking(name: str) -> bool:
    if not name:
        return False
    return name in BLOCKING_EXACT or any(name.startswith(p) for p in BLOCKING_PREFIXES)


def _forwarders(tree: ast.Module) -> dict[str, set[str]]:
    """把**某个参数**转发给 `to_thread` 的函数：`{函数名: {参数名, ...}}`。

    ★ 形态 3 的来源（实测）：`insight.py` 的

        async def _in_thread(fn, *args):
            return await asyncio.to_thread(fn, *args)

    ⇒ 于是 `await _in_thread(call)` 的**第一个实参**是被挪走的。
    """
    out: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        forwarded: set[str] = set()
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            if not (name.endswith("to_thread") or name.endswith("run_in_executor")):
                continue
            forwarded |= {a.id for a in node.args
                          if isinstance(a, ast.Name) and a.id in params}
        if forwarded:
            out[fn.name] = forwarded
    return out


def _resolve(tree: ast.Module, name_node: ast.Name) -> list[ast.AST]:
    """把一个**名字**解析到它指的函数定义（形态 2 / 3 都要）。"""
    return [c for c in ast.walk(tree)
            if isinstance(c, ast.FunctionDef | ast.Lambda)
            and getattr(c, "name", "") == name_node.id]


def _offloaded_roots(tree: ast.Module) -> list[ast.AST]:
    """所有「已经被挪到线程上」的**代码块根**（三种形态）。

    | 形态 | 例子 |
    |---|---|
    | 1 直接当实参 | `await asyncio.to_thread(lambda: http.get(code))` |
    | 2 按名字传 | `def call(): ...` ＋ `await asyncio.to_thread(call)` |
    | 3 转发包装 | `await _in_thread(call)`，而 `_in_thread` 把 `fn` 给了 `to_thread` |

    ★ 传的是**名字**时必须**解析到那个函数定义** —— 否则"被挪走"的只是那三个字母，
    而它体内的调用**不在**里面（形态 3 第一版就是这么漏的，实测假阳性）。
    """
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if not (name.endswith("to_thread") or name.endswith("run_in_executor")):
            continue
        for arg in node.args:
            roots.append(arg)                                   # 形态 1
            if isinstance(arg, ast.Name):                       # 形态 2
                roots += _resolve(tree, arg)

    # 形态 3：转发包装
    for wrapper, positions in _forwarders(tree).items():
        order = next(([a.arg for a in fn.args.args]
                      for fn in ast.walk(tree)
                      if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
                      and fn.name == wrapper), [])
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _dotted(node.func) != wrapper:
                continue
            for idx, arg in enumerate(node.args):
                if idx >= len(order) or order[idx] not in positions:
                    continue
                roots.append(arg)
                if isinstance(arg, ast.Name):
                    roots += _resolve(tree, arg)
    return roots


def _scan(tree: ast.Module) -> list[tuple[str, str, str, int]]:
    """扫一棵树 → `[(规则, async 函数名, 被调名, 行号)]`。主判据与自证共用。

    ## 2026-10-19：把 `_inside_to_thread` 的 O(n²) 改成一次预计算

    原实现每遇到一个 `ast.Call` 就重新算一遍 `_offloaded_roots(tree)` 并逐个
    `_walks` 比对 —— 全仓 90+ 文件时这条测试要 **9.7 秒**，而它只是 AST 扫描。
    现在每个 `to_thread` 代码块只展开一次，节点的 `id()` 收进集合，判断降为 O(1)。
    语义不变（节点身份比对），耗时降到亚秒级。
    """
    inside: set[int] = set()
    for root in _offloaded_roots(tree):
        inside.update(id(inner) for inner in _walks(root))

    out: list[tuple[str, str, str, int]] = []
    for outer in [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]:
        nested = {n.name for n in ast.walk(outer) if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(outer):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            if id(node) in inside:
                continue
            if _is_blocking(name):
                out.append(("A", outer.name, name, node.lineno))
            elif name in nested:
                out.append(("S", outer.name, name, node.lineno))
    return out


def _violations() -> list[str]:
    out: list[str] = []
    for path in sorted(WORKBENCH.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover —— 语法坏了会有别的判据先红
            continue
        rel = path.relative_to(ROOT).as_posix()
        for kind, outer, callee, lineno in _scan(tree):
            if f"{rel}:{outer}" in EXEMPT:
                continue
            why = ("已知会阻塞" if kind == "A"
                   else "嵌套同步函数（它存在的理由就是被 to_thread 挪走）")
            out.append(f"{rel}:{lineno} 在 async def {outer}() 里直接调了 {callee}() —— {why}")
    return out


def test_no_blocking_call_runs_directly_on_the_event_loop() -> None:
    """★★ I11：`async def` 里不得直接跑同步代码（规则 A ＋ 规则 S）。

    ★ 违反它的表现是**整个服务卡住**而不是报错 —— 所以这条判据必须给出
    **可执行的定位**（文件:行 ＋ 哪个 async 函数 ＋ 调了什么），而不是一句"有人违规了"。
    """
    bad = _violations()
    assert not bad, (
        "async 路径上出现了直接阻塞调用（I11）：\n  "
        + "\n  ".join(bad)
        + "\n  ⇒ 包一层 `await asyncio.to_thread(...)`；"
          "若确实不该包，在 EXEMPT 里**写明理由**加一条。"
    )


def test_the_scanner_can_actually_see_async_functions() -> None:
    """★ 前提断言：扫描器**真的读到了** async 函数。

    ★ 没有它的话，"一个都没找到"会让上面那条**通过** —— 那是"守护对象没扫到"，
    不是"都合规"。
    """
    count = 0
    for path in WORKBENCH.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        count += sum(1 for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef))
    assert count >= 20, f"只扫到 {count} 个 async 函数 —— 扫描范围是不是坏了？"


def test_the_blocking_list_is_not_empty() -> None:
    """★ 前提断言：名单非空 —— 空名单会让规则 A **恒真**。"""
    assert BLOCKING_EXACT, "阻塞调用名单空了 —— 规则 A 就不再验任何东西"


def test_both_rules_fire_on_planted_violations() -> None:
    """★★★ **判据自身的反证**：现场种违规，两条规则都要抓到、合规的不能抓。

    ★ 这是这条判据能不能被信任的**唯一**理由：主判据今天全绿，
    而"全绿"与"扫描器坏了"在输出上**长得一模一样**。

    ★ `good()` 那一段同样承重：一个"看见嵌套同步函数就报"的实现会把它误报，
    而那种实现会让判据变成噪音源。
    """
    src = (
        "import asyncio\n"
        "import time\n"
        "\n"
        "async def bad_a():\n"
        "    time.sleep(1)\n"
        "\n"
        "async def bad_s():\n"
        "    def helper():\n"
        "        return 1\n"
        "    return helper()\n"
        "\n"
        "async def good():\n"
        "    def helper():\n"
        "        return 1\n"
        "    return await asyncio.to_thread(helper)\n"
    )
    got = sorted(f"{kind}:{outer}" for kind, outer, _n, _l in _scan(ast.parse(src)))
    assert got == ["A:bad_a", "S:bad_s"], (
        f"抓错了：{got}（应当只抓 bad_a 与 bad_s，good 不该被抓）"
    )
