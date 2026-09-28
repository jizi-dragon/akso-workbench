"""全项目运行日志的**唯一装配点**。

## 它解决什么（2026-09-20 实测的现状）

改造前：全仓 **38 处** `logger.*` 调用（18 info · 18 warning · 1 error · 1 debug），
而 `basicConfig` / `FileHandler` / `addHandler` / `dictConfig` **一处都没有**。后果分两级：

| 级别 | 调用数 | 去向 | 结果 |
|---|---|---|---|
| `info` / `debug` | 19 | 无 handler → `logging.lastResort`（**WARNING 级**）→ 丢弃 | **完全不可见** |
| `warning` / `error` | 19 | `lastResort` → **stderr** → 桌面壳 `stdio: 'ignore'`（`desktop/main.js:88,99`） | **打包态不可见** |

最能说明问题的一条：`api/__init__.py::_tool_selfcheck()` 的注释写着
「**必须把结果喊出来**：少了哪个插件一眼可见」，而它的 `logger.error` **喊不出去**——
PITFALLS #32（打包漏收插件）唯一的启动期报警在打包态 100% 听不到。

## 三条设计不变量（改本文件前先读）

1. **日志失败绝不影响主流程**。与本仓 `approvals` / `cache.persist()` 同一纪律：
   handler 内部任何异常都被吞掉并降级为一次 stderr 兜底，**绝不向上抛**。
2. **凭据永不入日志**。靠 `RedactionFilter` 在**落盘前**统一打码，不依赖调用方自觉
   （调用方自觉是第三道防线，不是第一道）。
3. **不依赖 stdio、不依赖 CWD**。落点是 Python 进程自己打开的 `<DATA_DIR>/logs/`——
   壳会丢弃管道，打包态 CWD 也不确定。

## 为什么是**同步**写文件（而不是 QueueHandler）

设计稿里曾把"日志 I/O 移出请求线程"列为待办。实现时改为**不做**，理由是：
`QueueHandler` 在队列满时会**丢弃记录**——那正是本仓最忌讳的一类**静默失败**
（与 PITFALLS #42「写了审计但没人调用」同族：东西看起来在，实际没落盘）。
本应用是单机单人工具、调用点 38 处、单条记录几百字节，同步写的代价可以忽略。
**要改成异步，先补一条"队列溢出必须可见并计入指标"的机制**，否则别改。

## 与既有留痕的分工（不要重复造）

| 层 | 载体 | 职责 |
|---|---|---|
| L0 进程日志 | 本模块 → `logs/workbench-*.jsonl` | 启动横幅 / 装配 / 自检 / 异常栈 / 后台任务 |
| L1 请求日志 | 本模块 → 同一文件（`kind=http`） | 方法 / 路径 / 状态码 / 耗时 / traceId |
| L2 领域事件 | **既有 DB 表**（`agent_audit` / `sandbox_run` / `deepseek_usage` …） | 结构化、可查询的权威事实 |
| L3 审计 | **既有**（合规事实源） | 不碰 |

**日志只"叙述"并带上指向 L2 的 ID（`auditId` / `jobId`），不复制 L2 的内容。**
两处都写全量 = 两个会漂的事实源（本仓 2026-09-15「两套工具表并存」的教训）。
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import logging.handlers
import os
import re
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__, config

# ---------------------------------------------------------------- 配置项

LEVEL_ENV = "WORKBENCH_LOG_LEVEL"
DIR_ENV = "WORKBENCH_LOG_DIR"
FORMAT_ENV = "WORKBENCH_LOG_FORMAT"

DEFAULT_LEVEL = "INFO"
DEFAULT_BACKUP_COUNT = 14
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
"""单文件体积上限（超过即强制滚动一次）。它是**安全阀**，不是容量规划。"""

LOG_FILE_STEM = "workbench"
ROOT_LOGGER_NAME = "workbench"

_lock = threading.Lock()
_configured = False
_handler: logging.Handler | None = None
_log_dir: Path | None = None


# ---------------------------------------------------------------- 关联 ID

#: 关联 ID 用 `contextvars` 承载，于是**不必在几十个函数签名里穿参数**，
#: 且与 `async` 天然兼容（每个请求一个 context）。
_VARS: dict[str, contextvars.ContextVar[str]] = {
    "traceId": contextvars.ContextVar("wb_trace_id", default=""),
    "conversationId": contextvars.ContextVar("wb_conversation_id", default=""),
    "auditId": contextvars.ContextVar("wb_audit_id", default=""),
    "jobId": contextvars.ContextVar("wb_job_id", default=""),
    "tool": contextvars.ContextVar("wb_tool", default=""),
    "userId": contextvars.ContextVar("wb_user_id", default=""),
}


def new_trace_id() -> str:
    """一次请求的短追踪号（8 位十六进制，够用且便于人报读）。"""
    return uuid.uuid4().hex[:8]


@contextlib.contextmanager
def bind(**fields: str) -> Iterator[None]:
    """在块内绑定关联 ID；退出时**恢复**原值（支持嵌套）。

        with log.bind(auditId=inv.audit_id, tool=spec.name):
            logger.info("工具执行完成")
    """
    tokens: list[tuple[contextvars.ContextVar[str], contextvars.Token]] = []
    for key, value in fields.items():
        var = _VARS.get(key)
        if var is None:
            raise KeyError(f"未知关联字段 {key!r}；可用：{sorted(_VARS)}")
        tokens.append((var, var.set("" if value is None else str(value))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def correlation() -> dict[str, str]:
    """当前上下文里的关联 ID（非空的才返回），供 API 响应/审计等处复用同一口径。"""
    return {key: var.get() for key, var in _VARS.items() if var.get()}


# ---------------------------------------------------------------- 脱敏

#: 键值形态：`password=xxx` / `"api_key": "xxx"` / `Authorization: xxx`
#:
#: ⚠️ 键名两侧的 `"?` 是**必须的**：JSON 形态长成 `"token": "abc"`，
#: 键名后面紧跟一个**闭引号**才轮到 `:`。初版正则写成 `\b(key)\b(\s*[:=]\s*)`，
#: 于是**所有 JSON 形态的凭据都匹配不到**——而本项目自己的日志恰好是 JSONL。
#: 这条由 `tests/test_logging.py::test_redaction_covers_json_shape` 守着。
_KEY_VALUE = re.compile(
    r'(?i)(?P<key>"?\b(?:password|passwd|pwd|token|api[_-]?key|apikey|secret|authorization|'
    r'cookie|fernet[_-]?key|password_enc|credential|session[_-]?id)\b"?)'
    r'(?P<sep>\s*[:=]\s*)'
    r'(?P<quote>"?\'?)'
    r'(?P<value>[^\s,;"\'\)\]}]+)',
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{6,}")
#: 常见 API Key 前缀（DeepSeek/OpenAI 的 `sk-`；本仓 embeddingKey 也是这个形状）
_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9._\-]{6,}")
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----")


def redact_text(text: str) -> str:
    """对**自由文本**打码。键值形态 + 已知凭据形状两层。

    为什么不能只靠调用方不打印凭据：本仓已经栽过"约定在纸上、没在代码里"
    （PITFALLS #41 / #42）。脱敏必须在**落盘前的唯一通道**上做。

    替换时**保留键名/分隔符/引号**，只把值换成 `***`——这样 JSONL 行仍是合法 JSON，
    否则导出的日志会因打码而变成解析不了的残缺结构。
    """
    if not text:
        return text
    text = _PEM.sub("-----BEGIN <已打码>-----", text)
    text = _BEARER.sub(r"\1 ***", text)
    text = _SK_KEY.sub("sk-***", text)
    text = _KEY_VALUE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{m.group('quote')}***", text)
    return text


class RedactionFilter(logging.Filter):
    """落盘前的统一打码。**任何异常都退化为"丢弃原文"**，绝不放行未打码内容。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 —— logging 的接口名
        try:
            args = record.args
            if isinstance(args, dict):
                # 结构化的 dict 参数走既有单点（键名黑名单）
                from .plugin_api import redact_args

                record.args = redact_args(dict(args))
            message = record.getMessage()
            clean = redact_text(message)
            if clean != message:
                record.msg = clean
                # 必须是 None 而不是 ()：`getMessage()` 里 `if self.args:` 对空元组为假，
                # 但**消息里若含裸 `%`**（如"进度 100%"），`msg % ()` 会抛 ValueError。
                record.args = None
        except Exception:  # noqa: BLE001 —— 脱敏失败绝不放行原文
            record.msg = "<日志脱敏失败，原始内容已丢弃>"
            record.args = None
        return True


