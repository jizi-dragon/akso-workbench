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
                                                   "target_args", "timeout_s"},
              str(tool and sorted(tool["args"])))

        print("=== B. 前端文件真的被服务出去了（且含新代码）===")
        for path, needles in (
            # 弹窗模块：**交互必须在这里**，不在会滚走的轨迹里（2026-09-20 用户反馈）
            ("/static/js/approval-modal.js",
             ["showModal()", "requestDecision", "settleDecision", "pending-strip",
              'addEventListener("cancel"']),
            ("/static/js/chat-tools.js", ["decisionReceipt", "settleDecision"]),
            ("/static/js/chat.js",
             ['event.type === "confirm_required"', 'event.type === "confirm_resolved"',
              "decisionFromConfirm", "decisionFromApproval", "recoverPending"]),
            ("/static/css/chat-tools.css",
             [".tk-decision", ".pending-strip", ".decision-receipt"]),
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
