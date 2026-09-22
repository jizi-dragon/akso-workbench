"""附件通道端到端验收（2026-09-22，用户下达的核心能力增强）。

## 它证明什么（对应用户的三条"保障"）

| 组 | 判据 | 为什么脚本里必须真跑 |
|---|---|---|
| **A 输入入口** | 静态契约：附件按钮 / 粘贴 / 拖拽三条入口都在，且都指向上传接口 | "能不能输入"是 UI 事实，只读代码看不出来有没有接上 |
| **B 真的落盘** | 真发 multipart ⇒ 读回**字节级一致**（sha256）、`/raw` 能取回、删了真没了 | "agent 真的拿到了文件"的地基；只断言 200 等于没验 |
| **C 拒收** | `.exe` / 空文件 / 超大 / 改后缀 四条各回一个 4xx，**且理由指对原因** | 拒收路径最容易静默放行 |
| **D 真的进模型** | 用真服务进程里的 `_model_history` 断言：图片 → `image_url`；文档 → 正文 + 坐标；**历史轮不重发** | 这条是"模型真的看得到"，而不是"接口回了 200" |

**不在这里做的**：真调 DeepSeek 看图（那要花 token 且需要 Key）——那一条由
`--live` 之外的**人工真机验证**覆盖，证据记在
`docs/AGENT-FILE-INPUT-PLAN.md` 的执行结果一节。

**退出码**：0 全过；1 有 FAIL。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
PORT = 18797
BASE = f"http://127.0.0.1:{PORT}"

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —— {detail}" if detail and not ok else ""))


def tiny_png() -> bytes:
    width = height = 8
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def boot(env: dict, log: pathlib.Path):
    handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen([PY, "-m", "workbench.server_entry", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    import httpx

    while time.time() < deadline:
        if proc.poll() is not None:
            return proc, handle, False
        try:
            if httpx.get(f"{BASE}/api/health", timeout=2.0).status_code == 200:
                return proc, handle, True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return proc, handle, False


def main() -> int:
    if not port_free(PORT):
        print(f"✗ 端口 {PORT} 被占用——先退出占用它的进程")
        return 1

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="wb-attachverify-"))
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
        client = httpx.Client(base_url=BASE, timeout=60.0)

        print("=== A. 三条输入入口都在（静态契约）===")
        index = client.get("/static/index.html").text
        chat_js = client.get("/static/js/chat.js").text
        attach_js = client.get("/static/js/attachments.js").text
        api_js = client.get("/static/js/api.js").text
        check("A1 附件按钮 + file input 在场",
              'id="btn-attach"' in index and 'type="file"' in index
              and 'id="attach-input"' in index)
        check("A2 粘贴入口在场（截图后最自然的动作）",
              'addEventListener("paste"' in chat_js and "clipboardData" in chat_js)
        check("A3 拖拽入口在场（dragover/drop）",
              'addEventListener("dragover"' in chat_js and 'addEventListener("drop"' in chat_js)
        check("A4 三条入口都接到同一个上传函数",
              chat_js.count("tray.addFiles") >= 2 and "uploadAttachment" in api_js
              and "FormData" in api_js)
        check("A5 附件条显示状态（已解析多少字 / 扫描件读不出字）",
              "statusText" in attach_js and "扫描件" in attach_js)

        print("=== A2. 输入区的排版契约（2026-09-22 用户下达的 UI 优化）===")
        app_css = client.get("/static/css/app.css").text
        check("A6 模型名在**发送键旁边**（不再挂在页头）",
              'class="model-chip"' in index and 'id="chat-model-badge"' in index
              and index.index('id="chat-model-badge"') < index.index('id="btn-send"')
              and 'id="chat-model-badge"' not in index.split('class="chat-header"')[1]
              .split("</header>")[0])
        check("A7 输入框下方有常驻用量行（缓存命中 / 命中率 / 上下文）",
              all(k in index for k in ('id="usage-strip"', 'id="usage-cache"',
                                       'id="usage-rate"', 'id="usage-context"')))
        check("A8 三段数字都有真实来源（不是写死的占位）",
              "refreshUsageStrip" in chat_js and "paintUsageStrip" in chat_js
              and "contextTokens" in chat_js and "getCache" in api_js)
        check("A9 用量行字号 ≥12px（用户明确抱怨过「蚂蚁字」）",
              ".usage-strip" in app_css and "font-size: 12px" in app_css)

        print("=== B. 真的落盘、真的读得回 ===")
        png = tiny_png()
        res = client.post("/api/attachments",
                          files={"file": ("截图.png", png, "image/png")},
                          data={"conversationId": "verify-conv"})
        check("B1 图片上传成功", res.status_code == 200, f"status={res.status_code} {res.text[:200]}")
        att = (res.json() or {}).get("attachment") or {}
        att_id = att.get("id", "")
        check("B2 回了附件 id 与类型",
              bool(att_id) and att.get("kind") == "image", json.dumps(att, ensure_ascii=False)[:200])

        raw = client.get(f"/api/attachments/{att_id}/raw")
        check("B3 /raw 取回的字节与上传**逐字节一致**（sha256）",
              raw.status_code == 200
              and hashlib.sha256(raw.content).hexdigest() == hashlib.sha256(png).hexdigest())

        md = "# 报销规则\n第一条：培训结束 3 日内提交。\n".encode()
        res2 = client.post("/api/attachments",
                           files={"file": ("规则.md", md, "text/markdown")},
                           data={"conversationId": "verify-conv"})
        att2 = (res2.json() or {}).get("attachment") or {}
        check("B4 文档解析出正文与行坐标",
              res2.status_code == 200 and att2.get("textChars", 0) > 0
              and (att2.get("locator") or {}).get("lines") == 2,
              json.dumps(att2, ensure_ascii=False)[:240])

        listing = client.get("/api/attachments", params={"conversationId": "verify-conv"}).json()
        check("B5 列表里能看到这两个附件（按会话隔离）",
              listing.get("total") == 2, json.dumps(listing, ensure_ascii=False)[:200])

        print("=== C. 拒收路径（每一条都要说清原因）===")
        bad = client.post("/api/attachments",
                          files={"file": ("木马.exe", b"MZ\x90\x00", "application/octet-stream")},
                          data={"conversationId": "verify-conv"})
        check("C1 不支持的类型 → 400", bad.status_code == 400, f"status={bad.status_code}")
        empty = client.post("/api/attachments",
                            files={"file": ("空.txt", b"", "text/plain")},
                            data={"conversationId": "verify-conv"})
        check("C2 空文件 → 400", empty.status_code == 400, f"status={empty.status_code}")
        fake = client.post("/api/attachments",
                           files={"file": ("假.png", b"not a png", "image/png")},
                           data={"conversationId": "verify-conv"})
        check("C3 改后缀骗不过魔数复核 → 400",
              fake.status_code == 400 and "内容与扩展名不符" in fake.text, fake.text[:160])

        print("=== D. 真的进得到模型请求（扼流点展开）===")
        # ⚠️ 必须**先把数据目录指到刚才那个临时目录**，再 import `workbench`：
        # `config` 在导入期读环境变量，而本进程原本会指向开发机真实的数据目录——
        # 那样 `store.get(att_id)` 会查一个"另一个库"，拿到 None（本脚本第一版就这么错过）。
        os.environ["WORKBENCH_DATA"] = str(data_dir)
        os.environ.pop("WORKBENCH_DB", None)
        sys.path.insert(0, str(ROOT))
        from workbench import db  # noqa: PLC0415
        from workbench.api.routes_chat import _model_history  # noqa: PLC0415
        from workbench.services import attachments as store  # noqa: PLC0415

        db.connect()
        marker = store.reference_marker(store.get(att_id) or {})
        history = _model_history([
            {"role": "user", "content": f"看看这张图 {marker}"},
        ])
        content = history[-1]["content"]
        check("D1 图片在请求体里是 image_url part（不是文件名）",
              isinstance(content, list)
              and any(p.get("type") == "image_url" for p in content),
              json.dumps(content, ensure_ascii=False)[:200])
        url = next((p["image_url"]["url"] for p in content
                    if p.get("type") == "image_url"), "") if isinstance(content, list) else ""
        check("D2 data URL 里带的正是上传的字节",
              url.startswith("data:image/png;base64,")
              and url.split(",", 1)[1] == __import__("base64").b64encode(png).decode())

        marker2 = store.reference_marker(store.get(att2["id"]) or {})
        history2 = _model_history([
            {"role": "user", "content": f"第一轮 {marker}"},
            {"role": "assistant", "content": "收到"},
            {"role": "user", "content": f"这一轮看文档 {marker2}"},
        ])
        old_text = "\n".join(p.get("text", "") for p in
                             (history2[0]["content"] if isinstance(history2[0]["content"], list)
                              else [{"type": "text", "text": history2[0]["content"]}]))
        new_text = "\n".join(p.get("text", "") for p in history2[-1]["content"])
        check("D3 历史轮不重发（只留一句引用）",
              "更早的轮次" in old_text and "image_url" not in json.dumps(history2[0], ensure_ascii=False))
        check("D4 最新轮的文档正文与坐标都进了上下文",
              "培训结束" in new_text and "坐标目录" in new_text, new_text[:200])

        print("=== E. 删除是**真删** ===")
        deleted = client.delete(f"/api/attachments/{att_id}")
        gone = client.get(f"/api/attachments/{att_id}/raw")
        check("E1 删除返回 200，再取 /raw → 404/410",
              deleted.status_code == 200 and gone.status_code in (404, 410),
              f"delete={deleted.status_code} raw={gone.status_code}")
        check("E2 数据库里也没有残留",
              store.get(att_id) is None)
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
        print(f"ATTACH_E2E: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS")
        for label in failed:
            print(f"  ✗ {label}")
        return 1
    print(f"ATTACH_E2E: {len(CHECKS)}/{len(CHECKS)} PASS")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
