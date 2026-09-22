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
    print("配完回来跑（**读数据用 read**，它会读完自动重开监听）：")
    print(f"  .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\capture.py read --account {account}")
    print("（只有在**明确要停**——收工、换账号——时才用 stop）")
    return 0


def _pump() -> None:
    """**落盘之前先泵一次**：让排队的 Playwright 事件在 recorder 还活着时分发出来。

    ## 三条已证事实（都来自代码，不是推断）

    1. sync-Playwright 的 `request`/`response` 事件**只在 worker 线程进入一次 Playwright 调用**
       时才分发到 Python 回调；而 `monitor_stop` 的作业体是**纯 Python**
       （`recorder.stop()` → checkpoint + flush）⇒ **它自己不泵**。
    2. 反过来，`_do_session`（`browser_pool.py:445-449`）里有 `entry.page.is_closed()` 与
       `entry.page.title()` ⇒ **任何"列会话"的调用都会泵**；worker 在每次作业之后还会替活着的
       recorder 跑一次 `drain()`（补捞响应体与动作缓冲）。
    3. `MonitorSession.start()` 的顺序是 `page.evaluate(动作缓冲注入)` **在前**、
       `page.on("request"…)` **在后** ⇒ 若真有事件堆到 start 这一刻才分发，它们会落进
       **一具尸体**（旧 recorder `_active=False` 丢弃、新 recorder 还没挂监听）。

    ## 真机证据（2026-09-22 12:12，同一次会话，实测）

    停监听前先泵这一下，`total` 由 **0 → 10**：那 10 条 GET **一直堆在 Playwright 队列里
    没人分发**。⇒ 两种机制**都真实存在**，别只记一边：

    - 有人在泵时（工作台 UI 停在账号视图 ⇒ `accounts.js:1164` 的 3 秒轮询），队列被顺手清空；
    - **没人泵时，事件就一直堆着**，于是 `stop` 的 flush 写出**干净的 0** ——
      那 14 个 329 B 的空会话就是这个形态（`total` 连 GET 都计数，0 的含义是
      "**到落盘这一刻，被监听页面一条请求都没被分发过**"，不是"页面上什么都没发生"）。

    ⇒ 两条因此定死：① **flush 之前必须先泵**（本函数）；② `MonitorSession.start()` 必须
    **先挂监听、再注入**（否则 start 那一刻泵出来的旧事件正好落进"旧 recorder 已停用、
    新 recorder 还没监听"的尸体里）。
    """
    status, payload = _request("GET", "/api/browser/sessions")
    if status == 200:
        rows = payload.get("sessions") if isinstance(payload, dict) else payload
        n = len(rows) if isinstance(rows, list) else "?"
        print(f"① 先泵一次（列会话，{n} 个）—— 让排队的事件先分发出来再落盘")
    else:
        print(f"① 泵这一下没成功（{status}）—— 仍继续停，但这次落盘可能少东西，记下来")


def cmd_stop(account: str) -> int:
    _pump()
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


def cmd_read(account: str) -> int:
    """**读一次数据**：停 → 落盘 → 收编（脱敏）→ **立刻重新开始监听**。

    ## 为什么要把三步合成一条（2026-09-22 实测事故）

    `stop` 与 `start` 是**两步**，而"**读完忘了重开**"**不会报错**——用户接着在平台上操作，
    那一段**一条都录不到**；事后只能从他的描述里发现"那次没录到"。
    本次就这么丢了「**新建表单布局**」那一整步（而那正是工厂缺的那个动词，见 F66），
    还白让用户重做了一次。

    ⇒ 合成一条命令：**"忘记重开"在物理上不可能发生**。
    读取用 `read`；只有在**明确要停**（收工、换账号）时才用 `stop`。

    ★ **它的不变式**：命令返回后，监听**一定是开着的**。
    所以万一调用时监听**本来就没在跑**（正常流程里不该发生），它**不会中止**——
    只把这件事**报出来**，然后照样重开。
    """
    _pump()
    status, payload = _request("POST", "/api/monitor/stop", {"account_id": account})
    if status == 200:
        print("✓ 监听已停止，产物已落盘")
        print(f"  日志：{payload.get('log')}")
        print(f"  过滤统计：{payload.get('stats')}")
        print("\n收编（脱敏后进 data/sessions/）：")
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import ingest  # noqa: PLC0415

            ingest.main(["--latest"])
        except SystemExit as exc:  # ingest 用 SystemExit 报错；收编失败不该挡住重开
            print(f"（收编跳过：{exc}）")
    else:
        print(f"⚠️ 停止监听没成功（{status}：{payload.get('detail') or payload}）"
              "——**监听本来就没在跑**。")
        print("   这本身是个异常（正常流程里「读数据」时它本该在跑），先记下来，"
              "但不中止：下面照样重开。")
    print("\n★ 立刻重新开始监听（读完必重开）：")
    return cmd_start(account)


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
                            ("stop", "停止录制并落盘（**要收工才用它**）"),
                            ("read", "停 → 落盘 → 收编（脱敏）→ **立刻重新开始监听**")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--account", required=True, help="账号 id（capture.py list 可查）")
    sub.add_parser("list", help="列出托管会话与监听状态")

    args = parser.parse_args(argv)
    if args.command == "start":
        return cmd_start(args.account)
    if args.command == "stop":
        return cmd_stop(args.account)
    if args.command == "read":
        return cmd_read(args.account)
    return cmd_list()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
