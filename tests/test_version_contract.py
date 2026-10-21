"""版本唯一真源契约：`VERSION` 是唯一手改点，其余全是派生副本。

## 为什么需要这一份

2026-10-19 用户定稿：**凡是版本号与显示，先统一变量，以提交 GitHub 升版为主**。
`tools/bump.py` 以前以 `pyproject.toml` 为真源，而 Tauri 还要读
`tauri.conf.json` / `Cargo.toml`；如果继续“每个构建系统相信自己的那份”，
就会出现“装的是 0.4.1、壳显示 0.4.2、更新器以为是 0.4.0”这种**不报错**的漂移。

本文件守三件事：
1. `VERSION` 存在且是 semver；
2. `bump.py check` 在当前仓库为 0，且 stdout 只有版本号；
3. `_write()` 真的把**所有派生副本**写成同一个版本（含 Tauri 两处）；
4. 改动任意一份副本，`check` 必须红。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools import bump

ROOT = Path(__file__).resolve().parent.parent


def test_version_file_is_the_single_source() -> None:
    assert bump.VERSION_FILE == ROOT / "VERSION"
    assert bump.VERSION_FILE.is_file()
    text = bump.VERSION_FILE.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", text), f"VERSION 不是 semver：{text!r}"
    assert bump._read() == tuple(int(part) for part in text.split("."))


def test_bump_check_is_green_and_stdout_is_machine_only() -> None:
    version = bump.VERSION_FILE.read_text(encoding="utf-8").strip()
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bump.py"), "check"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == version, "stdout 必须只有版本号（调用方按行取）"
    assert "FAIL" not in proc.stderr


def _seed_repo(tmp_path: Path) -> dict[str, Path]:
    files = {
        "VERSION_FILE": tmp_path / "VERSION",
        "PYPROJECT": tmp_path / "pyproject.toml",
        "INIT_PY": tmp_path / "workbench" / "__init__.py",
        "UV_LOCK": tmp_path / "uv.lock",
        "VERSION_CACHE": tmp_path / ".version.json",
        "TAURI_CONF": tmp_path / "src-tauri" / "tauri.conf.json",
        "CARGO_TOML": tmp_path / "src-tauri" / "Cargo.toml",
    }
    for key in ("INIT_PY", "TAURI_CONF"):
        files[key].parent.mkdir(parents=True, exist_ok=True)

    files["VERSION_FILE"].write_text("0.4.1\n", encoding="utf-8")
    files["PYPROJECT"].write_text('[project]\nversion = "0.4.1"\n', encoding="utf-8")
    files["INIT_PY"].write_text('__version__ = "0.4.1"\n', encoding="utf-8")
    files["UV_LOCK"].write_text(
        '[[package]]\nname = "workbench"\nversion = "0.4.1"\n', encoding="utf-8")
    files["VERSION_CACHE"].write_text('{"version": "0.4.1"}', encoding="utf-8")
    files["TAURI_CONF"].write_text('{\n  "version": "0.4.1"\n}\n', encoding="utf-8")
    files["CARGO_TOML"].write_text(
        '[package]\nname = "workbench"\nversion = "0.4.1"\n\n[dependencies]\ntauri = "2"\n',
        encoding="utf-8",
    )
    return files


@pytest.mark.parametrize("key", [
    "PYPROJECT", "INIT_PY", "UV_LOCK", "VERSION_CACHE", "TAURI_CONF", "CARGO_TOML",
])
def test_bump_writes_every_derived_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str,
) -> None:
    files = _seed_repo(tmp_path)
    for name, path in files.items():
        monkeypatch.setattr(bump, name, path)

    bump._write(0, 5, 0)

    assert files["VERSION_FILE"].read_text(encoding="utf-8").strip() == "0.5.0"
    assert 'version = "0.5.0"' in files["PYPROJECT"].read_text(encoding="utf-8")
    assert '__version__ = "0.5.0"' in files["INIT_PY"].read_text(encoding="utf-8")
    assert 'version = "0.5.0"' in files["UV_LOCK"].read_text(encoding="utf-8")
    assert json.loads(files["VERSION_CACHE"].read_text(encoding="utf-8"))["version"] == "0.5.0"
    assert '"version": "0.5.0"' in files["TAURI_CONF"].read_text(encoding="utf-8")
    cargo = files["CARGO_TOML"].read_text(encoding="utf-8")
    assert 'name = "workbench"\nversion = "0.5.0"' in cargo
    assert 'tauri = "2"' in cargo


def test_bump_check_detects_a_single_stale_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    files = _seed_repo(tmp_path)
    for name, path in files.items():
        monkeypatch.setattr(bump, name, path)

    files["INIT_PY"].write_text('__version__ = "0.4.0"\n', encoding="utf-8")
    rc = bump._check()
    captured = capsys.readouterr()
    assert rc == 1
    assert "FAIL" in captured.err
    assert "0.4.0" in captured.err


@pytest.mark.parametrize(("action", "expected"), [
    ("push", "0.4.2"),    # 优化版：PATCH +1
    ("build", "0.5.0"),   # 正式版：MINOR +1，PATCH 归零
])
def test_bump_actions_follow_the_release_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, expected: str,
) -> None:
    files = _seed_repo(tmp_path)
    for name, path in files.items():
        monkeypatch.setattr(bump, name, path)
    monkeypatch.setattr(sys, "argv", ["bump.py", action])

    assert bump.main() == 0
    assert files["VERSION_FILE"].read_text(encoding="utf-8").strip() == expected

