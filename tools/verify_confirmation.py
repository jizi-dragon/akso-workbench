"""实时确认（`request_user_confirmation`）的**端到端**核验：真起服务、真发请求、真读静态资源。

## 它证明什么（组件测试证明不了的那一段）

`tests/test_user_confirmation.py` 用的是 `TestClient` 与假客户端——它证明得了**逻辑**，
证明不了**链路**。这一条补的正是 PITFALLS #53 的教训：`routes_agent.py` 里一个不存在的
`logger` 让 596 个单测全绿而真实请求 500。所以这里坚持：

- 用**生产入口** `python -m workbench.server_entry` 起服（打包态跑的就是它）；
- 走**真实 HTTP**（`httpx`），不 import 应用；
- 把 `workbench/static/` 的三个文件**真拉下来**看有没有新代码——
  "打包/静态目录漏收"这类事故只会在这里现形（PITFALLS #20 / #25 同族）；
- 临时数据目录，跑完即清。

## 用法

    .\\.venv\\Scripts\\python.exe tools\\verify_confirmation.py

退出码：`0` = 全过；`1` = 有失败或起不来。**手动诊断用**，不进 CI（它占固定端口）。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

PORT = 18795
BASE = f"http://127.0.0.1:{PORT}"

CONFIRM_TOOL = "request_user_confirmation"

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —— {detail}" if detail and not ok else ""))


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def boot(env: dict, stdio_log: pathlib.Path):
    """起 sidecar 并等就绪。stdout/stderr 落文件（不是 PIPE——PIPE 不读会阻塞子进程）。"""
    import httpx

    handle = stdio_log.open("w", encoding="utf-8")
    proc = subprocess.Popen([PY, "-m", "workbench.server_entry", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            if httpx.get(f"{BASE}/api/health", timeout=1.5).status_code == 200:
                return proc, handle, True
        except Exception:  # noqa: BLE001 —— 还没起来
            time.sleep(0.5)
    return proc, handle, False


def main() -> int:
    if not port_free(PORT):
        print(f"✗ 端口 {PORT} 被占用——请先退出占用它的进程")
        return 1
    if not pathlib.Path(PY).exists():
        print(f"✗ 找不到解释器 {PY}")
        return 1

    import httpx

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="wb-confirmverify-"))
    env = {
        **os.environ,
        "WORKBENCH_DATA": str(workdir / "data"),
        "WORKBENCH_LOG_DIR": str(workdir / "logs"),
        "WORKBENCH_PORT": str(PORT),
        "PYTHONIOENCODING": "utf-8",
    }
    print(f"临时数据目录 = {workdir}\n端口 = {PORT}（生产入口 server_entry）\n")

    proc, handle, ready = boot(env, workdir / "server-stdio.log")
    try:
        check("sidecar 起服并就绪（生产入口）", ready,
              (workdir / "server-stdio.log").read_text(encoding="utf-8", errors="replace")[-400:])
        if not ready:
            return 1
        client = httpx.Client(base_url=BASE, timeout=30.0)

        print("=== A. 工具目录（运行期中枢 → HTTP）===")
        catalog = client.get("/api/agent/tools").json()
        tool = next((t for t in catalog["tools"] if t["name"] == CONFIRM_TOOL), None)
        check("A1 目录里有这个工具", tool is not None)
        check("A2 risk=read（确认工具必须只读，否则确认自己要先过确认 = 无限递归）",
              bool(tool) and tool["risk"] == "read")
        check("A3 needs_confirmation 穿透了装配（不是被丢成默认值 False）",
              bool(tool) and tool["needs_confirmation"] is True,
              str(tool and tool.get("needs_confirmation")))
        check("A4 参数表齐全",
              bool(tool) and set(tool["args"]) == {"question", "details", "target_tool",
                                                   "target_args", "timeout_s",
                                                   "options_source"},
              str(tool and sorted(tool["args"])))

        print("=== B. 前端文件真的被服务出去了（且含新代码）===")
        for path, needles in (
            # 弹窗模块：**交互必须在这里**，不在会滚走的轨迹里（2026-09-20 用户反馈）
            ("/static/js/approval-modal.js",
             ["showModal()", "requestDecision", "settleDecision", "pending-strip",
              'addEventListener("cancel"',
              # 选择型确认（2026-09-21）：卡片栅格 + 交给右下角停靠卡 + 藏掉「同意」
              "tk-choice-grid", "tk-choice-card", "dockSelection",
              "ui.allow.hidden = isChoice"]),
            ("/static/js/account-dock.js",
             ["dockSelection", "restoreSelection", "getBoundingClientRect",
              "prefers-reduced-motion", "PICK_ACCOUNT_EVENT",
              # 让位：卡片不许压住输入区（2026-09-21 用户反馈第 1 条）
              "keepClearOfComposer", "ResizeObserver", "--dock-bottom"]),
            ("/static/js/chat-tools.js", ["decisionReceipt", "settleDecision"]),
            ("/static/js/chat.js",
             ['event.type === "confirm_required"', 'event.type === "confirm_resolved"',
              "decisionFromConfirm", "decisionFromApproval", "recoverPending",
              "event.options", "restoreSelection", "PICK_ACCOUNT_EVENT",
              # 界面主动发起的选择要标 local：否则它会掉进"等你拍板"的待办条
              "local: true"]),
            ("/static/css/chat-tools.css",
             [".tk-decision", ".pending-strip", ".decision-receipt",
              ".tk-choice-grid", ".account-dock", ".account-chip"]),
        ):
            res = client.get(path)
            missing = [n for n in needles if n not in res.text]
            check(f"B {path}（{res.status_code}，{len(res.text)}B）",
                  res.status_code == 200 and not missing,
                  f"缺：{missing}" if missing else "")

        # 旧的交互式内联卡片必须**不见了**：留着它就是一条"卡片也能点"的旁路
        tools_js = client.get("/static/js/chat-tools.js").text
        check("B2 轨迹里不再有带按钮的卡片（只留回执）",
              "approvalCard" not in tools_js and "confirmationCard" not in tools_js)

        print("=== C. HTTP 面的实时确认流程 ===")
        target_args = {"account_id": "ghost-live"}
        res = client.post("/api/agent/invoke", json={
            "tool": CONFIRM_TOOL,
            "args": {"question": "把 3 个对象提交到生产，是否继续？", "details": "可回滚",
                     "target_tool": "accounts_browser_close", "target_args": target_args},
        })
        check("C1 409 而不是 200（调用方绝不能以为用户已经同意了）",
              res.status_code == 409, f"status={res.status_code}")
        detail = res.json()["detail"]
        pending_id = detail.get("pendingId", "")
        check("C2 带 pendingId 与规范化后的 confirm",
              bool(pending_id) and detail["confirm"]["target"] == {
                  "tool": "accounts_browser_close", "risk": "write", "args": target_args},
              str(detail.get("confirm"))[:160])

        listed = client.get("/api/agent/pending").json()["pending"]
        row = next((p for p in listed if p["id"] == pending_id), None)
        check("C3 待确认**挂在目标工具头上**（用户同意的是那一次具体调用，不是一个意图）",
              bool(row) and row["tool"] == "accounts_browser_close",
              str(row and row["tool"]))
        # 弹窗靠 `kind` 决定标题与文案（"模型在问你" vs "宿主拦下了"）；
        # 页面加载时的认领也用它。缺了它，刷新之后那条待确认会被当成风险门渲染。
        check("C3b 清单里带 kind=confirmation（弹窗与认领都靠它区分两类决定）",
              bool(row) and row.get("kind") == "confirmation",
              str(row and row.get("kind")))

        check("C4 同意成功", client.post(f"/api/agent/pending/{pending_id}/approve").status_code == 200)

        after = client.post("/api/agent/invoke", json={
            "tool": "accounts_browser_close", "args": target_args})
        check("C5 同参数再调**不再被拦**（预先批准按「工具 + 参数指纹」认领成功）",
              after.status_code != 409, f"status={after.status_code}")
        other = client.post("/api/agent/invoke", json={
            "tool": "accounts_browser_close", "args": {"account_id": "ghost-other"}})
        check("C6 换了参数仍然被拦（同意 A ≠ 同意 B）", other.status_code == 409,
              f"status={other.status_code}")

        print("=== D. 宿主校验不被绕过（HTTP 面同样拒）===")
        bad = client.post("/api/agent/invoke", json={
            "tool": CONFIRM_TOOL, "args": {"question": "q", "target_tool": "read_local_file",
                                           "target_args": {"path": "C:/tmp/a.txt"}}})
        check("D1 只读目标 → 400 bad_args（不降级成纯意图确认）",
              bad.status_code == 400, f"status={bad.status_code}")
        ghost = client.post("/api/agent/invoke", json={
            "tool": CONFIRM_TOOL, "args": {"question": "q", "target_tool": "no_such_tool"}})
        check("D2 目标不存在 → 400 bad_args", ghost.status_code == 400,
              f"status={ghost.status_code}")

        print("=== E. 纯意图确认（不带 target）===")
        intent = client.post("/api/agent/invoke", json={
            "tool": CONFIRM_TOOL, "args": {"question": "我想先确认方向"}})
        check("E1 也是 409（要用户答复）", intent.status_code == 409,
              f"status={intent.status_code}")
        intent_row = next((p for p in client.get("/api/agent/pending").json()["pending"]
                           if p["id"] == intent.json()["detail"]["pendingId"]), None)
        check("E2 那条行挂在确认工具自己名下（不绑定任何调用）",
              bool(intent_row) and intent_row["tool"] == CONFIRM_TOOL,
              str(intent_row and intent_row["tool"]))
        client.post(f"/api/agent/pending/{intent.json()['detail']['pendingId']}/approve")
        intent_after = client.post("/api/agent/invoke", json={
            "tool": "accounts_browser_close", "args": {"account_id": "ghost-intent"}})
        check("E3 同意纯意图确认**不放行任何写操作**", intent_after.status_code == 409,
              f"status={intent_after.status_code}")

        print("=== F. 选择型确认：让用户在卡片上挑一个账号 ===")
        # ⚠️ **本脚本的"临时数据目录"里是有账号的**：`config.bootstrap_database()`
        # 会把旧库整个复制过来（ADR-0013 的"自动接管"，否则用户升级后会看到"账号全没了"）。
        # 所以"账号中心为空"那条路**验不了**——它由单测覆盖
        # （`tests/test_user_confirmation.py::test_empty_option_source_is_rejected_with_a_next_step`，
        # 那里是自己注入的候选项提供方，可以精确控制"空"）。
        unknown = client.post("/api/agent/invoke", json={
            "tool": CONFIRM_TOOL, "args": {"question": "用哪个？", "options_source": "nope"}})
        check("F1 未知来源 → 400（不返回空列表：那读起来像「用户没得选」）",
              unknown.status_code == 400
              and "未知的候选项来源" in str(unknown.json().get("detail", "")))

        mixed = client.post("/api/agent/invoke", json={
            "tool": CONFIRM_TOOL,
            "args": {"question": "用哪个？", "options_source": "accounts",
                     "target_tool": "accounts_browser_close",
                     "target_args": {"account_id": "x"}}})
        check("F2 选项 + target_tool → 400（选哪个是用户点完才知道的，没法提前绑定）",
              mixed.status_code == 400
              and "不能同时填" in str(mixed.json().get("detail", "")))

        # 造一个真实账号，再问一次
        env = client.post("/api/accounts/envs",
                          json={"name": "验证环境", "base_url": "http://10.0.0.9"}).json()
        account = client.post("/api/accounts", json={
            "env_id": env["id"], "username": "probe-user", "password": "p@ss",
            "note": "验证用"}).json()
        asked = client.post("/api/agent/invoke", json={
            "tool": CONFIRM_TOOL,
            "args": {"question": "用哪个账号？", "options_source": "accounts"}})
        check("F3 有账号时 → 409 并带候选项", asked.status_code == 409,
              f"status={asked.status_code} {str(asked.json())[:120]}")
        confirm = asked.json()["detail"]["confirm"]
        options = confirm.get("options") or []

        # 卡片是**服务端按账号表展开**的：它必须与「账号中心」那一页看到的是同一份东西。
        # 这条挡住的是"另做一份缓存 / 让模型转录候选"——那种实现会先漂，再静默错。
        center = client.get("/api/accounts").json()["accounts"]
        check("F3b 候选项与账号中心同源（不是另一份缓存，也不是模型转录的）",
              sorted(o["label"] for o in options) == sorted(a["username"] for a in center)
              and len(options) == len(center),
              f"卡片 {sorted(o['label'] for o in options)} vs 账号中心 "
              f"{sorted(a['username'] for a in center)}")

        picked = next((o for o in options if o.get("label") == "probe-user"), None)
        check("F3c 卡片字段来自账号表（value=账号 id / 环境 / 地址）",
              bool(picked) and picked.get("value") == account.get("id")
              and picked.get("description") == "验证环境"
              and (picked.get("meta") or {}).get("地址") == "http://10.0.0.9",
              str(picked)[:220])

        pending_id = confirm["pendingId"]
        row = next((p for p in client.get("/api/agent/pending").json()["pending"]
                    if p["id"] == pending_id), None)
        check("F4 待确认清单里也带 options（刷新后靠它把卡片重新画出来）",
              bool(row) and len(row.get("options") or []) == len(options),
              str(row and row.get("options"))[:150])

        miss = client.post(f"/api/agent/pending/{pending_id}/approve", json={"value": ""})
        check("F5 不带选项就放行 → 409（「同意」一个选择题等于什么都没答）",
              miss.status_code == 409, f"status={miss.status_code}")
        alien = client.post(f"/api/agent/pending/{pending_id}/approve", json={"value": "ghost"})
        check("F5b 不在候选项里的 value → 409（编出来的 id 不能一路变成参数）",
              alien.status_code == 409, f"status={alien.status_code}")
        ok = client.post(f"/api/agent/pending/{pending_id}/approve",
                         json={"value": account.get("id")})
        check("F5c 选中真实候选项 → 200，回执里带 decidedValue",
              ok.status_code == 200 and ok.json().get("decidedValue") == account.get("id"),
              f"status={ok.status_code} {str(ok.json())[:140]}")

        check("F6 没有会话 id 时不返回「最近一次选择」"
              "（那会让 A 会话显示出 B 会话选的账号，比不显示更糟）",
              client.get("/api/agent/selection").json().get("selection") is None,
              str(client.get("/api/agent/selection").json())[:140])

        print("=== G. 界面发起的入口（右下角「选择账号」默认态）===")
        no_conv = client.post("/api/agent/selection/pick", json={"conversationId": ""})
        check("G1 没有会话 id → 400（记不住的选择是假的选择：用户选完下一轮模型还在问）",
              no_conv.status_code == 400, f"status={no_conv.status_code}")

        conv = client.post("/api/conversations").json()
        ui = client.post("/api/agent/selection/pick",
                         json={"conversationId": conv["id"]})
        body = ui.json() if ui.status_code == 200 else {}
        check("G2 界面发起 → 200，且形状与 `confirm_required` 事件一致（同一套弹窗直接用）",
              ui.status_code == 200 and body.get("kind") == "selection"
              and len(body.get("options") or []) == len(center) and body.get("pendingId"),
              f"status={ui.status_code} {str(body)[:160]}")

        ui_pending = body.get("pendingId", "")
        ui_row = next((p for p in client.get("/api/agent/pending").json()["pending"]
                       if p["id"] == ui_pending), None)
        check("G3 它同样进待确认表（复用同一套批准与 value 校验）",
              bool(ui_row) and ui_row.get("kind") == "selection",
              str(ui_row and ui_row.get("kind")))

        bad = client.post(f"/api/agent/pending/{ui_pending}/approve", json={"value": "ghost"})
        check("G4 换入口 **不等于** 松校验：非法 value 照样拒",
              bad.status_code == 409, f"status={bad.status_code}")

        good = client.post(f"/api/agent/pending/{ui_pending}/approve",
                           json={"value": account.get("id")})
        check("G5 选定后 → 200", good.status_code == 200, f"status={good.status_code}")

        messages = client.get(f"/api/conversations/{conv['id']}/messages").json()["messages"]
        notes = [m for m in messages if m.get("role") == "note"]
        check("G6 会话里留下一条 note（模型下一轮才知道用哪个账号——否则用户白选了）",
              len(notes) == 1 and account.get("id", "") in notes[0].get("content", ""),
              str(messages)[:200])

        sel = client.get("/api/agent/selection",
                         params={"conversationId": conv["id"]}).json().get("selection")
        check("G7 选中结果可查（刷新后右下角那张卡靠它画回来）",
              bool(sel) and sel.get("value") == account.get("id"), str(sel)[:160])

        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
        handle.close()
        shutil.rmtree(workdir, ignore_errors=True)

    failed = [label for label, ok, _ in CHECKS if not ok]
    print()
    print("=" * 62)
    if failed:
        print(f"CONFIRM_E2E: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS")
        for label in failed:
            print(f"  ✗ {label}")
        return 1
    print(f"CONFIRM_E2E: {len(CHECKS)}/{len(CHECKS)} PASS")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
