"""工具层：PydanticAI 风格的声明式工具定义 + 注册表。

设计（借鉴 PydanticAI / OpenAI Agents SDK 的薄框架模式，不引入其依赖）：

    class ReadFileArgs(BaseModel):
        path: str = Field(description="要读取文件的绝对路径")

    @tool("读取本地文本文件的内容", args_model=ReadFileArgs)
    async def read_local_file(args: ReadFileArgs) -> str:
        ...

- JSON Schema 由 Pydantic 模型自动生成（单一事实来源，杜绝手写 schema 漂移）
- 模型传入的 arguments 在调用前经 Pydantic 校验，字段级错误直接回传给模型
- 工具函数只接收已校验的类型化参数，不再解析 dict
"""

from __future__ import annotations

import json
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from .types import ToolResult

MAX_PAGE_CHARS = 12000

ArgsModel = TypeVar("ArgsModel", bound=BaseModel)
ToolFunc = Callable[[Any], Awaitable[str]]


class Tool:
    """一个可被模型调用的工具：名称 + 描述 + 参数模型 + 实现函数。"""

    def __init__(self, name: str, description: str, args_model: type[BaseModel], func: ToolFunc):
        self.name = name
        self.description = description
        self.args_model = args_model
        self.func = func

    def to_api(self) -> dict:
        """转为 OpenAI function calling 的 tools 声明。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _openai_schema(self.args_model),
            },
        }

    async def execute(self, call_id: str, arguments_json: str) -> ToolResult:
        """校验参数并执行；任何失败都转为 is_error 观测，不抛出（不终止 Agent 循环）。"""
        try:
            raw = json.loads(arguments_json or "{}")
            if not isinstance(raw, dict):
                raise ValueError("arguments 必须是 JSON 对象")
            args = self.args_model.model_validate(raw)
        except ValidationError as exc:
            return ToolResult(
                tool_call_id=call_id,
                content=_validation_message(exc),
                is_error=True,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            return ToolResult(tool_call_id=call_id, content=f"参数解析失败：{exc}", is_error=True)

        try:
            content = await self.func(args)
            return ToolResult(tool_call_id=call_id, content=content, is_error=False)
        except Exception as exc:  # noqa: BLE001 —— 工具异常转为观测
            return ToolResult(tool_call_id=call_id, content=f"{type(exc).__name__}: {exc}", is_error=True)


def tool(description: str, args_model: type[BaseModel], *, name: str | None = None) -> Callable[[ToolFunc], Tool]:
    """声明式工具装饰器。name 缺省时取函数名（自动转下划线原名）。"""

    def decorator(func: ToolFunc) -> Tool:
        return Tool(name=name or func.__name__, description=description, args_model=args_model, func=func)

    return decorator


def _openai_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 模型 → OpenAI parameters schema（去掉 title 噪音，保证 required 正确）。"""
    schema = model.model_json_schema()
    schema.pop("title", None)
    definitions = schema.pop("$defs", None)
    if definitions:
        # 当前工具参数无嵌套模型；出现时把 $defs 内联展开
        schema = _inline_defs(schema, definitions)
    for prop in (schema.get("properties") or {}).values():
        prop.pop("title", None)
    return schema


def _inline_defs(schema: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    import json as _json

    text = _json.dumps(schema)
    for name, sub in definitions.items():
        sub = {k: v for k, v in sub.items() if k != "title"}
        text = text.replace(f'{{"$ref": "#/$defs/{name}"}}', _json.dumps(sub))
    return _json.loads(text)


def _validation_message(exc: ValidationError) -> str:
    parts = [f"{'.'.join(str(loc) for loc in err['loc']) or '参数'}: {err['msg']}" for err in exc.errors()]
    return "参数校验失败 — " + "；".join(parts)


class ToolRegistry:
    """按名称管理工具集合。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def list_api(self) -> list[dict]:
        return [t.to_api() for t in self._tools.values()]

    async def execute(self, call: dict) -> ToolResult:
        """执行模型的一次调用（未知工具也是 is_error 观测，供模型自我纠正）。"""
        tool = self._tools.get(call.get("name", ""))
        if tool is None:
            return ToolResult(
                tool_call_id=call.get("id", ""),
                content=f"未知工具：{call.get('name')}（可用工具：{', '.join(self._tools)}）",
                is_error=True,
            )
        return await tool.execute(call.get("id", ""), call.get("arguments") or "{}")


# ———— 内置工具 ————

ZERO_ARGS = type("ZeroArgs", (BaseModel,), {})


@tool("获取当前日期与时间（本地时区）", ZERO_ARGS)
async def get_current_time(_: BaseModel) -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


class ReadLocalFileArgs(BaseModel):
    path: str = Field(description="要读取文件的绝对路径")


@tool("读取本地文本文件的内容", ReadLocalFileArgs)
async def read_local_file(args: ReadLocalFileArgs) -> str:
    with open(args.path, "r", encoding="utf-8", errors="replace") as fp:
        return fp.read()


class ReadWebPageArgs(BaseModel):
    url: str = Field(description="完整网页地址（http/https）")


@tool(
    "读取公开网页正文（用于核实 NMPA/FDA/ICH 等官方监管页面原文），返回标题、来源与正文文本",
    ReadWebPageArgs,
)
async def read_web_page(args: ReadWebPageArgs) -> str:
    if not args.url.startswith(("http://", "https://")):
        raise ValueError("仅支持 http/https 地址")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True
    ) as client:
        res = await client.get(
            args.url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MogulWorkbench/1.0"},
        )
    res.raise_for_status()
    title, text = _extract_text(res.text)
    truncated = len(text) > MAX_PAGE_CHARS
    notice = "\n（正文过长，已截断）" if truncated else ""
    body = text[:MAX_PAGE_CHARS] + ("…" if truncated else "")
    return f"【{title or args.url}】\n来源：{args.url}{notice}\n\n{body}"


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


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(get_current_time)
    registry.register(read_local_file)
    registry.register(read_web_page)
    return registry
