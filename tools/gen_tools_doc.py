"""工具清单生成与治理门禁（docs/TOOLS.md 的唯一事实来源是运行期工具中枢）。

为什么需要它：2026-09-15 加自建 `web_search` 时，插件从 4 个变 5 个、工具从 19 个变 20 个，
而 CHANGELOG / PLUGIN-SDK / API / CODEBASE_OVERVIEW 四处**都还写着旧数字**——
用户的原话是「之前有一篇调研提到了创建了 19 个 tools，我完全不知情」。
**人记的数字必然会漂**，所以这里把"清单"和"数字"都变成可机检的：

1. `docs/TOOLS.md` 的清单块由本脚本从 `plugin_api.get_hub()` 生成（**不许手改**）；
2. 活跃文档里出现的"N 个工具 / N 个插件"一律与实际比对，对不上即红；
3. `REQUIRED_TOOLS` 必须都能在装配后的中枢里找到（缺 = 某个插件没装起来）。

用法：
    python tools/gen_tools_doc.py            # 检查（默认；对不上 exit 1）
    python tools/gen_tools_doc.py --write    # 用运行期中枢重写 docs/TOOLS.md 的清单块

配套：`tools/verify_packaging.py` 的 P7 管"插件进没进打包"，本脚本管"文档与运行期是否一致"。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workbench.api.routes_agent import REQUIRED_TOOLS  # noqa: E402
from workbench.plugin_api import ToolHub, get_hub, verify_required_tools  # noqa: E402

DOC = ROOT / "docs" / "TOOLS.md"
BEGIN = "<!-- TOOLS-TABLE:BEGIN —— 由 tools/gen_tools_doc.py 生成，勿手改；改插件后跑 --write -->"
END = "<!-- TOOLS-TABLE:END -->"

# 参与"N 个工具/插件"数字核对的活跃文档（**不含 CHANGELOG**：历史条目里的数字是当时的事实）
CLAIM_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "TOOLS.md",
    ROOT / "docs" / "PLUGIN-SDK.md",
    ROOT / "docs" / "API.md",
    ROOT / "docs" / "CODEBASE_OVERVIEW.md",
)

RISK_ORDER = {"read": 0, "write": 1, "dangerous": 2}
RISK_LABEL = {"read": "read", "write": "**write**", "dangerous": "**dangerous**"}


def _core(catalog: dict, key: str) -> list[dict]:
    """catalog 里 `origin=core` 的那部分。

    存在理由只有一个：**用户装的 Skill 会不会把文档门禁弄红**。
    `catalog()` 返回"当前注册的一切"，而本文档只承诺产品自带的能力——
    过滤集中在函数里，免得每处各写一遍条件（`render` 与计数都得用同一口径）。
    """
    return [item for item in catalog.get(key) or [] if item.get("origin") == "core"]


def _first_sentence(text: str, limit: int = 96) -> str:
    """工具 description 取首句并限长——清单要能一眼扫，不是文档搬运。"""
    flat = " ".join((text or "").split())
    for sep in ("；", "。", "（"):
        head = flat.split(sep, 1)[0]
        if 8 <= len(head) <= limit:
            flat = head
            break
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _timeout(hub: ToolHub, name: str) -> str:
    """超时从 spec 读（catalog 不带它）。取不到就写 —，不猜。"""
    spec = hub.get(name)
    return f"{spec.timeout_s:g}s" if spec is not None else "—"


def render(hub: ToolHub, catalog: dict) -> str:
    """渲染清单块。**只渲染 core（`origin=core`）**。

    过滤不是多余的：`catalog()` 返回的是"当前注册的一切"，用户装了 Skill 之后
    里面就有 installed 的工具。本文档承诺的是**产品自带**能力（与用户装了什么无关），
    所以这里显式按 origin 过滤——否则**某台装过 Skill 的机器跑门禁会红**，
    而红的原因跟文档对不对毫无关系。installed 的数量由 `/api/skills/summary` 实时回答。
    """
    plugins = _core(catalog, "plugins")
    tools = sorted(_core(catalog, "tools"),
                   key=lambda t: (t["plugin"], RISK_ORDER.get(t["risk"], 9), t["name"]))
    failures = catalog.get("failures") or []
    counts = {"read": 0, "write": 0, "dangerous": 0}
    for tool in tools:
        counts[tool["risk"]] = counts.get(tool["risk"], 0) + 1

    lines = [
        f"> 实测（由运行期工具中枢生成）：**{len(plugins)} 个插件 / {len(tools)} 个工具**。"
        f"风险分布：{counts['read']} read · {counts['write']} write · {counts['dangerous']} dangerous；"
        f"装配失败：{len(failures) if failures else '无'}。",
        "",
        "| 插件 | 标题 | 版本 | 工具数 | 负责什么 |",
        "|---|---|---|---|---|",
    ]
    for plugin in plugins:
        lines.append(
            f"| `{plugin['id']}` | {plugin.get('title', '')} | {plugin.get('version', '')} | "
            f"{len(plugin.get('tools') or [])} | {_first_sentence(plugin.get('description', ''), 60)} |"
        )
    if failures:
        lines += ["", "> ⚠️ **装配失败的插件**（坏插件被刻意隔离，功能会静默缺失）："]
        lines += [f"> - `{item.get('plugin')}`：{item.get('error')}" for item in failures]
    lines += [
        "",
        "| 工具 | 插件 | 风险 | 需确认门 | 超时 | 干什么 | 参数 |",
        "|---|---|---|---|---|---|---|",
    ]
    for tool in tools:
        risk = tool["risk"]
        args = tool.get("args") or {}
        arg_text = "、".join(f"`{name}`" for name in args) if args else "—"
        lines.append(
            f"| `{tool['name']}` | {tool['plugin']} | {RISK_LABEL.get(risk, risk)} | "
            f"{'✅' if risk in ('write', 'dangerous') else '—'} | {_timeout(hub, tool['name'])} | "
            f"{_first_sentence(tool['description'])} | {arg_text} |"
        )
    return "\n".join(lines)


def extract_block(text: str) -> str | None:
    if BEGIN not in text or END not in text:
        return None
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip("\n")


def splice_block(text: str, body: str) -> str:
    """把 BEGIN..END **整段**替换为 `BEGIN + body + END`。

    刻意不用 `text.replace(旧正文, 新正文)`：清单块**初始为空**时，`str.replace("", x)`
    会在**每个字符之间**插入 x——实测把一个 100 行的文档炸成 11 万行。
    整段替换（含标记）没有这个退化情形。
    """
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{body}\n{END}{tail}"


def _claim_problems(n_plugins: int, n_tools: int) -> list[str]:
    problems: list[str] = []
    for path in CLAIM_DOCS:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        for match in re.finditer(r"(\d+)\s*个工具", text):
            if int(match.group(1)) != n_tools:
                problems.append(f"{rel}: 写着「{match.group(0)}」，实际 {n_tools} 个")
        for match in re.finditer(r"(\d+)\s*个插件", text):
            if int(match.group(1)) != n_plugins:
                problems.append(f"{rel}: 写着「{match.group(0)}」，实际 {n_plugins} 个")
    return problems


def main() -> int:
    # GBK 控制台兜底：✔/✘/中文需要 UTF-8 输出（PITFALLS #5 的同类坑）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    hub = get_hub()
    catalog = hub.catalog()

    if not DOC.exists():
        print(f"✗ 缺少 {DOC.relative_to(ROOT)}：先建骨架（含 BEGIN/END 两个标记）再跑 --write")
        return 1

    text = DOC.read_text(encoding="utf-8")
    current = extract_block(text)
    if current is None:
        print(f"✗ {DOC.relative_to(ROOT)} 里找不到清单标记（BEGIN/END），无法生成")
        return 1

    expected = render(hub, catalog)

    if "--write" in sys.argv:
        DOC.write_text(splice_block(text, expected), encoding="utf-8", newline="")
        print(
            f"✓ 已重写 {DOC.relative_to(ROOT)} 的清单块"
            f"（core {len(_core(catalog, 'plugins'))} 插件 / {len(_core(catalog, 'tools'))} 工具）"
        )
        return 0

    problems: list[str] = []
    if current != expected:
        problems.append(
            f"{DOC.relative_to(ROOT)} 的清单块与运行期中枢**不一致**"
            "（跑 `python tools/gen_tools_doc.py --write` 重生成）"
        )
    core_plugins = len(_core(catalog, "plugins"))
    core_tools = len(_core(catalog, "tools"))
    problems += _claim_problems(core_plugins, core_tools)
    missing = verify_required_tools(hub, REQUIRED_TOOLS)
    if missing:
        problems.append(f"REQUIRED_TOOLS 缺失：{missing}（某个插件没装配起来）")

    if problems:
        print("TOOLS_DOC_CHECKS: FAIL")
        for item in problems:
            print(f"  ✗ {item}")
        return 1

    print(
        f"TOOLS_DOC_CHECKS: PASS（{core_plugins} 插件 / {core_tools} 工具 = core；"
        f"另有 installed {catalog['counts']['installed_tools']} 个工具不计入文档）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
