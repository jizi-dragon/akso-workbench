"""一次性迁移脚本：从三个原项目的 env 文件导入统一账号库（原文件只读不动）。

用法：
    .venv/Scripts/python tools/import_accounts.py [--dry-run]

对应计划 §2A 2.5；服务端同款能力在 POST /api/accounts/import。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workbench import config, db  # noqa: E402
from workbench.services import accounts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="原项目 env → 统一账号库 导入")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    args = parser.parse_args()

    config.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    db.connect()
    for env in accounts.scan_original_env_files():
        state = "找到" if env["exists"] else "未找到"
        print(f"[{env['module']}] {env['path']}：{state}，{len(env['envs'])} 个环境")
    print()
    for line in accounts.import_from_original_projects(dry_run=args.dry_run):
        print(line["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
