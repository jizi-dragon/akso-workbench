"""Skill 包：上传到共享库 → 从库里安装 → 动态生效 → 一键卸载。

用户 2026-09-17 定的形态：**先上传共享库，再从库里点安装**。

## 依赖方向

    skills.library ──▶ plugin_api（建 Plugin / 查工具）
                   ──▶ sandbox（将来跑脚本时校验目录）
                   ──▶ skills.{layout, manifest, security}

**不 import `api/`、不 import `db`**（留痕经 `set_recorder` 注入）——
与本仓 `plugin_api` / `sandbox.audit` 同一手法，保证可单测。

## 安全定位（务必读）

| 层 | 做什么 | 是不是安全边界 |
|---|---|---|
| `security.inspect_zip` | 上传期结构校验 + 可疑模式扫描 | ❌ **准入闸门**，挡住的是"坏形状" |
| **沙箱**（ADR-0014） | 执行期凭据不注入、目录不越界、进程树可终止 | ✅ 真正的强制层 |
| **确认门**（approvals） | 写/危险操作需人放行 | ✅ |

**本期只支持声明式 Skill**（工具 `wraps` 一个已有工具），
所以脚本执行面**尚未打开**——沙箱已就位，但"把脚本作为工具暴露"是下一期。
这也意味着当前安装任何 Skill 都**不会执行用户上传的代码**。
"""

from __future__ import annotations

from .layout import (
    INSTALLED_DIR,
    LIBRARY_DIR,
    SKILLS_ROOT,
    clear_installed,
    ensure_dirs,
    installed_current,
    installed_dir,
    library_version_dir,
    write_current,
)
from .layout import (
    describe as describe_layout,
)
from .library import (
    UploadResult,
    delete_from_library,
    describe,
    install,
    installed_list,
    list_library,
    set_recorder,
    uninstall,
    upload,
)
from .manifest import (
    SkillManifest,
    SkillPackageError,
    SkillTool,
    build_args_model,
    json_schema_to_fields,
    parse_skill_md,
)
from .security import ScanReport, inspect_zip

__all__ = [
    "INSTALLED_DIR",
    "LIBRARY_DIR",
    "SKILLS_ROOT",
    "ScanReport",
    "SkillManifest",
    "SkillPackageError",
    "SkillTool",
    "UploadResult",
    "build_args_model",
    "clear_installed",
    "delete_from_library",
    "describe",
    "describe_layout",
    "ensure_dirs",
    "inspect_zip",
    "install",
    "installed_current",
    "installed_dir",
    "installed_list",
    "json_schema_to_fields",
    "library_version_dir",
    "list_library",
    "parse_skill_md",
    "set_recorder",
    "uninstall",
    "upload",
    "write_current",
]
