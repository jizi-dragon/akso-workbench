"""共享库：上传 → 落库 → 列出 → 安装 → 卸载。

## 数据流（用户 2026-09-17 定的形态）

    用户上传 ZIP ──▶ 校验/扫描（security.py）──▶ 落库 library/<id>/<version>/
                                                      │
                                       界面列出库里的能力 ──▶ 点「安装」
                                                      │
                                    写 installed/<id>/current 指针 ──▶ 动态注册进 ToolHub
                                                      │
                                             点「卸载」──▶ 摘除插件 + 删指针

## "库"与"已安装"是两件事（这是本模块的核心语义）

| 动作 | 对库的影响 | 对已安装的影响 |
|---|---|---|
| **上传** | 新增版本 | 无 |
| **安装** | 无 | 指向该版本 |
| **卸载** | 无（**库里的东西不删**） | 删指针、摘插件 |
| **从库删除** | 删该能力的所有版本 | 若已安装则先卸载 |

**卸载不删库**是刻意的：卸载的意思是"不再生效"，不是"把上传的东西销毁"。
用户可能只是暂时不用，或想换版本。要销毁得走独立的删除动作。

## 与"云服务器上的共享库"的关系

本模块是**本地实现**。所有对库的读写都经 `library_root()` 一个入口取路径——
将来换成远端（HTTP 索引 + 下载）时，改这一层即可，上层（API/界面/安装逻辑）不动。

## 依赖方向

本模块 import `plugin_api`（要建 `Plugin`）与 `sandbox`（校验工作目录在沙箱根内），
**不 import `api/`**。`db` 的写入刻意经注入（`set_recorder`），保持可单测。
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..plugin_api import Plugin, PluginOrigin, Risk, ToolSpec
from . import layout, security
from .manifest import (
    SkillManifest,
    SkillPackageError,
    SkillTool,
    build_args_model,
    json_schema_to_fields,
    manifest_to_json,
    parse_skill_md,
)

logger = logging.getLogger("workbench.skills.library")

_recorder: Callable[[dict[str, Any]], None] | None = None

VALID_RISKS = {r.value: r for r in Risk}


def set_recorder(fn: Callable[[dict[str, Any]], None] | None) -> None:
    """注入持久化写入器（由 api 层接 db）。与沙箱审计同一手法：本模块不 import db。"""
    global _recorder
    _recorder = fn


def _record(event: dict[str, Any]) -> None:
    if _recorder is None:
        return
    try:
        _recorder(event)
    except Exception:  # noqa: BLE001 —— 留痕失败绝不影响主流程
        logger.warning("Skill 库留痕写入失败 event=%s", event.get("action"), exc_info=True)


@dataclass
class UploadResult:
    ok: bool
    capability_id: str = ""
    version: str = ""
    verdict: str = ""            # ok | warn | reject
    reason: str = ""
    tools: list[str] = None      # type: ignore[assignment]
    findings: list[dict] = None  # type: ignore[assignment]
    boundary: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "capability_id": self.capability_id, "version": self.version,
            "verdict": self.verdict, "reason": self.reason, "tools": self.tools or [],
            "findings": self.findings or [], "boundary": self.boundary,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 上传
# ─────────────────────────────────────────────────────────────────────────────


def upload(data: bytes, *, filename: str = "", allow_warn: bool = False) -> UploadResult:
    """把一份 Skill ZIP 收进共享库。

    `allow_warn=False`（默认）时，扫描出高危模式的能力**不落库**——
    用户要先看过扫描结果并显式确认（第二次上传带 `allow_warn=True`）才收。
    """
    layout.ensure_dirs()
    report = security.inspect_zip(data)
    if not report.ok:
        _record({"action": "upload_rejected", "detail": report.reason, "filename": filename})
        return UploadResult(ok=False, verdict="reject", reason=report.reason,
                            boundary=report.to_dict()["boundary"])

    if report.verdict == "warn" and not allow_warn:
        _record({"action": "upload_needs_confirm", "filename": filename,
                 "high": report.high_count})
        return UploadResult(
            ok=False, verdict="warn", reason=report.reason,
            findings=[f.to_dict() for f in report.findings],
            boundary="扫描到高危模式，需显式确认后才会收进共享库",
        )

    try:
        skill_md = security.extract_skill_md(data)
        manifest = parse_skill_md(skill_md)
    except SkillPackageError as exc:
        _record({"action": "upload_rejected", "detail": str(exc), "filename": filename})
        return UploadResult(ok=False, verdict="reject", reason=str(exc))

    # 被包装的工具必须真实存在——**上传期就查**，不要等安装时才失败
    from ..plugin_api import get_hub

    hub = get_hub()
    missing = [t.wraps for t in manifest.tools if t.wraps and hub.get(t.wraps) is None]
    if missing:
        return UploadResult(
            ok=False, verdict="reject",
            reason=(f"wraps 指向的工具不存在：{missing}。"
                    f"共享库里只能包装本工作台已有的工具（现有 {len(hub.specs())} 个）"),
        )
    # 脚本必须真的在包里——否则会出现"装上了、调用时才报文件不存在"
    declared_files = set(report.files)
    missing_scripts = [t.script for t in manifest.tools
                       if t.script and t.script not in declared_files]
    if missing_scripts:
        return UploadResult(
            ok=False, verdict="reject",
            reason=(f"script 指向的文件不在包内：{missing_scripts}。"
                    f"包内实际有：{sorted(declared_files)[:12]}"),
        )
    # 工具名不得与**核心**工具重名（`register_installed` 也会拦，这里提前给上传者明确理由）
    core_names = {s.name for s in hub.core_specs()}
    core_clash = [t.name for t in manifest.tools if t.name in core_names]
    if core_clash:
        return UploadResult(
            ok=False, verdict="reject",
            reason=f"工具名与核心工具冲突：{core_clash}——安装的能力不得覆盖产品自带工具",
        )

    target = layout.library_version_dir(manifest.id, manifest.version)
    _materialise(target, data, skill_md, manifest, report)

    _record({"action": "upload", "capability_id": manifest.id, "version": manifest.version,
             "verdict": report.verdict, "high": report.high_count, "filename": filename})
    logger.info("Skill 已入库：%s@%s（%s）", manifest.id, manifest.version, report.verdict)
    return UploadResult(ok=True, capability_id=manifest.id, version=manifest.version,
                        verdict=report.verdict, tools=[t.name for t in manifest.tools],
                        findings=[f.to_dict() for f in report.findings])


def _materialise(target: Path, data: bytes, skill_md: str,
                 manifest: SkillManifest, report: security.ScanReport) -> None:
    """把整包**解压**进库目录，再写元信息（`manifest.json` 最后写）。

    ## 为什么必须解压，而不能只存 `source.zip`

    脚本式工具的 handler 取的是"库里那一份"（`layout.library_version_dir`），
    执行前整目录复制进沙箱运行目录。初版只落了 `SKILL.md`/`source.zip`/`manifest.json`，
    于是**库目录里根本没有脚本文件**——装得上、一调用必然"脚本不存在"。
    这类"装上了但功能是死的"最难查，所以库目录必须是**可执行的权威副本**，
    `source.zip` 留作可复核的原件（两者都在）。

    ## 为什么先写暂存目录再整体换入

    直接往目标目录写，一旦中途失败就会留下"半个包"，而它看起来和完整的包一样
    （`manifest.json` 在不在，是 `_read_manifest` 判断版本是否可用的唯一依据）。
    暂存 + `rename` 让失败**要么没发生、要么不留痕**；`manifest.json` 最后写，
    作为"这一版已经完整落地"的标志。
    """
    staging = layout.staging_dir(target.parent)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        security.extract_to(data, staging)
        # 用解析过的那份覆盖包里的 SKILL.md（万一是 `utf-8 replace` 修过的字节）
        (staging / "SKILL.md").write_text(skill_md, encoding="utf-8", newline="\n")
        (staging / "source.zip").write_bytes(data)
        if report.findings:
            (staging / "scan.json").write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8", newline="\n")
        (staging / "manifest.json").write_text(
            manifest_to_json(manifest), encoding="utf-8", newline="\n")
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _origin_of(hub, tool_name: str) -> str:
    """某个工具来自 core 还是 installed（给界面与诊断用）。"""
    spec = hub.get(tool_name)
    if spec is None:
        return ""
    for plugin in hub.plugins:
        if plugin.id == spec.plugin_id:
            return plugin.origin.value
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 读库
# ─────────────────────────────────────────────────────────────────────────────


def _read_manifest(capability_id: str, version: str) -> dict | None:
    path = layout.LIBRARY_DIR / capability_id / version / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def list_library() -> list[dict]:
    """列出共享库里的全部能力（按能力聚合，含版本与是否已安装）。"""
    layout.ensure_dirs()
    out: list[dict] = []
    for capability_dir in sorted(layout.LIBRARY_DIR.iterdir()):
        if not capability_dir.is_dir() or capability_dir.name.startswith("."):
            continue
        versions = layout.library_versions(capability_dir.name)
        manifests = [m for v in versions if (m := _read_manifest(capability_dir.name, v))]
        if not manifests:
            continue
        latest = manifests[-1]
        current = layout.installed_current(capability_dir.name)
        tools = latest.get("tools", [])
        out.append({
            "capability_id": capability_dir.name,
            "name": latest.get("name") or capability_dir.name,
            "description": latest.get("description", ""),
            "latest_version": latest.get("version", versions[-1]),
            "versions": versions,
            "tools": [t["name"] for t in tools],
            # 哪些工具会**执行上传的代码**——界面必须能一眼看出来，
            # 否则"声明式"与"脚本式"在列表里长得一样（诚实边界的可见性要求）
            "script_tools": [t["name"] for t in tools if t.get("script")],
            "capabilities": latest.get("capabilities", []),
            "installed": current is not None,
            "installed_version": current.name if current else "",
            "has_scan_findings": (capability_dir / versions[-1] / "scan.json").is_file(),
        })
    return out


def installed_list() -> list[dict]:
    """列出已安装的能力（含它贡献的工具与是否启用）。"""
    from ..plugin_api import get_hub

    hub = get_hub()
    layout.ensure_dirs()
    out: list[dict] = []
    for plugin in hub.installed_plugins():
        current = layout.installed_current(plugin.capability_id)
        out.append({
            "capability_id": plugin.capability_id,
            "plugin_id": plugin.id,
            "title": plugin.title,
            "version": plugin.version,
            "tools": [s.name for s in plugin.tools],
            "enabled": hub.is_enabled(plugin.id),
            "installed_version": current.name if current else "",
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 安装 / 卸载
# ─────────────────────────────────────────────────────────────────────────────


def install(capability_id: str, version: str = "") -> dict:
    """从共享库安装（或切换版本）某个能力。**幂等**：重复安装同版本 = 重新注册。"""
    layout.ensure_dirs()
    from ..plugin_api import get_hub

    versions = layout.library_versions(capability_id)
    if not versions:
        raise SkillPackageError(f"共享库里没有这个能力：{capability_id}")
    version = version or versions[-1]
    if version not in versions:
        raise SkillPackageError(f"{capability_id} 没有版本 {version}（有：{versions}）")

    data = _read_manifest(capability_id, version)
    if data is None:
        raise SkillPackageError(f"{capability_id}@{version} 的 manifest.json 缺失或损坏，请重新上传")

    hub = get_hub()
    # 先摘旧的（换版本时不能两版并存），再装新的
    hub.deregister_capability(capability_id)
    hub.register_installed(_build_plugin(data))
    layout.write_current(capability_id, version)
    _record({"action": "install", "capability_id": capability_id, "version": version})
    logger.info("已安装能力：%s@%s", capability_id, version)
    return {"capability_id": capability_id, "version": version,
            "tools": [t["name"] for t in data.get("tools", [])]}


def uninstall(capability_id: str) -> dict:
    """卸载：摘插件 + 删 `current` 指针。**库里的版本一个都不删。**"""
    from ..plugin_api import get_hub

    hub = get_hub()
    removed_plugins = hub.deregister_capability(capability_id)
    layout.clear_installed(capability_id)
    _record({"action": "uninstall", "capability_id": capability_id,
             "plugins": removed_plugins})
    logger.info("已卸载能力：%s（摘除 %s）", capability_id, removed_plugins)
    return {"capability_id": capability_id, "removed_plugins": removed_plugins,
            "library_intact": True}


def delete_from_library(capability_id: str) -> dict:
    """从共享库彻底删除（含所有版本）。**已安装则先卸载。**"""
    root = layout.LIBRARY_DIR / capability_id
    if not root.is_dir():
        raise SkillPackageError(f"共享库里没有这个能力：{capability_id}")
    was_installed = layout.installed_current(capability_id) is not None
    if was_installed:
        uninstall(capability_id)
    shutil.rmtree(root)
    installed_root = layout.INSTALLED_DIR / capability_id
    if installed_root.is_dir():
        shutil.rmtree(installed_root)
    _record({"action": "delete_from_library", "capability_id": capability_id,
             "was_installed": was_installed})
    logger.info("已从共享库删除：%s（原已安装=%s）", capability_id, was_installed)
    return {"capability_id": capability_id, "was_installed": was_installed}


def _build_plugin(data: dict) -> Plugin:
    """把 manifest dict 翻译成一个可注册的 `Plugin`。**两种工具形态各走各的**。

    | 形态 | handler | 风险来源 |
    |---|---|---|
    | 声明式（`wraps`） | 转发给被包装工具，走 `hub.execute`（**同一套确认门与审计**） | 被包装工具与声明值的**较严**者 |
    | 脚本式（`script`） | 在**沙箱子进程**里跑包内脚本（`script_runner`） | 默认 `write`（见下） |

    ## 声明式：为什么不绕过 `hub.execute`

    绕过去（直接调 handler）会让安装的能力成为**审计盲区**，而且拿不到确认门。
    所以一律经中枢——"安装的能力也是能力，走同一条治理链路"。

    ## 脚本式：默认风险 `write`，且**不允许被降到 read**

    脚本是用户上传的代码。即便它"看起来只读"，我们**无法静态证明**它不写东西
    （沙箱能拦目录越界，但拦不住它在沙箱内写文件、也拦不住它慢慢跑）。
    把它标成 `read` 意味着"模型可以不经确认随便跑"——对上传代码这是错的默认。
    声明式可以 `read`，因为被包装工具的语义是我们自己定的。
    """
    from ..plugin_api import get_hub

    hub = get_hub()
    tools: list[ToolSpec] = []
    capability_id = data["id"]
    version = data.get("version", "0.0.0")

    for item in data.get("tools", []):
        name = item["name"]
        declared = VALID_RISKS.get(str(item.get("risk") or "").lower())
        script = item.get("script") or ""

        if script:
            # 脚本式：必须显式声明 args（manifest 已强制），风险不得低于 write
            risk = _stricter(declared or Risk.WRITE, Risk.WRITE)
            args_model = build_args_model(name, json_schema_to_fields(item["args"]))
            tools.append(ToolSpec(
                name=name,
                description=item["description"],
                args_model=args_model,
                handler=_make_script_handler(capability_id, version, script, name),
                risk=risk,
                # 策略超时是上限；这里跟随策略（`skill-script` = 120s）
                timeout_s=120.0,
            ))
            continue

        wrapped_name = item["wraps"]
        wrapped = hub.get(wrapped_name)
        if wrapped is None:
            raise SkillPackageError(f"安装失败：被包装的工具不存在 {wrapped_name!r}（库已过期？）")

        risk = _stricter(declared, wrapped.risk)
        if item.get("args"):
            args_model = build_args_model(name, json_schema_to_fields(item["args"]))
        else:
            args_model = wrapped.args_model

        tools.append(ToolSpec(
            name=name,
            description=item["description"],
            args_model=args_model,
            handler=_make_forwarder(wrapped_name),
            risk=risk,
            timeout_s=wrapped.timeout_s,
            audit_extra=wrapped.audit_extra,
        ))
    return Plugin(
        id=f"skill:{capability_id}",
        title=data.get("name") or capability_id,
        version=version,
        description=data.get("description", ""),
        tools=tools,
        origin=PluginOrigin.INSTALLED,
        capability_id=capability_id,
        removable=True,
    )


def _stricter(a: Risk | None, b: Risk) -> Risk:
    order = {Risk.READ: 0, Risk.WRITE: 1, Risk.DANGEROUS: 2}
    if a is None:
        return b
    return a if order[a] >= order[b] else b


def _make_forwarder(wrapped_name: str):
    """生成一个把调用转给被包装工具的 handler。

    ⚠️ **闭包捕获**：这里用默认参数把 `wrapped_name` 绑死，
    而不是让它从外层作用域读——本仓在 `approvals.py` 的确认门注入上
    踩过"闭包绑了旧实例"的坑（PITFALLS #34），同一类错误在这里的表现是
    "所有 Skill 工具都调用了最后一个被包装的工具"。
    """
    async def _handler(args, ctx, _target: str = wrapped_name):
        from ..plugin_api import get_hub as _hub

        payload = args.model_dump(exclude_none=True) if hasattr(args, "model_dump") else dict(args)
        invocation = await _hub().execute(_target, payload, ctx)
        return invocation.content

    return _handler


def _make_script_handler(capability_id: str, version: str, script: str, tool_name: str):
    """生成一个在**沙箱里跑包内脚本**的 handler（脚本式）。

    三件事必须做对：

    1. **包目录取"库里那一份"**（`layout.library_version_dir`），不是运行目录——
       库里才是权威副本；运行目录是执行时的临时拷贝（见 `script_runner.prepare_run_dir`）。
    2. **同步阻塞调用挪线程**：`sandbox.run` 会阻塞到脚本结束，
       直接 await 会卡死事件循环（本仓铁律，见 `docs/TOOLS.md` §4 第 2 条）。
    3. **失败变成可自我纠正的观测**（抛 `ValueError` → `ToolHub` 转成 error 观测），
       不是让异常穿出去终止整轮——脚本失败很常见，模型需要看到原因才能改参数重试。
    """
    async def _handler(args, ctx, _cap: str = capability_id, _ver: str = version,
                       _script: str = script, _tool: str = tool_name):
        import asyncio

        from .script_runner import run_script

        payload = args.model_dump(exclude_none=True) if hasattr(args, "model_dump") else dict(args)
        package_dir = layout.library_version_dir(_cap, _ver, create=False)
        outcome = await asyncio.to_thread(
            run_script,
            capability_id=_cap, version=_ver, package_dir=package_dir,
            script=_script, tool_name=_tool, args=payload,
        )
        if not outcome.ok:
            raise ValueError(outcome.error)
        return outcome.text

    return _handler


def describe() -> dict:
    return {"layout": layout.describe(), "library": list_library(),
            "installed": installed_list(), "ts": int(time.time() * 1000)}


__all__ = [
    "UploadResult",
    "SkillManifest",
    "SkillPackageError",
    "SkillTool",
    "delete_from_library",
    "describe",
    "install",
    "installed_list",
    "list_library",
    "set_recorder",
    "uninstall",
    "upload",
]
