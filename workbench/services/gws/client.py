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
from collections.abc import Callable, Iterable
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
    ("config", "test"),  # 向 server 发一个 GET 探连通性——只读。
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

#: 组件类型的**子类型后缀**候选（命名约定 `<父类型>_<子类型>`）。
#:
#: 来历：`layout_section` / `layout_control` / `lifecycle_status` 三个**有效但未声明**的类型
#: 就是靠这个形状找到的。前三个是**已实证**的，其余是同类推断（`discover_component_types`
#: 会逐个探测，探测失败的会进 `probed_missing`，**不会被当成存在**）。
COMPONENT_SUBTYPE_HINTS: tuple[str, ...] = (
    # —— 已实证（2026-09-22 真机：分别有 3569 / 14062 / 3433 个实例）
    "section", "control", "status",
    # —— 同类推断，待探测
    "field", "page", "item", "detail", "option", "column", "attr",
    "rule", "config", "node", "group",
)

#: 二进制字符串里出现过的**裸** component token（这些是类型名本身，不带前缀）。
COMPONENT_BARE_HINTS: tuple[str, ...] = ("object", "layout", "section", "control", "status")

#: 写平台的动词（与上面那张只读白名单**互斥**）。
#:
#: 单独立一张表不是为了好看，是为了让"这两个集合不许相交"变成一条**可断言的**结构保证
#: （`tests/test_gws_client.py::test_migration_verbs_are_not_in_the_readonly_allowlist`）。
#: 本仓的口径一贯如此：能变成硬闸门的东西，不要靠人记得。
MIGRATION_WRITE_VERBS: frozenset[tuple[str, ...]] = frozenset({
    ("migration", "export"),   # 在平台上创建配置包记录（不改配置，但不是只读）
    ("migration", "import"),   # 上传入站配置包（尚未生效）
    ("migration", "deploy"),   # 🔴 部署 ⇒ 配置在这里才真正生效
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

    @classmethod
    def from_env(cls, **overrides: object) -> "GwsConfig":
        """从**我们自己的**环境变量名读配置：`WORKBENCH_GWS_SERVICE` / `_APP_ID` / `_APP_SECRET`。

        ## 为什么不直接读 gws 认的 `HOST` / `APP_ID` / `APP_SECRET`

        那两个名字**太通用**（约束 ②）：宿主机上任何一个恰好叫 `HOST` 的变量都会被 gws 当成
        服务地址，而表现是"连到了不相干的平台"或"连不通"——**不报错**。
        走 `WORKBENCH_GWS_*` 前缀，由本模块**翻译**成 gws 认的名字再注入子进程，
        于是"哪个变量进了 gws 的环境"这件事是**我们决定的**，不是继承来的。

        ⚠️ 这是**跑通用的入口**，不是产品形态。产品形态按决策卡 **C2**：
        密钥存我们自己的库（Fernet 密文），调用时注入——见 `docs/CLI-CONFIG-PLAN.md` §9。

        **`None` 覆盖项一律忽略**（不是"覆盖成 None"）：调用方常写成
        `from_env(service=args.service or None)`，若 None 也生效，环境变量里的 service
        会被**静默清空**——症状是"凭据都对，却报缺 service"。2026-09-22 实测踩到过。
        """
        env = os.environ
        kwargs: dict[str, object] = {
            "service": env.get("WORKBENCH_GWS_SERVICE") or None,
            "app_id": env.get("WORKBENCH_GWS_APP_ID") or None,
            "app_secret": env.get("WORKBENCH_GWS_APP_SECRET") or None,
        }
        binary = env.get("WORKBENCH_GWS_BIN")
        if binary:
            kwargs["binary"] = Path(binary)
        kwargs.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class MigrationPackage:
    """一个配置迁移包在平台上的身份。三个子命令回的字段**不完全一样**，这里合成一个形状。

    | 字段 | export | import | 说明 |
    |---|---|---|---|
    | `name` | ✅ | ✅ | 包名（形如 `PKG-NO-001`） |
    | `package_id` | ✅ | ✅ | Guid；`deploy` 要的就是它 |
    | `file_path` | ✅ | ❌ | **下载标识**，不是本地路径 |
    | `type` / `deploy_status` | ❌ | ✅ | `type` 与 `deploy_status` 的**枚举值未知**（没有真机样本） |

    ⚠️ `deploy_status` 的取值表**我们不知道**（只见过示例值 `1`）。
    在拿到真机样本之前，**不要**用它做"能不能部署"的判据——那会是一个凭想象写的枚举。
    """

    name: str = ""
    package_id: str = ""
    file_path: str = ""
    type: int | None = None
    deploy_status: int | None = None

    @classmethod
    def from_payload(cls, payload: object) -> "MigrationPackage":
        if not isinstance(payload, dict):
            return cls()
        raw_type = payload.get("type")
        raw_status = payload.get("deploy_status")
        return cls(
            name=str(payload.get("name") or ""),
            package_id=str(payload.get("package_id") or ""),
            file_path=str(payload.get("file_path") or ""),
            type=raw_type if isinstance(raw_type, int) else None,
            deploy_status=raw_status if isinstance(raw_status, int) else None,
        )


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

    #: 信封的旁证键：真正的 `{code, message, data}` 信封里，`code` **绝不会单独出现**。
    _ENVELOPE_HINT_KEYS = ("message", "data", "errorCode", "requestID", "traceId")

    @classmethod
    def _looks_like_envelope(cls, payload: object) -> bool:
        """判断一份响应是不是平台的 `{code, message, data}` 信封。

        ## ⚠️ 为什么不能只看"有没有 `code` 键"（2026-09-22 真机实测踩到）

        `component instance show` 回的是一份**裸实例**，而**实例自己就有一个 `code` 字段**
        （布局编码，字符串，如 `change_hdh_mgt_base_layout__ak`）。只看 key 存在性时：

        ```
        平台拒绝：component instance show layout layout.… → code=change_hdh_mgt_base_layout__ak
        ```

        —— 平台**什么错都没犯**，是适配层把一份正常响应读成了业务失败。
        形态与本仓 PITFALLS 里那一族一模一样：**不抛异常、看起来像"平台拒了"**。

        同一条命令族里**两种形状并存**（`component metadata list` 回信封、
        `component instance show` 回裸对象），所以不能靠"这个命令回什么"来记，
        只能**按载荷形状判**。

        ## 判据（fail-closed 方向）

        - `code` 必须是 **int**（状态码是数字；领域对象里的 `code` 是字符串编码）；
        - 且满足其一：① 出现任一信封旁证键；② `code != 0`。

        第 ② 条是**刻意偏向 fail-closed** 的：万一将来出现一个"只有 `code` 没有 message/data"
        的错误信封，我们仍然会把它当失败抛出去，而不是当成数据静默交给调用方。
        """
        if not isinstance(payload, dict):
            return False
        code = payload.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            return False
        return code != 0 or any(key in payload for key in cls._ENVELOPE_HINT_KEYS)

    @classmethod
    def _unwrap(cls, payload: object, args: tuple[str, ...]) -> object:
        """拆平台信封 `{code, message, data}`；`code != 0` 抛 :class:`GwsPlatformError`。

        业务失败也是 **HTTP 200**（这一点与工厂的 `egmp.client` 完全一致），
        所以**唯一**的成败信号在 `code` 里——不能拿"进程退出码 0"当成功。

        但"有没有信封"本身要判（见 :meth:`_looks_like_envelope`）：
        **裸对象直接返回**，不要硬套信封语义。
        """
        if not cls._looks_like_envelope(payload):
            return payload
        assert isinstance(payload, dict)  # _looks_like_envelope 已保证
        code = payload.get("code")
        if code != 0:
            raise GwsPlatformError(
                f"平台拒绝：{' '.join(args)} → code={code} message={payload.get('message')!r}",
                code=int(code) if isinstance(code, int) else -1,
                data=payload.get("data"),
            )
        return payload.get("data")

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

    # ------------------------------------------------------------ 组件类型发现

    def component_type_meta(self, component_type: str) -> dict | None:
        """`component metadata show <type>` —— **廉价**的存在性探测。

        ## 为什么它是首选（2026-09-22 真机实测）

        | 探法 | 返回 | 代价（含 CLI 写日志） |
        |---|---|---|
        | `component instance list <type>` | 该类型**全部实例** | **MB 级**（`layout_control` 14062 行） |
        | **`component metadata show <type>`** | 一条元数据 / `null` | **几百字节** |

        实测判据：存在的类型回 `{component_type, name_cn, name_en, sort, parent_code}`；
        **不存在回 `null`**（`field` → `null`；`不存在的类型xyz` → `null`）。
        于是"探测 650 个候选"从**数 GB 日志**降到**几十 KB**。

        ## 它顺带揭示了模型的层级

        `component metadata list` 里 47 条的 `parent_code` **全是 `null`**，
        而 `show` 会给出真实父子关系：

        ```
        layout_control   → parent_code: "layout"      (sort 1853)
        lifecycle_status → parent_code: "lifecycle"   (sort 901)
        object_field     → parent_code: "object"      (sort 701)
        ```

        ⇒ **`metadata list` 只列"根类型"**，子类型只能经 `show` 看见。
        这正是我第一轮判错的原因（那 47 条里确实没有 `section`/`control`）。
        """
        data = self.run_readonly("component", "metadata", "show", component_type)
        return data if isinstance(data, dict) and data else None

    def component_type_exists(self, component_type: str, *, strict: bool = False) -> tuple[bool, str]:
        """探测某个 `component_type` 是否可用（**只读**）。返回 `(是否存在, 失败原因)`。

        - 默认走 :meth:`component_type_meta`（**廉价**，几百字节）；
        - `strict=True` 走 `component instance list`（拉全表，**MB 级**）——
          只在"我不光要它存在、还要确认能取到实例"时才用。

        **环境问题（超时/未配置/找不到二进制）一律向上抛**，不吞成"类型不存在"——
        那会输出一份"看起来完整、其实少了东西"的清单（本仓最忌的静默变差）。
        """
        try:
            if strict:
                self.run_readonly("component", "instance", "list", component_type)
                return True, ""
            return self.component_type_meta(component_type) is not None, ""
        except (GwsPlatformError, GwsProcessError) as exc:
            reason = getattr(exc, "stderr_tail", "") or str(exc)
            return False, reason.strip()[:200]
        except (GwsTimeout, GwsNotConfigured, GwsBinaryNotFound):
            raise  # 这几类是**环境**问题，不是"类型不存在"，别混进来

    def discover_component_types(
        self,
        candidates: Iterable[str] | None = None,
        *,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        """把 `component metadata list` 与**探测**合起来，给出真正的组件类型清单。

        ## 为什么需要它（2026-09-22 真机实测）

        `component metadata list` 回 **47** 类，而**那张表不完整**：

        | 类型 | 在 47 条里？ | 实测有实例吗 |
        |---|---|---|
        | `layout` / `lifecycle` / `object` … | ✅ 在 | ✅ |
        | **`layout_section`** | ❌ **不在** | ✅ **3569 个** |
        | **`layout_control`** | ❌ **不在** | ✅ **14062 个** |
        | **`lifecycle_status`** | ❌ **不在** | ✅ **3433 个** |

        而这几个恰恰是**承重的**（布局的内容本体、生命周期的状态）。
        只看那张表会得出"component 模型里没有 section/control"的**错误结论**——
        我第一轮就是这么判的，直到去探测才发现。

        ## 为什么现在可以把候选放大

        `component metadata list` 只列**根类型**（47 条的 `parent_code` 全是 `null`），
        子类型要看 `component metadata show <type>`。而后者**只回几百字节**，
        所以"650 个候选"从**数 GB 日志**降到**几十 KB**（见 :meth:`component_type_meta`）。

        命名约定是 **`<父类型>_<子类型>`**（`layout` → `layout_section` / `layout_control`；
        `lifecycle` → `lifecycle_status`；`object` → `object_field`），
        所以这里按这个形状生成候选并逐个探测。
        """
        declared = self.component_types()
        declared_names = [
            str(t.get("component_type")) for t in declared
            if isinstance(t, dict) and t.get("component_type")
        ]

        if candidates is None:
            candidates = []
            for name in declared_names:
                for suffix in COMPONENT_SUBTYPE_HINTS:
                    candidates.append(f"{name}_{suffix}")
            # 二进制里出现过的裸 token（`object` / `layout` / `section` / `control` / `status`）
            candidates.extend(COMPONENT_BARE_HINTS)

        found: list[dict] = []
        missing: dict[str, str] = {}
        seen: set[str] = set(declared_names)
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            if on_progress is not None:
                on_progress(cand)
            meta = None
            try:
                meta = self.component_type_meta(cand)
            except (GwsPlatformError, GwsProcessError) as exc:
                missing[cand] = (getattr(exc, "stderr_tail", "") or str(exc)).strip()[:200]
                continue
            if meta:
                found.append(meta)
            else:
                missing[cand] = "metadata show 回 null（类型不存在）"

        # 按 parent_code 归组：这棵树就是"list 只给根、子类型要靠 show"的实证
        by_parent: dict[str, list[str]] = {}
        for meta in found:
            parent = str(meta.get("parent_code") or "")
            by_parent.setdefault(parent, []).append(str(meta.get("component_type")))

        return {
            "declared": declared,
            "declared_names": declared_names,
            "undeclared": sorted(found, key=lambda m: int(m.get("sort") or 0)),
            "undeclared_but_real": sorted(
                str(m.get("component_type")) for m in found
            ),
            "by_parent": {k: sorted(v) for k, v in by_parent.items()},
            "probed_missing": missing,
            "probed_total": len(seen) - len(declared_names),
        }

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

    # ------------------------------------------------------------ 配置迁移（工厂**完全没有**的一块）
    #
    # ## 为什么这块值得先做
    #
    # 它是配置工厂的能力空白，而且**不依赖 component 实例 schema**——
    # 也就是说，在那个决定性未知（见 docs/CLI-CONFIG-PLAN.md §5）解决之前，
    # 这是唯一"确定能交付"的一块。
    #
    # ## 三跳：导出 → 入站导入 → 部署
    #
    # 这个形状**不是我们从手册抄的**，是 CLI 的响应体自己说出来的（2026-09-22 离线实测）：
    #
    # | 步 | 端点 | 响应 |
    # |---|---|---|
    # | 导出 | `POST /component/migration/export` | `{name, package_id, file_path}` |
    # | 导入 | `POST /component/migration/import`（multipart，字段名 `file`） | `{package_id, name, type, deploy_status}` |
    # | 部署 | `POST /component/migration/deploy/{package_id}` | **`{code, message}` —— 没有 `data`** |
    #
    # ⇒ 正好印证 [FACTORY-PLATFORM-COGNITION](../../../docs/FACTORY-PLATFORM-COGNITION.md) §7 的 **E4**：
    # **「入站包导入 ≠ 生效」**，中间还隔着一次部署。`import` 回的那个 `deploy_status`
    # 就是这个"还没生效"的状态位。
    #
    # ⚠️ **`deploy` 的成败无法由它自己的响应确认**（只回成功标志）——与本仓
    # PITFALLS #62 同形。判断部署是否真的生效**必须回读平台配置**，不能拿 `code == 0` 交差。

    def migration_export(
        self,
        *,
        summary: str | None = None,
        description: str | None = None,
        owner_name: str | None = None,
    ) -> "MigrationPackage":
        """导出一个配置迁移包。**会在平台上创建一条配置包记录**（不是纯只读）。

        `file_path` 是**下载标识**，不是本地路径——要拿到 zip 还得再走一次
        `attachment` 下载（那一步**不在本模块**：它写本地磁盘，属于另一个决策面）。
        """
        body: dict[str, str] = {}
        if summary:
            body["summary"] = summary
        if description:
            body["description"] = description
        if owner_name:
            body["owner_name"] = owner_name
        # 刻意**不**用 pydantic BaseModel 承载这个体：约束 21（必须显式建模）防的是
        # "HTTP 请求体多了一个字段被静默忽略"，而这里的体是**我们逐字段拼出来的**，
        # 没有"外部送进来的多余字段"可漏。加一层模型只会多一层要同步的形状。
        args = ["migration", "export"]
        if body:
            args += ["--data", json.dumps(body, ensure_ascii=False)]
        data = self.run(*args)
        return MigrationPackage.from_payload(data)

    def migration_import(self, package_zip: str | Path) -> "MigrationPackage":
        """把一个 zip 配置包**导入**成"入站配置包"。**不生效**（见上面三跳表）。

        Fail-Closed：路径不存在、不是文件、扩展名不是 `.zip` —— **一律拒**，
        并且**在起子进程之前就拒**（不要拿一个坏路径去换一条 CLI 的报错回来）。
        """
        path = Path(package_zip)
        if not path.is_file():
            raise GwsError(f"配置包文件不存在：{path}")
        if path.suffix.lower() != ".zip":
            raise GwsError(f"配置包必须是 zip（CLI 的 multipart 就只收 zip）：{path.name}")
        data = self.run("migration", "import", "--file", str(path))
        return MigrationPackage.from_payload(data)

    def migration_deploy(self, package_id: str) -> None:
        """**部署**已导入的配置包 ⇒ 配置在这里才真正生效。

        🔴 **这是本模块唯一会改变平台配置的动作。** 三件事必须说清楚：

        1. 它的响应**没有 `data`**，只有 `{code, message}` ⇒ **不能用它确认生效**；
        2. 生效与否**只能靠回读平台配置**（对照部署前的快照）；
        3. 平台侧**没有撤销键**——配置包只支持全量迁移，回滚要靠再导一个反向包。
           这与 [FACTORY-VERIFICATION-PLAN](../../../docs/FACTORY-VERIFICATION-PLAN.md) §7
           记的"无撤销键资源"是同一族风险（选项集不可删、生命周期状态只能停用）。

        ⚠️ 本方法**当前不做人工确认**——因为适配层还没有 `Risk` 通道。
        等它被接成 Agent 工具时，**必须**是 `Risk.DANGEROUS` 并过确认门（铁律 6/9）。
        """
        if not package_id or not str(package_id).strip():
            raise GwsError("package_id 不能为空")
        self.run("migration", "deploy", str(package_id))
