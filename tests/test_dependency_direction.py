r"""分层依赖方向的机器守卫（AGENTS.md 规则 7）。

## 为什么必须有这道门禁

规则 7 写的是「`plugins → plugin_api`，**永不反向**」，并注明"业务能力必须能整体拆除
而不碰宿主"。但这条契约**没有任何东西守着**，而它被违反过一次真实的事故：

> `plugins/insight.py` 的四个工具里有三个 `from ..api import routes_insight` ——
> 插件直接去调 **HTTP handler**（`read_artifact` 还返回 `FileResponse`，于是插件里
> 长出 `getattr(response, "body", b"")` 这种绕法）。**更实的后果是：路径守卫的真身
> 也跟着住在 `api/` 里** —— 插件是靠"复用页面那条路"才拿到它的，拆掉 API 层会把守卫
> 一起拆掉，而失败形态只是 `hub.failures` 里多一条（**静默**）。
>
> 2026-09-24 把逻辑下沉到 `services/egmp/insight/jobs.py` 修掉（见该模块文档串）。

## 为什么用 AST 而不是 grep（这条踩过）

本仓的契约判据第一次写成 `Select-String -Pattern 'from \.\.api'` 时**误报**了：
修好之后，`plugins/insight.py` 的**文档串**里还留着"此前本插件只能 `from ..api import
routes_insight`"这句解释历史的话。字符串搜索分不出"真的 import"与"提到这个 import"。

同一个教训本仓已经交过一次学费（`net_guard` 那条"不许盲跟重定向"的机检最初搜源码文本，
被三段解释"为什么不要这么做"的注释骗过）。**所以凡是"禁止某个写法"的门禁，一律走 AST。**

## 四道方向

| 方向 | 为什么禁止 |
|---|---|
| `plugins` → `api` | 规则 7。插件要能整体拆除而不碰宿主 |
| `services` → `api` | 服务层是逻辑，`api` 是传输形状；反向依赖会把守卫与逻辑重新搅在一起 |
| `harness` → `api` / `plugins` | 循环引擎不该知道 HTTP 与具体能力集（它只认 `ToolHub`） |
| `plugin_api` → `db` / `plugins` / `api` / `services` / `harness` | 它是**被注入**的：连审计与确认门都靠注入，所以它不能认识宿主的任何一部分 |
"""

from __future__ import annotations

import ast
import pathlib

WORKBENCH = pathlib.Path(__file__).resolve().parents[1] / "workbench"


def _module_name(path: pathlib.Path) -> str:
    return ".".join(path.relative_to(WORKBENCH.parent).with_suffix("").parts)


def _imports(path: pathlib.Path) -> list[tuple[str, int]]:
    """`(被导入的绝对模块名, 行号)`，**相对导入已解析成绝对名**。

    只走 AST：文档串与注释里提到 `from ..api import x` 不算 import。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module = _module_name(path)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = module.split(".")
                base = parts[: len(parts) - node.level]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            found.append((target, node.lineno))
    return found


def _violations(pattern: str, forbidden: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for path in sorted(WORKBENCH.glob(pattern)):
        if "__pycache__" in path.parts:
            continue
        for target, line in _imports(path):
            if any(target == f or target.startswith(f + ".") for f in forbidden):
                rel = path.relative_to(WORKBENCH.parent)
                out.append(f"{rel}:{line} -> {target}")
    return out


def test_plugins_never_import_the_api_layer() -> None:
    """规则 7 的正题：`plugins → plugin_api`，**永不反向**。"""
    bad = _violations("plugins/*.py", ("workbench.api",))
    assert not bad, (
        "插件 import 了 API 层：\n  " + "\n  ".join(bad) + "\n"
        "处置：把被调用的**逻辑**下沉到 `workbench/services/**`（不要 import handler，"
        "也不要复制一份实现——两份实现会让守卫只对一边生效，那正是本条要防的事）。"
    )


def test_services_never_import_the_api_layer() -> None:
    """服务层是逻辑，`api/` 是传输形状 —— 反向依赖会把两者重新搅在一起。"""
    bad = _violations("services/**/*.py", ("workbench.api",))
    assert not bad, "服务层 import 了 API 层：\n  " + "\n  ".join(bad)


def test_harness_does_not_know_http_or_concrete_tools() -> None:
    """循环引擎只认 `ToolHub` 接口，不该知道 HTTP 层，也不该认识具体插件。"""
    bad = _violations("harness/**/*.py", ("workbench.api", "workbench.plugins"))
    assert not bad, "harness import 了 api 或 plugins：\n  " + "\n  ".join(bad)


def test_plugin_api_depends_on_nothing_of_the_host() -> None:
    """`plugin_api` 是**被注入**的：它不能认识宿主的任何一部分（连 `db` 都不行）。

    它提供 `Risk` / `ToolSpec` / `ToolHub` / `ToolContext`，而审计、确认门、审批令牌
    一律由宿主注入 —— 这就是"插件可以整体拆除而不碰宿主"能成立的原因。
    """
    bad = _violations("plugin_api.py", (
        "workbench.db", "workbench.plugins", "workbench.api",
        "workbench.services", "workbench.harness", "workbench.config",
    ))
    assert not bad, "plugin_api 依赖了宿主：\n  " + "\n  ".join(bad)


def test_the_gate_itself_catches_a_real_violation() -> None:
    """★ 守卫自己的反证：`_violations` 必须真的报得出来。

    没有这一条，上面四条断言可能只是**因为解析不出东西**而恒绿 ——
    `_imports` 一旦被改坏（比如 AST 分支写错），四条门禁会一起变成摆设，
    而它们**看起来还是绿的**。这与 PITFALLS #87（圆环那条"看字符串里有没有
    `conic-gradient`"的断言修前是绿的）是同一类失败。
    """
    assert _imports(WORKBENCH / "plugins" / "insight.py"), "解析不出任何 import，门禁是摆设"

    # 造一个必然违规的模块，走的是与门禁同一条代码路径
    sample = WORKBENCH / "plugins" / "_gate_probe_tmp.py"
    sample.write_text("from ..api import routes_insight\n", encoding="utf-8")
    try:
        bad = _violations("plugins/*.py", ("workbench.api",))
        assert any("_gate_probe_tmp" in item for item in bad), (
            f"守卫漏掉了显而易见的违规（说明它已经失效）：{bad}"
        )
    finally:
        sample.unlink(missing_ok=True)
