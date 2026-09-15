"""插件 SDK 契约：Agent 核心的**唯一公开扩展面**。

## 为什么是这个文件

`docs/research/core-plugin-separation-research.md` 的结论：本项目的插件作者只有一个
（你自己）、全是可信代码、数量 1–3 个，因此**不做** entry_points 自动发现 / 独立进程 /
热插拔 / 版本协商矩阵——那些都是为"第三方不可信插件 + 大规模生态"付出的成本。
真正要买的是**边界**：业务能力（`services/egmp/`）能整体拆除而不碰宿主。

因此这里的契约刻意做小：一个 `ToolHub`（工具注册表）+ 一个 `ToolPlugin` 协议。
研究与先例（`docs/PLUGIN-UPSTREAM-RESEARCH.md` §A4-5）都指向同一条判断——
**`harness` 的工具注册表比 router 更值得作为插件 API 的唯一公开面**。

## 三条不变量（改这个文件前先读）

1. **宿主拥有装配权**：插件只提供 `register(hub)`，加载顺序、失败隔离、审计写入全在
   宿主手里。插件抛异常 = 跳过该插件，**绝不阻断应用启动**（与 `api/__init__.py`
   的 `_optional_routers` 同一语义）。
2. **零领域知识的反向依赖**：本文件**不得** import 任何插件（`workbench/plugins/*`）
   或平台服务（`services/egmp/*`）。依赖方向永远是 `plugins → plugin_api`，**永不反向**。
   一旦反向，插件就不再"可整体拆除"。
3. **一个工具只有一处定义**：`ToolHub` 是工具清单的**单一事实源**——HTTP
   `/api/agent/tools`、对话 Agent 的 function calling schema、审计表，三处必须都从它读。
   2026-09-15 之前这三处各写各的（`harness/tools.py` 三个基础工具、
   `routes_agent.py` 四个域工具，两套并存且互不可见），这就是要收口的东西。

## 写一个插件

    from .. import plugin_api as pa

    class LookupArgs(BaseModel):
        code: str = pa.Arg("对象编码")

    PLUGIN = pa.Plugin(
        id="demo",
        title="示例插件",
        tools=[
            pa.ToolSpec(
                name="demo_lookup",
                description="按编码查对象",
                args_model=LookupArgs,
                risk=pa.Risk.READ,
                handler=lookup,
                audit_extra=pa.audit_env_key("env_id"),
            ),
        ],
    )

    def register(hub: pa.ToolHub) -> None:
        hub.add_plugin(PLUGIN)

工具命名约定：`<plugin_id>_<动作>`，且**必须是 ASCII**——DeepSeek/OpenAI 的 function
name 只接受 `[A-Za-z0-9_-]{1,64}`，中文工具名会在请求侧被拒（不是本地校验能发现的错误）。
`ToolHub.add_plugin` 会在装配期就断言这一点。

## 风险分级与确认门

`Risk.READ` 不过门；`Risk.WRITE` / `Risk.DANGEROUS` 必须拿到**单次确认令牌**才执行
（见 `approvals.py`）。分级是**安全属性**，不是元数据装饰：Monitor 的 DANGEROUS 分类
必须实证来源、不许凭观察猜端点（CODEBASE_OVERVIEW §Conventions）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("workbench.plugin_api")

MAX_RESULT_CHARS = 16_000
"""单条工具结果回填给模型的上限。超出部分截断并显式告知模型（不是静默丢弃）。"""

FUNCTION_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_REDACT_KEYS = frozenset({
    "password", "passwd", "token", "api_key", "apikey", "secret",
    "password_enc", "fernetkey", "authorization", "cookie",
})


class Risk(str, Enum):
    """工具的风险分级（决定是否过确认门）。"""

    READ = "read"
    """只读：不改变平台/本机状态的查询。不过门。"""

    WRITE = "write"
    """写操作：会改变平台或本机状态（创建对象、启动会话、关闭会话）。过门。"""

    DANGEROUS = "dangerous"
    """危险操作：批量写入、不可回滚、可能影响生产环境。过门 + UI 必须二次确认。"""


def Arg(description: str, **kwargs: Any) -> Any:
    """工具参数声明（`Field` 的薄封装，仅为让插件侧读起来短一点）。"""
    return Field(description=description, **kwargs)


class ZeroArgs(BaseModel):
    """无参工具的参数模型（OpenAI 要求 parameters 必须是 object schema）。"""


ZERO_ARGS = ZeroArgs


class ToolContext(BaseModel):
    """一次工具调用的运行上下文（宿主注入，插件**只读**）。

    插件不得自行决定审计 id / 确认令牌 / 数据目录——这些由宿主下发，
    这正是 `docs/research/core-plugin-separation-research.md` §B9-4 要消掉的耦合。
    """

    audit_id: str = ""
    source: str = "http"
    """调用来源：`agent`（对话循环）/ `http`（/api/agent/invoke）/ `test`。"""
    conversation_id: str = ""
    approval_token: str = ""
    """单次确认令牌。风险工具**只有**带它才会被执行。"""
    timeout_s: float | None = None
    """覆盖工具自带的超时（默认 None = 用 `ToolSpec.timeout_s`）。

    默认必须是 None 而不是某个数值：有默认值的话它会把每个工具的 timeout_s 静默盖掉
    （2026-09-15 实测：0.05s 超时的探针工具照样跑完 0.5s）。"""

    model_config = {"arbitrary_types_allowed": True}


@dataclass(frozen=True)
class ToolSpec:
    """一个可被模型调用的工具：声明 + 实现 + 风险分级。"""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel, ToolContext], Awaitable[Any]]
    risk: Risk = Risk.READ
    plugin_id: str = ""
    timeout_s: float = 60.0
    audit_extra: Callable[[BaseModel], dict[str, Any]] | None = None
    """可选：从参数里抽审计维度（环境/对象/账号），写进 agent_audit.meta。

    同样不得含凭据——`redact_args` 会再过一遍，但别指望它兜底。
    """

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

    def valid(self) -> list[str]:
        """装配期自检；返回问题清单（空 = 合格）。"""
        problems: list[str] = []
        if not FUNCTION_NAME_RE.match(self.name):
            problems.append(
                f"工具名 {self.name!r} 不合法：function name 只接受 [A-Za-z0-9_-]{{1,64}}"
            )
        if not isinstance(self.risk, Risk):
            problems.append(f"工具 {self.name} 的 risk 必须是 Risk 枚举，得到 {self.risk!r}")
        if not self.description.strip():
            problems.append(f"工具 {self.name} 缺少 description（模型靠它选工具）")
        if not (isinstance(self.args_model, type) and issubclass(self.args_model, BaseModel)):
            problems.append(f"工具 {self.name} 的 args_model 必须是 pydantic BaseModel 子类")
        return problems


@dataclass(frozen=True)
class Plugin:
    """一个插件：id + 标题 + 它贡献的工具集合。"""

    id: str
    title: str
    tools: list[ToolSpec] = field(default_factory=list)
    version: str = "0.0.0"
    description: str = ""


@runtime_checkable
class ToolPlugin(Protocol):
    """插件协议：宿主只认这一个入口。

    用 `Plugin` 数据类 + `register(hub)` 函数是最省事的写法；需要动态贡献工具时
    再实现本协议的 `register` 即可。
    """

    def register(self, hub: ToolHub) -> None:  # pragma: no cover - 结构协议
        ...


class ToolStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    NEEDS_APPROVAL = "needs_approval"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    UNKNOWN_TOOL = "unknown_tool"
    BAD_ARGS = "bad_args"


@dataclass
class Invocation:
    """一次工具调用的完整结果（harness 与 HTTP 面共用）。"""

    tool: str
    status: ToolStatus
    content: str
    audit_id: str = ""
    duration_ms: int = 0
    risk: Risk = Risk.READ
    plugin_id: str = ""
    pending_id: str = ""
    """status=NEEDS_APPROVAL 时的待确认操作 id（凭它去 /api/agent/pending/{id}/approve）。"""
    approval_token: str = ""
    """用户放行后拿到的单次令牌；模型原样重试同一次调用即可通过确认门。"""
    risk_reason: str = ""
    """为什么需要确认（给人看的一句话，由审批模块生成）。"""
    request_id: str = ""
    """模型侧的 tool_call_id。"""
    meta: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def is_error(self) -> bool:
        return self.status is not ToolStatus.OK


class ToolHub:
    """工具注册表 + 执行器：Agent 核心的单一能力来源。

    生命周期约定：`ToolHub` 是**进程级单例**（`get_hub()`），装配在导入期完成，
    之后只读。热插拔**不支持也不做**（研究 §A5：那是为第三方生态付的成本）。
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._plugins: dict[str, Plugin] = {}
        self._failures: list[dict[str, str]] = []
        self._loaded = False
        self._audit_writer: Callable[[dict[str, Any]], None] | None = None
        self._approval_gate: (
            Callable[[ToolSpec, BaseModel, str, str], tuple[bool, str, str]] | None
        ) = None

    # ------------------------------------------------------------ 装配

    def add_plugin(self, plugin: Plugin) -> None:
        """装配一个插件。工具名冲突 = 后到的被拒（不是覆盖）。"""
        if plugin.id in self._plugins:
            raise ValueError(f"插件 id 重复：{plugin.id}")
        accepted: list[ToolSpec] = []
        for spec in plugin.tools:
            if spec.plugin_id and spec.plugin_id != plugin.id:
                raise ValueError(f"插件 {plugin.id} 的工具 {spec.name} plugin_id 不一致")
            bound = ToolSpec(
                name=spec.name, description=spec.description, args_model=spec.args_model,
                handler=spec.handler, risk=spec.risk, plugin_id=plugin.id,
                timeout_s=spec.timeout_s, audit_extra=spec.audit_extra,
            )
            problems = bound.valid() + self._collision(bound)
            if problems:
                raise ValueError(f"插件 {plugin.id} 的工具 {spec.name} 不合格：" + "；".join(problems))
            accepted.append(bound)
        self._plugins[plugin.id] = Plugin(
            id=plugin.id, title=plugin.title, tools=accepted,
            version=plugin.version, description=plugin.description,
        )
        for spec in accepted:
            self._tools[spec.name] = spec
        logger.info("插件已装配：%s（%d 个工具）", plugin.id, len(accepted))

    def _collision(self, spec: ToolSpec) -> list[str]:
        existing = self._tools.get(spec.name)
        if existing is None:
            return []
        return [f"工具名已被插件 {existing.plugin_id} 占用（工具名是全局唯一键）"]

    def load(self, loader: Callable[[ToolHub], None]) -> None:
        """跑一次装配函数，**隔离失败**：坏插件只记不进清单，宿主照常启动。"""
        try:
            loader(self)
        except Exception as exc:  # noqa: BLE001 —— 插件坏了不能拖垮应用
            self._failures.append({"plugin": getattr(loader, "__name__", str(loader)),
                                   "error": f"{type(exc).__name__}: {exc}"})
            logger.warning("插件装配失败，已跳过：%s", exc, exc_info=True)
        self._loaded = True

    def mark_load_failure(self, plugin: str, error: str) -> None:
        """记一条装配失败（供 loader 的逐插件 try/except 使用）。"""
        self._failures.append({"plugin": plugin, "error": error})
        logger.warning("插件 %s 装配失败，已跳过：%s", plugin, error)

    # ------------------------------------------------------------ 宿主服务注入

    def set_audit_writer(self, writer: Callable[[dict[str, Any]], None]) -> None:
        """注入审计写入器（由 `approvals`/api 层提供，避免 plugin_api 依赖 db）。"""
        self._audit_writer = writer

    def set_approval_gate(
        self,
        gate: Callable[[ToolSpec, BaseModel, str, str], tuple[bool, str, str]],
    ) -> None:
        """注入确认门：`(spec, 已校验参数, 令牌, 会话id) → (放行?, pending_id, 需确认原因)`。"""
        self._approval_gate = gate

    # ------------------------------------------------------------ 查询

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._plugins.values())

    @property
    def failures(self) -> list[dict[str, str]]:
        return list(self._failures)

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_api(self) -> list[dict]:
        """全部工具的 function calling 声明。"""
        return [spec.to_api() for spec in self._tools.values()]

    def list_api_for(self, plugins: list[str] | None) -> list[dict]:
        """按插件白名单取工具声明（None = 全部）。"""
        if plugins is None:
            return self.list_api()
        wanted = set(plugins)
        return [s.to_api() for s in self._tools.values() if s.plugin_id in wanted]

    def catalog(self) -> dict:
        """工具目录（HTTP `/api/agent/tools` 的响应体）。"""
        return {
            "plugins": [
                {
                    "id": p.id,
                    "title": p.title,
                    "version": p.version,
                    "description": p.description,
                    "tools": [s.name for s in p.tools],
                }
                for p in self._plugins.values()
            ],
            "tools": [
                {
                    "name": s.name,
                    "description": s.description,
                    "risk": s.risk.value,
                    "plugin": s.plugin_id,
                    "args": _args_doc(s.args_model),
                }
                for s in self._tools.values()
            ],
            "failures": self.failures,
        }

    # ------------------------------------------------------------ 执行

    async def execute(
        self,
        name: str,
        arguments: str | dict[str, Any] | None,
        ctx: ToolContext | None = None,
    ) -> Invocation:
        """校验 → 过确认门 → 执行 → 审计。**任何失败都转为结果，不抛出。**

        这是刻意的：Agent 循环里工具异常必须是"可自我纠正的观测"，而不是终止循环的
        异常（见 harness/loop.py 的事件契约）。
        """
        spec = self._tools.get(name)
        if spec is None:
            return Invocation(
                tool=name, status=ToolStatus.UNKNOWN_TOOL, risk=Risk.READ,
                error=f"未知工具：{name}",
                content=f"未知工具：{name}（可用工具：{', '.join(sorted(self._tools))}）",
            )

        ctx = ctx or ToolContext()
        audit_id = ctx.audit_id or uuid.uuid4().hex[:12]
        ctx = ctx.model_copy(update={"audit_id": audit_id})

        try:
            args = _validate(spec, arguments)
        except _BadArguments as exc:
            inv = Invocation(tool=name, status=ToolStatus.BAD_ARGS, content=str(exc),
                             audit_id=audit_id, risk=spec.risk, plugin_id=spec.plugin_id,
                             request_id=_call_id(arguments), error=str(exc))
            self._audit(spec, inv, {})
            return inv

        # 确认门：风险工具没有单次令牌就不执行
        if spec.risk is not Risk.READ and self._approval_gate is not None:
            passed, pending_id, reason = self._approval_gate(
                spec, args, ctx.approval_token, ctx.conversation_id
            )
            if not passed:
                inv = Invocation(
                    tool=name, status=ToolStatus.NEEDS_APPROVAL,
                    content=(
                        f"⏸ 工具 {name} 属于「{spec.risk.value}」级操作，已暂停等待用户确认"
                        f"（pending_id={pending_id}）。不要在未确认前重试；"
                        "先向用户说明将要做什么，等界面放行后再重试同一次调用。"
                    ),
                    audit_id=audit_id, risk=spec.risk, plugin_id=spec.plugin_id,
                    pending_id=pending_id, request_id=_call_id(arguments),
                    risk_reason=reason,
                )
                self._audit(spec, inv, _extra(spec, args))
                return inv

        started = time.perf_counter()
        budget = ctx.timeout_s or spec.timeout_s
        try:
            result = await asyncio.wait_for(spec.handler(args, ctx), timeout=budget)
            content, meta = _render(result)
            inv = Invocation(
                tool=name, status=ToolStatus.OK, content=content, audit_id=audit_id,
                risk=spec.risk, plugin_id=spec.plugin_id, request_id=_call_id(arguments),
                duration_ms=int((time.perf_counter() - started) * 1000), meta=meta,
            )
        except asyncio.TimeoutError:
            inv = Invocation(
                tool=name, status=ToolStatus.TIMEOUT, audit_id=audit_id, risk=spec.risk,
                plugin_id=spec.plugin_id, request_id=_call_id(arguments),
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=f"超时（{budget:g}s）",
                content=f"工具 {name} 执行超时（{budget:g}s）",
            )
        except Exception as exc:  # noqa: BLE001 —— 工具异常 = 观测，不是崩溃
            inv = Invocation(
                tool=name, status=ToolStatus.ERROR, audit_id=audit_id, risk=spec.risk,
                plugin_id=spec.plugin_id, request_id=_call_id(arguments),
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
                content=f"{type(exc).__name__}: {exc}",
            )
        self._audit(spec, inv, _extra(spec, args))
        return inv

    def _audit(self, spec: ToolSpec, inv: Invocation, extra: dict[str, Any]) -> None:
        if self._audit_writer is None:
            return
        try:
            self._audit_writer({
                "id": inv.audit_id,
                "tool": spec.name,
                "plugin": spec.plugin_id,
                "risk": spec.risk.value,
                "status": inv.status.value,
                "args": _redact(extra),
                "summary": (inv.content or inv.error)[:1000],
                "duration_ms": inv.duration_ms,
                "meta": {"source": inv.meta.get("source", "")},
            })
        except Exception:  # noqa: BLE001 —— 审计失败绝不影响工具结果
            logger.warning("审计写入失败：%s", spec.name, exc_info=True)


