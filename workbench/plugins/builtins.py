"""内置工具插件：与业务无关的通用能力（时间 / 读文件 / 读网页 / 请求用户确认）。

前三个工具从 `harness/tools.py` 迁到这里，行为有**一处收紧**：

- `read_local_file` 原来是**无限制的绝对路径读**——任何凭据文件都能被模型一句话读出来
  （而它的结果会原样进对话、进审计）。现在两档收紧：
  ① **敏感路径一律拒绝**（数据库 / 登录态 / token 缓存 / 账号备份 / 浏览器 profile /
     SSH 与云凭据 / 系统目录，见 `config.SENSITIVE_PATH_PARTS`——**全项目唯一那张表**）；
  ② **应用目录之外**只允许读"文本类后缀 + 体积上限"的文件，二进制与超大文件直接拒。

  为什么不干脆用确认门（`Risk.WRITE`）：那样**每一次合法读文件都要用户点一下**，
  而"读一份用户指定的报表/日志"是真实且高频的用法，过度打扰会让人习惯性点"放行"，
  反而把确认门用废。这里选择"按内容形态设边界"，把人的注意力留给真正改平台的操作
  （`factory_create` / `accounts_browser_open`）。
"""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from .. import config
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
    if not url.startswith(("http://", "https://")):
        raise ValueError("仅支持 http/https 地址")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True
    ) as client:
        res = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MogulWorkbench/1.0"},
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
    description="与业务无关的通用能力：时间、读本地文件、读网页正文、请求用户确认",
    version="1.1.0",
    tools=[
        pa.ToolSpec(
            name="get_current_time",
            description="获取当前日期与时间（本地时区）",
            args_model=pa.ZERO_ARGS,
            handler=get_current_time,
            risk=pa.Risk.READ,
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
            timeout_s=40,
            audit_extra=pa.audit_env_key("url"),
        ),
        pa.ToolSpec(
            name="request_user_confirmation",
            description=(
                "【高风险任务前置】在执行敏感、不可逆或会影响真实平台的操作**之前**，"
                "先向用户弹确认框并**等待他当场答复**（同意 / 拒绝）。"
                "适用场景：批量提交 / 删除 / 发布、修改账号或权限、提交表单、"
                "对生产环境生效的任何写入、以及用户没说清但后果较大的动作。"
                "用法：把将要执行的那次调用写进 target_tool + target_args，"
                "用户同意后**直接调用 target_tool**（参数必须完全一致）即可执行，"
                "不会再被问第二遍；用户拒绝或超时就**不要执行**，也不要绕过。"
                "注意：本工具买的是「先问再做」，**不能**用它代替风险门——"
                "write/dangerous 工具自己仍会走确认门，两者互补"
            ),
            args_model=ConfirmationArgs,
            handler=request_user_confirmation,
            risk=pa.Risk.READ,
            timeout_s=10,
            needs_confirmation=True,
            confirm_payload=confirmation_payload,
            audit_extra=pa.audit_env_key("target_tool"),
        ),
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
