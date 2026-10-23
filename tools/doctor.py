"""`python tools/doctor.py [--live] [--json]`：能力诊断（等价 `/api/diagnostics`）。

默认静态：不启动 MCP、不联网。`--live` 只读内存注册表。
输出只含稳定 code 与脱敏路径，可直接贴进 issue。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workbench import db
from workbench.diagnostics import collect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="akso-workbench 能力诊断")
    parser.add_argument("--live", action="store_true", help="读取内存中的 MCP 注册表状态")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)

    db.connect()
    report = collect(live=args.live)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    summary = report["summary"]
    print(f"diagnostics schema={report['schemaVersion']} "
          f"errors={summary['error']} warnings={summary['warning']} info={summary['info']}")
    for item in report["issues"]:
        print(f"  [{item['severity']:7}] {item['code']}: {item['message']}")
        if item["facts"]:
            print(f"            {json.dumps(item['facts'], ensure_ascii=False)}")
    print("modules:", ", ".join(f"{name}={_brief(value)}" for name, value in report["modules"].items()))
    return 0 if report["ok"] else 1


def _brief(value: dict) -> str:
    keys = ("tools", "servers", "library", "installed", "loggedIn", "mode", "platform")
    return " ".join(f"{key}={value[key]}" for key in keys if key in value) or "ok"


if __name__ == "__main__":
    sys.exit(main())
