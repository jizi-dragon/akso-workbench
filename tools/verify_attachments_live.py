r"""真机联调（**要花 token**）：把图片与表格**真的发给模型**，看它能不能答对、会不会编。

这是附件链路唯一**不可替代**的那道门禁：`verify_attachments.py` 与
`tests/test_attachments.py` 证明的是"请求体里有没有那份内容"，而**模型有没有真的读到**
只有真调一次才知道（Python 侧全绿而模型答"我读不到"是这条链路上最可能的假通过）。

## 前置条件（不满足会明说，不会静默跳过）

1. 工作台**正在运行**（默认端口 18765；换个端口：`python tools\verify_attachments_live.py 18799`）；
2. **设置里配了真实可用的 DeepSeek Key**（临时库/假 Key 会得到 401）；
3. 它会往你的库里**建一条会话**并在结束时删掉（`--keep` 可保留）。

## 判据（全部是模型**不可能猜对**的东西）

| # | 输入 | 通过条件 |
|---|---|---|
| 1 | 64×64 纯 `#0000FF` PNG ⇒ 问颜色 | 答"蓝色"（**8×8 太小会被读成"紫色"**——那是感知边界，见 §6.2，不是链路缺陷） |
| 2 | 96×96 白底 + 正中 32×32 黑方块 ⇒ 问形状 | 答"正方形/方块"——**真的看见了**，不是猜颜色 |
| 3 | xlsx 里只有「市场部 8 人」⇒ 问人数 | 答"8" |
| 4 | 同一份 xlsx ⇒ 问"有没有研发部" | 答"没有"（**不许编造**） |
| 5 | 第 3 问的回答 | **不许撤回自己**（不得出现"瞎猜 / 没有真的读取 / 其实没看到"）——2026-09-22 真机实录：模型答对了前两问，第三轮却主动说"前面是瞎猜的"，根因是旧轮附件的那句引用话术（见 PITFALLS #89） |

用法：`python tools\verify_attachments_live.py [端口] [--keep]`
"""

from __future__ import annotations

import json
import struct
import sys
import zlib

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
KEEP = "--keep" in sys.argv
PORT = int(ARGS[0]) if ARGS else 18765
BASE = f"http://127.0.0.1:{PORT}"


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    r, g, b = rgb
    raw = b"".join(b"\x00" + bytes((r, g, b)) * size for _ in range(size))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def square_png(size: int = 96, square: int = 32) -> bytes:
    """白底 + 正中一个黑色方块：问"什么形状"时，答对才算**真的看见**（不是猜颜色）。"""
    rows = []
    lo = (size - square) // 2
    hi = lo + square
    for y in range(size):
        row = bytearray(b"\x00")
        for x in range(size):
            inside = lo <= x < hi and lo <= y < hi
            row += b"\x00\x00\x00" if inside else b"\xff\xff\xff"
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def xlsx_bytes() -> bytes:
    import io

    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "编制"
    sheet.append(["部门", "人数", "年度费用"])
    sheet.append(["生产部", 12, 3600])
    sheet.append(["质量部", 5, 1500])
    sheet.append(["市场部", 8, 2400])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def ask(client: httpx.Client, attachment_id: str, question: str,
        conversation_id: str = "") -> tuple[str, str]:
    """发一条带附件的消息，返回 (模型这一轮的完整文本, 会话 id)。

    会话 id 要**带回去复用**：每一问都新开会话的话，第二次问的附件属于上一段对话，
    工具会（正确地）拒绝读取——第一版联调就是这么把"隔离生效"误读成"读不到"的。
    """
    answer = ""
    conv = conversation_id
    with client.stream("POST", f"{BASE}/api/chat/stream",
                       json={"conversationId": conversation_id, "message": question,
                             "attachmentIds": [attachment_id]},
                       timeout=180.0) as response:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            if event.get("type") == "start":
                conv = event.get("conversationId") or conv
            elif event.get("type") == "token":
                answer += event.get("text", "")
            elif event.get("type") == "final":
                answer = event.get("content") or answer
            elif event.get("type") == "error":
                answer += f"\n[错误] {event.get('message')}"
    return answer.strip(), conv


def judge(answer: str, expect: str) -> tuple[bool, str]:
    """判断"真的读到了"：期望内容在场，**且**回答里没有"读不到/拒绝"这类话。

    为什么要后半条：第一版只看 `expect in answer`，而模型说的是
    "附件 `att-8b763c25` 属于另一个会话" —— 那句话里**恰好含 8**，于是判成了通过。
    判据写松了会造假绿，而这条联调的全部意义就是"它真的读到了吗"。
    """
    blocked = any(word in answer for word in ("读不到", "拒绝", "无法读取", "不能看", "无从"))
    return (expect in answer) and not blocked, ("未读到/被拒" if blocked else "")


