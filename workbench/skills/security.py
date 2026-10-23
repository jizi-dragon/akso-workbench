"""Skill 包的安全校验：ZIP 结构 + 内容扫描。

## 定位：这是**准入闸门**，不是安全边界

本仓真正的强制层是 `workbench/sandbox/`（凭据不注入、目录不越界、进程树可终止，
）。
本模块做的是**上传期**的三件事：

1. **结构合规**（ZIP 形状、根目录有 `SKILL.md`、禁二进制）——挡住"包本身是坏的"；
2. **内容扫描**——把可疑模式**标出来给人看**（`findings`），不是假装能判定恶意；
3. **准入结论**（`ok` / `warn` / `reject`）——`reject` 的东西**连库都进不去**。

**为什么不把扫描当边界**：静态扫描对混淆、编码、动态拼接天然无效。
把它当边界会给人虚假安全感——而这正是 反复强调要避免的。
所以扫描只做两件事：**拒掉明确的坏形状**，**把可疑行为摊开给人看**。

## 与 ADP Skills 规范的一致处（刻意对齐）

- ZIP ≤ **10 MB**、文件数 ≤ **300**、`SKILL.md` 必须在**根目录**
- **不允许任何二进制文件**（.exe/.dll/.png/.zip…），只允许纯文本
这两条直接抄自腾讯云 ADP 的公开规范——那边已经用生产流量验证过这套约束是可用的，
而我们的场景（同事上传、无法逐个审计）与它高度一致。
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import SkillPackageError

MAX_ZIP_BYTES = 10 * 1024 * 1024
"""整包上限（与 ADP 一致）。"""

MAX_FILES = 300
"""包内文件数上限（与 ADP 一致）。"""

MAX_SINGLE_FILE_BYTES = 2 * 1024 * 1024
"""单文件解压后上限。防"压缩炸弹"：10MB 的 zip 能解出几个 GB。"""

#: 允许的文件后缀（纯文本类）。**白名单**，其余一律拒。
ALLOWED_SUFFIXES: frozenset[str] = frozenset({
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".csv", ".sql", ".html", ".css",
})

#: 明确拒绝的后缀（二进制/可执行/归档）。白名单已经拦住了，这里是为了**给出人话理由**。
REJECTED_SUFFIXES: frozenset[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".bin", ".msi", ".bat", ".cmd", ".com", ".scr",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".jar", ".whl",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".pdf", ".docx", ".xlsx",
    ".pyd", ".pyc", ".class", ".node", ".wasm",
})

#: 可疑模式 → (严重度, 人话说明)。严重度 `high` 会让准入结论变成 `warn`（需人确认）。
#:
#: ⚠️ **这些正则故意写得啰嗦且会误报**：目的是"把要人看一眼的地方摊开"，
#: 不是"自动判定恶意"。误报的代价是用户多点一次确认，漏报的代价是任意代码执行
#: ——两者不对称，所以宁可吵。
SUSPICIOUS_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (r"\beval\s*\(", "high", "动态执行代码（eval）"),
    (r"\bexec\s*\(", "high", "动态执行代码（exec）"),
    (r"__import__\s*\(", "high", "动态导入模块"),
    (r"\bcompile\s*\([^)]*['\"]exec['\"]", "high", "编译后执行代码"),
    (r"\bsubprocess\b|\bos\.system\b|\bos\.popen\b|\bos\.spawn", "high", "启动子进程"),
    (r"\brequests\.|\bhttpx\.|\burllib\.request\b|\bsocket\.|\baiohttp\b", "high", "网络访问"),
    (r"\bopen\s*\([^)]*['\"][wa]", "medium", "写文件"),
    (r"\bos\.remove\b|\bshutil\.rmtree\b|\bos\.unlink\b", "medium", "删除文件"),
    (r"\bbase64\.b64decode\b|\bcodecs\.decode\b|\bmarshal\.loads\b", "high", "解码/反序列化（常用于绕过扫描）"),
    (r"(?i)password|passwd|secret|token|api[_-]?key|credential", "medium", "涉及凭据的字样"),
    (r"\bchmod\b|\bchown\b|\bsetuid\b", "medium", "改权限"),
    (r"\bregistry\b|HKEY_|winreg", "medium", "读写注册表"),
    (r"\\\\[^\\]+\\(c|admin|ipc)\$", "high", "UNC/管理共享路径"),
    (r"(?i)curl\s+http|wget\s+http|Invoke-WebRequest|iwr\s+http", "high", "命令行下载"),
)


@dataclass
class Finding:
    """一条扫描发现。"""

    severity: str          # high | medium | low
    kind: str
    file: str = ""
    line: int = 0
    excerpt: str = ""

    def to_dict(self) -> dict:
        return {"severity": self.severity, "kind": self.kind, "file": self.file,
                "line": self.line, "excerpt": self.excerpt}


@dataclass
class ScanReport:
    """扫描结论。"""

    ok: bool
    verdict: str            # ok | warn | reject
    reason: str = ""
    files: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    total_bytes: int = 0

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "high")

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "verdict": self.verdict, "reason": self.reason,
            "files": self.files, "total_bytes": self.total_bytes,
            "findings": [f.to_dict() for f in self.findings],
            "high_count": self.high_count,
            # 界面上必须同时显示这句，避免把 warn 读成"安全"
            "boundary": "扫描是准入闸门，不是安全边界；真正的强制在沙箱层",
        }


def _reject(reason: str, **kw) -> ScanReport:
    return ScanReport(ok=False, verdict="reject", reason=reason, **kw)


def _safe_member_name(name: str) -> str:
    """规范化 ZIP 内成员名，并拒绝路径穿越。"""
    normalized = name.replace("\\", "/").lstrip("/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise SkillPackageError(f"ZIP 内含路径穿越：{name!r}")
    # Windows 盘符形式（C:/...）
    if parts and re.fullmatch(r"[A-Za-z]:", parts[0]):
        raise SkillPackageError(f"ZIP 内含绝对路径：{name!r}")
    return "/".join(parts)


def inspect_zip(data: bytes) -> ScanReport:
    """校验 ZIP 的结构与内容。**不解压到磁盘**（先判定再落地）。"""
    if not data:
        return _reject("空文件")
    if len(data) > MAX_ZIP_BYTES:
        return _reject(f"包过大：{len(data)} 字节 > 上限 {MAX_ZIP_BYTES}")

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return _reject(f"不是合法的 ZIP：{exc}")

    infos = [i for i in archive.infolist() if not i.is_dir()]
    if not infos:
        return _reject("ZIP 内没有文件")
    if len(infos) > MAX_FILES:
        return _reject(f"文件数过多：{len(infos)} > 上限 {MAX_FILES}")

    names: list[str] = []
    findings: list[Finding] = []
    total = 0
    try:
        for info in infos:
            member = _safe_member_name(info.filename)
            if not member:
                continue
            suffix = Path(member).suffix.lower()
            if suffix in REJECTED_SUFFIXES:
                return _reject(f"包含被禁止的文件类型：{member}（{suffix}）")
            if suffix not in ALLOWED_SUFFIXES:
                return _reject(
                    f"文件类型不在允许清单内：{member}（{suffix or '无后缀'}）。"
                    f"只允许纯文本：{'/'.join(sorted(ALLOWED_SUFFIXES))}"
                )
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                return _reject(
                    f"单文件过大：{member} 解压后 {info.file_size} 字节 "
                    f"> 上限 {MAX_SINGLE_FILE_BYTES}（防压缩炸弹）"
                )
            total += info.file_size
            names.append(member)
    except SkillPackageError as exc:
        return _reject(str(exc))

    # 根目录必须有 SKILL.md（不可套一层文件夹）
    if "SKILL.md" not in names:
        nested = [n for n in names if n.endswith("/SKILL.md")]
        hint = f"（发现 {nested[0]}，多套了一层文件夹）" if nested else ""
        return _reject(f"根目录必须直接包含 SKILL.md{hint}", files=names, total_bytes=total)

    # 内容扫描
    for info in infos:
        member = _safe_member_name(info.filename)
        if not member or Path(member).suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        try:
            text = archive.read(info).decode("utf-8", "replace")
        except (OSError, zipfile.BadZipFile):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for pattern, severity, kind in SUSPICIOUS_PATTERNS:
                if re.search(pattern, line):
                    findings.append(Finding(
                        severity=severity, kind=kind, file=member, line=line_no,
                        excerpt=line.strip()[:140],
                    ))

    verdict = "warn" if any(f.severity == "high" for f in findings) else "ok"
    reason = (
        f"扫描到 {sum(1 for f in findings if f.severity == 'high')} 处高危模式，"
        "需人工确认后再安装"
        if verdict == "warn" else ""
    )
    return ScanReport(ok=True, verdict=verdict, reason=reason, files=names,
                      findings=findings, total_bytes=total)


def extract_skill_md(data: bytes) -> str:
    """从 ZIP 里取出根目录的 `SKILL.md` 文本。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if _safe_member_name(info.filename) == "SKILL.md":
                return archive.read(info).decode("utf-8", "replace")
    raise SkillPackageError("ZIP 根目录没有 SKILL.md")


def extract_to(data: bytes, target: Path) -> list[str]:
    """把包解压到 `target`（调用方保证 target 已存在且在数据目录内）。

    **再次逐个校验成员名**——不在 `inspect_zip` 通过后就放松，因为这两个函数
    将来可能被不同路径调用（"检查过的东西不要再信一遍"是本仓反复踩过的坑）。
    """
    written: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member = _safe_member_name(info.filename)
            if not member:
                continue
            destination = (target / member).resolve()
            if target.resolve() not in destination.parents and destination != target.resolve():
                raise SkillPackageError(f"解压目标越出目录：{member}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(info))
            written.append(member)
    return written
