"""生成 CycloneDX SBOM（`uv.lock` → `sbom.cdx.json`）。

## 为什么是 SBOM 而不是"再列一遍依赖"

`uv.lock` 是**构建真源**，但它是 uv 的格式、没有哈希清单，也没有产品组件标识。
SBOM 是交付物的一部分：出事时能回答"这一版到底带了哪些库、哪个版本"。
这里只做**离线、可重复**的生成（不查漏洞库、不联网）。

用法：
    python tools/sbom.py [--out tmp/sbom.cdx.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "uv.lock"
VERSION_FILE = ROOT / "VERSION"


def _version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def build(lock_path: Path = LOCK, *, version: str | None = None) -> dict:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = data.get("package") or []
    if not isinstance(packages, list):
        raise ValueError("uv.lock 的 package 不是数组")
    components: list[dict] = []
    for pkg in sorted(packages, key=lambda item: str(item.get("name", "")).lower()):
        name = str(pkg.get("name") or "").strip()
        ver = str(pkg.get("version") or "").strip()
        if not name or not ver:
            continue
        components.append({
            "type": "library",
            "name": name,
            "version": ver,
            "purl": f"pkg:pypi/{name}@{ver}",
        })
    digest = hashlib.sha256(json.dumps(components, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "akso-workbench",
                "version": version or _version(),
            },
            "properties": [{"name": "workbench:componentsDigest", "value": digest}],
            "tools": [{"vendor": "akso-workbench", "name": "tools/sbom.py"}],
        },
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 CycloneDX SBOM")
    parser.add_argument("--out", default="tmp/sbom.cdx.json", help="输出路径")
    args = parser.parse_args(argv)
    bom = build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"SBOM: {len(bom['components'])} components → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
