"""MCP（Model Context Protocol）客户端：把外部工具服务器接进本工作台。

## 为什么有这一层

WorkBuddy 一类 Agent 工作台的「基础五件套」是：agent 循环、tools、skills、sandbox、MCP。
本仓前四件早已原生化，MCP 此前只有一句「通道已预留」（`CORE-ARCHITECTURE` §7.2）。
这个模块补的是**最小可用且安全默认**的那一半：stdio 传输、初始化握手、`tools/list`、
`tools/call`，以及一个**只认用户配置文件、模型永远不能指定命令**的启动面。

## 三条纪律（都是本仓已有的）

**① 配置是用户写的，不是模型写的。**
`mcp.json` 里的 `command/args` 只能由人放进数据目录；模型能做的只有调用**已经列出**的工具。
因此不存在"模型让宿主 `curl | sh`"这条路径 —— 这是 MCP 与"任意命令执行"之间唯一的分界，
比在参数里做命令白名单可靠得多（命令里可以藏的东西，正则列不完）。

**② 不继承宿主的全部环境变量**（规则 13）。
MCP server 只需要 PATH / TEMP / 用户目录这类运行环境；`WORKBENCH_*`、云令牌、
API Key 一律不传。要传的密钥由用户在 `mcp.json` 的 `env` 里**显式**写。

**③ 结果一律是 UNTRUSTED。**
MCP 返回的是外部系统写的内容（网页、工单、消息），不是我们自己边界内的数据。
这条在注册层声明（`mcp_registry`），不在本模块里 —— 传输层不该知道信任分级。

## 传输

stdio，**一行一个 JSON-RPC 2.0 消息**（MCP 的 stdio 传输没有 Content-Length 头）。
请求串行化：一个 server 一个连接、一把锁。这是刻意的 ——
并发调用同一个 MCP server 的收益很小，而"响应串台"的代价是拿到别人的结果。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config

logger = logging.getLogger("workbench.mcp")

#: 配置文件名（放在数据目录下，与 workbench.db 同级）。
CONFIG_FILENAME = "mcp.json"

#: MCP 协议版本：先用一个被广泛实现的版本；服务端返回什么就按什么用。
PROTOCOL_VERSION = "2024-11-05"

#: 单次请求的兜底超时。工具调用可在 server 配置里单独调大。
DEFAULT_TIMEOUT_S = 60.0
MAX_TIMEOUT_S = 600.0

#: server 名限制得比工具名更严：它会被拼进 `mcp_<server>_<tool>`（≤64 字符）。
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")

#: 允许继承的环境变量。**白名单，不是黑名单**：漏掉一个只是 server 跑不起来，
#: 多带一个可能就是把宿主凭据送出去。
_ENV_ALLOW = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)


class McpError(RuntimeError):
    """MCP 协议/进程错误。消息面向用户（会被写进工具观测或设置页）。

    `code` 是 JSON-RPC 错误码（有就填），调用方按**码**分类，不按消息文本
    （旧实现用 `"-32601" in str(exc)`：server 换一句措辞，ping 的兼容分支就失效，
    健康检查会把一台正常 server 每 30 秒重启一次）。
    """

    def __init__(self, message: str, *, code: int | None = None,
                 kind: str = "protocol") -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind


@dataclass(frozen=True)
class ServerConfig:
    """一个 MCP server 的静态配置。`risk` 保持字符串，由注册层翻译成 `Risk`。"""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    enabled: bool = True
    risk: str = "write"
    #: 按工具覆盖风险：`{"echo": "read"}`。server 级的 `risk` 是默认值。
    tool_risk: dict[str, str] = field(default_factory=dict)
    timeout_s: float = DEFAULT_TIMEOUT_S
    description: str = ""
    protocol_version: str = PROTOCOL_VERSION


_RISK_VALUES = {"read", "write", "dangerous"}


def _parse_risk(raw: object) -> tuple[str, dict[str, str]]:
    """`risk` 支持字符串（整台 server）或 `{"default": "...", "tools": {...}}`。"""
    if raw is None:
        return "write", {}
    if isinstance(raw, str):
        value = raw.lower()
        if value not in _RISK_VALUES:
            raise ValueError("risk 只能是 read / write / dangerous")
        return value, {}
    if not isinstance(raw, dict):
        raise ValueError("risk 必须是字符串或 {default, tools} 对象")
    default = str(raw.get("default", "write")).lower()
    if default not in _RISK_VALUES:
        raise ValueError("risk.default 只能是 read / write / dangerous")
    tools = raw.get("tools") or {}
    if not isinstance(tools, dict):
        raise ValueError("risk.tools 必须是 {工具名: read/write/dangerous} 对象")
    parsed: dict[str, str] = {}
    for name, value in tools.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("risk.tools 的键和值都必须是字符串")
        level = value.lower()
        if level not in _RISK_VALUES:
            raise ValueError(f"工具 {name} 的 risk 只能是 read / write / dangerous")
        parsed[name] = level
    return default, parsed


def config_path() -> Path:
    """配置文件位置：`<数据目录>/mcp.json`。

    ★ 这个文件**可以含凭据**（`env` 里的 webhook/token），所以它必须进
    `config.SENSITIVE_PATH_PARTS` —— 只读工具不能把它整份喂给模型。
    """
    return config.DATA_DIR / CONFIG_FILENAME


def _parse_server(name: str, raw: object) -> ServerConfig:
    if not SERVER_NAME_RE.match(name):
        raise ValueError("server 名只允许 1–32 位 ASCII 字母/数字/._-，且不能以符号开头")
    if not isinstance(raw, dict):
        raise ValueError("配置必须是一个对象")
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("缺少 command（字符串）")
    args = raw.get("args") or []
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        raise ValueError("args 必须是字符串数组")
    env = raw.get("env") or {}
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not isinstance(v, (str, int, float)) for k, v in env.items()
    ):
        raise ValueError("env 必须是字符串到字符串的对象")
    cwd = raw.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("cwd 必须是字符串")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    risk, tool_risk = _parse_risk(raw.get("risk"))
    protocol = str(raw.get("protocolVersion") or raw.get("protocol_version") or PROTOCOL_VERSION)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", protocol):
        raise ValueError("protocolVersion 必须是 YYYY-MM-DD 形状")
    timeout = raw.get("timeout_s", raw.get("timeout", DEFAULT_TIMEOUT_S))
    try:
        timeout_s = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_s 必须是数字") from exc
    if not (1 <= timeout_s <= MAX_TIMEOUT_S):
        raise ValueError(f"timeout_s 必须在 1–{MAX_TIMEOUT_S:g} 秒之间")
    description = str(raw.get("description", ""))[:200]
    return ServerConfig(
        name=name,
        command=command.strip(),
        args=tuple(args),
        env={str(k): str(v) for k, v in env.items()},
        cwd=cwd,
        enabled=enabled,
        risk=risk,
        tool_risk=tool_risk,
        timeout_s=timeout_s,
        description=description,
        protocol_version=protocol,
    )


def load_config(path: Path | None = None) -> tuple[dict[str, ServerConfig], list[str]]:
    """读配置 → `(servers, errors)`。**坏条目只跳过自己**，不拖垮其余 server。

    返回 errors 而不是抛异常：设置页需要把"哪一条写错了"显示出来，
    而一个写错的 server 不该让另外三个能用的也起不来。
    """
    target = Path(path) if path is not None else config_path()
    if not target.exists():
        return {}, []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, [f"配置文件无法读取/不是合法 JSON：{target}（{exc}）"]
    if not isinstance(raw, dict):
        return {}, ["配置文件顶层必须是对象"]
    servers_raw = raw.get("mcpServers")
    if not isinstance(servers_raw, dict):
        return {}, ["缺少 mcpServers 对象（形状与 WorkBuddy/Claude Desktop 一致）"]
    servers: dict[str, ServerConfig] = {}
    errors: list[str] = []
    for name, item in servers_raw.items():
        try:
            servers[name] = _parse_server(name, item)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
    return servers, errors


def safe_env(extra: dict[str, str]) -> dict[str, str]:
    """子进程环境：白名单继承 ＋ 配置里显式给的变量。

    ★ 额外给 Python server 两个编码变量：MCP stdio 的协议是 **UTF-8**，
    而 Windows 上 Python 子进程的 stdout 默认是 GBK（`PYTHONIOENCODING` 未设时）。
    不设它，一个用 Python 写的 MCP server 返回中文就会变成一串 U+FFFD ——
    而客户端按规范解 UTF-8，错在编码不在协议，很难归因。
    非 Python server 忽略这两个变量；用户在 `env` 里显式覆盖时以用户为准。
    """
    env = {key: os.environ[key] for key in _ENV_ALLOW if key in os.environ}
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(extra)
    return env


def render_result(result: dict) -> str:
    """把 `tools/call` 的 result 压成给模型看的文本。

    MCP 的 content 是**块数组**（text / image / resource…）。全部塞 JSON 会浪费上下文；
    文本块直接拼，非文本块给一句可读的占位（它本来也不能被模型直接看）。
    """
    parts: list[str] = []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "image":
            data = block.get("data") or ""
            parts.append(f"[图片 {block.get('mimeType') or 'unknown'}，{len(str(data))} base64 字符]")
        elif kind == "resource":
            resource = block.get("resource") or {}
            parts.append(f"[资源 {resource.get('uri') or ''}]\n{resource.get('text') or ''}")
        else:
            parts.append(json.dumps(block, ensure_ascii=False))
    if not parts:
        parts.append(json.dumps(result, ensure_ascii=False))
    text = "\n".join(part for part in parts if part)
    if result.get("isError"):
        raise McpError(text or "MCP 工具返回了错误")
    return text


class McpClient:
    """一个 stdio MCP 连接。**同步**：调用方负责 `asyncio.to_thread`（规则 I11）。"""

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self.server_info: dict = {}
        self.protocol_version = cfg.protocol_version
        self.tools: list[dict] = []
        self.tools_changed = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []
        self._lock = threading.Lock()
        self._request_id = 0
        self._threads: list[threading.Thread] = []
        self._closed = False

    # ---------------------------------------------------------------- 生命周期

    @property
    def connected(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and not self._closed

    @property
    def stderr_tail(self) -> str:
        """最近几行 stderr（**只给诊断用**，不直接进 status/日志正文）。"""
        return " / ".join(self._stderr[-5:])

    def start(self) -> None:
        if self._proc is not None:
            return
        if self._closed:
            raise McpError("这个连接已经关闭")
        cmd = [self.cfg.command, *self.cfg.args]
        cwd = None
        if self.cfg.cwd:
            cwd = Path(self.cfg.cwd)
            if not cwd.is_dir():
                raise McpError(f"cwd 不存在或不是目录：{cwd}")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=str(cwd) if cwd else None, env=safe_env(self.cfg.env),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs,
            )
        except OSError as exc:
            raise McpError(f"启动 MCP server 失败（{self.cfg.command}）：{exc}") from exc

        for target in (self._read_stdout, self._read_stderr):
            thread = threading.Thread(target=target, name=f"mcp-{self.cfg.name}", daemon=True)
            thread.start()
            self._threads.append(thread)

        try:
            result = self._request(
                "initialize",
                {
                    "protocolVersion": self.cfg.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "akso-workbench", "version": "0.4"},
                },
                timeout=min(self.cfg.timeout_s, 30.0),
            )
            self.server_info = result.get("serverInfo") or {}
            self.protocol_version = str(result.get("protocolVersion") or self.cfg.protocol_version)
            self._notify("notifications/initialized", {})
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        proc, self._proc = self._proc, None
        self._closed = True
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001 —— 关不掉就强杀，不能把进程留在后台
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- MCP 方法

    def list_tools(self) -> list[dict]:
        """`tools/list`，**跟随 `nextCursor` 分页**（最多 20 页，防 server 死循环）。"""
        collected: list[dict] = []
        cursor: str | None = None
        for _page in range(20):
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params, timeout=min(self.cfg.timeout_s, 30.0))
            tools = result.get("tools") or []
            if not isinstance(tools, list):
                raise McpError("tools/list 返回的 tools 不是数组")
            for item in tools:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
                    continue
                collected.append(item)
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        else:
            raise McpError("tools/list 分页超过 20 页，已中止（server 的 nextCursor 可能没有前进）")
        self.tools = collected
        return collected

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=self.cfg.timeout_s
        )
        return render_result(result)

    def ping(self, timeout: float = 3.0) -> None:
        """标准 MCP `ping`，健康检查用。

        ★ 个别 server 没实现 ping（返回 `-32601 Method not found`）——那**不代表连接坏了**，
        当成健康即可；否则每 30 秒就会把一台正常 server 重启一次。
        """
        try:
            self._request("ping", {}, timeout=timeout)
        except McpError as exc:
            if exc.code == -32601:
                return
            raise

    # ---------------------------------------------------------------- 进程 IO

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                # 标准通知：工具清单变了。不消费它的话，server 热更新工具后
                # 我方 `_tools` 会一直是旧的（重新注册要等下次手动重连）。
                if "tools/list_changed" in line:
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        message = {}
                    if message.get("method") == "notifications/tools/list_changed":
                        self.tools_changed.set()
                self._lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._lines.put(None)  # EOF 哨兵：让等响应的线程立刻醒

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                text = line.rstrip()
                if not text:
                    continue
                self._stderr.append(text)
                del self._stderr[:-40]
                logger.debug("MCP[%s] stderr: %s", self.cfg.name, text)
        except (OSError, ValueError):
            pass

    def _send(self, message: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise McpError("MCP server 未连接")
        try:
            proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError(f"向 MCP server 写请求失败：{exc}") from exc

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, *, timeout: float) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise McpError(f"MCP server 不在运行（{self.cfg.name}）")
            self._request_id += 1
            req_id = self._request_id
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise McpError(f"MCP {method} 超时（{timeout:g}s）")
                try:
                    line = self._lines.get(timeout=remaining)
                except queue.Empty as exc:
                    raise McpError(f"MCP {method} 超时（{timeout:g}s）") from exc
                if line is None:
                    code = self._proc.poll() if self._proc else None
                    # ★ 不把 stderr 拼进异常：配置的 env 里可能有 token，而这条消息会进
                    # 工具观测/设置页。stderr 只进 DEBUG 日志（已走统一脱敏通道）。
                    raise McpError(f"MCP server 已退出（exit={code}）")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("MCP[%s] 非 JSON 输出：%s", self.cfg.name, line[:200])
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("id") == req_id:
                    if "error" in message:
                        error = message.get("error") or {}
                        raw_code = error.get("code")
                        code = raw_code if isinstance(raw_code, int) else None
                        raise McpError(
                            f"MCP {method} 失败：{error.get('message') or error}",
                            code=code,
                            kind="rpc",
                        )
                    return message.get("result") or {}
                # server 反向请求（sampling 等）：本客户端不支持，明确拒绝而不是装死
                if message.get("id") is not None and message.get("method"):
                    try:
                        self._send({
                            "jsonrpc": "2.0", "id": message["id"],
                            "error": {"code": -32601, "message": "client does not support this method"},
                        })
                    except McpError:
                        pass
                # 其余是通知（progress / message），忽略
