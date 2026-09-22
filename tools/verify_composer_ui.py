r"""输入区的**真渲染**验证：模型名在发送键旁、下方那行用量**真的画出来了**（2026-09-22 用户下达）。

## 为什么静态断言不够（这个脚本存在的唯一理由）

`tools/verify_attachments.py` 的 A6–A9 是**字符串**断言：它们在 `index.html` / `chat.js` / `app.css`
的**源码文本**里找 `id="usage-strip"`、`contextTokens`、`font-size: 12px`。
那种断言能抓"字段被删了"，但抓不到这一整类**看起来完全正常**的失败：

| 静态断言说 | 浏览器里实际发生 |
|---|---|
| `id="usage-strip"` 在场 | 它 `hidden` 着，或者被 `.composer` 的 `overflow:hidden` 裁掉了 ⇒ **用户看不见** |
| `font-size: 12px` 在 CSS 里 | 别处一条更具体的规则把它压回 11px ⇒ **还是蚂蚁字**（用户上一轮的抱怨原文） |
| `paintUsageStrip` 读 `contextTokens` | 事件里**没有**那个键 ⇒ 永远显示「上下文 —」，而静态断言全绿 |
| `chat-model-badge` 在 `btn-send` **之前** | 二者换行成两行 ⇒ 名字跑到发送键**下面**去了 |

所以这里**真起生产入口、真开 chromium、真点会话、真按发送**，然后读**渲染后**的
几何与计算样式。SSE 用一条**与后端同形**的夹具喂进去（形状由
`tests/test_agent_loop.py::test_usage_event_carries_context_usage_for_the_composer_strip`
钉住）——**不花 token、不碰用户的数据目录**。

> 为什么允许在这里自己起 chromium：本脚本是**对静态资源本身的渲染检查**，
> 不是平台自动化，也不碰 `browser_pool`（规则 23/25 管的是产品代码里的浏览器操作；
> `tools/verify_wheel_page.py` / `verify_accounts_ui.py` 是同一形态的先例）。

用法：`python tools\verify_composer_ui.py`
"""

from __future__ import annotations

