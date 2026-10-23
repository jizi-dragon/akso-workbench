"""工作区快照 / 回退：给"写操作做错了"留一条可执行的退路。

## 为什么现在只做接缝

产品当前没有 WRITE 工具，但脚本式 Skill 已经能在沙箱里改工作区。等第一个写工具
落地再补快照，就会在"已经写过一次"之后才有安全网。所以先把**存储与恢复**做成
独立模块，由 `sandbox.run` 在显式开关下调用（默认关，避免每次执行都扫一遍工作区）。

## 边界（与 Reasonix 的 checkpoint 同一口径）

- 只覆盖**文件**，不跟踪 shell/脚本的副作用（`rm`、数据库写、外发请求撤不了）。
- 只覆盖**调用时存在**的文件；快照之外的新文件不会出现在恢复里。
- 有硬上限（文件数 / 字节数），超限**显式标记** `truncated`，不假装完整。
- 路径必须落在工作区根内（`Path.is_relative_to`，防 `..`/符号链接逃逸）。
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config

MAX_FILES = 200
MAX_BYTES = 20 * 1024 * 1024


class CheckpointError(RuntimeError):
    """快照/恢复失败。调用方决定是阻断还是仅记录。"""


@dataclass
class Capture:
    id: str
    root: str
    files: list[str] = field(default_factory=list)
    bytes: int = 0
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "root": self.root,
            "files": list(self.files),
            "bytes": self.bytes,
            "truncated": self.truncated,
        }


def checkpoints_dir() -> Path:
    path = config.DATA_DIR / "checkpoints"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_id() -> str:
    return f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def _safe_target(root: Path, rel: str) -> Path:
    """`rel` 必须是 root 内的普通相对路径；否则抛。"""
    base = root.resolve(strict=False)
    candidate = (base / rel).resolve(strict=False)
    if not candidate.is_relative_to(base):
        raise CheckpointError(f"快照路径逃出工作区：{rel!r}")
    return candidate


def capture(root: str | Path, *, max_files: int = MAX_FILES,
            max_bytes: int = MAX_BYTES) -> Capture:
    """把 `root` 下的普通文件复制进 `<数据目录>/checkpoints/<id>/`。"""
    base = Path(root).resolve(strict=False)
    if not base.is_dir():
        raise CheckpointError(f"工作区不是目录：{root}")
    if max_files < 1 or max_bytes < 0:
        raise CheckpointError("max_files 必须 ≥1，max_bytes 必须 ≥0")

    capture_id = _new_id()
    target = checkpoints_dir() / capture_id
    files_dir = target / "files"
    files_dir.mkdir(parents=True, exist_ok=False)

    capture = Capture(id=capture_id, root=str(base))
    for path in sorted(base.rglob("*")):
        if capture.truncated:
            break
        if path.is_symlink() or not path.is_file():
            continue
        size = path.stat().st_size
        if len(capture.files) >= max_files or capture.bytes + size > max_bytes:
            capture.truncated = True
            break
        rel = path.relative_to(base).as_posix()
        dest = files_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        capture.files.append(rel)
        capture.bytes += size

    manifest = {
        **capture.to_dict(),
        "createdAt": int(time.time() * 1000),
        "maxFiles": max_files,
        "maxBytes": max_bytes,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return capture


def load(capture_id: str) -> dict:
    if not capture_id or "/" in capture_id or "\\" in capture_id or ".." in capture_id:
        raise CheckpointError(f"非法快照 id：{capture_id!r}")
    manifest_path = checkpoints_dir() / capture_id / "manifest.json"
    if not manifest_path.exists():
        raise CheckpointError(f"快照不存在：{capture_id}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def restore(capture_id: str) -> dict:
    """把快照覆盖回它自己的 root。返回 `{restored, skipped}`。"""
    manifest = load(capture_id)
    root = Path(manifest["root"]).resolve(strict=False)
    if not root.is_dir():
        raise CheckpointError(f"快照的工作区已不存在：{root}")
    files_dir = checkpoints_dir() / capture_id / "files"
    restored = 0
    for rel in manifest.get("files") or []:
        source = _safe_target(files_dir, rel)
        dest = _safe_target(root, rel)
        if not source.is_file():
            raise CheckpointError(f"快照文件缺失：{rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        restored += 1
    return {"id": capture_id, "restored": restored,
            "truncated": bool(manifest.get("truncated"))}


def list_captures() -> list[dict]:
    out: list[dict] = []
    for manifest_path in sorted(checkpoints_dir().glob("*/manifest.json")):
        try:
            out.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out
