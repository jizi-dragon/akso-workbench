"""SBOM 判据：uv.lock 的每个包都进组件清单，且输出可重复。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import sbom  # noqa: E402


def test_real_lock_produces_components() -> None:
    bom = sbom.build()
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.5"
    assert len(bom["components"]) >= 10
    names = {item["name"] for item in bom["components"]}
    assert "fastapi" in names and "pydantic" in names
    assert all(item["purl"].startswith("pkg:pypi/") for item in bom["components"])


def test_output_is_deterministic() -> None:
    first = sbom.build()
    second = sbom.build()
    assert first == second
    assert first["metadata"]["properties"][0]["value"] == second["metadata"]["properties"][0]["value"]


def test_fake_lock_and_cli(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(
        '[[package]]\nname = "alpha"\nversion = "1.0"\n\n'
        '[[package]]\nname = "beta"\nversion = "2.0"\n', encoding="utf-8")
    bom = sbom.build(lock, version="9.9.9")
    assert [c["name"] for c in bom["components"]] == ["alpha", "beta"]
    assert bom["metadata"]["component"]["version"] == "9.9.9"

    out = tmp_path / "sbom.json"
    assert sbom.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["components"]
