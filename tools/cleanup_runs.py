"""沙箱 `runs/` 保留策略（默认只预览，`--yes` 才删）。

审计发现：脚本式 Skill 每次调用复制整包到 `runs/<id>/package`，从不清理；
一个 10 MB 的包调用 100 次 = 1 GB。默认保留是刻意的（现场证据），但需要显式清理。

用法：
    python tools/cleanup_runs.py                     # 预览（默认 30 天 / 200 个 / 512 MB）
    python tools/cleanup_runs.py --days 7 --yes      # 真删
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workbench import db  # noqa: E402
from workbench.sandbox import layout  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理沙箱 runs/（默认只预览）")
    parser.add_argument("--days", type=int, default=30, help="保留天数（0 = 不按时间删）")
    parser.add_argument("--count", type=int, default=200, help="最多保留多少个 run")
    parser.add_argument("--max-mb", type=int, default=512, help="总字节上限（MB）")
    parser.add_argument("--yes", action="store_true", help="真的删除（默认 dry-run）")
    args = parser.parse_args(argv)

    if args.days < 0 or args.count < 0 or args.max_mb < 0:
        parser.error("--days / --count / --max-mb 必须 ≥ 0")
    db.connect()
    result = layout.cleanup_runs(
        keep_days=args.days,
        keep_count=args.count,
        max_bytes=args.max_mb * 1024 * 1024,
        dry_run=not args.yes,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["dryRun"]:
        print("★ 这只是预览：一条都没删。确认后加 --yes。")
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