import json
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
PORT = 18796
BASE = f"http://127.0.0.1:{PORT}"

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  —— {detail}" if detail and not ok else ""))


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def boot(env: dict, log: pathlib.Path):
    handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen([PY, "-m", "workbench.server_entry", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
    import httpx

    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            return proc, handle, False
        try:
            if httpx.get(f"{BASE}/api/health", timeout=2.0).status_code == 200:
                return proc, handle, True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return proc, handle, False


# —— SSE 夹具：与 `harness/loop.py` 发出的**同一批事件、同一批键** ——
# `usage` 的四个键逐字抄自 `_context_usage()`；键名写错时前端只会安静地显示「—」，
# 所以夹具里的形状必须由后端那侧的用例来钉（见模块 docstring）。
SSE_FIXTURE = "".join(
    "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
    for ev in [
        {"type": "status", "message": "生成中"},
        {"type": "token", "text": "收到。"},
        {"type": "final", "content": "收到。"},
        {"type": "usage", "usage": {
            "calls": 1, "promptTokens": 65536,
            "cacheHitTokens": 65000, "cacheMissTokens": 536,
            "completionTokens": 12, "reasoningTokens": 0,
            "hitRate": 0.9918, "summaryCalls": 0,
            "contextTokens": 65536, "contextWindow": 131072,
            "contextThreshold": 0.8, "contextRatio": 0.5,
        }},
        {"type": "done", "metricId": "m-verify", "titleUpdated": False},
    ]
)


def main() -> int:
    if not port_free(PORT):
        print(f"✗ 端口 {PORT} 被占用——先退出占用它的进程")
        return 1

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="wb-composerverify-"))
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
              (workdir / "server-stdio.log").read_text(encoding="utf-8", errors="replace")[-400:])
        if not ready:
            return 1
        client = httpx.Client(base_url=BASE, timeout=30.0)

        # 一个**假的** Key：本轮不发真请求（SSE 被浏览器侧拦下），只是让输入框可用
        client.put("/api/settings", json={"apiKey": "sk-verify-not-a-real-key"})
        conv = client.post("/api/conversations").json() or {}
        conv_id = conv.get("id", "")

        # 只连库、**先不写**：F0 要看的是"一条用量都还没有时"那一行长什么样
        os.environ["WORKBENCH_DATA"] = str(data_dir)
        os.environ.pop("WORKBENCH_DB", None)
        sys.path.insert(0, str(ROOT))
        from workbench import db  # noqa: PLC0415

        db.connect()

        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1180, "height": 820})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # 拦截 SSE：**只拦这一条**，其余请求照常打到真服务（静态资源、/api/agent/cache 都是真的）
            page.route("**/api/chat/stream", lambda route: route.fulfill(
                status=200, headers={"Content-Type": "text/event-stream"},
                body=SSE_FIXTURE))
            page.goto(f"{BASE}/", wait_until="networkidle")
            page.wait_for_selector("#conversation-list .conversation-title", timeout=15000)

            print("=== F. 布局：模型名真的在发送键旁边 ===")
            check("F0 一条用量都没有时，这一行**也在**（占位「—」，不是有数据才冒出来）",
                  page.locator("#usage-strip").is_visible()
                  and page.locator("#usage-cache").inner_text().strip() == "缓存命中 —"
                  and page.locator("#usage-context").inner_text().strip() == "上下文 —",
                  f"visible={page.locator('#usage-strip').is_visible()} "
                  f"text={page.locator('#usage-cache').inner_text()!r} "
                  f"/ {page.locator('#usage-context').inner_text()!r}")

            # 会话累计用量：命中 3000 / 未命中 1000 ⇒ 75%（"好"档，绿）。
            # 写在**服务端**（同一个临时库）而不是塞给前端：G3/G4 要证的正是"数字来自服务端"。
            db.execute(
                "INSERT INTO deepseek_usage (id, conversation_id, model, calls, prompt_tokens, "
                "cache_hit_tokens, cache_miss_tokens, completion_tokens, reasoning_tokens, "
                "summary_calls, latency_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("verify-usage-1", conv_id, "deepseek-flash", 1, 4000, 3000, 1000, 20, 0, 0, 800,
                 int(time.time() * 1000)),
            )
            page.click("#conversation-list .conversation-title")
            page.wait_for_timeout(600)
            badge = page.locator("#chat-model-badge").bounding_box()
            send = page.locator("#btn-send").bounding_box()
            header = page.locator(".chat-header").bounding_box()
            check("F1 模型名在发送键**左侧**且同一行（不是掉到下一行）",
                  bool(badge and send) and badge["x"] + badge["width"] <= send["x"] + 1
                  and abs((badge["y"] + badge["height"] / 2) - (send["y"] + send["height"] / 2)) <= 6,
                  f"badge={badge} send={send}")
            check("F2 模型名不在页头（页头里查不到它）",
                  page.locator(".chat-header #chat-model-badge").count() == 0
                  and bool(header))
            check("F3 模型名显示的是**当前生效**的模型，不是写死的占位",
                  page.locator("#chat-model-badge").inner_text().strip() == "deepseek-flash",
                  page.locator("#chat-model-badge").inner_text())

            print("=== G. 用量行：位置、内容、颜色、字号（都是**渲染后**的值）===")
            strip = page.locator("#usage-strip").bounding_box()
            composer = page.locator(".composer").bounding_box()
            check("G1 用量行**在输入框下方**（几何位置，不是源码顺序）",
                  bool(strip and composer) and strip["y"] >= composer["y"] + composer["height"] - 2,
                  f"strip={strip} composer={composer}")
            check("G2 用量行可见（没有 hidden / 没被裁掉）",
                  page.locator("#usage-strip").is_visible())
            cache_text = page.locator("#usage-cache").inner_text().strip()
            rate_text = page.locator("#usage-rate").inner_text().strip()
            check("G3 缓存命中显示**本会话累计**的真值（3000 hit ⇒ 3.0k）",
                  cache_text == "缓存命中 3.0k tok", cache_text)
            check("G4 命中率 75%（3000/4000）且按「好」档上色",
                  rate_text == "75%"
                  and page.locator("#usage-rate").get_attribute("data-level") == "good",
                  f"{rate_text} / {page.locator('#usage-rate').get_attribute('data-level')}")

            print("=== H. 一轮真实问答之后：上下文那段**画出来了** ===")
            page.fill("#composer-input", "只回答两个字：收到")
            page.click("#btn-send")
            page.wait_for_timeout(1200)
            ctx_text = page.locator("#usage-context").inner_text().strip()
            check("H1 上下文段显示 本轮真值 / 窗口（65536 / 131072 ⇒ 66k / 131k，50%）",
                  "66k" in ctx_text and "131k" in ctx_text and "50%" in ctx_text, ctx_text)
            check("H2 50% 落在 warn 档（接近阈值要变色提醒）",
                  page.locator("#usage-context").get_attribute("data-level") == "warn",
                  str(page.locator("#usage-context").get_attribute("data-level")))
            check("H3 圆环真的画了（conic-gradient + 比例变量）",
                  "conic-gradient" in page.evaluate(
                      "() => getComputedStyle(document.querySelector('#usage-context .usage-ring'))"
                      ".backgroundImage")
                  and page.evaluate(
                      "() => document.querySelector('#usage-context .usage-ring')"
                      ".style.getPropertyValue('--usage-ratio')") == "0.5",
                  page.evaluate("() => getComputedStyle(document.querySelector("
                                "'#usage-context .usage-ring')).backgroundImage")[:80])
            check("H4 缓存段仍是**服务端累计真值**，没被这一轮的事件数字顶掉",
                  page.locator("#usage-rate").inner_text().strip() == "75%"
                  and "3.0k" in page.locator("#usage-cache").inner_text(),
                  f"rate={page.locator('#usage-rate').inner_text()} "
                  f"cache={page.locator('#usage-cache').inner_text()}")

            print("=== I. 用户抱怨过的那件事：字号 ===")
            size = page.evaluate(
                "() => parseFloat(getComputedStyle(document.querySelector('#usage-strip')).fontSize)")
            chip = page.evaluate(
                "() => parseFloat(getComputedStyle(document.querySelector('.model-chip')).fontSize)")
            check(f"I1 用量行渲染字号 {size}px ≥ 12px（「蚂蚁字」判据）", size >= 12,
                  f"{size}px")
            check(f"I2 模型名渲染字号 {chip}px ≥ 12px", chip >= 12, f"{chip}px")
            check("I3 用量行没有换行成多行（高度 ≈ 单行）",
                  bool(strip) and strip["height"] <= 26, str(strip and strip["height"]))

            check("I4 零 JS 错误（整段交互没抛出任何异常）", not errors, "; ".join(errors[:3]))
            page.screenshot(path=str(ROOT / "tmp" / "composer-strip.png"), full_page=False)
            browser.close()

        print(f"\n截图：{ROOT / 'tmp' / 'composer-strip.png'}（肉眼复核那行字的排版）")
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
        print(f"COMPOSER_UI: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS")
        for label in failed:
            print(f"  ✗ {label}")
        return 1
    print(f"COMPOSER_UI: {len(CHECKS)}/{len(CHECKS)} PASS")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