# ---------------------------------------------------------------- 单例


_hub: ToolHub | None = None


def get_hub() -> ToolHub:
    """进程级工具中枢（唯一装配点）。"""
    global _hub
    if _hub is None:
        _hub = ToolHub()
        from . import approvals as approvals_mod  # 本地导入：避免模块循环
        from . import plugins as plugins_pkg

        approvals_mod.install(_hub)
        plugins_pkg.load_all(_hub)
    return _hub


def reset_hub() -> None:
    """清空单例（仅测试用；生产路径没有热插拔）。"""
    global _hub
    _hub = None


def verify_required_tools(hub: ToolHub, required: tuple[str, ...]) -> list[str]:
    """宿主侧"必需扩展点存在性"自检：返回缺失的工具名。

    为什么必须有这一条：坏插件是被**静默跳过**的（刻意的，见 `ToolHub.load`），
    于是"打包漏收插件 / 插件 import 失败"表现成"应用照常启动，但某个功能没了"。
    启动时把清单对一遍，就能把这类问题从"用户点下去才发现"提前到"启动日志里就有"。
    对应研究里的公开失败案例：PyInstaller 漏收 distribution 元数据 → 插件静默消失
    （`docs/PLUGIN-UPSTREAM-RESEARCH.md` §R3）。
    """
    known = {spec.name for spec in hub.specs()}
    return [name for name in required if name not in known]


