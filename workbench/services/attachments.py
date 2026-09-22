"""附件的落盘 / 校验 / 留痕（用户交给 agent 的文件与图片）。

## 唯一真源是磁盘上的文件

`<RUNTIME_DIR>/attachments/<会话 id>/<att_id>__<安全名>`（原始字节）
`<RUNTIME_DIR>/attachments/<会话 id>/<att_id>.text`（解析出的正文）

数据库那行（迁移 21）只存**元数据 + 坐标**：名字、mime、字节数、sha256、解析状态、
正文长度、`locator` JSON。表被清掉还能靠扫目录重建，反过来不行——与 `skill_audit`
是同一条纪律。

## 三条不可妥协的校验（每一轮上传都过）

1. **类型白名单**（扩展名）+ **魔数复核**（`parsing.sniff_ok`）：扩展名可以骗人；
2. **大小**（`config.ATTACHMENTS_MAX_BYTES`）与**每轮个数**（`MAX_PER_TURN`）；
3. **路径不信任**：落盘名由服务端生成，会话目录名只允许 `[A-Za-z0-9_-]`，
   读回时再用 `Path.is_relative_to` 复查（规则 22——字符串前缀判断会放过 `abc_evil`）。
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any

from .. import config, db
from . import parsing

logger = logging.getLogger("workbench.attachments")

#: 会话目录名的合法形状：**只允许** uuid/hex/连字符下划线（不接受用户输入的任何路径成分）
_SAFE_CONV = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PENDING_CONVERSATION = "_pending"
"""上传时还没有会话 id（新会话的第一条消息之前）时的落点。

