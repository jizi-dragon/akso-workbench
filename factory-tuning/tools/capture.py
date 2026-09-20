"""起/停平台监听（**调产品已有的接口，不新增采集代码**）。

## 为什么需要这个脚本

产品的监听入口在「账号中心 → 某个托管会话 → 开始监听」。要用它你得：
先开应用 → 进账号中心 → 找到那个会话 → 点按钮 → 记住日志路径。

而"为了调优工厂去录一段"这件事会做很多次，每次都点一遍界面很低效。
本脚本把那一串收成一条命令——**它只发 HTTP，不改产品一行代码**。

## 用法

    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\capture.py start --account <id>
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\capture.py stop  --account <id>
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\capture.py list

`start` 会在**应用内置 Chromium** 里打开该账号的平台页（`/api/monitor/start` 内部会
按需 `_do_open`），然后挂上录制。**接下来你就在那个窗口里正常配置**——
目标不是"给工厂演示"，而是"照你平时的方式配一遍"，因为要学的正是你平时的做法。

> ⚠️ 录到的平台动作是**真实写入**（你是在真平台上配东西）。本脚本不改变这一点，
> 也不会加速或自动化任何操作。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = os.environ.get("WORKBENCH_BASE", "http://127.0.0.1:18765")


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{DEFAULT_BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return res.status, json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"detail": raw}
    except urllib.error.URLError as exc:
        # 工作台没起来是最常见的情况，直接给一句能照做的话，而不是抛栈
        raise SystemExit(
            f"✗ 连不上工作台（{DEFAULT_BASE}）：{exc.reason}\n"
            "  先启动它：cd desktop; npm start（或 .\\.venv\\Scripts\\python.exe -m workbench.main）"
        ) from exc


def cmd_start(account: str) -> int:
    status, payload = _request("POST", "/api/monitor/start", {"account_id": account})
    if status != 200:
        print(f"✗ 开始监听失败（{status}）：{payload.get('detail') or payload}")
        return 1
    print("✓ 监听已开启")
    print(f"  日志：{payload.get('log')}")
    print(f"  过滤统计：{payload.get('stats')}")
    print("\n接下来在**应用内置的那个 Chromium 窗口**里正常配置一遍。")
    print("配完回来跑：")
    print(f"  .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\capture.py stop --account {account}")
    return 0


def cmd_stop(account: str) -> int:
    status, payload = _request("POST", "/api/monitor/stop", {"account_id": account})
    if status != 200:
        print(f"✗ 停止监听失败（{status}）：{payload.get('detail') or payload}")
        return 1
    print("✓ 监听已停止，产物已落盘")
    print(f"  日志：{payload.get('log')}")
    print(f"  过滤统计：{payload.get('stats')}")
    print("\n收进本目录（**这一步做脱敏**）：")
    print("  .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\ingest.py --latest")
    return 0


def cmd_list() -> int:
    status, payload = _request("GET", "/api/browser/sessions")
    if status != 200:
        print(f"✗ 读取会话失败（{status}）：{payload}")
        return 1
    rows = payload.get("sessions") if isinstance(payload, dict) else payload
    if not rows:
        print("（当前没有托管会话）")
        return 0
    print(f"{'账号 id':38} {'状态':10} {'监听中':6} 标题")
    for row in rows:
        print(f"{str(row.get('account_id') or row.get('id') or '?'):38} "
              f"{str(row.get('status') or '?'):10} "
              f"{'是' if row.get('monitoring') else '否':6} "
              f"{str(row.get('title') or '')[:40]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="平台监听的开/关（调产品已有接口）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("start", "开内置浏览器并开始录制"),
                            ("stop", "停止录制并落盘")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--account", required=True, help="账号 id（capture.py list 可查）")
    sub.add_parser("list", help="列出托管会话与监听状态")

    args = parser.parse_args(argv)
    if args.command == "start":
        return cmd_start(args.account)
    if args.command == "stop":
        return cmd_stop(args.account)
    return cmd_list()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
