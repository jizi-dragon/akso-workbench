"""版本号演进工具（用户 2026-10-21 定稿）：

- push（优化版）   → PATCH +1          0.4.1 → 0.4.2
- build（正式版）  → MINOR +1 且 PATCH 归零   0.4.1 → 0.5.0

**版本唯一真源 = 仓库根目录的 `VERSION`**（单行 `major.minor.patch`）。
`pyproject.toml`、`workbench/__init__.py`、`uv.lock`、`.version.json` 以及 Tauri 的
`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml` 都是**派生副本**，由本工具同步写入。
**任何一处落下，都会让“装的是哪一版”说不清。**

用法：
    python tools/bump.py push    # 优化版：只升 PATCH，不打包发布
    python tools/bump.py build   # 正式版：MINOR+1、PATCH 归零，由 build_tauri.ps1 调用
    python tools/bump.py show    # 显示当前版本
    python tools/bump.py check   # 只读核对 VERSION 与所有派生副本（不同步则 exit 1）

⚠ **stdout 是机器接口**：成功路径上 stdout 只有版本号一行，诊断一律走 stderr。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "workbench" / "__init__.py"
UV_LOCK = ROOT / "uv.lock"
VERSION_CACHE = ROOT / ".version.json"
TAURI_CONF = ROOT / "src-tauri" / "tauri.conf.json"
CARGO_TOML = ROOT / "src-tauri" / "Cargo.toml"

# VERSION = 真源；以下为派生副本：
# pyproject.toml / workbench/__init__.py / uv.lock / .version.json /
# src-tauri/tauri.conf.json / src-tauri/Cargo.toml。
# > 2026-10-09：原先还有浏览器扩展的 manifest.json 与 package.json；扩展已移出本仓库。
# > 2026-10-19：补上 uv.lock（实测漂移过）。
# > 2026-10-19：真源从 pyproject.toml 改为 VERSION；所有副本由 bump 统一写。
# > 2026-10-19：Electron 退役，desktop/package*.json 不再是副本。
_UV_PROJECT_RE = re.compile(r'(\[\[package\]\]\nname = "workbench"\nversion = ")[^"]*(")')
_CARGO_VERSION_RE = re.compile(r'(?m)^(version\s*=\s*")([^"]*)(")')

# ⚠ **stdout 只允许出现版本号本身**（成功路径一行 `major.minor.patch`）。
# 调用方（tools/build_tauri.ps1 第 1 步）用 `(& python tools\bump.py build).Trim()` 取版本，
# 凡是往 stdout 打的**别的任何东西**都会被当成版本号一路流到产物命名与扩展校验
# （实测形态：`Workbench-XXXX 中未找到 version 字段-setup.exe`）。
# 故一切诊断/错误信息一律走 stderr。
def _die(msg: str) -> SystemExit:
    print(msg, file=sys.stderr)
    return SystemExit(msg)


def _read() -> tuple[int, int, int]:
    if not VERSION_FILE.exists():
        raise _die("缺少 VERSION 文件（版本唯一真源）")
    text = VERSION_FILE.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        raise _die(f"VERSION 内容不是 semver：{text!r}")
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def _write_tauri(version: str) -> None:
    """同步 Tauri 的版本副本；工程缺失说明仓库不完整，直接报错。"""
    if not TAURI_CONF.exists() or not CARGO_TOML.exists():
        raise _die("缺少 src-tauri/tauri.conf.json 或 Cargo.toml（Tauri 工程不完整）")
    conf = TAURI_CONF.read_text(encoding="utf-8")
    conf, n = re.subn(
        r'^(\s*"version"\s*:\s*")[^"]*(")', rf"\g<1>{version}\g<2>",
        conf, count=1, flags=re.M,
    )
    if n != 1:
        raise _die("src-tauri/tauri.conf.json 中未找到唯一的 version 字段")
    TAURI_CONF.write_text(conf, encoding="utf-8", newline="\n")

    cargo = CARGO_TOML.read_text(encoding="utf-8")
    # 只改 [package] 段内的第一处 version，避免碰到依赖版本。
    head, sep, tail = cargo.partition("[dependencies]")
    head, n = _CARGO_VERSION_RE.subn(rf"\g<1>{version}\g<3>", head, count=1)
    if n != 1:
        raise _die("src-tauri/Cargo.toml 的 [package] 段中未找到 version")
    CARGO_TOML.write_text(head + sep + tail, encoding="utf-8", newline="\n")


def _write(major: int, minor: int, patch: int) -> None:
    version = f"{major}.{minor}.{patch}"
    VERSION_FILE.write_text(version + "\n", encoding="utf-8", newline="\n")

    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(r'^(version\s*=\s*")\d+\.\d+\.\d+(")', rf"\g<1>{version}\g<2>", text, flags=re.M)
    PYPROJECT.write_text(text, encoding="utf-8", newline="\n")

    init_text = INIT_PY.read_text(encoding="utf-8")
    init_text = re.sub(r'^__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', init_text, flags=re.M)
    INIT_PY.write_text(init_text, encoding="utf-8", newline="\n")

    # uv.lock 里本项目那一条（`[[package]] name = "workbench"`）。只改版本号，
    # 其余由 `uv lock` 负责；版本号是唯一会随 bump 变化的部分。
    if UV_LOCK.exists():
        uv_text = UV_LOCK.read_text(encoding="utf-8")
        uv_text, n = _UV_PROJECT_RE.subn(rf"\g<1>{version}\g<2>", uv_text)
        if n != 1:
            raise _die("uv.lock 中未找到唯一的 workbench 包条目")
        UV_LOCK.write_text(uv_text, encoding="utf-8", newline="\n")

    VERSION_CACHE.write_text(json.dumps({"version": version}), encoding="utf-8")
    _write_tauri(version)


def _uv_project_version(text: str) -> str | None:
    match = re.search(r'\[\[package\]\]\nname = "workbench"\nversion = "([^"]+)"', text)
    return match.group(1) if match else None


def _tauri_versions() -> list[tuple[str, str]]:
    """Tauri 版本副本（Electron 退役后这是唯一的桌面壳）。"""
    checks: list[tuple[str, str]] = []
    if TAURI_CONF.exists():
        match = re.search(r'^\s*"version"\s*:\s*"([^"]*)"', TAURI_CONF.read_text(encoding="utf-8"), re.M)
        checks.append(("src-tauri/tauri.conf.json", match.group(1) if match else "<未找到>"))
    if CARGO_TOML.exists():
        head = CARGO_TOML.read_text(encoding="utf-8").partition("[dependencies]")[0]
        match = _CARGO_VERSION_RE.search(head)
        checks.append(("src-tauri/Cargo.toml", match.group(2) if match else "<未找到>"))
    return checks


def _check() -> int:
    """核对 VERSION 与所有派生副本（只读，不改任何文件）。

    为什么不靠人记：手改单一文件是**已发生过**的事故形态（安装包命名与产物校验错位），
    而 2026-10-19 又实测到 `uv.lock` 停在 0.3.13 这类**不会自己报错**的漂移。
    本命令把“VERSION 与副本同步”变成一条可机检的断言。
    """
    major, minor, patch = _read()
    version = f"{major}.{minor}.{patch}"
    checks: list[tuple[str, str]] = [("VERSION", VERSION_FILE.read_text(encoding="utf-8").strip())]

    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]*)"', text, re.M)
    checks.append(("pyproject.toml", match.group(1) if match else "<未找到>"))

    init_match = re.search(r'^__version__\s*=\s*"([^"]*)"', INIT_PY.read_text(encoding="utf-8"), re.M)
    checks.append(("workbench/__init__.py", init_match.group(1) if init_match else "<未找到>"))

    if UV_LOCK.exists():
        checks.append(("uv.lock", _uv_project_version(UV_LOCK.read_text(encoding="utf-8")) or "<未找到>"))
    if VERSION_CACHE.exists():
        try:
            checks.append((".version.json", str(json.loads(VERSION_CACHE.read_text(encoding="utf-8"))["version"])))
        except Exception:  # noqa: BLE001
            checks.append((".version.json", "<不可解析>"))
    checks.extend(_tauri_versions())

    bad = [(name, got) for name, got in checks if got != version]
    for name, got in checks:
        print(f"  {'OK  ' if got == version else 'FAIL'} {name} = {got}", file=sys.stderr)
    if bad:
        print(f"版本不同步：VERSION = {version}，失配 {len(bad)} 处", file=sys.stderr)
        return 1
    print(version)
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "show"
    if action == "check":
        return _check()
    major, minor, patch = _read()
    if action == "show":
        print(f"{major}.{minor}.{patch}")
        return 0
    if action == "push":
        patch += 1
    elif action == "build":
        minor += 1
        patch = 0
    else:
        print(f"未知动作：{action}（push / build / show）", file=sys.stderr)
        return 1
    _write(major, minor, patch)
    print(f"{major}.{minor}.{patch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())