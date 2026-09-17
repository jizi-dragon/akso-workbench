"""Skill 共享库与安装管理面：上传 / 列出 / 安装 / 卸载 / 删除。

## 与 `/api/agent/*` 的分工

| 端点族 | 回答什么 |
|---|---|
| `/api/agent/tools` | **产品自带**能力有哪些（core）——文档承诺的就是这部分 |
| `/api/skills/*` | **我上传/安装**了哪些能力（installed）——因人而异，实时回答 |

两边读的是**同一个** `ToolHub`（单一事实源没变），只是按 `origin` 分开呈现。
分开的理由是硬的：`tools/gen_tools_doc.py` 断言"文档里的 N 个工具 == 运行期中枢"，
而"用户装了什么"不可能写进文档。

## 为什么上传走 multipart 而不是 JSON

Skill 是 ZIP。本仓已有 `python-multipart` 依赖（`routes_factory` 上传蓝图在用），
沿用同一形态，前端不必自己 base64。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import db
from ..plugin_api import get_hub
from ..skills import library as L

router = APIRouter(prefix="/api/skills", tags=["skills"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class InstallIn(BaseModel):
    capability_id: str = Field(..., min_length=1, description="共享库里的能力 id")
    version: str = Field("", description="留空 = 装最新版")


class DeleteIn(BaseModel):
    capability_id: str = Field(..., min_length=1)


@router.get("/summary")
def summary() -> dict:
    """一页话概览：库里有几个、装了几个、各贡献多少工具。"""
    library = L.list_library()
    installed = L.installed_list()
    hub = get_hub()
    return {
        "library": library,
        "installed": installed,
        "counts": {
            "library": len(library),
            "installed": len(installed),
            "core_tools": len(hub.core_specs()),
            "installed_tools": len(hub.specs()) - len(hub.core_specs()),
        },
        "layout": L.describe()["layout"],
        "boundary": (
            "本期只支持**声明式** Skill（工具 wraps 一个已有工具），"
            "因此安装 Skill **不会执行任何上传的代码**。"
            "脚本式执行面待下一期接入沙箱。"
        ),
    }


@router.post("/upload")
async def upload(file: UploadFile = File(...), allow_warn: bool = False) -> dict:
    """上传一个 Skill ZIP 到共享库。

    `allow_warn=False`（默认）时，扫描到高危模式会返回 `verdict=warn` 且**不入库**——
    调用方看过 `findings` 后带 `allow_warn=true` 重投才收。
    这是刻意的两步：**高危的东西不该"点一下上传就生效"**。
    """
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"包过大：{len(data)} 字节 > 上限 {MAX_UPLOAD_BYTES}")
    result = L.upload(data, filename=file.filename or "", allow_warn=allow_warn)
    payload = result.to_dict()
    # 拒绝用 400：这是调用方能自己修的问题（改包重传），不是服务端故障
    if not result.ok:
        raise HTTPException(400, detail=payload)
    return payload


@router.post("/install")
def install(body: InstallIn) -> dict:
    try:
        return L.install(body.capability_id, body.version)
    except L.SkillPackageError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/uninstall")
def uninstall(body: DeleteIn) -> dict:
    """卸载：摘插件 + 删指针。**共享库里的版本一个都不删。**"""
    try:
        return L.uninstall(body.capability_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/delete")
def delete(body: DeleteIn) -> dict:
    """从共享库**彻底删除**（含所有版本）。已安装则先卸载。"""
    try:
        return L.delete_from_library(body.capability_id)
    except L.SkillPackageError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/plugins/{plugin_id}/enabled")
def set_enabled(plugin_id: str, enabled: bool = True) -> dict:
    """启用/禁用任意插件（**含核心插件**）。

    与"卸载"的区别：禁用只是不进模型可见的工具表，插件仍在清单里，
    界面显示"已禁用"并能一键恢复。**核心插件只能禁用、不能卸载**。
    """
    hub = get_hub()
    try:
        hub.set_plugin_enabled(plugin_id, enabled)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"plugin_id": plugin_id, "enabled": enabled,
            "visible_tools": len(hub.specs())}


@router.get("/audit")
def audit(limit: int = 50) -> dict:
    """Skill 库操作留痕（上传/安装/卸载/删除）。

    与工具调用审计（`/api/agent/audit`）、沙箱执行审计（`/api/sandbox/runs`）分开：
    这条记的是**能力供给链**上的动作——"谁在什么时候把这个 Skill 装进来了"。
    """
    rows = db.query(
        "SELECT id, action, capability_id, version, verdict, detail, created_at "
        "FROM skill_audit ORDER BY created_at DESC LIMIT ?",
        (min(limit, 500),),
    )
    return {"audit": rows}


@router.get("/library/raw/{capability_id}/{version}/{name}")
def read_library_file(capability_id: str, version: str, name: str) -> dict:
    """读共享库里某个版本的文本文件（`SKILL.md` / `manifest.json` / `scan.json`）。

    **只允许这三个名字**——不做通用文件读取：那会变成"用 URL 探数据目录"的洞
    （本仓服务端无认证，R1）。
    """
    allowed = {"SKILL.md", "manifest.json", "scan.json"}
    if name not in allowed:
        raise HTTPException(404, f"只允许读：{', '.join(sorted(allowed))}")
    from ..skills import layout

    try:
        path = layout.library_version_dir(capability_id, version, create=False) / name
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, f"文件不存在：{capability_id}@{version}/{name}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if name.endswith(".json"):
        try:
            return {"capability_id": capability_id, "version": version, "name": name,
                    "data": json.loads(text)}
        except ValueError:
            pass
    return {"capability_id": capability_id, "version": version, "name": name, "text": text}
