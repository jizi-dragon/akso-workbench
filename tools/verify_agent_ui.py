"""「本次运行」时间线的前端端到端验证（计划 §6.2）。

## 它验的是"渲染出来长什么样"，不是"源码里有这些字样"

★ 这条纪律本仓已经栽过一次（PITFALLS #87）：圆环那条"看 `backgroundImage` 里有没有
`conic-gradient`"的断言**是绿的**，而实际上那 10 个角度全被判成背景色。
所以这里：

- 面板**真的点开**（`click`），不是断言 HTML 里有那个 `id`；
- 摘要那一行**读计算样式**（`getComputedStyle`），不是断言它有 `rt-span-summary` 这个类名
  —— ★ 我写这个模块时**凭印象编了几个不存在的徽章类名**（`badge-ok` / `badge-danger`），
  后果是徽章退化成灰底灰字而**界面上看不出哪里错了**。类名对不对推不出颜色对不对。

## 它怎么造数据

会话与运行**直接写进服务端那个临时库**（`RunRecorder`，与生产同一条路径），
然后用浏览器去读 —— 这样验的是"真库 → 真接口 → 真渲染"整条链路，
而不是"我塞一个假 JSON 进前端"。

## 用法

    .\\.venv\\Scripts\\python.exe tools\\verify_agent_ui.py
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ── 自足运行（2026-09-24 加）──────────────────────────────────────────────
#
# ★ 此前这几行是**没有**的，后果有两条，都很实在：
#
# ① **它会读写真实数据目录**（`WORKBENCH_DATA` 没设 ⇒ 用的是用户那份库）。
#    本脚本会做新建/重命名/停用/删除盒子（且 `dialog` 一律 accept）——
#    也就是**在真实数据上做破坏性操作**。
#
# ② ★★ **它绑的是生产端口 18765**。如果应用**正在运行**：`uvicorn` 起不来
#    （它的报错被丢进 DEVNULL），而下面的就绪探测会**连上那个正在跑的应用** ——
#    于是这一整套"验收"打在**真实实例**上。
#    这正是 [ADR-0012](../docs/adr/0012-fixed-ports.md) 记的那个危险：
#    **"旧进程还活着"与"测试戳到了真端口"两件事都不报错。**
#
# ⇒ 现在：临时数据目录 + 独立端口 + **端口被占就拒跑**（而不是连上去）。
WORKBENCH_DATA = pathlib.Path(tempfile.mkdtemp(prefix="wb-verify-"))
_ENV = {**os.environ, "WORKBENCH_DATA": str(WORKBENCH_DATA),
        "WORKBENCH_LOG_DIR": str(WORKBENCH_DATA / "logs"),
        "PYTHONIOENCODING": "utf-8"}


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
PORT = 18802
BASE = f"http://127.0.0.1:{PORT}"

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n        {detail}" if detail and not ok else ""))


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def boot(env: dict, log: pathlib.Path):
    handle = log.open("wb")
    proc = subprocess.Popen([PY, "-m", "workbench.server_entry", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    import httpx

    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/api/bootstrap", timeout=2.0).status_code == 200:
                return proc, handle, True
        except Exception:  # noqa: BLE001 —— 还没起来
            time.sleep(0.3)
    return proc, handle, False


def main() -> int:
    if not port_free(PORT):
        print(f"✗ 端口 {PORT} 被占用——先退出占用它的进程")
        return 1

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="wb-agentui-"))
    data_dir = workdir / "data"
    env = {
        **os.environ,
        "WORKBENCH_DATA": str(data_dir),
        "WORKBENCH_LOG_DIR": str(workdir / "logs"),
        "WORKBENCH_PORT": str(PORT),
        "PYTHONIOENCODING": "utf-8",
    }
    print(f"临时数据目录 = {workdir}\n端口 = {PORT}（生产入口 server_entry）\n")

    proc, handle, ready = boot(env, workdir / "server-stdio.log")
    try:
        import httpx

        check("sidecar 起服并就绪（生产入口）", ready,
              (workdir / "server-stdio.log").read_text(encoding="utf-8", errors="replace")[-500:])
        if not ready:
            return 1

        client = httpx.Client(base_url=BASE, timeout=30.0)
        client.put("/api/settings", json={"apiKey": "sk-verify-not-a-real-key"})
        conv = client.post("/api/conversations").json() or {}
        conv_id = conv.get("id", "")
        check("建了一个会话", bool(conv_id), str(conv))

        # ── 直接写进服务端那个库（与生产同一条路径：RunRecorder） ──────────
        os.environ["WORKBENCH_DATA"] = str(data_dir)
        os.environ.pop("WORKBENCH_DB", None)
        sys.path.insert(0, str(ROOT))
        from workbench import db  # noqa: PLC0415
        from workbench.harness import trace  # noqa: PLC0415

        db.connect()
        recorder = trace.RunRecorder(conv_id, "deepseek-flash")
        recorder.span(trace.SPAN_MODEL, "deepseek-flash", started=0.0, duration_ms=1200,
                      attrs={"messages": 2, "tools": 18,
                             "usage": {"prompt_tokens": 2000, "prompt_cache_hit_tokens": 1800,
                                       "prompt_cache_miss_tokens": 200}})
        recorder.span(trace.SPAN_TOOL, "get_current_time", started=0.0, duration_ms=7,
                      attrs={"auditId": "a1", "risk": "read", "status": "ok", "step": 1})
        # ★ 摘要那次调用：`summary=True` 由产生它的地方标（`_SpanClient.chat`）
        recorder.span(trace.SPAN_MODEL, "deepseek-flash", started=0.0, duration_ms=3400,
                      attrs={"kind": "chat", "summary": True, "messages": 26,
                             "usage": {"prompt_tokens": 15000, "prompt_cache_hit_tokens": 0,
                                       "prompt_cache_miss_tokens": 15000}})
        recorder.finish({"prompt_tokens": 17000, "prompt_cache_hit_tokens": 1800})
        check("种了一次含摘要调用的运行", recorder.steps == 3, str(recorder.steps))

        # ── 接口面 ───────────────────────────────────────────────────────
        got = client.get("/api/agent/runs", params={"conversationId": conv_id}).json()
        runs = got.get("runs", [])
        check("GET /api/agent/runs 返回这次运行", len(runs) == 1 and runs[0]["steps"] == 3,
              str(got)[:400])
        check("span 随 run 一起回来（不是两个请求）",
              bool(runs) and len(runs[0].get("spans", [])) == 3,
              str(runs[0].get("spans") if runs else None)[:300])

        empty = client.get("/api/agent/runs", params={"conversationId": "不存在的会话"})
        check("空结果是 200 + 空数组（不是 404）",
              empty.status_code == 200 and empty.json().get("runs") == [],
              f"{empty.status_code} {empty.text[:200]}")

        # ── 渲染 ─────────────────────────────────────────────────────────
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1180, "height": 860})
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(BASE, wait_until="networkidle")

            # 切到那个会话（面板读的是 state.currentConversationId）
            page.evaluate(
                "async (id) => { const { state } = await import('/static/js/app.js');"
                " state.currentConversationId = id; }",
                conv_id,
            )

            button = page.query_selector("#btn-run-timeline")
            check("输入区有「运行」按钮", button is not None)
            panel = page.query_selector("#run-timeline")
            check("面板初始是隐藏的", panel is not None and not panel.is_visible())
            if button:
                button.click()
                page.wait_for_selector(".rt-span", timeout=8000)
            check("点一下面板就打开", bool(panel and panel.is_visible()))

            spans = page.query_selector_all(".rt-span")
            check("三步都渲染出来了", len(spans) == 3, f"实际 {len(spans)} 个 .rt-span")

            summary = page.query_selector(".rt-span-summary")
            check("摘要那次调用被单独标出来", summary is not None)
            if summary:
                # ★ 读**计算样式**，不是读类名
                style = page.evaluate(
                    "el => { const s = getComputedStyle(el);"
                    " return { border: s.borderLeftColor, width: s.borderLeftWidth,"
                    " bg: s.backgroundColor, text: el.textContent }; }",
                    summary,
                )
                check("摘要那一行真的画了红色左边框",
                      style["width"].startswith("3") and "220, 38, 38" in style["border"],
                      str(style))
                check("摘要那一行的底色是红的（不是透明）",
                      style["bg"] not in ("rgba(0, 0, 0, 0)", "transparent"), str(style))
                check("文字里说明了「为什么标红」",
                      "全 miss" in style["text"], style["text"][:200])

            # 状态徽章：读计算样式，防"类名对但颜色没生效"
            badges = page.evaluate(
                "() => Array.from(document.querySelectorAll('.rt-status')).map(el => {"
                " const s = getComputedStyle(el); return { text: el.textContent, color: s.color }; })"
            )
            check("状态徽章有三种（完成/完成/完成 或含失败）",
                  len(badges) == 3 and all(b["text"] for b in badges), str(badges))
            check("徽章**不是**灰色基类（说明色板类名真的存在）",
                  all(b["color"] != "rgb(81, 99, 127)" for b in badges),
                  f"读到的颜色：{[b['color'] for b in badges]}")

            # 缓存命中：模型那一步 90%，摘要那一步 0%
            cache = page.evaluate(
                "() => Array.from(document.querySelectorAll('.rt-cache')).map(el => el.textContent)"
            )
            check("缓存命中按**每一步**显示", any("90%" in c for c in cache), str(cache))
            check("摘要那一步的缓存显示 0%（它是全 miss）", any("0%" in c for c in cache), str(cache))

            check("页面没有 JS 报错", not errors, str(errors[:3]))
            browser.close()

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        handle.close()

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    print(f"\nAGENT_UI_CHECKS: {passed}/{len(CHECKS)}")
    for label, ok, detail in CHECKS:
        if not ok:
            print(f"  ✗ {label}\n    {detail}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
