"""Tauri updater 清单生成契约（内部 HTTPS 静态源）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import tauri_update_manifest as tm


def _artifact(tmp_path: Path, version: str = "0.4.1") -> tuple[Path, Path]:
    exe = tmp_path / f"Workbench_{version}_x64-setup.exe"
    exe.write_bytes(b"fake installer")
    sig = Path(str(exe) + ".sig")
    sig.write_text("SIGNATURE-CONTENT\n", encoding="utf-8")
    return exe, sig


def test_manifest_contains_windows_platform_and_signature_content(tmp_path: Path) -> None:
    exe, sig = _artifact(tmp_path)
    manifest = tm.build_manifest(
        version="0.4.1",
        artifact=exe,
        signature_file=sig,
        base_url="https://updates.example.com/workbench/0.4.1/",
        notes="修复 A",
        pub_date="2026-10-19T10:00:00Z",
    )
    assert manifest["version"] == "0.4.1"
    assert manifest["notes"] == "修复 A"
    assert manifest["pub_date"] == "2026-10-19T10:00:00Z"
    platform = manifest["platforms"]["windows-x86_64"]
    assert platform["signature"] == "SIGNATURE-CONTENT"
    assert platform["url"] == (
        "https://updates.example.com/workbench/0.4.1/Workbench_0.4.1_x64-setup.exe"
    )


def test_manifest_rejects_plain_http(tmp_path: Path) -> None:
    exe, sig = _artifact(tmp_path)
    with pytest.raises(tm.ManifestError, match="HTTPS"):
        tm.build_manifest(
            version="0.4.1",
            artifact=exe,
            signature_file=sig,
            base_url="http://updates.example.com/workbench",
        )


def test_manifest_rejects_artifact_with_wrong_version(tmp_path: Path) -> None:
    exe, sig = _artifact(tmp_path, version="0.4.0")
    with pytest.raises(tm.ManifestError, match="版本"):
        tm.build_manifest(
            version="0.4.1",
            artifact=exe,
            signature_file=sig,
            base_url="https://updates.example.com/workbench",
        )


def test_manifest_rejects_missing_signature(tmp_path: Path) -> None:
    exe, _ = _artifact(tmp_path)
    with pytest.raises(tm.ManifestError, match="签名文件不存在"):
        tm.build_manifest(
            version="0.4.1",
            artifact=exe,
            signature_file=tmp_path / "missing.sig",
            base_url="https://updates.example.com/workbench",
        )


def test_cli_writes_manifest(tmp_path: Path) -> None:
    exe, _ = _artifact(tmp_path)
    out = tmp_path / "latest.json"
    rc = tm.main([
        "--version", "0.4.1",
        "--artifact", str(exe),
        "--base-url", "https://updates.example.com/workbench/0.4.1",
        "--notes", "test",
        "--pub-date", "2026-10-19T10:00:00Z",
        "--output", str(out),
    ])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["platforms"]["windows-x86_64"]["signature"] == "SIGNATURE-CONTENT"
