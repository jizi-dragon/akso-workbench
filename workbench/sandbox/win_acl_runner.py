"""可选的 DSH Windows ACL runner 接缝：把 workspace-write 交给已验证的原生实现。

## 为什么是“接缝”而不是内置运行时

DSH 的 `@deepseek-ai/dsh-sandbox-windows-acl`（BSD-3-Clause）已经验证了
capability SID + WRITE_RESTRICTED + Low label + deny DELETE_CHILD 的完整编排。
Workbench 是 Python 单栈；把 Node/koffi 运行时打进安装包会显著增加打包/签名成本，
所以这里只提供**显式配置**的接缝：

    WORKBENCH_WINDOWS_ACL_RUNNER = <...>/lib/runner.js
    WORKBENCH_NODE = <node.exe>   （可选，默认 PATH 上的 node）

配置后，`workspace-write` 策略会用 Node runner 包一层：

    node runner.js --workspace <root> --temp <private> --mode workspace-write -- <argv...>

runner 自己创建受限令牌、授予 workspace/temp、拒绝外部写入；失败以 127 退出，
Workbench 按普通非零退出处理并留审计。未配置时退回原有“声明”路径（不假装强制）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def runner_path() -> Path | None:
    raw = os.environ.get("WORKBENCH_WINDOWS_ACL_RUNNER", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def node_path() -> str | None:
    explicit = os.environ.get("WORKBENCH_NODE", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    return shutil.which("node")


def available() -> bool:
    return IS_WINDOWS and runner_path() is not None and node_path() is not None


def wrap(argv: list[str], *, workspace_root: Path, temp_dir: Path) -> list[str]:
    """把原始 argv 包成 DSH runner 的 argv。调用方保证 `available()`。"""
    runner = runner_path()
    node = node_path()
    if runner is None or node is None:
        raise RuntimeError("DSH Windows ACL runner 未配置")
    temp_dir.mkdir(parents=True, exist_ok=True)
    return [
        node, str(runner),
        "--workspace", str(workspace_root),
        "--temp", str(temp_dir),
        "--mode", "workspace-write",
        "--",
        *argv,
    ]
