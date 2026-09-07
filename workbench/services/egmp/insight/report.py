"""清单 Markdown 渲染（akso-cc report.ts 的 Python 化）。

章节口径（与上游 report.ts 一致）：
# Gaia 系统配置清单 → 环境 bullets → ## 概览（12 项指标）→ ## 对象清单 →
## 选项集 → ## 工作流 → ## 菜单组 → ## 菜单项 → ## 未解析 objectId → ## 失败项
"""

from __future__ import annotations

from typing import Any


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") if c is not None else "—" for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def to_markdown(inventory: dict[str, Any]) -> str:
    meta = inventory.get("meta", {})
    summary = inventory.get("summary", {})
    parts: list[str] = ["# Gaia 系统配置清单", ""]
    parts.append(f"- baseUrl：{meta.get('baseUrl') or '—'}")
    parts.append(f"- envId：{meta.get('envId') or '—'}")
    parts.append(f"- 生成时间：{meta.get('generatedAt') or '—'}")
    parts.append(f"- 发现通道：{meta.get('discoveryVia') or '—'}")
    parts.append("")

    parts.append("## 概览")
    parts.append(_table(["指标", "数量"], [[k, v] for k, v in summary.items()]))
    parts.append("")

    parts.append("## 对象清单")
    obj_rows = []
    for obj in inventory.get("objects", []):
        obj_rows.append([
            obj.get("code"), obj.get("name"), obj.get("source"),
            len(obj.get("fields") or []), len(obj.get("statuses") or []),
            len(obj.get("formLayouts") or []), len(obj.get("listLayouts") or []),
            "是" if obj.get("enableLifeCycle") else "否",
        ])
    parts.append(_table(
        ["编码", "名称", "来源", "字段数", "状态数", "表单布局", "列表布局", "生命周期"], obj_rows,
    ))
    parts.append("")

    parts.append("## 选项集")
    pick_rows = [
        [p.get("code") or f"id:{p.get('id')}", p.get("name"), len(p.get("options") or []),
         "、".join(p.get("usedBy") or []) or "—"]
        for p in inventory.get("picklists", [])
    ]
    parts.append(_table(["编码", "名称", "选项数", "引用字段"], pick_rows) if pick_rows else "_（无）_")
    parts.append("")

    parts.append("## 工作流")
    wf_keys: list[str] = []
    for wf in inventory.get("workflows", [])[:6]:
        for key in wf.keys():
            if key not in wf_keys and key not in {"id"}:
                wf_keys.append(key)
        if len(wf_keys) >= 6:
            break
    parts.append(_table(wf_keys or ["—"], [[wf.get(k) for k in wf_keys] for wf in inventory.get("workflows", [])])
                 if wf_keys else "_（无）_")
    parts.append("")

    parts.append("## 菜单组")
    parts.append(_table(["名称", "编码", "id"],
                        [[g.get("name"), g.get("code"), g.get("id")] for g in inventory.get("menuGroups", [])])
                 or "​")
    parts.append("")

    parts.append("## 菜单项")
    parts.append(_table(["名称", "编码", "objectId", "parentMenuId"],
                        [[m.get("name"), m.get("code"), m.get("objectId"), m.get("parentMenuId")]
                         for m in inventory.get("menus", [])]))
    parts.append("")

    parts.append("## 未解析 objectId")
    unresolved = inventory.get("unresolvedObjectIds", [])
    parts.append("、".join(map(str, unresolved)) if unresolved else "_（无）_")
    parts.append("")

    parts.append("## 失败项")
    fail_rows = [[f.get("item"), f.get("error")] for f in inventory.get("failures", [])]
    parts.append(_table(["条目", "错误"], fail_rows) if fail_rows else "_（无）_")
    return "\n".join(parts) + "\n"
