"""生成 Tauri updater 的静态 `latest.json`（内部 HTTPS 更新源）。

Tauri updater 的签名校验不可关闭：
- 安装包由 CI 用 `TAURI_SIGNING_PRIVATE_KEY` 签名，产物是 `*.exe.sig`；
- `latest.json` 的 `signature` 字段必须是 **.sig 文件内容**（不是路径）；
- `url` 必须指向同一个安装包的完整 HTTPS 地址。

本工具只做“机械拼接 + 前置校验”，不接触私钥，也不上传：
    python tools/tauri_update_manifest.py \
        --version 0.4.1 \
        --artifact src-tauri/target/release/bundle/nsis/Workbench_0.4.1_x64-setup.exe \
        --base-url https://updates.example.com/workbench/0.4.1 \
        --notes "..." \
        --output src-tauri/target/release/bundle/nsis/latest.json

建议发布顺序：先上传安装包与 `.sig`（不可变路径），最后原子替换 `latest.json`。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ManifestError(ValueError):
    pass


def read_version() -> str:
    text = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not text:
        raise ManifestError("VERSION 为空")
    return text


def build_manifest(
    *,
    version: str,
    artifact: Path,
    signature_file: Path,
    base_url: str,
    notes: str = "",
    pub_date: str | None = None,
) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version or ""):
        raise ManifestError(f"版本号非法：{version!r}")
    if not artifact.is_file():
        raise ManifestError(f"安装包不存在：{artifact}")
    if not signature_file.is_file():
        raise ManifestError(f"签名文件不存在：{signature_file}")
    if f"_{version}_" not in artifact.name:
        raise ManifestError(
            f"安装包文件名没有包含版本 {version}：{artifact.name}（防止把旧包配新版本号）"
        )
    if not base_url.lower().startswith("https://"):
        raise ManifestError("更新源必须是 HTTPS（Tauri 生产更新不接受明文 HTTP）")
    signature = signature_file.read_text(encoding="utf-8").strip()
    if not signature:
        raise ManifestError("签名文件为空")

    base = base_url.rstrip("/")
    url = f"{base}/{artifact.name}"
    when = pub_date or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "version": version,
        "notes": notes,
        "pub_date": when,
        "platforms": {
            "windows-x86_64": {
                "signature": signature,
                "url": url,
            }
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Tauri updater latest.json")
    parser.add_argument("--version", default="", help="默认读仓库 VERSION")
    parser.add_argument("--artifact", required=True, help="NSIS 安装包路径")
    parser.add_argument("--signature", default="", help=".sig 路径；默认 <artifact>.sig")
    parser.add_argument("--base-url", required=True, help="安装包所在目录的完整 HTTPS 地址")
    parser.add_argument("--notes", default="", help="更新说明")
    parser.add_argument("--pub-date", default="", help="RFC3339 时间；默认当前 UTC")
    parser.add_argument("--output", required=True, help="输出 latest.json 路径")
    args = parser.parse_args(argv)

    try:
        version = args.version.strip() or read_version()
        artifact = Path(args.artifact)
        signature = Path(args.signature) if args.signature else Path(str(artifact) + ".sig")
        manifest = build_manifest(
            version=version,
            artifact=artifact,
            signature_file=signature,
            base_url=args.base_url,
            notes=args.notes,
            pub_date=args.pub_date.strip() or None,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, ManifestError) as exc:
        print(f"生成 latest.json 失败：{exc}", file=sys.stderr)
        return 1
    print(f"已写入 {output}（version={version}，platform=windows-x86_64）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
