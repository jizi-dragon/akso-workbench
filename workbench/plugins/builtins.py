"""内置工具插件：与业务无关的通用能力（时间 / 读文件 / 读网页 / 读附件 / 请求用户确认）。

前三个工具从 `harness/tools.py` 迁到这里，行为有**一处收紧**：

- `read_local_file` 原来是**无限制的绝对路径读**——任何凭据文件都能被模型一句话读出来
  （而它的结果会原样进对话、进审计）。现在两档收紧：
  ① **敏感路径一律拒绝**（数据库 / 登录态 / token 缓存 / 账号备份 / 浏览器 profile /
     SSH 与云凭据 / 系统目录，见 `config.SENSITIVE_PATH_PARTS`——**全项目唯一那张表**）；
  ② **应用目录之外**只允许读"文本类后缀 + 体积上限"的文件，二进制与超大文件直接拒。

  为什么不干脆用确认门（`Risk.WRITE`）：那样**每一次合法读文件都要用户点一下**，
  而"读一份用户指定的报表/日志"是真实且高频的用法，过度打扰会让人习惯性点"放行"，
  反而把确认门用废。这里选择"按内容形态设边界"，把人的注意力留给真正改平台的操作。
  （2026-10-19：内置浏览器下线后产品暂无 WRITE 工具；确认门机制仍在，接入写工具时生效。）

2026-09-22 新增两个附件工具（用户下达「核心 agent 能力增强：用户会直接传图片或文件」）：
`attachment_list` / `attachment_read`。它们与 `read_local_file` 的分工是：
**用户交上来的**（有受控的落盘位置与会话归属）走附件工具；**用户机器上的任意路径**走
`read_local_file`（敏感路径仍然一律拒）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .. import config, net_guard
from .. import plugin_api as pa

PLUGIN_ID = "builtin"

MAX_PAGE_CHARS = 12_000
MAX_LOCAL_FILE_BYTES = 2 * 1024 * 1024

#: 敏感路径表：**已收归 `config.SENSITIVE_PATH_PARTS` 唯一事实源**（2026-09-20）。
#:
#: 这里原来有**第二张**表（只有 Chrome/Edge/Firefox profile 那类**应用之外**的路径），
#: 于是本工具（`risk=read`，**不过确认门**）能**完整读出**：
#:
#:   - `runtime/browser-states/*.json`  → cookie + 登录态
#:   - `runtime/egmp-tokens/*.json`     → 平台 JWT
#:   - 账号备份 `accounts.json`          → **含 Fernet 密钥与全部密文口令**
#:
#: 而**同一个路径沙箱是拦的**——两张表并存，只读工具用的是弱的那张。已实测复现。
#: 现在统一走 `config.is_sensitive_path()`，并有 `tests/test_sensitive_paths.py` 钉住
#: "两边是同一个对象"。

#: 应用目录之外允许读取的文本类后缀（防"把 exe 当文本读进来"）。
_TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".json", ".jsonl", ".csv", ".tsv", ".log", ".yaml", ".yml",
    ".xml", ".drawio", ".ini", ".cfg", ".conf", ".toml", ".html", ".htm",
    ".py", ".js", ".ts", ".css", ".sql", ".env.example",
})


class ReadLocalFileArgs(BaseModel):
    path: str = pa.Arg("要读取文件的绝对路径（工作台数据目录与洞察工作区之外需用户确认）")


class ReadWebPageArgs(BaseModel):
    url: str = pa.Arg("完整网页地址（http/https）")


class ConfirmationArgs(BaseModel):
    """`request_user_confirmation` 的参数。

    ⚠️ 字段名用 snake_case（与其余工具一致）：`_openai_schema()` 不过别名，
    模型看到的就是这些字面量。`TextPart` 那一类 camelCase 只出现在**出口**（SSE / API）。
    """

    question: str = pa.Arg(
        "要向用户确认的问题，一句话说清**将要做什么、影响谁**。"
        "例：「把 3 个对象（A01、A02、A03）提交到生产环境蓝图 B12，是否继续？」"
    )
    details: str = pa.Arg(
        "补充说明：影响面（几个对象 / 哪个环境 / 哪个账号）、能否回滚、"
        "以及为什么需要这一步。用户靠它决定点不点同意，别留空话",
        default="",
    )
    target_tool: str = pa.Arg(
        "可选：**将要执行的那个工具名**。填了就表示「用户同意后，我将调用它」。"
        "只有 write / dangerous 级工具才需要填（read 级会被拒）",
        default="",
    )
    target_args: dict[str, Any] = pa.Arg(
        "可选：传给 target_tool 的参数对象，必须与它自己声明的参数一致。"
        "用户同意的就是**这一组具体参数**；事后换参数要重新问",
        default_factory=dict,
    )
    timeout_s: float = pa.Arg(
        "可选：等用户答复的秒数（默认 300，上限 1800）", default=0
    )
    options_source: str = pa.Arg(
        "可选：**让服务端生成候选项**，界面会把它们画成一张张可点的卡片"
        "（而不是「同意 / 拒绝」）。目前只支持 `accounts` = 账号中心里的账号，"
        "卡片上带账号 / 环境 / 环境地址 / 状态，用户点哪张、那张的账号就是答复。"
        "用户不确定用哪个账号、或者你需要在多个环境之间选一个时用它。"
        "注意：**不要自己编候选项**（服务端按真表生成）；也**不要**和 target_tool 同时填"
        "——选哪一个是用户点完才知道的，没法提前写进 target_args",
        default="",
    )


async def get_current_time(_: BaseModel, ctx: pa.ToolContext) -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def is_inside_app(path: Path) -> bool:
    """路径是否落在"本应用自己的目录"里（数据目录 / 运行产物 / 洞察工作区）。

    用 `is_relative_to` 而不是字符串前缀——`.../insight/abc` 在字符串上"包含"
    `.../insight/abc_evil`（这正是 `routes_insight` 修过的那个守卫缺陷）。
    """
    roots: list[Path] = [Path(config.DATA_DIR).resolve(), Path(config.RUNTIME_DIR).resolve()]
    try:
        from ..services.egmp.insight import workspace as workspace_mod

        roots.append(Path(workspace_mod.workspace_root()).resolve())
    except Exception:  # noqa: BLE001 —— 工作区取不到不影响其余判定
        pass
    target = Path(path).expanduser().resolve()
    for root in roots:
        try:
            if target.is_relative_to(root):
                return True
        except (OSError, ValueError):
            continue
    return False


async def read_local_file(args: ReadLocalFileArgs, ctx: pa.ToolContext) -> str:
    path = Path(pa.require(args.path, "path 不能为空")).expanduser()
    _guard_path(path)
    with path.open("r", encoding="utf-8", errors="replace") as fp:
        return fp.read()


def _guard_path(path: Path) -> None:
    """读文件的边界。

    ## 顺序是**承重的**（2026-09-20 修正）

    改造前的顺序是「先查敏感表 → 再 `is_inside_app` 就 `return`」，于是：

    1. 敏感表是**本文件自己那张残表**，不含 `browser-states` / `egmp-tokens` /
       `workbench.db` / `accounts.json`；
    2. `is_inside_app()` 的根包含 `DATA_DIR` 与 `RUNTIME_DIR`——**而凭据恰好都在那里**；
    3. 第 2 步 `return` 之后，**连"文本后缀 + 2MB 上限"这两道也不生效**。

    ⇒ 结果是"应用目录内**完全不受限**"（而工具描述写的是"任意读"，读起来像受限）。
    实测：主库被返回 1,056,412 字符；三个真实形态的凭据产物被**完整**读出。

    ## 现在的顺序

        1. 解析路径
        2. **敏感表判定（与位置无关）**  ← 关键：放在 `is_inside_app` **之前**
        3. 目录 → 拒
        4. 应用目录内 → 放行（只豁免"后缀 + 体积"两道，**不豁免**敏感表）
        5. 应用目录外 → 文本类后缀 + 体积上限
    """
    try:
        resolved = path.resolve()
        size = resolved.stat().st_size
    except OSError as exc:
        raise ValueError(f"文件不可读：{exc}") from exc

    # ① 敏感表**与位置无关**：应用目录内也要拦（凭据正是在那里）。
    if config.is_sensitive_path(resolved):
        raise ValueError(
            "拒绝读取：该路径属于本机凭据 / 浏览器数据区"
            "（含数据库、登录态、token 缓存、账号备份、SSH/云凭据等）。"
            "需要里面的内容请由用户自己打开后粘贴，不要读取整个文件"
        )

    if resolved.is_dir():
        raise ValueError("这是一个目录；请给出具体文件路径")

    # ② 应用目录内只豁免"后缀 + 体积"，不豁免上面的敏感表。
    if is_inside_app(resolved):
        return

    if resolved.suffix.lower() not in _TEXT_SUFFIXES:
        raise ValueError(
            f"拒绝读取应用目录之外的 {resolved.suffix or '无后缀'} 文件："
            f"仅允许文本类（{', '.join(sorted(_TEXT_SUFFIXES)[:8])} …）"
        )
    if size > MAX_LOCAL_FILE_BYTES:
        raise ValueError(
            f"文件过大（{size / 1048576:.1f} MB > {MAX_LOCAL_FILE_BYTES / 1048576:.0f} MB）："
            "请让用户改成只提供需要的片段"
        )


async def read_web_page(args: ReadWebPageArgs, ctx: pa.ToolContext) -> str:
    url = str(pa.require(args.url, "url 不能为空"))
    # 出站守卫：**逐跳**校验。net_guard 的文档串里写了威胁模型与它的诚实边界
    # （限损措施，不是 DNS 绑定防护）。
    #
    # ⚠️ 这里刻意**不用** httpx 的 `follow_redirects=True`：那个写法只校验第一跳，
    # 一个完全正常的公网地址 `302 Location: http://127.0.0.1:18765/api/...`
    # 就绕过了整个守卫 —— 而首跳校验看起来一切正常。
    res = await net_guard.guarded_get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MogulWorkbench/1.0"},
        timeout=20.0,
    )
    res.raise_for_status()
    title, text = _extract_text(res.text)
    truncated = len(text) > MAX_PAGE_CHARS
    notice = "\n（正文过长，已截断）" if truncated else ""
    body = text[:MAX_PAGE_CHARS] + ("…" if truncated else "")
    return f"【{title or url}】\n来源：{url}{notice}\n\n{body}"


class _TextExtractor(HTMLParser):
    """把 HTML 约简为标题 + 纯文本（跳过脚本/样式/导航等噪音标签）。"""

    _SKIP = {"script", "style", "noscript", "iframe", "svg", "canvas", "nav", "footer", "header"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.parts: list[str] = []
        self.title = ""

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def _extract_text(html: str) -> tuple[str, str]:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:  # noqa: BLE001 —— 容错：残缺 HTML 也尽量抽取
        pass
    return extractor.title.strip(), "\n".join(extractor.parts)


# ---------------------------------------------------------------- 附件（用户传上来的文件）


class AttachmentListArgs(BaseModel):
    """无参数：列的就是**当前会话**的附件。"""


class AttachmentReadArgs(BaseModel):
    attachment_id: str = pa.Arg("附件 id（形如 att-1a2b3c4d，从 attachment_list 或用户消息里拿）")
    unit: str = pa.Arg("按什么取：chars（字符区间，默认）/ page / sheet / table / line",
                       default="chars")
    index: int = pa.Arg("page / table / line 的序号（从 1 起）", default=0, ge=0)
    name: str = pa.Arg("sheet 的工作表名（unit=sheet 时用）", default="")
    start: int = pa.Arg("unit=chars 时的起始字符偏移；unit=line 时的起始行号（从 1 起）",
                        default=0, ge=0)
    max_chars: int = pa.Arg("本次最多回多少字符", default=6000, ge=200, le=40000)


def _attachment_row(attachment_id: str, ctx: pa.ToolContext) -> dict[str, Any]:
    """取附件并做**会话归属校验**。

    为什么必须校验：附件是按会话存的，而模型手上的 id 可能是上一段对话里的。
    不校验的话，用户在 A 会话里传的报价单会被 B 会话的模型读出来——
    这是"数据串台"，它的表现是模型答得头头是道但内容来自别处。
    """
    from ..services import attachments as store

    att = store.get(str(attachment_id or "").strip())
    if att is None:
        raise ValueError(f"没有这个附件：{attachment_id}（用 attachment_list 看当前有哪些）")
    current = str(ctx.conversation_id or "")
    owner = str(att.get("conversation_id") or "")
    if current and owner and owner != current:
        raise ValueError(
            f"附件 {attachment_id} 属于另一个会话（不属于当前这段对话）⇒ 拒绝读取。"
            "请让用户在当前会话里重新上传")
    return att


async def attachment_list(_: BaseModel, ctx: pa.ToolContext) -> str:
    """当前会话里用户交过哪些文件（id / 名字 / 类型 / 规模 / 解析状态）。"""
    from ..services import attachments as store

    def run() -> list[dict[str, Any]]:
        rows = store.list_for(str(ctx.conversation_id or ""))
        out = []
        for r in rows:
            kind = str(r.get("kind") or "")
            row = {
                "id": r.get("id"), "name": r.get("name"), "kind": kind,
                "bytes": r.get("bytes"), "status": r.get("status"),
                "textChars": r.get("text_chars"), "note": r.get("parse_note") or "",
                "locator": store.locator(r),
            }
            # ⚠️ 图片**必须自带一句"没有文本不等于读不出"**（2026-09-22 真机实测）：
            # 模型看到 `textChars: 0` 会推断"这个文件是空的/损坏的"，然后**拒绝看图**
            # ——实录「看不出颜色 —— 这个文件只有 168 字节、textChars=0，像是空文件或损坏的图」，
            # 而像素其实就在它的上下文里（同一轮的下一问它又答对了"正方形"）。
            # 工具回什么，模型就信什么；这里少一句话，代价是整条图片链路看起来是坏的。
            if kind == "image":
                row["note"] = (f"{row['note']} 图片没有可提取文本（`textChars` 恒为 0）；"
                               "它的**像素直接随消息给你看**，能看就直接答，"
                               "不要用本工具判断它是否有效。").strip()
            out.append(row)
        return out

    rows = await asyncio.to_thread(run)
    return json.dumps({"total": len(rows), "attachments": rows}, ensure_ascii=False, default=str)


def _slice_by_unit(text: str, locator: dict[str, Any], args: AttachmentReadArgs) -> tuple[str, str]:
    """按坐标取一段，返回 (正文, 定位说明)。**取不到就明说取不到**（不返回空串糊过去）。"""
    unit = str(args.unit or "chars").lower()
    if unit == "chars":
        begin = int(args.start or 0)
        return text[begin:begin + args.max_chars], f"字符 {begin}–{begin + args.max_chars}"
    if unit == "line":
        lines = text.splitlines()
        begin = max(1, int(args.start or 1))
        chunk = lines[begin - 1: begin - 1 + max(1, args.max_chars // 60)]
        return "\n".join(chunk), f"第 {begin} 行起（共 {len(lines)} 行）"
    key, wanted = ("page", int(args.index or 0)) if unit == "page" else (None, 0)
    if unit == "page":
        entry = next((p for p in locator.get("pages") or []
                      if int(p.get("page") or 0) == wanted), None)
        label = f"第 {wanted} 页"
    elif unit == "sheet":
        entry = next((s for s in locator.get("sheets") or []
                      if str(s.get("sheet") or "") == str(args.name or "")), None)
        label = f"工作表 {args.name!r}"
    elif unit == "table":
        entry = next((t for t in locator.get("tables") or []
                      if int(t.get("table") or 0) == int(args.index or 0)), None)
        label = f"表 {int(args.index or 0)}"
    else:
        return "", f"未知的 unit={unit!r}（可选：chars / page / sheet / table / line）"
    if entry is None:
        return "", (f"**没有 {label}**（可能不存在、也可能超出解析范围）——"
                    "不要据此推断内容；用 locator 里列出的坐标重试")
    begin = int(entry.get("offset") or 0)
    chars = int(entry.get("chars") or 0)
    return text[begin:begin + min(chars, args.max_chars)], label


async def attachment_read(args: AttachmentReadArgs, ctx: pa.ToolContext) -> str:
    """按页 / 工作表 / 表 / 行 / 字符区间取附件正文——**长文件靠它，而不是靠一次性全塞**。"""
    from ..services import attachments as store

    def run() -> dict[str, Any]:
        att = _attachment_row(args.attachment_id, ctx)
        text = store.parsed_text(att)
        locator = store.locator(att)
        if not text:
            return {"ok": False, "attachment": att.get("name"), "kind": att.get("kind"),
                    "note": att.get("parse_note") or "这份附件没有可读文本",
                    "textChars": 0,
                    "hint": "图片直接看（多模态）；扫描件/空文件请让用户换一种格式"}
        chunk, label = _slice_by_unit(text, locator, args)
        clipped = len(chunk) > args.max_chars
        return {
            "ok": bool(chunk),
            "attachment": att.get("name"),
            "attachmentId": att.get("id"),
            "unit": args.unit, "range": label,
            "textChars": int(att.get("text_chars") or 0),
            "note": att.get("parse_note") or "",
            "text": chunk[:args.max_chars],
            "clipped": clipped,
            "hint": ("这一段已截到 max_chars，继续取请把 start/偏移往后推"
                     if clipped else "需要别的位置就换 unit/index（页 / 工作表 / 表 / 行）"),
        }

    result = await asyncio.to_thread(run)
    return json.dumps(result, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- 请求用户确认


def confirmation_payload(args: ConfirmationArgs) -> dict[str, Any]:
    """把参数翻译成确认内容（键名是**出口**的口径，见 `plugin_api.ConfirmPlan`）。

    只搬运，不判断：`target_tool` 是否存在、是否只读、参数是否合法，
    **全部由 `ToolHub._prepare_confirmation` 校验**——插件说了不算。
    """
    payload: dict[str, Any] = {"question": args.question, "details": args.details}
    if args.target_tool:
        payload["targetTool"] = args.target_tool
        payload["targetArgs"] = dict(args.target_args or {})
    if args.options_source:
        payload["optionsSource"] = args.options_source
    if args.timeout_s:
        payload["timeoutS"] = args.timeout_s
    return payload


async def request_user_confirmation(args: ConfirmationArgs, ctx: pa.ToolContext) -> str:
    """**这个 handler 永远不会被调用**，留着只为满足 `ToolSpec.handler` 必填。

    `needs_confirmation=True` 的工具由宿主在 `ToolHub.execute()` 里直接拦下并登记确认，
    用户的答复本身就是结果（`harness/loop.py::_apply_confirmation` 把它变成终态观测）。

    真被执行到 = "宿主拦下"这条路径坏了。此时**必须抛**：返回一句"已确认"会让模型
    以为用户真的同意了，而那正是本工具唯一要防的事。
    """
    raise RuntimeError(
        "request_user_confirmation 的 handler 不该被执行：确认结果由 harness/loop.py 回填。"
        "看到这条说明 needs_confirmation 的拦截路径没有生效"
    )


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="内置工具",
    description="与业务无关的通用能力：时间、读本地文件、读网页正文、读用户交的附件、请求用户确认",
    version="1.2.0",
    tools=[
        pa.ToolSpec(
            name="get_current_time",
            description="获取当前日期与时间（本地时区）",
            args_model=pa.ZERO_ARGS,
            handler=get_current_time,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            # ★ 全仓**唯一**标了并发的工具（计划 §7.4）：纯计算 —— 读一次系统时钟，
            # 没有 IO、没有共享状态、没有 `to_thread`。
            # 其余工具一律保持缺省 `False`（见 `ToolSpec.parallel_safe` 的文档串：
            # 核实一个标一个，别按"它看起来只是读"批量放行）。
            parallel_safe=True,
            timeout_s=5,
        ),
        pa.ToolSpec(
            name="read_local_file",
            description=(
                "读取本地文本文件。**敏感路径一律拒绝**（数据库/登录态/token 缓存/账号备份/"
                "浏览器 profile/SSH 与云凭据）；应用目录之外仅限文本类后缀且不超过 2MB"
            ),
            args_model=ReadLocalFileArgs,
            handler=read_local_file,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            timeout_s=30,
            audit_extra=pa.audit_env_key("path"),
        ),
        pa.ToolSpec(
            name="read_web_page",
            description=(
                "读取公开网页正文（用于核实 NMPA/FDA/ICH 等官方监管页面原文），"
                "返回标题、来源与正文文本"
            ),
            args_model=ReadWebPageArgs,
            handler=read_web_page,
            risk=pa.Risk.READ,
            trust=pa.Trust.UNTRUSTED,
            timeout_s=40,
            audit_extra=pa.audit_env_key("url"),
        ),
        pa.ToolSpec(
            name="attachment_list",
            description=(
                "**用户在对话里交给你的文件/图片**有哪些（id、名字、类型、规模、解析状态、坐标目录）。"
                "先看这个再决定怎么读——用户说「那份表」「这个 PDF」时，id 就在这里。"
                "只列**当前会话**的附件"
            ),
            args_model=AttachmentListArgs,
            handler=attachment_list,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            timeout_s=15,
        ),
        pa.ToolSpec(
            name="attachment_read",
            description=(
                "按坐标读附件正文：`unit=page/sheet/table/line/chars`。"
                "**长文件必须用它按需取**（内联在上下文里的只有开头一段）；"
                "取不到时它会明说「没有这一页/这张表」，**不要**据此猜内容。"
                "回答里要说出处（第几页 / 哪张表 / 第几行）时，坐标也从这里来"
            ),
            args_model=AttachmentReadArgs,
            handler=attachment_read,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            timeout_s=30,
            audit_extra=pa.audit_env_key("attachment_id", "unit"),
        ),
        pa.ToolSpec(
            name="request_user_confirmation",
            description=(
                "【高风险任务前置】在执行敏感、不可逆或会影响真实平台的操作**之前**，"
                "先向用户弹确认框并**等待他当场答复**（同意 / 拒绝），"
                "也可以用来**让用户在卡片上挑一个账号 / 环境**。"
                "适用场景：批量提交 / 删除 / 发布、修改账号或权限、提交表单、"
                "对生产环境生效的任何写入、用户没说清但后果较大的动作，"
                "以及**需要用户在多个账号或环境之间选一个**的时候（用 options_source）。"
                "用法：① 确认——把将要执行的那次调用写进 target_tool + target_args，"
                "用户同意后**直接调用 target_tool**（参数必须完全一致）即可执行，"
                "不会再被问第二遍；用户拒绝或超时就**不要执行**，也不要绕过。"
                "② 选择——填 options_source（如 `accounts`），用户点中的那张卡片的"
                "值就是答复；**拿到答复后就用它继续**，不要为同一件事再问一遍。"
                "注意：本工具买的是「先问再做」，**不能**用它代替风险门——"
                "write/dangerous 工具自己仍会走确认门，两者互补"
            ),
            args_model=ConfirmationArgs,
            handler=request_user_confirmation,
            risk=pa.Risk.READ,
            trust=pa.Trust.LOCAL,
            timeout_s=10,
            needs_confirmation=True,
            confirm_payload=confirmation_payload,
            audit_extra=pa.audit_env_key("target_tool"),
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
