"""沙箱策略：**声明**一份"这段不受信代码被允许做什么"。

设计要点（完整决策见 [ADR-0014](../../docs/adr/0014-sandbox-for-untrusted-code.md)）：

1. **策略是有限枚举的具名集合**，不是"用户随便填的一组开关"。
   理由与本仓既有的"`_MODULES` 是显式清单"一致：**可枚举的东西才能被 review、被测试、被审计引用**。
   新增一条策略 = 改代码 + 加测试，而不是改一个配置文件。
2. **默认最保守**（`severity` 越小越紧）。放宽必须是显式选择，且会写进审计。
3. 本层是**纯数据 + 纯校验**：不 import `db`、不 import `services`、不 import 插件。
   这样它能在**没装依赖的机器上**被测试，也守住了依赖方向。

## 诚实边界（ADR-0014 §D-3）

这里的 `allow_network` 之类字段是**声明**，不是强制。除下列已强制项外，其余是给执行器与审计用的意图记录。
**别把它当安全边界读**——真正的强制项在 `SandboxPolicy.enforced` 里逐条列出。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# 敏感路径表**已收归 `config` 唯一事实源**，这里只是再导出，
# 让既有的 `from ..sandbox.policy import is_sensitive_path` 调用点原样可用。
#
# **为什么收走**（2026-09-20 实测的 P0）：本文件原有一张表、`plugins/builtins.py`
# 另有一张，而只读工具用的是后者——后者**不含** `browser-states` / `egmp-tokens` /
# `workbench.db` / `accounts.json`，于是 `read_local_file`（`risk=read`、不过确认门）
# 能**完整读出** cookie、平台 JWT 与含 Fernet 密钥的账号备份。
# 两张表并存 = 两个会漂的事实源，与 2026-09-15「两套工具表并存」同类。
#
# `tests/test_sensitive_paths.py` 断言两个模块拿到的是**同一个对象**
# （不是"内容相等"——那样下次还是可能只改一边）。
from ..config import SENSITIVE_PATH_PARTS, is_sensitive_path  # noqa: F401  再导出

__all__ = [
    "ENV_ALLOW_EXACT", "ENV_ALLOW_PREFIX", "POLICIES", "SENSITIVE_PATH_PARTS",
    "SandboxError", "SandboxPolicy", "Severity", "build_env", "describe_policies",
    "get_policy", "is_sensitive_path", "looks_like_secret", "within_root",
]


class SandboxError(RuntimeError):
    """沙箱拒绝执行（Fail-Closed：宁可不跑，也不放宽）。"""


#: 策略名允许的形状（ASCII，且不以 `-`/`_` 开头）。
#: **刻意不用 `str.isalnum()`**——它对中文返回 True，见 `validate()` 的注释。
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class Severity(int, Enum):
    """策略严格度。**数值越小越紧**——比较时用 `<`/`>` 不是直觉方向，故显式写出来。"""

    STRICT = 1   #: 最紧：只允许在沙箱根内跑，白名单环境，最短超时
    NORMAL = 2   #: 默认：允许在沙箱根内读写，白名单环境
    LOOSE = 3    #: 最松：允许读宿主的只读资源目录（仍不给凭据、仍不越出沙箱根）


#: ── 环境变量白名单 ────────────────────────────────────────────────────────────
#:
#: 为什么是白名单而不是黑名单：**黑名单永远漏**（`AWS_SECRET_ACCESS_KEY` / `GH_TOKEN` /
#: `WORKBENCH_*` / 将来的任何新变量），而本仓的凭据正好住在宿主的进程环境与 `%APPDATA%` 里。
#: 只放过"子进程没有它就起不来"的那些。
ENV_ALLOW_EXACT: frozenset[str] = frozenset({
    # POSIX：没有 PATH 就找不到解释器；LANG/LC_* 影响输出编码
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    # Windows：SystemRoot 缺失时 Win32 API 会大面积失败；PATHEXT/COMSPEC 是 cmd 解析所需
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
    "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
})

#: 允许的**前缀**（只放行明确无害的族）。
ENV_ALLOW_PREFIX: tuple[str, ...] = ("LC_",)

#: ── 敏感路径拒绝表 ────────────────────────────────────────────────────────────
#: 定义处是 `config.SENSITIVE_PATH_PARTS`（见文件顶部导入处的说明与本模块 `__all__`）。


@dataclass(frozen=True)
class SandboxPolicy:
    """一份具名沙箱策略。"""

    name: str
    severity: Severity
    #: 超时（秒）。**硬上限**，超时即杀整棵进程树。
    timeout_s: float
    #: 单个输出流最多保留多少字节（超出即**停止读取并显式标记被截断**，不是静默丢弃）。
    max_output_bytes: int = 256 * 1024
    #: 是否允许子进程再派生子进程。Windows 上由 Job Object 的进程数上限强制。
    allow_subprocesses: bool = False
    #: 声明：是否允许联网。**本期不强制**（强制需 WFP/防火墙，见 ADR-0014 §3）。
    allow_network: bool = False
    #: 额外允许注入的环境变量（**显式列出**；不得含凭据，`validate()` 会拒）。
    extra_env: tuple[str, ...] = ()
    #: 说明（给审计与管理页读）。
    description: str = ""

    @property
    def enforced(self) -> tuple[str, ...]:
        """**真正被代码强制**的项。其余字段只是声明——别读错。"""
        enforced = ["进程树整棵终止（超时/取消）", "环境变量白名单", "工作目录必须在沙箱根内",
                    "敏感路径拒绝", "输出字节上限", "执行审计留痕"]
        if not self.allow_subprocesses:
            enforced.append("子进程数上限 = 1（Job Object）")
        return tuple(enforced)

    def fingerprint(self) -> str:
        """策略指纹：写进审计，用来回答"这次执行当时用的是哪份策略"。"""
        payload = json.dumps({
            "name": self.name, "severity": int(self.severity), "timeout_s": self.timeout_s,
            "max_output_bytes": self.max_output_bytes, "allow_subprocesses": self.allow_subprocesses,
            "allow_network": self.allow_network, "extra_env": sorted(self.extra_env),
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def validate(self) -> None:
        """装配期自检。**不合格直接抛**——沙箱策略写错不能"尽力而为"。"""
        # ⚠️ 必须用 ASCII 正则，**不能用 `str.isalnum()`**：那个对中文返回 True
        # （实测 `"中文名".isalnum() is True`），于是"中文策略名"会通过校验。
        # 策略名会进 API 路径与审计，形状必须受限。
        if not _NAME_RE.fullmatch(self.name or ""):
            raise SandboxError(
                f"策略名不合法（只允许 ASCII 字母数字与 -_，且不以 -_ 开头）：{self.name!r}"
            )
        if self.timeout_s <= 0:
            raise SandboxError(f"策略 {self.name} 的 timeout_s 必须为正数，得到 {self.timeout_s}")
        if self.timeout_s > 3600:
            raise SandboxError(f"策略 {self.name} 的 timeout_s 超过 1 小时上限：{self.timeout_s}")
        if self.max_output_bytes <= 0:
            raise SandboxError(f"策略 {self.name} 的 max_output_bytes 必须为正数")
        for key in self.extra_env:
            if key.upper() in ENV_ALLOW_EXACT or key.upper().startswith(ENV_ALLOW_PREFIX):
                continue
            if looks_like_secret(key):
                raise SandboxError(
                    f"策略 {self.name} 试图注入疑似凭据的环境变量 {key!r}——"
                    "沙箱存在的首要理由就是**不把凭据喂给不受信代码**，请改名或去掉"
                )


def looks_like_secret(name: str) -> bool:
    """名字像凭据吗？保守判定：命中即拒（宁可误拒一个人畜无害的变量名）。"""
    upper = name.upper()
    return any(token in upper for token in
               ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "COOKIE", "AUTH"))


def build_env(policy: SandboxPolicy, base: dict[str, str] | None = None,
              extra: dict[str, str] | None = None) -> dict[str, str]:
    """按策略从宿主环境筛出子进程可见的环境变量。

    **这是本模块最重要的一行语义**：`proc.py` 当年写的是 `dict(os.environ)`（全量继承），
    于是子进程默认拿得到宿主的一切。这里反过来——**默认什么都不给，只给白名单**。

    `extra` 是**调用方（我们自己代码）**额外注入的固定值（如 `WORKBENCH_SKILL_ID`），
    叠加在白名单之后。它不绕过白名单——宿主环境仍然是被筛过的，
    `extra` 只补几个我们自己算出来的常量（`script_runner` 用它是为了让脚本知道
    "我是哪个 Skill、哪个工具、包在哪"，否则一个包里的多个脚本没法共享逻辑）。
    """
    src = os.environ if base is None else base
    allowed = {k: v for k, v in src.items()
               if k.upper() in ENV_ALLOW_EXACT or k.upper().startswith(ENV_ALLOW_PREFIX)}
    for key in policy.extra_env:
        for candidate in (key, key.upper(), key.lower()):
            if candidate in src:
                allowed[key] = src[candidate]
                break
    if extra:
        allowed.update({str(k): str(v) for k, v in extra.items()})
    return allowed


def within_root(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> bool:
    """`path` 是否在 `root` 之内（**解析后**比较，避免 `..` 与符号链接绕过）。

    用 `Path.resolve()` 而不是字符串前缀比较——字符串前缀会把
    `/sandbox/abc_evil` 判成在 `/sandbox/abc` 之内（本仓在 `routes_insight` 的产物路径守卫上
    踩过同类问题，见 `docs/CODEBASE_OVERVIEW.md` C2）。
    """
    try:
        target = Path(path).resolve()
        base = Path(root).resolve()
    except OSError:
        return False
    return target == base or base in target.parents


#: ── 具名策略集合（有限、可枚举、可测试）────────────────────────────────────────
POLICIES: dict[str, SandboxPolicy] = {
    "strict-readonly": SandboxPolicy(
        name="strict-readonly",
        severity=Severity.STRICT,
        timeout_s=30,
        allow_subprocesses=False,
        allow_network=False,
        description="只读短任务：解析、校验、格式转换。不给派生进程、不给网络、30 秒上限。",
    ),
    "skill-script": SandboxPolicy(
        name="skill-script",
        severity=Severity.NORMAL,
        timeout_s=120,
        allow_subprocesses=False,
        allow_network=False,
        description="Skill 脚本（本期首个消费方）：可在沙箱根内读写，不联网、不派生进程、2 分钟上限。",
    ),
    "mcp-stdio": SandboxPolicy(
        name="mcp-stdio",
        severity=Severity.NORMAL,
        timeout_s=300,
        allow_subprocesses=True,
        allow_network=True,
        description="MCP stdio 服务端（预留，本期不启用）：允许派生进程与联网，5 分钟上限。",
    ),
}

for _p in POLICIES.values():
    _p.validate()


def get_policy(name: str) -> SandboxPolicy:
    """按名取策略。未知名字 **Fail-Closed**（不是回落到默认策略）。"""
    policy = POLICIES.get(name)
    if policy is None:
        raise SandboxError(
            f"未知沙箱策略：{name!r}。可用：{', '.join(sorted(POLICIES))}。"
            "（不做「回落到默认」——策略名打错必须报错，否则等于悄悄放宽）"
        )
    return policy


def describe_policies() -> list[dict]:
    """给管理页/API 的形状。"""
    return [
        {
            "name": p.name,
            "severity": int(p.severity),
            "timeout_s": p.timeout_s,
            "max_output_bytes": p.max_output_bytes,
            "allow_subprocesses": p.allow_subprocesses,
            "allow_network": p.allow_network,
            "description": p.description,
            "fingerprint": p.fingerprint(),
            "enforced": list(p.enforced),
        }
        for p in sorted(POLICIES.values(), key=lambda x: (int(x.severity), x.name))
    ]
