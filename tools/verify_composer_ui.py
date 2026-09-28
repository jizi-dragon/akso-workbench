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

import base64
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


def read_png(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """解一张非交错的 8 位 PNG → (宽, 高, 逐像素 RGBA)。

    **为什么自己写**：venv 里没有 Pillow / numpy（`import PIL` → ModuleNotFoundError，
    2026-09-22 实测），而"那个圆环到底填了多少"这个问题**只能看像素**——
    任何 CSS 值断言都证明不了它（见 `_ring_fill` 的注释）。这段只支持 Playwright
    截图会产出的形态（8 位、color type 2/6、非交错），够用且不引入依赖。
    """
    import struct
    import zlib

    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    pos, idat, width, height, depth, color = 8, b"", 0, 0, 0, 0
    while pos < len(data):
        (size,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + size]
        if tag == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and interlace == 0 and color in (2, 6), (depth, color, interlace)
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + size

    channels = 4 if color == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * channels
    out: list[tuple[int, int, int, int]] = []
    prev = bytearray(stride)
    at = 0
    for _ in range(height):
        filt = raw[at]
        line = bytearray(raw[at + 1:at + 1 + stride])
        at += 1 + stride
        for i in range(stride):  # 五种滤波（PNG 规范 9.2）
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filt == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        for x in range(width):
            px = line[x * channels:(x + 1) * channels]
            out.append((px[0], px[1], px[2], px[3] if channels == 4 else 255))
        prev = line
    return width, height, out


def _nearest(px: tuple[int, int, int, int], palette: dict[str, tuple[int, int, int]]) -> str:
    """把一个像素判给最接近的**已知颜色**（比"看着像不像彩色"可靠得多）。"""
    best, best_d = "?", 1 << 30
    for name, rgb in palette.items():
        d = sum((px[i] - rgb[i]) ** 2 for i in range(3))
        if d < best_d:
            best, best_d = name, d
    return best


# 圆环的两个已知颜色（改 CSS 里的 token 时这里要跟着改；不一致会直接红）
TRACK_RGB = (0xD9, 0xE8, 0xF9)          # --ink-4：空的那一段
TONE_WARN_RGB = (0xD9, 0x77, 0x06)      # --amber：50% 落在 warn 档
# 比例 0.5 ⇒ conic-gradient 从 12 点顺时针填到 6 点：右半圈该填、左半圈该空
RING_ANGLES_FILLED = (30, 60, 90, 120, 150)
RING_ANGLES_EMPTY = (210, 240, 270, 300, 330)
RING_ANGLES = RING_ANGLES_FILLED + RING_ANGLES_EMPTY


def ring_fill(shot: bytes, tone: tuple[int, int, int]) -> dict:
    """量一个圆环截图：**该填的角**与**该空的角**在环带上各是什么颜色。

    为什么非要量像素（这条是 2026-09-22 用户报「图标应该按比例填充」之后补的）：
    当时的断言是 `backgroundImage` 里有 `conic-gradient` 且 `--usage-ratio == 0.5`
    ——**源码全对，画出来却不是那么回事**。内圈是用 `box-shadow: inset 0 0 0 2px <背景色>`
    抠的，而 inset 阴影只覆盖外缘，圆心那块**没被抠掉**；12px 的图标于是一整块实心小圆，
    比例只体现在"半块饼"上，肉眼根本读不出"这是一个按比例填充的圆"。
    这一类失败**只有像素能分辨**：`conic-gradient` 在场 ≠ 用户看见了半圈。

    自校准：元素截图是圆的**外接方框**，左上角必定在圆外 ⇒ 用那个像素当背景色，
    不写死任何颜色。环带取半径 72% 处（外缘与圆心之间），逐角度判给最近的已知颜色。
    """
    import math

    w, h, px = read_png(shot)
    bg = px[0][:3]
    palette = {"tone": tone, "track": TRACK_RGB, "bg": bg}
    cx, cy = (w - 1) / 2, (h - 1) / 2
    r_band = (w / 2.0) * 0.72
    r_hole = (w / 2.0) * 0.22

    def at(deg: float, radius: float) -> tuple[int, int, int, int]:
        rad = math.radians(deg)
        x = int(round(cx + radius * math.sin(rad)))
        y = int(round(cy - radius * math.cos(rad)))
        return px[y * w + x]

    around = {deg: _nearest(at(deg, r_band), palette) for deg in RING_ANGLES}
    return {
        "size": w,
        "bg": bg,
        "around": around,
        "hole": _nearest(at(45, r_hole), palette),
        "hole_emptyside": _nearest(at(225, r_hole), palette),
    }


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


# —— SSE 夹具：与 `routes_chat._chat_events` 发出的**同一批事件、同一批键、同一顺序** ——
# `start` 必须在最前面：前端在它里面拿到 `conversationId` 并清空附件条
# （第一版夹具漏了它 ⇒ 发完消息附件条不走，J7 直接红——夹具不忠实，测试就会撒谎）。
# `attachment` 逐条跟着 `start`（服务端就是在这里告诉界面"这几个文件跟着这条消息走了"）。
# `usage` 的四个键逐字抄自 `harness/loop.py::_context_usage()`；键名写错时前端只会安静地
# 显示「—」，所以夹具里的形状必须由后端那侧的用例来钉（见模块 docstring）。
SSE_EVENTS = [
    {"type": "start", "conversationId": "{conv}"},
    {"type": "status", "message": "生成中"},
    {"type": "token", "text": "收到。"},
    {"type": "final", "content": "收到。"},
    {"type": "usage", "usage": {
        "calls": 1, "promptTokens": 65536,
        "cacheHitTokens": 65000, "cacheMissTokens": 536,
        "completionTokens": 12, "reasoningTokens": 0,
        "hitRate": 0.9918, "summaryCalls": 0,
        # ⚠️ 这里的 `contextWindow: 131072` 是**合成事件的取值，不是产品默认值** ——
        # 它选 131072 只为让 `contextRatio` 正好 0.5，好让 H4–H6 那条像素断言
        # （"按 12 点顺时针填到 6 点，右半圈填、左半圈空"）有个确定的几何目标。
        # 产品默认窗口 2026-09-24 起是 `deepseek-flash` 的满窗口 1048576（见
        # `harness/types.py`）—— **不要把这里的数字改成它**，那会让比例变成 0.0625、
        # 像素断言失去判别力。两者本就不该同步：一个测渲染，一个定产品行为。
        "contextTokens": 65536, "contextWindow": 131072,
        "contextThreshold": 0.8, "contextRatio": 0.5,
    }},
    {"type": "done", "metricId": "m-verify", "titleUpdated": False},
]


def sse_fixture(conversation_id: str, attachments: list[tuple[str, str]]) -> str:
    """把事件拼成 SSE 正文。

    两处必须是**真的**，否则测试会撒谎：
    - `conversationId`：前端拿到它就去 `/api/agent/cache?conversationId=…` 取本会话累计，
      填个假 id 会让用量行当场变回「—」（H7 红——那是夹具的错，不是产品的错）；
    - `attachment`：真实 `_chat_events` 是**逐个附件**发一条（`for att_id, name in
      iter_markers(user_text)`），漏掉它们等于把"界面能画出附件"从测试里挖掉。
    """
    events = [{k: (conversation_id if v == "{conv}" else v) for k, v in ev.items()}
              for ev in SSE_EVENTS]
    start = events[0]
    rest = events[1:]
    markers = [{"type": "attachment", "attachmentId": aid, "name": name}
               for aid, name in attachments]
    return "".join("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                   for ev in [start, *markers, *rest])


def tiny_png() -> bytes:
    """8×8 纯红 PNG：走**真**上传链路（服务端会验魔数），且足够小。"""
    import struct
    import zlib

    size = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


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
            # 4× 密度：圆环那个图标只有 14 CSS px，按 1× 截图量不出"哪半圈是什么颜色"
            page = browser.new_page(viewport={"width": 1180, "height": 820},
                                    device_scale_factor=4)
            errors: list[str] = []
            sent_bodies: list[str] = []
            # SSE 夹具要用的"这一轮到底带了哪几个附件"——上传完成之后才填得上，
            # 所以用一个可变容器给路由闭包读（路由在页面加载前就注册了）
            feed: dict[str, list[tuple[str, str]]] = {"attachments": []}
            page.on("pageerror", lambda e: errors.append(str(e)))

            # 拦截 SSE：**只拦这一条**，其余请求照常打到真服务（静态资源、/api/agent/cache
            # 都是真的）。顺带把**请求体**留下来——J5 要证的就是"附件 id 真在里面"。
            def _fake_stream(route) -> None:
                sent_bodies.append(route.request.post_data or "")
                route.fulfill(status=200, headers={"Content-Type": "text/event-stream"},
                              body=sse_fixture(conv_id, feed["attachments"]))

            page.route("**/api/chat/stream", _fake_stream)
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
            check("H1 上下文段 = 图标 + 占用比例（50%）",
                  page.locator("#usage-context .usage-ring").count() == 1
                  and "50%" in ctx_text, ctx_text)
            check("H2 精确 token 数没有丢，挪进了悬浮提示",
                  "66k" in page.locator("#usage-context").get_attribute("title", timeout=3000)
                  and "131k" in page.locator("#usage-context").get_attribute("title"),
                  str(page.locator("#usage-context").get_attribute("title")))
            check("H3 50% 落在 warn 档（接近阈值要变色提醒）",
                  page.locator("#usage-context").get_attribute("data-level") == "warn",
                  str(page.locator("#usage-context").get_attribute("data-level")))

            # 图标**只有像素能判**：4× 密度截图 → 自写 PNG 解码 → 逐角度判色
            ring_png = page.locator("#usage-context .usage-ring").screenshot()
            (ROOT / "tmp" / "composer-ring-4x.png").write_bytes(ring_png)
            ring = ring_fill(ring_png, TONE_WARN_RGB)
            filled_ok = [a for a in RING_ANGLES_FILLED if ring["around"][a] == "tone"]
            empty_ok = [a for a in RING_ANGLES_EMPTY if ring["around"][a] == "track"]
            check(f"H4 比例内那半圈**真的是填充色**（{len(filled_ok)}/5 个角度）",
                  len(filled_ok) == len(RING_ANGLES_FILLED),
                  f"图 {ring['size']}px，逐角度判定 {ring['around']}")
            check(f"H5 比例外那半圈**真的是空轨道**（{len(empty_ok)}/5 个角度）"
                  "——「一整块实心圆」与「外缘一圈背景色」都会在这里红",
                  len(empty_ok) == len(RING_ANGLES_EMPTY),
                  f"逐角度判定 {ring['around']}，背景 {ring['bg']}")
            check("H6 圆心是空的（圆环不是实心饼）",
                  ring["hole"] == "bg",
                  f"半径 22% 处判成 {ring['hole']}（背景 {ring['bg']}）")
            check("H7 缓存段仍是**服务端累计真值**，没被这一轮的事件数字顶掉",
                  page.locator("#usage-rate").inner_text().strip() == "75%"
                  and "3.0k" in page.locator("#usage-cache").inner_text(),
                  f"rate={page.locator('#usage-rate').inner_text()} "
                  f"cache={page.locator('#usage-cache').inner_text()}")

            print("=== J. 三条输入入口：不是「源码里有」，而是**真的能把文件送进请求体** ===")
            # 目标①（用户 2026-09-22 下达的三条保障之一）此前只有**静态**判据
            # （`verify_attachments.py` 的 A1–A5 在源码里找字样）。而"按钮在场"与
            # "粘贴真的能收文件"是两件事——同 PITFALLS #87 的教训。这一组走**真手势**：
            # 真点按钮（expect_file_chooser）、真派发 paste/drop（带真 File 对象的 DataTransfer），
            # 再按发送，然后**读拦截下来的请求体**，断言 `attachmentIds` 真在里面。
            sent_bodies.clear()

            # ① 按钮：点它必须弹出文件选择器，选中的文件必须走完上传
            with page.expect_file_chooser() as chooser_info:
                page.click("#btn-attach")
            chooser_info.value.set_files({"name": "规则.md", "mimeType": "text/markdown",
                                          "buffer": "# 报销规则\n第一条：培训结束 3 日内提交。\n".encode()})
            # 等**那一个** chip：附件条上先出现的是「◌ 正在上传解析 1 个…」，
            # 按 `.attach-name` 的第一个去断言会抓到它（第一版就是这么假红的）
            page.wait_for_function(
                "() => [...document.querySelectorAll('.attach-chip')]"
                ".some((c) => c.querySelector('.attach-name')?.textContent === '规则.md'"
                " && c.querySelector('.attach-meta')?.textContent.includes('已解析'))",
                timeout=15000)
            check("J1 按钮入口：点「📎 附件」真的弹选择器，选中的文件进附件条且显示解析状态",
                  page.locator(".attach-chip .attach-name").first.inner_text() == "规则.md",
                  page.locator(".attach-chip").first.inner_text().replace("\n", " / "))

            # ② 粘贴：**截图之后最自然的动作**——所以这里粘的就是一张真 PNG
            # （不是 md：真实用法里 Ctrl+V 粘的多半是截图，而图片走的是另一条分支：
            #   `image_url` part + 缩略图 chip）
            pasted = page.evaluate(
                """(b64) => {
                  const bin = atob(b64);
                  const bytes = new Uint8Array(bin.length);
                  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
                  const dt = new DataTransfer();
                  dt.items.add(new File([bytes], "截图.png", {type: "image/png"}));
                  const input = document.getElementById('composer-input');
                  return input.dispatchEvent(new ClipboardEvent('paste',
                    {clipboardData: dt, bubbles: true, cancelable: true}));
                }""",
                base64.b64encode(tiny_png()).decode(),
            )
            page.wait_for_function(
                "() => [...document.querySelectorAll('.attach-name')]"
                ".some((el) => el.textContent === '截图.png')", timeout=15000)
            # `dispatchEvent` 返回 false ⇔ handler 调了 `preventDefault`：
            # 不调的话浏览器会把**文件名当成文字**插进输入框，用户会看到一串乱码夹在问题里
            check("J2 粘贴入口：粘贴的**图片**真的被收下，且 preventDefault 生效"
                  "（文件名没被塞进输入框）",
                  pasted is False, f"dispatchEvent → {pasted}")

            # ③ 拖拽：dragover 要有视觉反馈，drop 要真的收下
            dragged = page.evaluate(
                """() => {
                  const dt = new DataTransfer();
                  dt.items.add(new File(["姓名,部门\\n张三,研发部"], "拖进来的.csv",
                                        {type: 'text/csv'}));
                  const wrap = document.getElementById('composer-wrap');
                  wrap.dispatchEvent(new DragEvent('dragover',
                    {dataTransfer: dt, bubbles: true, cancelable: true}));
                  const highlighted = wrap.classList.contains('is-dropping');
                  wrap.dispatchEvent(new DragEvent('drop',
                    {dataTransfer: dt, bubbles: true, cancelable: true}));
                  return {highlighted, cleared: !wrap.classList.contains('is-dropping')};
                }"""
            )
            page.wait_for_function(
                "() => [...document.querySelectorAll('.attach-name')]"
                ".some((el) => el.textContent === '拖进来的.csv')", timeout=15000)
            check("J3 拖拽入口：拖进来有高亮、松手后收起，并且文件真的进附件条",
                  dragged["highlighted"] and dragged["cleared"], str(dragged))
            check("J4 三条入口的文件都在附件条里（3 个）",
                  page.locator(".attach-chip").count() == 3,
                  f'{page.locator(".attach-chip").count()} 个')

            # 发送 → 读**拦截下来的请求体**：这才是"能正常输入"的终点。
            # 先把附件清单取回来喂给 SSE 夹具：真实的 `_chat_events` 是**逐个附件**发
            # `attachment` 事件的（`for att_id, name in iter_markers(user_text)`），
            # 夹具漏了那几个事件，就等于把"界面能画出附件"这件事从测试里挖掉了。
            listed = client.get("/api/attachments",
                                params={"conversationId": conv_id}).json()
            feed["attachments"] = [(a["id"], a["name"]) for a in listed["attachments"]]
            page.fill("#composer-input", "看看这三个文件")
            page.click("#btn-send")
            page.wait_for_timeout(1500)
            body = json.loads(sent_bodies[-1]) if sent_bodies else {}
            ids = body.get("attachmentIds") or []
            check("J5 发送时三条入口的文件都进了请求体（attachmentIds 3 个）",
                  len(ids) == 3, json.dumps(body, ensure_ascii=False)[:200])
            check("J6 服务端为这三个文件真的落了盘（按会话查得到）",
                  listed.get("total") == 3
                  and {a["name"] for a in listed["attachments"]}
                  == {"规则.md", "截图.png", "拖进来的.csv"},
                  json.dumps(listed, ensure_ascii=False)[:200])
            check("J7 发送之后附件条清空（它们已经绑在那条消息上了）",
                  not page.locator("#attach-strip").is_visible())

            # 「我发过哪几个文件」必须留在消息里 —— 这是 `attachment` 事件存在的唯一理由，
            # 而前端此前**没人接它**（2026-09-22 由这一组查出来）。
            live_names = page.locator(".message-row.user .msg-attach .attach-name").all_inner_texts()
            check("J8 消息气泡里画出这三个附件（服务端的 attachment 事件被接住了）",
                  sorted(live_names) == ["截图.png", "拖进来的.csv", "规则.md"],
                  str(live_names))
            check("J9 图片给缩略图、文档给文件图标（两种 chip 都画对了）",
                  page.evaluate(
                      "() => {const c=[...document.querySelectorAll('.message-row.user .msg-attach')];"
                      " return {thumbs: c.filter(x=>x.querySelector('img.attach-thumb')).length,"
                      " glyphs: c.filter(x=>x.querySelector('.attach-glyph')).length};}"
                  ) == {"thumbs": 1, "glyphs": 2},
                  page.evaluate("() => [...document.querySelectorAll('.message-row.user .msg-attach')]"
                                ".map(x=>x.textContent).join(' | ')"))

            # 刷新后由库里那条带标记的消息画回来：标记**不能**漏成原文（`[附件 att-…]`）。
            # 这里要**放开拦截**、走真服务端：标记是 `_chat_events` 写进库的（`_request_history`），
            # 而被拦下来的那一轮**根本没到服务端**，库里干干净净——那样这条判据就成了空转。
            # 临时库里那把 Key 是假的 ⇒ 模型调用会立刻 401，不花一分钱 token。
            print("=== K. 刷新之后：库里只有标记，界面必须画回 chip ===")
            page.unroute("**/api/chat/stream")
            page.set_input_files("#attach-input", [
                {"name": "拍照.png", "mimeType": "image/png", "buffer": tiny_png()},
                {"name": "附件说明.md", "mimeType": "text/markdown",
                 "buffer": "# 说明\n这条是给刷新后那条判据用的。\n".encode()},
            ])
            page.wait_for_function(
                "() => ['拍照.png','附件说明.md'].every((n) =>"
                " [...document.querySelectorAll('.attach-name')].some((el) => el.textContent === n))",
                timeout=15000)
            page.fill("#composer-input", "这一轮走真服务端（验证标记落库）")
            page.click("#btn-send")
            page.wait_for_function(
                "() => document.getElementById('chat-running-badge').hidden === true", timeout=60000)
            msgs = client.get(f"/api/conversations/{conv_id}/messages").json()["messages"]
            stored = [m["content"] for m in msgs if m["role"] == "user"][-1]
            check("K1 服务端把这两个附件写成了标记（库里存的是 `[附件 att-…：名]`）",
                  stored.count("[附件 ") == 2 and "att-" in stored,
                  stored.replace("\n", " ⏎ ")[:200])

            page.reload(wait_until="networkidle")
            page.wait_for_selector("#conversation-list .conversation-title", timeout=15000)
            page.click("#conversation-list .conversation-title")
            page.wait_for_selector(".message-row.user .msg-attach", timeout=15000)
            bubble = page.locator(".message-row.user .bubble-user").last.inner_text()
            names = page.locator(".message-row.user .msg-attach .attach-name").all_inner_texts()
            hrefs = page.evaluate(
                "() => [...document.querySelectorAll('.message-row.user .msg-attach')]"
                ".map((a) => a.getAttribute('href'))")
            shapes = page.evaluate(
                "() => {const c=[...document.querySelectorAll('.message-row.user .msg-attach')];"
                " return {thumbs: c.filter(x=>x.querySelector('img.attach-thumb')).length,"
                " glyphs: c.filter(x=>x.querySelector('.attach-glyph')).length};}")
            check("K2 刷新后画回 2 个 chip（图片 + 文档），气泡里没有 `[附件 …]` 原文",
                  sorted(names) == ["拍照.png", "附件说明.md"]
                  and "附件 att-" not in bubble and "[附件" not in bubble,
                  f"names={names} bubble={bubble!r}")
            check("K3 chip 指向**真附件**（href 是各自的 id），且图片给缩略图、文档给图标",
                  len(set(hrefs)) == 2
                  and all(h and h.startswith("/api/attachments/att-") for h in hrefs)
                  and shapes == {"thumbs": 1, "glyphs": 1},
                  f"hrefs={hrefs} shapes={shapes}")
            page.screenshot(path=str(ROOT / "tmp" / "composer-attachments.png"), full_page=False)

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
            # 两张图都留给**肉眼复核**：整页看排版，4× 的元素图看那个圆环与它旁边那行字
            page.locator("#usage-strip").screenshot(
                path=str(ROOT / "tmp" / "composer-strip-4x.png"))
            page.screenshot(path=str(ROOT / "tmp" / "composer-strip.png"), full_page=False)
            browser.close()

        print(f"\n截图：{ROOT / 'tmp' / 'composer-strip.png'}（整页）"
              f"与 {ROOT / 'tmp' / 'composer-strip-4x.png'}（用量行 4×）")
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
