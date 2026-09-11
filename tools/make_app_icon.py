"""生成桌面端应用图标 desktop/icon.ico（托盘 + NSIS 安装包 + exe 资源共用）。

背景（2026-09-11 实测）：仓库里的 `desktop/icon.ico` 自首次提交起就是**坏文件**——
它原本是有效 ICO，但整份二进制被"当文本另存为 Unicode"了一次（UTF-16LE BOM + 每字节占 2 字节），
且该过程有损（无法还原）。后果有两个，都是静默的：
  1. `electron-builder` 报 `image desktop/icon.ico shas unknown format` → **构建直接失败**；
  2. Electron 托盘 `new Tray(路径)` 加载失败 → 托盘图标一直是空白（main.js 里 try/catch 吞掉了）。

素材：扩展的品牌图标 `extensions/quick-login/assets/Icon{16,48,128}.png`（最大 128）。
本工具以 128 为源，LANCZOS 重采样生成 16/24/32/48/64/128/256 七个尺寸的 ICO——
Windows 会按场合自取合适尺寸（任务栏/资源管理器/安装包向导各不同）。

用法（需要 Pillow，仅生成时需要，运行时不依赖）：
    .venv\\Scripts\\python.exe -m pip install pillow
    .venv\\Scripts\\python.exe tools\\make_app_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "extensions" / "quick-login" / "assets" / "Icon128.png"
OUT = ROOT / "desktop" / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("缺少 Pillow：先执行 .venv\\Scripts\\python.exe -m pip install pillow")
        return 2

    if not SRC.is_file():
        print(f"找不到源图：{SRC}")
        return 2

    src = Image.open(SRC).convert("RGBA")
    if src.size != (128, 128):
        print(f"警告：源图尺寸 {src.size}（预期 128x128），将按其实际尺寸重采样")

    # ⚠ Pillow 的 ICO 写出**不会放大**：请求尺寸大于源图时该条目被静默跳过。
    # 而 electron-builder 要求 Windows 图标至少 256x256 → 先 LANCZOS 放大到 256 作基准，
    # 再一次性写出全部尺寸（128 及以下仍是原生清晰度，256 为放大档，仅用于大图标场合）。
    base = src.resize((256, 256), Image.LANCZOS)
    base.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    size = OUT.stat().st_size
    head = OUT.read_bytes()[:6]
    ok = head[:4] == b"\x00\x00\x01\x00"
    print(f"已生成 {OUT.relative_to(ROOT)}：{size} 字节，ICO 头 = {list(head)} → {'有效' if ok else '无效'}")
    if ok:
        import struct

        count = struct.unpack("<H", head[4:6])[0]
        got = sorted({struct.unpack("<BBBBHHII", OUT.read_bytes()[6 + i * 16 : 22 + i * 16])[0] or 256 for i in range(count)})
        print(f"  含尺寸：{got}（需含 256 才满足 electron-builder 要求）")
        if 256 not in got:
            print("  ✗ 缺 256 条目——electron-builder 会拒绝该图标")
            return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