# ---------------------------------------------------------------- 格式

_TEXT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


class JsonlFormatter(logging.Formatter):
    """一行一个 JSON 对象。

    为什么 JSONL 而不是纯文本：需求是"便于后期查找漏洞"，那意味着**过滤与聚合**
    （所有 ERROR、某次 traceId 的全部行）。文本要靠正则猜，JSONL 直接解析。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).astimezone().isoformat(
                timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "pid": record.process,
            "ver": __version__,
        }
        payload.update(correlation())
        kind = getattr(record, "wb_kind", "")
        if kind:
            payload["kind"] = kind
        extra = getattr(record, "wb_fields", None)
        if isinstance(extra, dict):
            payload.update({k: v for k, v in extra.items() if v is not None})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """开发态的人读单行（同一套字段，只是排版不同，**不是另一套日志**）。"""

    def __init__(self) -> None:
        super().__init__(_TEXT_FORMAT, datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        corr = correlation()
        if corr:
            base += "  [" + " ".join(f"{k}={v}" for k, v in corr.items()) + "]"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


# ---------------------------------------------------------------- 滚动

class DailySizeRotatingFileHandler(logging.handlers.TimedRotatingFileHandler):
    """按天滚动 + 体积安全阀。

    ## 为什么自己写这一层

    stdlib 的 `TimedRotatingFileHandler` **不支持 `maxBytes`**，而设计稿要的是双重滚动。
    直接叠一个 `RotatingFileHandler` 会变成**两个 handler 争同一个文件**。

    所以这里的做法是：**两种触发走同一条滚动路径**（都按时间戳归档），只是触发条件多一个。
    这样只有一个写者、一套命名、一套保留策略。

    ## 两个必须自己覆盖的 stdlib 行为（都会**静默丢数据**）

    ### ① 归档名会撞车 → 先前归档被删除

    `when="midnight"` 时 stdlib 用**滚动边界时间**（即当天 0 点）格式化成后缀，
    所以**同一天滚第二次会得到完全相同的归档名**；而 `doRollover` 遇到同名文件会先
    `os.remove(dfn)` → **第一次的归档被静默删掉**。
    体积滚动一旦在一天内触发两次，就会稳定丢数据，且没有任何报错。

    修法：`rotation_filename()` 在目标已存在时追加单调序号。
    回归断言：`tests/test_logging.py::test_two_size_rolls_same_day_do_not_overwrite`。

    ### ② `extMatch` 不同步 → 归档永不清理

    `getFilesToDelete()` 靠 `extMatch` 判断"哪些文件是我的归档"。名字格式改了而它没改，
    那些文件就**永远不在删除名单里** → 磁盘无限增长（而且是在"滚动看起来正常"的前提下）。

    修法：`extMatch` 与 `rotation_filename()` 产出的形状**一起改**。
    回归断言：`tests/test_logging.py::test_old_archives_are_cleaned_up`。
    """

    def __init__(self, filename: Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
                 backup_count: int = DEFAULT_BACKUP_COUNT) -> None:
        super().__init__(
            str(filename), when="midnight", interval=1, backupCount=backup_count,
            encoding="utf-8", delay=False, utc=False,
        )
        self.maxBytes = max_bytes
        # 与 `rotation_filename()` 的产出形状保持一致（见类文档 ②）
        self.extMatch = re.compile(r"^\d{4}-\d{2}-\d{2}(\.\d+)?$", re.ASCII)

    def rotation_filename(self, default_name: str) -> str:
        """归档名唯一化（见类文档 ①）。"""
        name = super().rotation_filename(default_name)
        if not os.path.exists(name):
            return name
        index = 1
        while os.path.exists(f"{name}.{index}"):
            index += 1
        return f"{name}.{index}"

    def shouldRollover(self, record: logging.LogRecord) -> int:
        if super().shouldRollover(record):
            return 1
        if self.maxBytes > 0 and self.stream is not None:
            try:
                self.stream.seek(0, 2)
                if self.stream.tell() >= self.maxBytes:
                    return 1
            except (OSError, ValueError):
                return 0
        return 0

    def handle(self, record: logging.LogRecord) -> bool:
        """**不变量 1 的强制点**：本 handler 的任何失败都不得传到调用方。

        为什么必须自己包一层：`logging.Handler.handle()` **没有 try/except**——
        它直接 `self.emit(record)`。stdlib 只在 `StreamHandler.emit` **内部**包了 try
        （失败走 `handleError`），所以"格式器抛异常"这类情况本来就被吞；
        但只要 emit 里出现该 try 之外的失败（磁盘句柄失效、编码错误、
        `shouldRollover` 里的路径异常…），异常会一路传到业务代码——
        于是"记一条日志"能**打断一次工具调用**。

        本仓对此有明确纪律（`approvals.write_audit` / `cache.persist` 都吞异常）：
        **留痕是义务，不是执行的前置条件**。故在这里把它变成结构保证，
        而不是"指望 stdlib 的 try 覆盖到"。
        """
        try:
            return super().handle(record)
        except Exception as exc:  # noqa: BLE001 —— 见 docstring
            _stderr_fallback(f"写日志失败（已忽略，不影响主流程）：{type(exc).__name__}: {exc}")
            return False


# ---------------------------------------------------------------- 装配


def log_dir() -> Path:
    """日志目录（`WORKBENCH_LOG_DIR` 优先，否则 `<DATA_DIR>/logs`）。"""
    env = os.environ.get(DIR_ENV)
    return Path(env) if env else (config.DATA_DIR / "logs")


def current_log_file() -> Path:
    return log_dir() / f"{LOG_FILE_STEM}-{datetime.now():%Y%m%d}.jsonl"


def _resolve_level(raw: str | None) -> int:
    name = (raw or DEFAULT_LEVEL).strip().upper()
    level = logging.getLevelName(name)
    return level if isinstance(level, int) else logging.INFO


def _resolve_format() -> str:
    raw = (os.environ.get(FORMAT_ENV) or "").strip().lower()
    if raw in ("jsonl", "json", "text"):
        return "jsonl" if raw.startswith("json") else "text"
    # 打包态默认 JSONL（给人看不如给工具看）；开发态默认文本（终端里更顺眼）
    return "jsonl" if config.FROZEN else "text"


def _handler_usable(handler: logging.Handler | None) -> bool:
    """handler 还能写吗？

    ## 为什么需要这个检查（一个真实的尖锐点）

    `logging.config.dictConfig()` 在非 incremental 模式下会调用
    `_clearExistingHandlers()` → **关闭所有既有 handler**（它们仍挂在 logger 上，
    但 `stream` 已经关了）。此后每次写都会抛 `ValueError: I/O operation on closed file`，
    而我们自己的 `handle()` 会把它吞掉 —— 于是**日志静默停止**，而 `_configured` 仍是 `True`，
    下一句 `configure()` 直接早返回，谁都不会重建它。

    触发场景很具体：**第三方按默认配置调用 `uvicorn.run`**（它的 `log_config` 默认不是 `None`）。
    本仓两个入口都传了 `log_config=None` 且先于 uvicorn 装配，所以正常路径不受影响——
    但"正常路径不受影响"不等于"结构上是安全的"，所以这里加一道探测。
    """
    if handler is None:
        return False
    stream = getattr(handler, "stream", None)
    if stream is None:
        return False
    try:
        return not stream.closed
    except Exception:  # noqa: BLE001 —— 拿不到状态就当不可用，重建更安全
        return False


def configure(*, level: str | int | None = None, force: bool = False) -> Path | None:
    """装配日志。**幂等**——重复调用不会叠加 handler、不会重复输出。

    返回实际落盘的文件路径；装配失败返回 `None`（**绝不抛出**，见不变量 1）。
    """
    global _configured, _handler, _log_dir

    with _lock:
        if _configured and not force and _handler_usable(_handler):
            return current_log_file()
        if _configured and not force:
            # 之前装过、但已经不可写（多半是别处的 dictConfig 关掉了它）→ 重建而不是早返回
            _stderr_fallback("既有日志 handler 已不可写，正在重建（日志不会因此中断）")
        try:
            target_dir = log_dir()
            target_dir.mkdir(parents=True, exist_ok=True)
            _log_dir = target_dir

            handler = DailySizeRotatingFileHandler(
                target_dir / f"{LOG_FILE_STEM}-{datetime.now():%Y%m%d}.jsonl",
                max_bytes=int(os.environ.get("WORKBENCH_LOG_MAX_BYTES") or DEFAULT_MAX_BYTES),
                backup_count=int(os.environ.get("WORKBENCH_LOG_BACKUP_COUNT")
                                 or DEFAULT_BACKUP_COUNT),
            )
            handler.setFormatter(
                JsonlFormatter() if _resolve_format() == "jsonl" else TextFormatter())
            handler.addFilter(RedactionFilter())
            handler._workbench_owned = True  # type: ignore[attr-defined]  # 便于幂等替换

            root = logging.getLogger()
            # **只摘自己装的**：pytest 的 caplog、别处装的 handler 都不动
            for existing in list(root.handlers):
                if getattr(existing, "_workbench_owned", False):
                    root.removeHandler(existing)
                    with contextlib.suppress(Exception):
                        existing.close()
            root.addHandler(handler)

            resolved = _resolve_level(level if level is not None else os.environ.get(LEVEL_ENV))
            root.setLevel(resolved)
            # 业务代码统一挂在 `workbench.*` 命名空间下；显式放开它的级别
            logging.getLogger(ROOT_LOGGER_NAME).setLevel(resolved)

            # uvicorn 的访问日志由我们自己的 L1 中间件承担，默认压到 WARNING。
            # 它的错误日志要留（启动失败、协议异常）。
            logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
            logging.getLogger("uvicorn.error").setLevel(resolved)

            _handler = handler
            _configured = True
            return current_log_file()
        except Exception:  # noqa: BLE001 —— 不变量 1：装配失败不能拖垮应用
            _configured = False
            _handler = None
            _stderr_fallback("日志装配失败，本次运行不会有日志文件")
            return None


def _stderr_fallback(line: str) -> None:
    """最后一道：连 handler 都建不起来时，至少往 stderr 写一行。**绝不抛**。"""
    try:
        os.write(2, f"[workbench-logging] {line}\n".encode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        pass


def reset_for_tests() -> None:
    """仅测试用：摘掉本模块装的 handler 并复位装配标志。

    与 `hub.reset_hub()` / `approvals.reset_store()` 同一约定——
    否则一个测试把 root logger 配上文件 handler 之后，后续测试会跟着往那个文件写，
    且 root 的级别被改掉会影响 `caplog` 类断言。
    """
    global _configured, _handler, _log_dir
    with _lock:
        root = logging.getLogger()
        for existing in list(root.handlers):
            if getattr(existing, "_workbench_owned", False):
                root.removeHandler(existing)
                with contextlib.suppress(Exception):
                    existing.close()
        _handler = None
        _log_dir = None
        _configured = False


def install_excepthooks() -> None:
    """把"没人接的异常"接进日志。

    为什么必须有：`stdio: 'ignore'` 之下，未捕获异常的默认去向（stderr）是**黑洞**。
    现场表现是"服务没起来"，而没有任何可查的东西。
    """
    import sys
    import threading

    logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.crash")

    def _hook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.critical("未捕获异常", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook

    def _thread_hook(args) -> None:  # type: ignore[no-untyped-def]
        if issubclass(args.exc_type, SystemExit):
            return
        logger.critical("线程内未捕获异常（%s）", args.thread.name if args.thread else "?",
                        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    with contextlib.suppress(Exception):  # 3.8+
        threading.excepthook = _thread_hook


def banner(*, host: str | None = None, port: int | None = None) -> str:
    """启动横幅：**现场最常问的那几个环境事实**。

    版本 / pid / 是否冻结 / 端口 / 数据目录 / 日志路径 —— 一条说完。

    ## 为什么这里**不**报工具装配情况

    那是 `api/__init__.py::_tool_selfcheck()` 的职责（插件数、装配失败逐条、必需工具缺失）。
    曾经两处都写，结果是"工具中枢：N 个插件 / M 个工具"在日志里出现**两次**——
    日志里的重复不是小事，它让"这条是什么时候记的"变得难判断。

    分工：**本函数答"这台机器上跑的是哪一版、在哪些路径上"**；
    `_tool_selfcheck()` 答"能力齐不齐"。

    ## 为什么调用点在 `api.lifespan` 而不是入口脚本

    入口脚本只覆盖 `main.py` 与 `server_entry.py` 两条路；而**桌面壳的开发态**是直接
    spawn `python -m uvicorn workbench.api:app`（`desktop/main.js::spawnServer` 的开发态回退），
    **不经过任何入口脚本**。lifespan 是所有启动路径的唯一交集，放这里才不漏。
    """
    logger = logging.getLogger(f"{ROOT_LOGGER_NAME}.startup")
    parts = [f"Workbench v{__version__} pid={os.getpid()} frozen={config.FROZEN}"]
    if host and port:
        parts.append(f"http://{host}:{port}")
    parts.append(f"data={config.DATA_DIR}")
    parts.append(f"log={current_log_file()}")
    line = " | ".join(parts)
    logger.info("%s", line)
    return line


def log_event(kind: str, message: str, /, **fields: Any) -> None:
    """记一条**结构化**事件（`kind` 用于过滤，`fields` 会平铺进 JSON）。

    用法（L1 请求日志就是走这条）：

        log_event("http", "GET /api/agent/tools", status=200, durationMs=12, path="/api/agent/tools")
    """
    logging.getLogger(f"{ROOT_LOGGER_NAME}.{kind}").info(
        message, extra={"wb_kind": kind, "wb_fields": fields})


# ---------------------------------------------------------------- 检索（供 /api/logs/*）

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def available_days() -> list[str]:
    """有哪些天的日志可读（倒序）。"""
    directory = log_dir()
    if not directory.exists():
        return []
    days: set[str] = set()
    for path in directory.glob(f"{LOG_FILE_STEM}-*.jsonl*"):
        match = re.match(rf"^{LOG_FILE_STEM}-(\d{{8}})", path.name)
        if match:
            days.add(match.group(1))
    return sorted(days, reverse=True)


def tail(*, limit: int = 200, level: str = "", contains: str = "", trace: str = "",
         kind: str = "", day: str = "") -> list[dict[str, Any]]:
    """读最近的日志行并过滤（**从新到旧**，最多 `limit` 条）。

    刻意只读**一个文件**（默认当天）：日志是排障用的，不是搜索引擎；
    跨天检索请用 `/api/logs/download` 导出后离线分析。
    """
    limit = max(1, min(int(limit), 2000))
    day = day or datetime.now().strftime("%Y%m%d")
    directory = log_dir()
    if not directory.exists():
        return []

    # 当天的归档（`.YYYY-MM-DD_HH-MM-SS`）也要算进来：体积滚动之后，
    # 最新内容可能已经在归档文件里，只看主文件会"看不到刚发生的事"。
    candidates = sorted(
        [p for p in directory.glob(f"{LOG_FILE_STEM}-{day}.jsonl*") if p.is_file()],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    floor = _LEVEL_ORDER.get(level.strip().upper(), 0)
    needle = contains.strip()
    trace = trace.strip()
    kind = kind.strip()

    out: list[dict[str, Any]] = []
    for path in candidates:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # 非 JSONL（开发态 text 格式）或写了一半的行：当纯文本处理
                row = {"msg": line, "level": "INFO", "raw": True}
            if floor and _LEVEL_ORDER.get(str(row.get("level", "")).upper(), 0) < floor:
                continue
            if trace and row.get("traceId") != trace:
                continue
            if kind and row.get("kind") != kind:
                continue
            if needle and needle not in json.dumps(row, ensure_ascii=False):
                continue
            out.append(row)
            if len(out) >= limit:
                return out
    return out