⚠️ **它是"未绑定"的哨兵，不是一个真的会话**。2026-09-22 真机联调踩到的坑：
`attach_to_conversation` 原本用 `if not att.get("conversation_id")` 判断"还没绑"，
而 `"_pending"` 是**真值** ⇒ 永远不绑 ⇒ 附件被留在 `_pending` 下，
会话里的 `attachment_list` 看不到它、`attachment_read` 还回一句"属于另一个会话"。
判据：`tests/test_attachments.py::test_pending_upload_is_bound_on_first_send`。
"""

#: 消息正文里的附件引用标记。**它是 Python→JS→模型三方的契约**（改它要同步三处）：
#: `routes_chat._model_history` 按它展开成 content parts，前端按它渲染回执。
MARKER_RE = re.compile(r"\[附件 (att-[0-9a-f]{8})：([^\]\n]{0,120})\]")


def attachments_root() -> Path:
    return config.ATTACHMENTS_DIR


def conversation_dir(conversation_id: str) -> Path:
    safe = str(conversation_id or "")
    if not _SAFE_CONV.match(safe):
        raise ValueError(f"会话 id 形状不合法，拒绝落盘：{safe[:40]!r}")
    path = attachments_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_id() -> str:
    return "att-" + secrets.token_hex(4)


def slug(name: str) -> str:
    """把原名压成**安全的显示名**：只留中文/字母/数字/点/下划线/连字符，其余换 `_`。"""
    base = Path(str(name or "file")).name
    cleaned = re.sub(r"[^\w.\u4e00-\u9fff-]+", "_", base, flags=re.UNICODE).strip("._")
    return (cleaned or "file")[:80]


def stored_name(att_id: str, name: str) -> str:
    return f"{att_id}__{slug(name)}"


def text_path(att: dict[str, Any]) -> Path:
    return conversation_dir(str(att.get("conversation_id") or "")) / f"{att['id']}.text"


def raw_path(att: dict[str, Any]) -> Path:
    """附件原始文件的绝对路径（**复查过包含关系**，越界即抛）。"""
    directory = conversation_dir(str(att.get("conversation_id") or ""))
    path = (directory / str(att.get("stored_name") or "")).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("附件路径越界，拒绝读取")
    return path


def reference_marker(att: dict[str, Any]) -> str:
    return f"[附件 {att['id']}：{slug(str(att.get('name') or ''))}]"


def iter_markers(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in MARKER_RE.finditer(str(text or ""))]


# ---------------------------------------------------------------- 写入


def store(*, conversation_id: str, name: str, data: bytes) -> dict[str, Any]:
    """校验 → 落盘 → 解析 → 入库。**任何拒绝都抛 ValueError**（路由转 400/413）。"""
    filename = str(name or "").strip() or "file"
    ext = parsing.extension_of(filename)
    if ext not in parsing.SUPPORTED:
        raise ValueError(
            f"不支持的文件类型 {ext or '（无扩展名）'}；"
            f"支持：{'、'.join(sorted(parsing.SUPPORTED))}")

    size = len(data)
    if size == 0:
        raise ValueError("文件是空的（0 字节）——请重新导出后再传")
    if size > config.ATTACHMENTS_MAX_BYTES:
        raise ValueError(
            f"文件过大：{size / 1048576:.1f} MB > 上限 "
            f"{config.ATTACHMENTS_MAX_BYTES / 1048576:.0f} MB")

    existing = list_for(conversation_id)
    if len(existing) >= config.ATTACHMENTS_MAX_PER_TURN * 4:
        raise ValueError(
            f"这个会话的附件太多了（{len(existing)} 个）——请先删掉不再需要的，"
            "或开一个新会话")

    if not parsing.sniff_ok(data, ext):
        raise ValueError(
            f"文件内容与扩展名不符（{ext}）：可能改了后缀、或文件损坏 ⇒ 已拒收，"
            "请传原始文件")
    if ext in (".docx", ".xlsx"):
        directory = conversation_dir(conversation_id)
        probe = directory / f".probe-{secrets.token_hex(4)}{ext}"
        probe.write_bytes(data)
        try:
            if not parsing.zip_is_intact(probe):
                raise ValueError(f"这不是一个有效的 {ext} 文件（zip 结构不完整）")
        finally:
            probe.unlink(missing_ok=True)

    att_id = new_id()
    directory = conversation_dir(conversation_id)
    target = directory / stored_name(att_id, filename)
    target.write_bytes(data)

    parsed = parsing.parse_file(target, name=filename)
    text_file = directory / f"{att_id}.text"
    if parsed.text:
        text_file.write_text(parsed.text, encoding="utf-8")
    else:
        text_file.unlink(missing_ok=True)

    row = {
        "id": att_id,
        "conversation_id": conversation_id,
        "name": filename,
        "stored_name": target.name,
        "mime": _mime_for(ext),
        "kind": parsed.kind,
        "bytes": size,
        "sha256": hashlib.sha256(data).hexdigest(),
        "status": "parsed" if parsed.text else "stored",
        "parse_note": parsed.note,
        "text_chars": len(parsed.text),
        "locator": parsing.locator_json(parsed),
        "created_at": int(time.time() * 1000),
    }
    db.execute(
        "INSERT INTO attachments (id, conversation_id, name, stored_name, mime, kind,"
        " bytes, sha256, status, parse_note, text_chars, locator, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row["id"], row["conversation_id"], row["name"], row["stored_name"], row["mime"],
         row["kind"], row["bytes"], row["sha256"], row["status"], row["parse_note"],
         row["text_chars"], row["locator"], row["created_at"]),
    )
    return row


_MIME = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain",
    ".csv": "text/csv", ".docx": "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp",
}


def _mime_for(ext: str) -> str:
    return _MIME.get(ext, "application/octet-stream")


# ---------------------------------------------------------------- 读取


def get(att_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM attachments WHERE id = ?", (str(att_id),))


def list_for(conversation_id: str) -> list[dict[str, Any]]:
    return db.query(
        "SELECT * FROM attachments WHERE conversation_id = ? ORDER BY created_at",
        (str(conversation_id),),
    )


def parsed_text(att: dict[str, Any]) -> str:
    """读解析产物（上传时就落盘了，这里**不重新解析**）。"""
    try:
        return text_path(att).read_text(encoding="utf-8")
    except OSError:
        return ""


def attach_to_conversation(att_id: str, conversation_id: str) -> dict[str, Any] | None:
    """把附件绑到会话上（上传时可能还没有会话 id）。**只允许从未绑定改成已绑定。**

    "未绑定"的判定必须**同时认空串与 `_pending`**：前端在新会话的第一条消息之前
    上传时，落点就是这个哨兵（见 `PENDING_CONVERSATION` 的说明——只判空串会让它永远绑不上）。

    ## 绑定要**连文件一起搬**（2026-09-22 联调实测）

    正文是按会话目录落的（`<root>/<会话 id>/<id>__<名>`），而路径是**从这一行的
    `conversation_id` 推出来的**。所以只改 DB 不搬文件，读的时候就会去新会话目录里找
    一个还躺在 `_pending/` 下的文件 —— 表现是 `No such file or directory`
    （真机实测：图片问颜色时模型回"附件读取失败：No such file or directory"，
    而同一个会话里的 xlsx 因为上传时就带了会话 id，一切正常）。

    顺序：**先搬、搬成功再改库**。搬失败就保持"未绑定"（库与文件仍在同一处，
    不会出现"库里说在 A、文件其实在 B"这种最难查的状态）。
    """
    att = get(att_id)
    if att is None:
        return None
    owner = str(att.get("conversation_id") or "")
    if owner not in ("", PENDING_CONVERSATION) or not conversation_id:
        return att
    if not _move_between_conversations(att, owner or PENDING_CONVERSATION,
                                       str(conversation_id)):
        logger.warning("附件 %s 的文件搬不过去，保持未绑定（库与文件仍在原处）", att_id)
        return att
    db.execute("UPDATE attachments SET conversation_id = ? WHERE id = ?",
               (str(conversation_id), str(att_id)))
    return get(att_id)


def _move_between_conversations(att: dict[str, Any], from_conversation: str,
                                to_conversation: str) -> bool:
    """把附件的**两个**文件（原件 + 解析产物）搬到目标会话目录。同盘 rename，原子且快。"""
    try:
        source = conversation_dir(from_conversation)
        target = conversation_dir(to_conversation)
    except ValueError:
        return False
    moved_any = False
    for name in (str(att.get("stored_name") or ""), f"{att.get('id')}.text"):
        if not name:
            continue
        src = source / name
        if not src.is_file():
            continue
        dst = target / name
        try:
            if dst.exists():
                dst.unlink()
            src.replace(dst)
            moved_any = True
        except OSError:
            return False
    return moved_any or not (source / str(att.get("stored_name") or "")).exists()


def delete(att_id: str) -> bool:
    att = get(att_id)
    if att is None:
        return False
    try:
        raw_path(att).unlink(missing_ok=True)
        text_path(att).unlink(missing_ok=True)
    except (OSError, ValueError):
        # 文件删不掉也要把表里的记录删掉：留着一条指向不存在文件的记录，
        # 下一次"读附件"会给出一个看不出原因的失败。
        pass
    db.execute("DELETE FROM attachments WHERE id = ?", (str(att_id),))
    return True


def locator(att: dict[str, Any]) -> dict[str, Any]:
    import json

    try:
        value = json.loads(str(att.get("locator") or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
