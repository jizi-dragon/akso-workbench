"""版本号演进工具（规则 A，用户定稿）：

- push（代码修改推送）  → PATCH +1          0.0.1 → 0.0.2
- build（构建安装包）   → MINOR +1 且 PATCH 重置 1   0.0.2 → 0.1.1 → 0.2.1

版本唯一真源 = pyproject.toml [project].version；
workbench/__init__.py 的 __version__、desktop/package.json 的 version、
扩展 manifest.json 与 package.json 的 version 由本工具同步写入
（五写机制——electron-builder 与扩展弹窗可见版本均随项目演进）。
.version.json 为本地缓存。

用法：
    python tools/bump.py push    # 推送前手动执行（或由 pre-push 钩子调用）
    python tools/bump.py build   # 由 tools/build.ps1 在构建开头自动执行
    python tools/bump.py show    # 显示当前版本
    python tools/bump.py check    # 只读核对五写是否同步（不同步则 exit 1）

⚠ **stdout 是机器接口**：成功路径上 stdout 只有版本号一行，诊断一律走 stderr。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT_PY = ROOT / "workbench" / "__init__.py"
DESKTOP_PKG = ROOT / "desktop" / "package.json"
EXT_MANIFEST = ROOT / "extensions" / "quick-login" / "packages" / "extension" / "manifest.json"
EXT_PACKAGE = ROOT / "extensions" / "quick-login" / "package.json"
VERSION_CACHE = ROOT / ".version.json"

# ⚠ **stdout 只允许出现版本号本身**（成功路径一行 `major.minor.patch`）。
# 调用方（tools/build.ps1 第 1 步）用 `(& python tools\bump.py build).Trim()` 取版本，
# 凡是往 stdout 打的**别的任何东西**都会被当成版本号一路流到产物命名与扩展校验
# （实测形态：`Workbench-XXXX 中未找到 version 字段-setup.exe`）。
# 故一切诊断/错误信息一律走 stderr。
def _die(msg: str) -> SystemExit:
    print(msg, file=sys.stderr)
    return SystemExit(msg)


def _read() -> tuple[int, int, int]:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text, re.M)
    if not match:
        raise _die("pyproject.toml 中未找到 version 字段")
    return tuple(int(x) for x in match.groups())  # type: ignore[return-value]


def _write(major: int, minor: int, patch: int) -> None:
    version = f"{major}.{minor}.{patch}"
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(r'^(version\s*=\s*")\d+\.\d+\.\d+(")', rf"\g<1>{version}\g<2>", text, flags=re.M)
    PYPROJECT.write_text(text, encoding="utf-8", newline="\n")

    init_text = INIT_PY.read_text(encoding="utf-8")
    init_text = re.sub(r'^__version__\s*=\s*"[^"]*"', f'__version__ = "{version}"', init_text, flags=re.M)
    INIT_PY.write_text(init_text, encoding="utf-8", newline="\n")

    if DESKTOP_PKG.exists():
        pkg_text = DESKTOP_PKG.read_text(encoding="utf-8")
        pkg_text, n = re.subn(
            r'^(\s*"version"\s*:\s*")[^"]*(")', rf"\g<1>{version}\g<2>", pkg_text, count=1, flags=re.M
        )
        if n != 1:
            raise _die("desktop/package.json 中未找到唯一的 version 字段")
        DESKTOP_PKG.write_text(pkg_text, encoding="utf-8", newline="\n")

    # 扩展版本与项目同步（用户定稿）：popup/管理页可见版本随项目演进。
    # ⚠ 这两个文件**必须存在**：旧实现是 `if ext_file.exists()` 静默跳过，
    # 于是"扩展 manifest 没跟上项目版本"会以**成功**的姿态过去——而安装包会带着旧扩展，
    # 用户看到的版本与桌面端不一致（build.ps1 第 2 步的版本一致性校验正是防这个）。
    for ext_file in (EXT_MANIFEST, EXT_PACKAGE):
        if not ext_file.exists():
            raise _die(f"缺少 {ext_file.relative_to(ROOT)}——扩展版本无法与项目同步")
        text = ext_file.read_text(encoding="utf-8")
        text, n = re.subn(
            r'("version"\s*:\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1
        )
        if n != 1:
            raise _die(f"{ext_file.name} 中未找到 version 字段")
        ext_file.write_text(text, encoding="utf-8", newline="\n")

    VERSION_CACHE.write_text(json.dumps({"version": version}), encoding="utf-8")


def _check() -> int:
    """核对五写是否已同步到 `pyproject.toml` 的版本（只读，不改任何文件）。

    为什么不靠人记：手改单一文件是**已发生过**的事故形态（安装包命名与产物校验错位）。
    本命令把"五写同步"变成一条可机检的断言，供发版前与 CI 调用。
    """
    major, minor, patch = _read()
    version = f"{major}.{minor}.{patch}"
    checks: list[tuple[str, str]] = []

    init_match = re.search(r'^__version__\s*=\s*"([^"]*)"', INIT_PY.read_text(encoding="utf-8"), re.M)
    checks.append(("workbench/__init__.py", init_match.group(1) if init_match else "<未找到>"))

    if DESKTOP_PKG.exists():
        pk_match = re.search(r'^\s*"version"\s*:\s*"([^"]*)"', DESKTOP_PKG.read_text(encoding="utf-8"), re.M)
        checks.append(("desktop/package.json", pk_match.group(1) if pk_match else "<未找到>"))
    for ext_file in (EXT_MANIFEST, EXT_PACKAGE):
        if ext_file.exists():
            m = re.search(r'"version"\s*:\s*"([^"]*)"', ext_file.read_text(encoding="utf-8"))
            checks.append((str(ext_file.relative_to(ROOT)), m.group(1) if m else "<未找到>"))
    if VERSION_CACHE.exists():
        try:
            checks.append((".version.json", str(json.loads(VERSION_CACHE.read_text(encoding="utf-8"))["version"])))
        except Exception:  # noqa: BLE001
            checks.append((".version.json", "<不可解析>"))

    bad = [(name, got) for name, got in checks if got != version]
    for name, got in checks:
        print(f"  {'OK  ' if got == version else 'FAIL'} {name} = {got}", file=sys.stderr)
    if bad:
        print(f"版本五写不同步：pyproject.toml = {version}，失配 {len(bad)} 处", file=sys.stderr)
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
        patch = 1
    else:
        print(f"未知动作：{action}（push / build / show）", file=sys.stderr)
        return 1
    _write(major, minor, patch)
    print(f"{major}.{minor}.{patch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
