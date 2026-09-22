"""gws CLI 子进程封装 —— 四条硬约束，全部来自实测或本仓铁律。

## 为什么走 CLI 而不是直接 HTTP

平台研发的 `gws` 把配置写入收敛成一个**公开、版本化**的端点
（`POST /openapi/v3.8.1/component/instances/save`，66 个内置端点各有路由与参数表），
而配置工厂现在用的是 58 个**私有、无版本、逆向自浏览器流量**的端点
（`/api/platform/*` 37 + `/api/config/*` 15）。后者在平台升级时最先断
（`kernel/endpoints.py::is_versioned()` 就是为这件事写的）。

## 四条硬约束（改这个文件前先读懂它们各自防的是什么）

### ① 凭据**绝不经 argv**

`--app-id` / `--secret-key` 是 CLI 的全局 flag，但**不能用**：
argv 在进程列表里对**同机所有用户**可见（任务管理器、`Get-CimInstance Win32_Process`、
崩溃转储、我们自己的日志行）。所以走 `APP_ID` / `APP_SECRET` 环境变量——
它只对同用户可见，比 argv 低一档。

⚠️ **这不是"更安全"的结论**，是"两种都不理想里较不坏的那个"：
CLI 自己的 `~/.gws/config.yaml` 把 `app_secret` **明文落盘**，与本仓
[ADR-0007](../../../docs/adr/0007-credentials-never-plaintext.md)「凭据全程密文，明文只在内存」**直接冲突**。
产品形态应当是：密钥存我们自己的库（Fernet 密文），**调用时经 env 注入**，不落 CLI 的配置文件。

### ② 环境变量走**白名单**，不全量继承

规则 13 的铁律在这里有一个**具体的**后果，不只是教条：
gws 认的环境变量名太通用了——`HOST` / `TIMEOUT` / `APP_ID` / `OUTPUT` / `SILENT`。
全量继承宿主的 `os.environ`，任何一个恰好叫 `HOST` 的变量都会把**别的服务的地址**
喂给 gws，而表现是"连到了一个不相干的平台"或"连不通"，**不报错**。

### ③ stdin 必须 `DEVNULL`（**实测**）

未配置 service 时，gws 不是报错，而是**进入交互式提问**：

```
━━━━ 需要配置服务地址 ━━━━━━━━━━━━━━━━━━━━━━━━━━━
服务地址: _
```

给了管道却不关，调用方会**挂满整个超时**；关掉之后它立刻以 exit=1 收尾。
本模块一律 `stdin=DEVNULL` + `--service` 显式传入，把这条路从物理上排除。

### ④ 只读动词**白名单 fail-closed**

`run_readonly()` 只放行白名单里的动词，未知动词**一律拒**（不"回落到通用 run"）。
同族的还有 `component_instance_save()`：它**不接受 `--operation-type` 的默认值**——
CLI 的默认是 **30 覆盖**，而那意味着"手滑一次就整份覆盖"。必须由调用方显式声明意图。

### 附：**永不封装** `gws api call`

它是「任意方法 + 任意路径」的 HTTP 逃逸口。一旦包成 Agent 工具，
本仓的 `Risk` 分级、确认门、`precheck` 会被**整体绕过**（`plugin_api.ToolHub` 的硬闸门形同虚设）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- 错误分层
#
# 分层的唯一目的是**让调用方分得清"该修什么"**：
# 装没装（BinaryNotFound）/ 配没配（NotConfigured）/ 平台拒了（PlatformError）/
# 我们这边超时（Timeout）/ 其它（ProcessError）。混成一个 RuntimeError 时，
# 排障只能读字符串——这正是 PITFALLS #58「截断与参数错分开」的同一条教训。


class GwsError(RuntimeError):
    """gws 适配层所有失败的基类。"""


class GwsBinaryNotFound(GwsError):
    """找不到 gws 可执行文件（含"找到了但无法执行"）。"""


class GwsNotConfigured(GwsError):
    """没配 service / app_id / app_secret —— 是**配置问题**，不是代码问题。"""


class GwsTimeout(GwsError):
    """子进程超时被杀。"""


class GwsProcessError(GwsError):
    """非零退出，且输出不是平台信封（多为 CLI 自身的用法错误）。"""

    def __init__(self, message: str, *, code: int, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.stderr_tail = stderr_tail


class GwsPlatformError(GwsError):
    """平台的业务失败：HTTP 200 但信封 `code != 0`（与工厂 `ApiError` 同一族）。

    注意 CLI 的 `save` **只回 `{code, message}`、不回 id、不回显内容**
    ——与本仓 PITFALLS #62「接口只回成功标志」是同一个形状：
    ⇒ **写完必须回读**，不能拿 `code == 0` 当"改对了"的证据。
    """

    def __init__(self, message: str, *, code: int, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


# ---------------------------------------------------------------- 环境与路径

#: 允许传给子进程的宿主环境变量（**白名单**，理由见模块文档约束 ②）。
#: 这些是 Windows 上进程能正常启动的最低集合；`PATH` 两种大小写都给，
#: 因为 Python 在 Windows 上读到的键名大小写**跟随创建时的写法**。
_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH", "Path", "PATHEXT", "SystemRoot", "SYSTEMROOT", "windir",
    "TEMP", "TMP", "COMSPEC",
    # gws 要往 $HOME/.gws 读写自己的配置/Token/日志，所以 HOME 类要给它
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    # Windows 中文/编码相关：不给它，CLI 的中文输出会按 ANSI 落盘
    "PYTHONUTF8", "PYTHONIOENCODING",
)

#: 我们主动设置的环境变量（gws 认这些名字，见 `gws config --help`）。
_ENV_SERVICE = "HOST"
_ENV_APP_ID = "APP_ID"
_ENV_APP_SECRET = "APP_SECRET"
_ENV_SILENT = "SILENT"
_ENV_OUTPUT = "OUTPUT"
_ENV_TIMEOUT = "TIMEOUT"

#: 只读动词白名单（元组前缀匹配，逐元素相等，**不做子串匹配**）。
#: 这里每一条都是 2026-09-22 离线实测可发现的动词（`gws <cmd> --help` 与内置 `apis list`）。
READ_ONLY_VERBS: frozenset[tuple[str, ...]] = frozenset({
    ("version",),
    ("doctor",),
    ("help",),
    ("apis", "list"),
    ("apis", "show"),
    ("api", "list"),
    ("api", "describe"),
    ("api", "search"),
    ("api-version", "recommended"),
    ("api-version", "list"),
    ("config", "show"),
    ("auth", "status"),
    ("auth", "user-info"),
    ("auth", "public-key"),
    ("agents", "status"),
    ("log", "list"),
    ("log", "show"),
    ("metadata", "list-objects"),
    ("metadata", "object", "show"),
    ("metadata", "object", "fields"),
    ("metadata", "object", "field"),
    ("metadata", "object", "query"),
    ("metadata", "object", "field-query"),
    ("metadata", "object-type", "list"),
    ("metadata", "object-type", "show"),
    ("metadata", "lifecycle", "show"),
    ("metadata", "lifecycle", "status-list"),
    ("metadata", "lifecycle", "status-show"),
    ("metadata", "picklist", "list"),
    ("metadata", "picklist", "show"),
    ("component", "metadata", "list"),
    ("component", "metadata", "show"),
    ("component", "instance", "list"),
    ("component", "instance", "show"),
    ("component", "instance", "query"),
    ("component", "instance", "query-by-paths"),
    ("data", "get"),
    ("data", "list"),
    ("data", "get-index"),
    ("data", "link-address"),
    ("aql", "query"),
    ("aql", "query-single"),
    ("aql", "pages"),
    ("task", "show"),
})

#: 操作类型（`component instance save --operation-type`）。
#: ⚠️ **CLI 的默认值是 30（覆盖）**，本模块**不接受默认**——必须显式传。
OP_CREATE = 10
OP_UPDATE = 20
OP_OVERWRITE = 30
OPERATION_TYPES: dict[int, str] = {OP_CREATE: "新增", OP_UPDATE: "修改", OP_OVERWRITE: "覆盖"}


def _binary_candidates() -> list[Path]:
    """gws 可执行文件的候选位置，**按"越可能是操作员特意放的"排序**。"""
    exe = "gws.exe" if sys.platform == "win32" else "gws"
    out: list[Path] = []
    env = os.environ.get("WORKBENCH_GWS_BIN")
    if env:
        out.append(Path(env))
    # 我们自己的运行时目录（操作员手动放置，或将来随包分发）
    from ... import config  # 延迟导入：本模块要能被 tools/ 下的脚本独立使用

    out.append(config.RUNTIME_DIR / "gws" / exe)
    # 官方 install.ps1 的两个落点
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "gws" / exe)
    program_files = os.environ.get("ProgramFiles") or os.environ.get("PROGRAMFILES")
    if program_files:
        out.append(Path(program_files) / "gws" / exe)
    return out


def resolve_binary(explicit: str | Path | None = None) -> Path:
    """定位 gws 可执行文件。找不到抛 :class:`GwsBinaryNotFound`（带处置办法）。

    **刻意不把二进制提交进仓库**：它是 9.5 MB 的**未签名**第三方产物
    （`Get-AuthenticodeSignature` = `NotSigned`），提交进 git 等于把一个
    无法审计、无法溯源、每次升级都产生巨大二进制 diff 的东西塞进版本历史。
    """
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise GwsBinaryNotFound(f"指定的 gws 可执行文件不存在：{path}")
        return path

    tried: list[str] = []
    for cand in _binary_candidates():
        tried.append(str(cand))
        if cand.is_file():
            return cand

    which = shutil.which("gws")
    if which:
        return Path(which)

    hint = (
        "未找到 gws 可执行文件。三种处置（任选其一）：\n"
        "  1) 把 gws.exe 放到 %APPDATA%\\Workbench\\runtime\\gws\\ 下；\n"
        "  2) 设环境变量 WORKBENCH_GWS_BIN=<gws.exe 的完整路径>；\n"
        "  3) 跑官方 install.ps1（注意：需 UAC 提权、会写系统 PATH，并把指引装配进本机 AI 工具目录）。\n"
        "已尝试：" + "；".join(tried) + ("；PATH" if not which else "")
    )
    raise GwsBinaryNotFound(hint)


@dataclass(frozen=True)
class GwsConfig:
    """一次调用的目标环境与凭据。

    `app_secret` 只经环境变量下传（约束 ①），**永不进 argv、永不进日志**。
    """

    service: str | None = None
    app_id: str | None = None
    app_secret: str | None = field(default=None, repr=False)
    binary: Path | None = None
    #: 子进程超时。CLI 自己的 `TIMEOUT` 默认 30s，这里留出余量；
    #: 两者是**两层**，不是一回事：内层是 CLI 对单次 HTTP 的超时，外层是进程级兜底。
    timeout_s: float = 90.0
    #: 传给 CLI 的超时（秒），经 `TIMEOUT` 环境变量下发。
    request_timeout_s: int = 30

    def missing(self) -> list[str]:
        """缺哪些必填项（用于给出**一句能照做**的报错）。"""
        gaps: list[str] = []
        if not self.service:
            gaps.append("service（服务地址）")
        if not self.app_id:
            gaps.append("app_id（eGMP 后台 →「应用管理」申请）")
        if not self.app_secret:
            gaps.append("app_secret（与 app_id 配对）")
        return gaps


class GwsClient:
    """gws 子进程调用器（**同步**）。

    按铁律 11/12：调用方若在 async 上下文里，必须 `asyncio.to_thread(client.run, ...)`，
    否则阻塞的是整个事件循环——表现是"服务卡住"，不是报错。
    """

    def __init__(self, cfg: GwsConfig | None = None, *, argv_prefix: list[str] | None = None) -> None:
        self.cfg = cfg or GwsConfig()
        #: 真实命令行前缀，默认 `[gws 可执行文件]`。
        #: 存在的理由是**可测**：单测把它换成 `[sys.executable, <假 CLI 脚本>]`，
        #: 于是"解析 / 错误映射 / 只读闸门"这些逻辑能在**真子进程**下被验证，
        #: 而不是打桩掉整个 subprocess（那会把本模块最该被测的部分测没了）。
        self._argv_prefix = argv_prefix

    # ------------------------------------------------------------ 内部

    def _prefix(self) -> list[str]:
        if self._argv_prefix is not None:
            return list(self._argv_prefix)
        return [str(resolve_binary(self.cfg.binary))]

    def _env(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env[_ENV_SILENT] = "true"
        env[_ENV_OUTPUT] = "json"
        env[_ENV_TIMEOUT] = str(self.cfg.request_timeout_s)
        if self.cfg.service:
            env[_ENV_SERVICE] = self.cfg.service
        if self.cfg.app_id:
            env[_ENV_APP_ID] = self.cfg.app_id
        if self.cfg.app_secret:
            env[_ENV_APP_SECRET] = self.cfg.app_secret
        return env

    def _scrub(self, text: str) -> str:
        """把凭据从任何要外传的文本里抹掉。

        为什么不指望"我们不会打印它"：CLI 的 `--verbose`、报错回显、
        将来的 `config show` 变体都可能把配置原样吐出来。
        这是**兜底**，不是主要防线（与 `plugin_api.redact_args` 的定位一致）。
        """
        for secret in (self.cfg.app_secret, self.cfg.app_id):
            if secret and len(secret) >= 4:
                text = text.replace(secret, "***")
        return text

    @staticmethod
    def _verb_of(args: list[str]) -> tuple[str, ...]:
        """取前导的**非 flag** 参数作为动词（遇到第一个 `-` 开头的就停）。"""
        verb: list[str] = []
        for arg in args:
            if arg.startswith("-"):
                break
            verb.append(arg)
        return tuple(verb)

    @staticmethod
    def _is_readonly(verb: tuple[str, ...]) -> bool:
        """元组**前缀**匹配（逐元素相等）。

        刻意不用子串：`component instance listx` 不该因为"长得像 list"而被放行，
        这与本仓"沙箱策略名必须用 ASCII 正则、不能用 `str.isalnum()`"是同一种谨慎。
        """
        return any(verb[: len(allowed)] == allowed for allowed in READ_ONLY_VERBS)

    # ------------------------------------------------------------ 公开

    def run(self, *args: str, expect_json: bool = True, timeout_s: float | None = None) -> object:
        """执行一条 gws 命令，返回信封里的 `data`（`expect_json=True` 时）。

        **不做只读校验**——需要只读保证请用 :meth:`run_readonly`。
        """
        if not args:
            raise GwsError("空命令行")
        argv = [*self._prefix(), "-s", *args]
        try:
            proc = subprocess.run(  # noqa: S603 —— argv 是 list，无 shell，无字符串拼接
                argv,
                env=self._env(),
                stdin=subprocess.DEVNULL,  # 约束 ③：不给它交互的机会
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s if timeout_s is not None else self.cfg.timeout_s,
            )
        except FileNotFoundError as exc:
            raise GwsBinaryNotFound(f"无法执行 gws：{argv[0]}（{exc}）") from exc
        except subprocess.TimeoutExpired as exc:
            raise GwsTimeout(
                f"gws 超时（{timeout_s if timeout_s is not None else self.cfg.timeout_s:.0f}s）："
                f"{' '.join(args)}"
            ) from exc

        stdout = self._scrub(proc.stdout or "")
        stderr = self._scrub(proc.stderr or "")

        if proc.returncode != 0:
            # 未配置是**最常见的失败**，单独成一类：它该引导用户去配置，而不是去查代码。
            if "服务地址" in stderr or "服务地址" in stdout:
                gaps = self.cfg.missing()
                raise GwsNotConfigured(
                    "gws 尚未配置目标环境"
                    + (f"，缺：{'、'.join(gaps)}" if gaps else "")
                    + f"；命令：{' '.join(args)}"
                )
            raise GwsProcessError(
                f"gws 退出码 {proc.returncode}：{' '.join(args)}",
                code=proc.returncode,
                stderr_tail=stderr[-2000:],
            )

        if not expect_json:
            return stdout

        text = stdout.strip()
        if not text:
            raise GwsProcessError(
                f"gws 无输出（预期 JSON）：{' '.join(args)}", code=0, stderr_tail=stderr[-2000:]
            )
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise GwsProcessError(
                f"gws 输出不是 JSON：{' '.join(args)}（首 200 字：{text[:200]!r}）",
                code=0,
                stderr_tail=stderr[-2000:],
            ) from exc
        return self._unwrap(payload, args)

    def run_readonly(self, *args: str, **kwargs: object) -> object:
        """只读执行。动词不在 :data:`READ_ONLY_VERBS` 里就拒（fail-closed）。"""
        verb = self._verb_of(list(args))
        if not self._is_readonly(verb):
            raise GwsError(
                f"拒绝执行非只读命令：{' '.join(verb) or '(空)'}。"
                "只读白名单见 workbench/services/gws/client.py::READ_ONLY_VERBS；"
                "要写平台请走带显式意图的专用方法。"
            )
        return self.run(*args, **kwargs)

    @staticmethod
    def _unwrap(payload: object, args: tuple[str, ...]) -> object:
        """拆平台信封 `{code, message, data}`；`code != 0` 抛 :class:`GwsPlatformError`。

        业务失败也是 **HTTP 200**（这一点与工厂的 `egmp.client` 完全一致），
        所以**唯一**的成败信号在 `code` 里——不能拿"进程退出码 0"当成功。
        """
        if isinstance(payload, dict) and "code" in payload:
            code = payload.get("code")
            if code not in (0, "0", None) or (code is None and payload.get("message")):
                raise GwsPlatformError(
                    f"平台拒绝：{' '.join(args)} → code={code} message={payload.get('message')!r}",
                    code=int(code) if isinstance(code, (int, str)) and str(code).lstrip("-").isdigit() else -1,
                    data=payload.get("data"),
                )
            return payload.get("data")
        return payload

    # ------------------------------------------------------------ 只读便捷封装

    def version(self) -> str:
        return str(self.run_readonly("version", expect_json=False)).strip()

    def doctor(self) -> str:
        """环境诊断。⚠️ 它的**退出码不可用**（实测：有检查未通过时仍 exit=0），故返回原文。"""
        return str(self.run_readonly("doctor", expect_json=False))

    def component_types(self) -> list[dict]:
        """组件类型全表：`[{component_type, name_cn, name_en, sort, parent_code}]`。

        这是**整条 CLI 配置路线的第一块地基**——`component instance save <type>` 里的
        `<type>` 取值只能从这里拿，CLI 不提供静态词表。
        """
        data = self.run_readonly("component", "metadata", "list")
        return data if isinstance(data, list) else []

    def component_instances(self, component_type: str) -> list[dict]:
        """某类型组件实例的列表（**全量**，用于读旧 → 合并 → 提交）。"""
        data = self.run_readonly("component", "instance", "list", component_type)
        return data if isinstance(data, list) else []

    def component_instance(self, component_type: str, full_path: str) -> dict:
        """单个组件实例详情。`full_path` 形如 `object.aa__c`（`<类型>.<code>`）。

        这是**探针的核心命令**：拿它与工厂已取证的线格式逐字对照，
        就能判定那套真机知识能不能迁移到这个通道。
        """
        data = self.run_readonly("component", "instance", "show", component_type, full_path)
        return data if isinstance(data, dict) else {}

    def component_query(self, component_type: str | None = None, select_columns: list[str] | None = None) -> list[dict]:
        body: dict = {}
        if component_type:
            body["component_type"] = component_type
        if select_columns:
            body["select_columns"] = select_columns
        data = self.run_readonly("component", "instance", "query", "--data", json.dumps(body))
        return data if isinstance(data, list) else []

    def object_query(self, match: str) -> list[dict]:
        data = self.run_readonly("metadata", "object", "query", match)
        return data if isinstance(data, list) else []

    def object_fields(self, code: str) -> list[dict]:
        data = self.run_readonly("metadata", "object", "fields", code)
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------ 写入（**显式意图**）

    def component_instance_save(
        self,
        component_type: str,
        instances: list[dict],
        *,
        operation_type: int,
    ) -> None:
        """保存组件实例。**`operation_type` 必须显式传**（约束 ④）。

        CLI 的默认值是 **30（覆盖）**——把"整份覆盖"设成默认，
        与本仓踩过的 D-B/D-F「全量替换却不读旧」是同一个坑的另一种写法。
        ⇒ 这里**不接受默认值**：谁想覆盖，谁就得把 `OP_OVERWRITE` 写出来。
        """
        if operation_type not in OPERATION_TYPES:
            raise GwsError(
                f"operation_type 必须是 {sorted(OPERATION_TYPES)} 之一，得到 {operation_type!r}"
            )
        if not isinstance(instances, list) or not instances:
            raise GwsError("instances 必须是非空数组（CLI 不校验，我们替它校验）")
        self.run(
            "component", "instance", "save", component_type,
            "--data", json.dumps(instances, ensure_ascii=False),
            "--operation-type", str(operation_type),
            expect_json=True,
        )