# ---------------------------------------------------------------- 工具实现辅助


def ok(**payload: Any) -> dict[str, Any]:
    """成功结果的统一形状（`{"ok": true, ...}`）。"""
    return {"ok": True, **payload}


def audit_env_key(*keys: str) -> Callable[[BaseModel], dict[str, Any]]:
    """从参数里抽若干标量作为审计维度（缺失则跳过）。"""

    def extract(args: BaseModel) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in keys:
            value = getattr(args, key, None)
            if value not in (None, ""):
                out[key] = value
        return out

    return extract


def split_codes(raw: str) -> list[str]:
    """逗号分隔编码串 → 去空列表（spider 的对象入参口径）。"""
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


def require(value: Any, message: str) -> Any:
    """必填校验：空值抛 ValueError（会被执行器转成 is_error 观测）。"""
    if value in (None, "", [], {}):
        raise ValueError(message)
    return value


# ---------------------------------------------------------------- 内部


class _BadArguments(Exception):
    pass


def _call_id(arguments: Any) -> str:
    return arguments.get("__call_id__", "") if isinstance(arguments, dict) else ""


def _validate(spec: ToolSpec, arguments: str | dict[str, Any] | None) -> BaseModel:
    raw: Any = arguments
    if isinstance(raw, str) or raw is None:
        try:
            raw = json.loads(raw or "{}")
        except ValueError as exc:
            raise _BadArguments(f"参数解析失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise _BadArguments("arguments 必须是 JSON 对象")
    raw.pop("__call_id__", None)
    try:
        return spec.args_model.model_validate(raw)
    except ValidationError as exc:
        parts = [f"{'.'.join(str(loc) for loc in err['loc']) or '参数'}: {err['msg']}"
                 for err in exc.errors()]
        raise _BadArguments("参数校验失败 — " + "；".join(parts)) from exc


def _extra(spec: ToolSpec, args: BaseModel) -> dict[str, Any]:
    if spec.audit_extra is None:
        return {}
    try:
        return dict(spec.audit_extra(args))
    except Exception:  # noqa: BLE001 —— 维度抽取失败不该影响调用
        return {}


def _render(result: Any) -> tuple[str, dict[str, Any]]:
    """工具返回值 → (给模型的文本, 给审计的结构化 meta)。"""
    if isinstance(result, str):
        return _truncate(result), {}
    if isinstance(result, BaseModel):
        return _truncate(result.model_dump_json(exclude_none=True)), {}
    if isinstance(result, dict):
        meta = {k: v for k, v in result.items()
                if isinstance(v, (str, int, float, bool)) and k in {"status", "job_id", "source"}}
        text = json.dumps(result, ensure_ascii=False, default=str, indent=2)
        return _truncate(text), meta
    return _truncate(str(result)), {}


def _truncate(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"\n…（结果过长已截断，原文 {len(text)} 字符）"


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """剔除凭据类键（审计与日志的硬要求：凭据不落明文）。"""
    return _redact(args)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {
            k: ("***" if str(k).lower() in _REDACT_KEYS else _redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v, depth + 1) for v in value[:20]]
    if isinstance(value, str) and len(value) > 300:
        return value[:300] + "…"
    return value


def _args_doc(model: type[BaseModel]) -> dict[str, str]:
    """参数说明（给 HTTP 目录的展示用，不影响模型 schema）。"""
    out: dict[str, str] = {}
    for name, info in model.model_fields.items():
        out[name] = info.description or ("必填" if info.is_required() else "可选")
    return out


def _openai_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 模型 → OpenAI parameters schema（去掉 title 噪音，内联 $defs）。"""
    schema = model.model_json_schema()
    schema.pop("title", None)
    definitions = schema.pop("$defs", None)
    if definitions:
        text = json.dumps(schema)
        for def_name, sub in definitions.items():
            sub = {k: v for k, v in sub.items() if k != "title"}
            text = text.replace(f'{{"$ref": "#/$defs/{def_name}"}}', json.dumps(sub))
        schema = json.loads(text)
    for prop in (schema.get("properties") or {}).values():
        prop.pop("title", None)
    return schema
