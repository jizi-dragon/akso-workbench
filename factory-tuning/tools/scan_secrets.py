"""提交前兜底扫描：仓库里不许有**令牌形状**的串。

## 为什么还要这一层

`ingest.py` 已经在落盘前脱敏了。这一层是**兜底**，防的是三类意外：

1. 有人手工把一份原始 `monitor-log.json` 拷进了仓库；
2. `redact.py` 的正则漏了某个新形态（平台换了令牌格式）；
3. 采集时把别的东西（例如一个含 token 的 curl 命令）一起写进了 data/。

**扫描规则与脱敏规则共用同一组正则**（`redact.py::find_secrets`）——
这是刻意的：如果两套规则各写一份，就会出现"脱敏漏掉的形状、扫描也认不出来"
这种最坏的组合，而那时你会以为扫过了就安全。

## 用法

    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\scan_secrets.py
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\scan_secrets.py factory-tuning/data

退出码：`0` = 干净；`1` = 扫到了（**不要提交**，先按提示回去重采）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from redact import find_secrets  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 只扫这些后缀。二进制/大文件没必要，也不该被当成文本读进来。
#:
#: ⚠️ `.py` **曾经不在这个集合里**，而 `ALLOWLIST` 里有两条 `.py`——于是那两条豁免
#: 永远用不上（死条目），Python 源码成了一个**没人看过的盲区**：往 `.py` 里硬编码一个
#: token，这层兜底完全看不见。2026-09-21 补上。见 docs/PITFALLS.md #66。
#: **教训**：豁免清单必须与扫描范围交叉验证——豁免一个"根本不在扫描范围内的东西"，
#: 给人的感觉是"这块我管过了"，而事实是"这块从来没被看过"。
SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".log", ".csv", ".yaml", ".yml", ".py"}

#: 本目录里**允许**出现令牌形状的串：它们是讲规则的文档与代码本身
#: （比如 README 里写着"JWT 形如 eyJ…"）。
#: 刻意用**精确路径**而不是目录豁免——豁免一个目录，就等于那个目录里以后放什么都不会被扫到。
ALLOWLIST = {
    "factory-tuning/tools/redact.py",
    "factory-tuning/README.md",
    "factory-tuning/tools/scan_secrets.py",
    # 自检里那三个常量（`JWT` / `FERNET` / `TOKEN_URLSAFE`）是**故意的测试向量**：
    # A6–A8 正是靠它们断言"该抓的抓得住"。补上 `.py` 后它们会被扫到（实测共 3 处，
    # 全部集中在这一个文件），所以按**文件**豁免，而不是把 `.py` 从 SUFFIXES 里拿掉
    # ——后者等于为了躲 3 个假阳性，重新把整个源码面变成盲区。
    "factory-tuning/tests/selfcheck.py",
}


def scan(path: pathlib.Path) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for file in sorted(path.rglob("*")):
        if not file.is_file() or file.suffix.lower() not in SUFFIXES:
            continue
        rel = file.relative_to(ROOT).as_posix() if file.is_relative_to(ROOT) else str(file)
        if rel in ALLOWLIST:
            continue
        if any(part in {".venv", "node_modules", "__pycache__"} for part in file.parts):
            continue
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.count("\ufffd") > 0:
            # 读出来就有替换字符 = 多半不是文本，跳过（而不是把二进制噪声当命中）
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for hit in find_secrets(line, limit=1):
                hits.append((rel, line_no, hit))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描仓库里是否有令牌形状的串")
    parser.add_argument("paths", nargs="*", help="要扫的路径（默认 factory-tuning/）")
    args = parser.parse_args(argv)

    targets = [pathlib.Path(p) for p in args.paths] or [ROOT / "factory-tuning"]
    hits: list[tuple[str, int, str]] = []
    for target in targets:
        if not target.exists():
            print(f"✗ 路径不存在：{target}")
            return 1
        hits += scan(target)

    if hits:
        print(f"✗ 扫到 {len(hits)} 处疑似凭据——**不要提交**：\n")
        for rel, line_no, sample in hits[:40]:
            print(f"  {rel}:{line_no}  {sample}")
        print("\n处置：删掉这些数据，回到采集那一步重新 ingest（脱敏在落盘时做）。")
        print("      若确认是误报（比如文档里在讲令牌长什么样），"
              "把它加进 scan_secrets.py 的 ALLOWLIST——**按文件加，不要按目录加**。")
        return 1

    print(f"✓ 干净：扫了 {len(targets)} 个路径，没有令牌形状的串")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
