"""Agent Skills 标准（`SKILL.md` 文件夹）的本地运行时。

## 与 ZIP 库的分工

| 形态 | 入口 | 运行时 | 适用 |
|---|---|---|---|
| **文件夹 Skill**（本模块） | `<root>/<name>/SKILL.md` | 目录发现 + 按需读正文 | 指令型 Skill；标准格式，跨工具可搬 |
| ZIP 包（`library.py`） | 上传共享库 → 安装 | 解压副本 + 可选工具 | 需要声明/脚本工具的 Skill |

文件夹 Skill **不执行代码**：它只提供说明正文，由 `read_skill` 按需加载。
这与 Agent Skills 的"渐进披露"一致（先目录、后正文），也避免"放一个目录就等于执行任意脚本"。

## 发现根（先到先得，重名由更靠前的根覆盖）

1. `WORKBENCH_SKILLS_DIRS`（`os.pathsep` 分隔）——显式配置；
2. `<cwd>/.agents/skills`——Agent Skills 常见约定；
3. `<cwd>/.workbench/skills`——本仓项目级约定；
4. `<DATA_DIR>/skills/folders`——用户级目录。

## 边界

- 只读 `SKILL.md`；不读/执行同目录脚本；
- 正文 ≤ 32 KB，description ≤ 500 字；
- 符号链接目录跳过；名称必须是 `[a-z0-9][a-z0-9-]{0,63}`；
- 坏目录只跳过自己，不影响其它 Skill。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .. import config

logger = logging.getLogger("workbench.skills.folders")

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
MAX_BODY_CHARS = 32_000


@dataclass(frozen=True)
class FolderSkill:
    name: str
    description: str
    version: str
    body: str
    path: Path
    root: Path

    def summary(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "source": "folder",
            "path": str(self.path),
            "body_chars": len(self.body),
        }


def roots() -> list[Path]:
    """发现根，去重后只保留存在的目录。"""
    candidates: list[Path] = []
    raw = os.environ.get("WORKBENCH_SKILLS_DIRS", "")
    candidates.extend(Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip())
    cwd = Path.cwd()
    candidates.extend([cwd / ".agents" / "skills", cwd / ".workbench" / "skills",
                       config.DATA_DIR / "skills" / "folders"])
    seen: set[str] = set()
    out: list[Path] = []
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        out.append(resolved)
    return out


def _parse(path: Path, root: Path) -> FolderSkill:
    text = (path / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        raise ValueError("SKILL.md 必须以 YAML frontmatter 起始")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md 的 frontmatter 未闭合")
    try:
        front = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter 不是合法 YAML：{exc}") from exc
    if not isinstance(front, dict):
        raise ValueError("frontmatter 必须是对象")
    name = str(front.get("name") or path.name).strip()
    if not NAME_RE.fullmatch(name):
        raise ValueError(f"skill name 只允许小写字母/数字/连字符（≤64）：{name!r}")
    description = str(front.get("description") or "").strip()
    if not description:
        raise ValueError("缺少 description（Agent Skills 要求）")
    version = str(front.get("version") or "0.1.0").strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"version 必须是 x.y.z：{version!r}")
    body = parts[2].strip()
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(f"SKILL.md 正文超过 {MAX_BODY_CHARS} 字符")
    return FolderSkill(name=name, description=description[:500], version=version,
                       body=body, path=path, root=root)


def discover() -> list[FolderSkill]:
    """扫描全部根；重名先到先得，坏目录只跳过自己。"""
    found: dict[str, FolderSkill] = {}
    for root in roots():
        try:
            children = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            if child.is_symlink() or not child.is_dir():
                continue
            if not (child / "SKILL.md").is_file():
                continue
            try:
                skill = _parse(child, root)
            except (OSError, ValueError) as exc:
                logger.warning("跳过无效 Skill 目录 %s：%s", child, exc)
                continue
            if skill.name not in found:
                found[skill.name] = skill
    return [found[name] for name in sorted(found)]


def catalog() -> list[dict]:
    """给 `list_skills` 用的目录（**不读正文**）。"""
    return [skill.summary() for skill in discover()]


def read(name: str) -> str:
    """按名称读正文（渐进披露的第二步）。未找到 → `KeyError`。"""
    target = (name or "").strip()
    for skill in discover():
        if skill.name == target:
            return f"# {skill.name}\n\n{skill.description}\n\n{skill.body}"
    raise KeyError(f"没有这个文件夹 Skill：{target or '（空）'}")
