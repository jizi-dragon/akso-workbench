"""Tauri 壳的静态契约：配置、版本、资源与更新源。

真正的 `tauri build` 在 CI 里需要 Rust/NSIS/WebView2，太重；这里把“构建前就能发现”的
错配挡在 pytest 里：
- `tauri.conf.json` 的 version 必须与 `VERSION` 一致（bump 会写，但手改会漂）；
- identifier 不能以 `.app` 结尾（Tauri 会警告，macOS/NSIS 语义也容易混）；
- updater endpoint 必须是 HTTPS；
- resources 必须指向可构建的 sidecar 目录（当前由 `tools/build_tauri.ps1` 生成 `dist/WorkbenchServer`）；
- `Cargo.toml` 版本与关键插件依赖必须在。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
CONF = TAURI / "tauri.conf.json"
CARGO = TAURI / "Cargo.toml"


def _conf() -> dict:
    return json.loads(CONF.read_text(encoding="utf-8"))


def test_tauri_version_matches_version_file() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert _conf()["version"] == version
    cargo = CARGO.read_text(encoding="utf-8")
    assert f'version = "{version}"' in cargo


def test_identifier_is_not_app_suffix_and_is_stable() -> None:
    identifier = _conf()["identifier"]
    assert identifier == "com.workbench.desktop"
    assert not identifier.endswith(".app"), "Tauri 会警告 identifier 以 .app 结尾"


def test_updater_endpoint_is_https_and_pubkey_present() -> None:
    updater = _conf()["plugins"]["updater"]
    assert updater["pubkey"].strip(), "缺少 updater 公钥"
    endpoints = updater["endpoints"]
    assert endpoints and all(url.startswith("https://") for url in endpoints)


def test_bundle_is_nsis_per_user_and_resources_are_declared() -> None:
    bundle = _conf()["bundle"]
    assert bundle["targets"] == ["nsis"]
    assert bundle["createUpdaterArtifacts"] is True
    assert bundle["windows"]["nsis"]["installMode"] == "currentUser"
    assert "../dist/WorkbenchServer" in bundle["resources"]
    assert (TAURI / "ui" / "index.html").is_file()
    assert (TAURI / "icons" / "icon.ico").is_file()


def test_cargo_has_sidecar_relevant_plugins() -> None:
    cargo = CARGO.read_text(encoding="utf-8")
    for crate in ("tauri-plugin-updater", "tauri-plugin-single-instance", "tauri-plugin-opener"):
        assert crate in cargo
    main = (TAURI / "src" / "main.rs").read_text(encoding="utf-8")
    assert "WORKBENCH_PARENT_PID" in main
    assert "wait_health" in main
    assert "on_new_window" in main
