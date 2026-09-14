"""把真实 eGMP 平台的响应录成**回放夹具**（只读，不改平台任何配置）。

录一次、长期复用：之后 `tests/` 与离线排查都能在没有平台的情况下跑通洞察全链路。
夹具交给 `workbench/services/egmp/replay.py` 的 `ReplayClient` 回放，格式见该模块。

用法：
    # 按统一账号库的账号录制（口令从 Fernet 库解密，不落盘）
    .\\.venv\\Scripts\\python.exe tools\\record_insight_fixture.py ^
        --account <account_id> --objects training_mgr__c --command spider ^
        --out tests/fixtures/egmp/spider-training-mgr.json

    # 列出可用账号
    .\\.venv\\Scripts\\python.exe tools\\record_insight_fixture.py --list-accounts

安全边界：
- 只调用洞察的**只读**链路；脚本自身不含任何写端点。
- 录到的是平台响应，**可能含业务元数据**（字段名/状态名/工作流名），提交前请自行确认可否入库。
  仓库里现行的 `tests/fixtures/egmp/spider-training-mgr.json` 是「真实形态但换过名」的版本：
  结构照录，ID 与业务命名替换为确定性占位值——**照录的原始夹具不要直接提交**。
- 口令只在内存中解密使用，不写入夹具。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _list_accounts() -> int:
    from workbench import db
    from workbench.services import accounts as accounts_svc

    db.connect()
    rows = accounts_svc.list_accounts()
    if not rows:
        print("（账号库为空——请先在账号中心新增环境与账号）")
        return 0
    print(f"账号 {len(rows)} 个：")
    for row in rows:
        print(f"  {row.get('id')}  {row.get('username')}  {row.get('env_base_url')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="录制洞察只读链路的回放夹具")
    parser.add_argument("--account", help="统一账号库的账号 id")
    parser.add_argument("--objects", default="", help="对象编码，逗号分隔")
    parser.add_argument("--command", default="spider", choices=["spider"],
                        help="录哪条链路（inventory/understand 已下线，现存 spider）")
    parser.add_argument("--out", help="夹具输出路径（.json）")
    parser.add_argument("--name", default="", help="夹具名称（默认取对象编码）")
    parser.add_argument("--list-accounts", action="store_true", help="列出账号后退出")
    args = parser.parse_args()

    if args.list_accounts:
        return _list_accounts()
    if not args.account or not args.out:
        parser.error("需要 --account 与 --out（或使用 --list-accounts）")

    from workbench import db
    from workbench.services import accounts as accounts_svc
    from workbench.services.egmp import insight, replay
    from workbench.services.egmp.client import EgmpClient, login

    db.connect()

    account = accounts_svc.get_account(args.account)
    if not account:
        print(f"FAIL  账号不存在：{args.account}")
        return 1
    base_url = account.get("env_base_url") or ""
    if not base_url:
        print(f"FAIL  账号 {account.get('username')} 所属环境未配置 baseUrl")
        return 1

    codes = [c.strip() for c in args.objects.split(",") if c.strip()]
    if not codes:
        print(f"FAIL  {args.command} 需要 --objects")
        return 1

    print(f"登录 {base_url}（账号 {account.get('username')}）…")
    password = accounts_svc.reveal_password(args.account)
    token = login(base_url, account["username"], password, str(account.get("env_id") or ""))

    fixture = replay.make_fixture(
        args.name or (codes[0] if codes else args.command),
        base_url=base_url,
        env_name=str(account.get("env_name") or ""),
        note=f"由 tools/record_insight_fixture.py 录制（command={args.command}, objects={','.join(codes)}）",
    )

    out = Path(args.out)
    logs: list[str] = []
    with EgmpClient(base_url, token) as http:
        recorder = replay.Recorder(http, fixture)
        try:
            insight.run_spider(recorder, codes, on_log=logs.append)
        except Exception as exc:  # noqa: BLE001 —— 录制失败也要把已录部分落盘
            print(f"WARN  链路中途失败：{type(exc).__name__}: {exc}")
            print("      已录到的交互仍会保存，便于定位缺哪一步。")

    replay.save_fixture(fixture, out)
    for line in logs[-12:]:
        print(f"      {line}")
    print(f"\nPASS  已录制 {len(fixture['interactions'])} 条交互 → {out}")
    print("      提交前请确认平台元数据可否入库；不要把口令写进夹具。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