def main() -> int:
    with httpx.Client(timeout=120.0) as client:
        health = client.get(f"{BASE}/api/health")
        print(f"服务 {BASE} 健康检查 = {health.status_code}")
        if health.status_code != 200:
            return 1

        print("\n=== 1) 图片：64×64 纯蓝，问它什么颜色 ===")
        up1 = client.post(f"{BASE}/api/attachments",
                          files={"file": ("色块.png", solid_png((0, 0, 255)), "image/png")},
                          data={"conversationId": ""})
        att1 = up1.json()["attachment"]
        print(f"上传：{att1['id']} {att1['name']} {att1['bytes']}B kind={att1['kind']}")
        answer1, conv = ask(client, att1["id"], "这张图片是什么颜色？只回答颜色两个字，不要解释。")
        print(f"会话：{conv}")
        print(f"模型回答：{answer1!r}")
        ok1, why1 = judge(answer1, "蓝")
        print(f"⇒ {'✅ 读到了' if ok1 else '❌ 没读到'} {why1}")

        print("\n=== 1b) 图片：白底正中一个黑方块，问它什么形状（真的看见了才算过）===")
        up1b = client.post(f"{BASE}/api/attachments",
                           files={"file": ("形状.png", square_png(), "image/png")},
                           data={"conversationId": conv})
        att1b = up1b.json()["attachment"]
        answer1b, conv = ask(client, att1b["id"],
                             "这张图里画的是什么形状？只回答形状，不要解释。", conv)
        print(f"模型回答：{answer1b!r}")
        ok1b = any(word in answer1b for word in ("方形", "正方形", "方块", "矩形"))
        print(f"⇒ {'✅ 看见了' if ok1b else '❌ 没看见'}")

        print("\n=== 2) 表格：数字只在文件里（市场部 8 人），问它 ===")
        up2 = client.post(f"{BASE}/api/attachments",
                          files={"file": ("编制.xlsx", xlsx_bytes(),
                                          "application/vnd.openxmlformats-officedocument."
                                          "spreadsheetml.sheet")},
                          data={"conversationId": conv})
        att2 = up2.json()["attachment"]
        print(f"上传：{att2['id']} {att2['name']} {att2['bytes']}B "
              f"status={att2['status']} 解析 {att2['textChars']} 字")
        answer2, conv = ask(client, att2["id"],
                            "附件里市场部有多少人？只回答数字，不要解释。", conv)
        print(f"模型回答：{answer2!r}")
        ok2, why2 = judge(answer2, "8")
        print(f"⇒ {'✅ 读到了' if ok2 else '❌ 没读到'} {why2}")

        print("\n=== 2b) 不许**撤回自己**：它不能声称前面的读取是瞎猜 ===")
        # 真机实录（2026-09-22）：第 3 问的回答后面挂着「前两轮关于色块和形状的图片，
        # 我并没有真的读取附件内容，回答是瞎猜的」。它前面**答对了**（纯蓝/正方形都不是
        # 能猜出来的），却把自己的正确感知否掉——根因是旧轮附件那句引用话术
        # （「已交过你、正文没有重复发送」对图片不成立）。这条判据就盯这个。
        disowned = [word for word in ("瞎猜", "猜的", "没有真的读取", "其实没看到", "编的")
                    if word in answer2]
        ok2b = not disowned
        print(f"⇒ {'✅ 没有撤回' if ok2b else '❌ 撤回了自己：' + '、'.join(disowned)}")

        print("\n=== 3) 负样本：文件里没有的东西必须说没有 ===")
        answer3, conv = ask(client, att2["id"],
                            "附件里有没有『研发部』？如果文件里没有这个词，请只回答：没有。", conv)
        print(f"模型回答：{answer3!r}")
        blocked3 = any(word in answer3 for word in ("读不到", "拒绝", "无法读取"))
        ok3 = ("没有" in answer3) and not blocked3
        print(f"⇒ {'✅ 没编造' if ok3 else '❌ 没读到或可能编造了'}")

    print("\n" + "=" * 60)
    print(f"LIVE_ATTACH: 颜色={ok1} 形状={ok1b} 表格={ok2} 不撤回={ok2b} 负样本={ok3}")
    if KEEP:
        print(f"会话保留：{conv}")
    else:
        try:
            with httpx.Client(timeout=30.0) as c:
                c.delete(f"{BASE}/api/conversations/{conv}")
            print(f"会话已删除（--keep 可保留）：{conv}")
        except Exception as error:  # noqa: BLE001 —— 清理失败不该影响结论
            print(f"⚠ 会话没能删掉（{error}）：{conv}")
    return 0 if (ok1 and ok1b and ok2 and ok2b and ok3) else 1


if __name__ == "__main__":
    raise SystemExit(main())
