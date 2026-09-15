"""内置工具插件：与业务无关的通用能力（时间 / 读文件 / 读网页）。

三个工具从 `harness/tools.py` 迁到这里，行为有**一处收紧**：

- `read_local_file` 原来是**无限制的绝对路径读**——任何凭据文件都能被模型一句话读出来
  （而它的结果会原样进对话、进审计）。现在两档收紧：
  ① **敏感路径一律拒绝**（凭据库/密钥/浏览器 cookie 库/系统目录，见 `_BLOCKED_PARTS`）；
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

#: 敏感路径片段：命中即拒（大小写不敏感）。这些是"一句话就能读走凭据"的位置，
#: 没有"用户其实想看它"的合理场景——要看请用户自己打开。
_BLOCKED_PARTS = (
    "\\windows\\system32\\config",
    "\\appdata\\local\\google\\chrome\\user data",
    "\\appdata\\local\\microsoft\\edge\\user data",
    "\\appdata\\roaming\\mozilla\\firefox\\profiles",
    "\\.ssh\\",
    "\\.aws\\",
    "\\.azure\\",
    "\\.gnupg\\",
    "\\secrets\\",
    "\\credentials",
    "\\cookies",
    "\\login data",
    "\\key3.db",
    "\\key4.db",
    "\\logins.json",
)

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
    """读文件的边界（见模块文档）：敏感路径拒绝；应用外只收文本类小文件。"""
    lowered = str(path).lower()
    if any(part in lowered for part in _BLOCKED_PARTS):
        raise ValueError(
            "拒绝读取：该路径属于本机凭据/浏览器数据区。"
            "需要里面的内容请由用户自己打开后粘贴，不要读取整个文件"
        )
    try:
        resolved = path.resolve()
        size = resolved.stat().st_size
    except OSError as exc:
        raise ValueError(f"文件不可读：{exc}") from exc
    if resolved.is_dir():
        raise ValueError("这是一个目录；请给出具体文件路径")
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


PLUGIN = pa.Plugin(
    id=PLUGIN_ID,
    title="内置工具",
    description="与业务无关的通用能力：时间、读本地文件、读网页正文",
    version="1.0.0",
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
                "读取本地文本文件（应用目录内的文件任意读；应用目录之外仅限文本类后缀，"
                "且拒绝凭据库/浏览器数据等敏感路径）"
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
    ],
)


def register(hub: pa.ToolHub) -> None:
    hub.add_plugin(PLUGIN)
