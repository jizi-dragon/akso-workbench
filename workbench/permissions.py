"""工具权限策略 v1：`deny > ask > allow`，模式 `ask` / `auto`。

## 为什么需要它

`Risk` 分级回答的是"这个工具**能不能改状态**"，不是"**这个工作区**里允许它做什么"。
两者叠加才是用户想要的策略。典型场景：

- 只读工具默认放行，但某个工具（例如会外发 URL 的）在这个工作区里必须**每次问**；
- 写工具默认要确认，但用户明确信任某个工具，允许它在 `auto` 模式下不打断；
- 某个工具**永远不许**在这个工作区跑（`deny` 是硬阻断，`auto` 也绕不过）。

## 规则语法

    工具名                  → 匹配该工具的全部调用
    工具名(主体)             → 主体取参数里的 path / file_path / account_id / env_id / url
    支持 `*` 通配（`skill_*`、`accounts_*`、`path:*/secrets/*`）

优先级固定：**`deny` > `ask` > `allow` > 默认**。
默认：`READ` 放行；`WRITE` 在 `ask` 模式下要确认、`auto` 模式下放行。
`allow` 规则**不会**绕开已有的风险确认门——它只表示"策略层不再额外拦"。

## 为什么配置非法要 Fail-Closed

一条写错的规则（多一个括号）如果被静默忽略，用户以为"我禁用了它"，实际没禁。
所以 `load()` 遇到非法配置直接抛 `PermissionConfigError`，`ToolHub` 把异常
翻成 `deny` 并写审计——**宁可这次调用不动，也不要按用户没写成的策略跑**。
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .plugin_api import Risk, ToolSpec
from .services import storage

SETTING_KEY = "permissions"
MODES = ("ask", "auto")
MAX_RULES = 100
MAX_RULE_CHARS = 200

_SUBJECT_KEYS = ("path", "file_path", "account_id", "env_id", "url", "name")


class PermissionConfigError(ValueError):
    """权限配置无法解析/校验失败。调用方必须按 Fail-Closed 处理。"""


@dataclass(frozen=True)
class Rule:
    tool: str
    subject: str = ""
    text: str = ""

    def matches(self, tool: str, subject: str) -> bool:
        # Windows 路径/工具名大小写不敏感：deny 规则不能因为 `Secrets` 写成 `secrets`
        # 就被绕过（`os.path.normcase` 在 POSIX 上是恒等）。
        if not fnmatch.fnmatchcase(os.path.normcase(tool), os.path.normcase(self.tool)):
            return False
        if not self.subject:
            return True
        if not subject:
            return False
        return fnmatch.fnmatchcase(
            os.path.normcase(subject), os.path.normcase(self.subject))


@dataclass(frozen=True)
class Policy:
    mode: str = "ask"
    allow: tuple[Rule, ...] = ()
    ask: tuple[Rule, ...] = ()
    deny: tuple[Rule, ...] = ()

    def decide(self, spec: ToolSpec, args: BaseModel) -> tuple[str, str]:
        """返回 `(allow|ask|deny, 人话理由)`。"""
        subject = _subject(args)
        for rule in self.deny:
            if rule.matches(spec.name, subject):
                return "deny", f"deny 规则 {rule.text!r} 命中（硬阻断，auto 也不放行）"
        for rule in self.ask:
            if rule.matches(spec.name, subject):
                return "ask", f"ask 规则 {rule.text!r} 命中（必须先确认）"
        for rule in self.allow:
            if rule.matches(spec.name, subject):
                return "allow", f"allow 规则 {rule.text!r} 命中"
        if spec.risk is Risk.READ:
            return "allow", "默认：只读工具"
        if self.mode == "auto":
            return "allow", "默认：auto 模式放行写工具（仍受风险门约束）"
        return "ask", "默认：写工具需要确认"

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "allow": [rule.text for rule in self.allow],
            "ask": [rule.text for rule in self.ask],
            "deny": [rule.text for rule in self.deny],
            "precedence": "deny > ask > allow > 默认",
            "modes": list(MODES),
        }


def _subject(args: BaseModel | dict[str, Any]) -> str:
    data = args.model_dump() if isinstance(args, BaseModel) else dict(args or {})
    for key in _SUBJECT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_rule(text: str) -> Rule:
    raw = str(text or "").strip()
    if not raw:
        raise PermissionConfigError("权限规则不能为空")
    if len(raw) > MAX_RULE_CHARS:
        raise PermissionConfigError(f"权限规则过长（>{MAX_RULE_CHARS} 字符）：{raw[:40]}…")
    if "(" in raw:
        if not raw.endswith(")") or raw.count("(") != 1:
            raise PermissionConfigError(f"权限规则括号不配对：{raw!r}（正确形状：tool 或 tool(subject)）")
        tool, subject = raw[:-1].split("(", 1)
        tool, subject = tool.strip(), subject.strip()
        if not tool:
            raise PermissionConfigError(f"权限规则缺少工具名：{raw!r}")
        return Rule(tool=tool, subject=subject, text=raw)
    return Rule(tool=raw, text=raw)


def _rule_list(raw: Any, field: str) -> tuple[Rule, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PermissionConfigError(f"{field} 必须是字符串数组")
    if len(raw) > MAX_RULES:
        raise PermissionConfigError(f"{field} 规则过多（>{MAX_RULES} 条）")
    return tuple(parse_rule(item) for item in raw)


def _parse(raw: str) -> Policy:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PermissionConfigError(f"权限配置不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise PermissionConfigError("权限配置顶层必须是对象")
    mode = str(data.get("mode") or "ask")
    if mode not in MODES:
        raise PermissionConfigError(f"mode 只能是 {' / '.join(MODES)}，收到 {mode!r}")
    return Policy(
        mode=mode,
        allow=_rule_list(data.get("allow"), "allow"),
        ask=_rule_list(data.get("ask"), "ask"),
        deny=_rule_list(data.get("deny"), "deny"),
    )


def load() -> Policy:
    raw = storage.get_setting(SETTING_KEY) or ""
    if not raw:
        return Policy()
    return _parse(raw)


def save(*, mode: str | None = None, allow: list[str] | None = None,
         ask: list[str] | None = None, deny: list[str] | None = None) -> Policy:
    """合并式保存（`None` = 不动的字段），校验通过才落库。"""
    current = load()
    merged = current.to_json()
    if mode is not None:
        merged["mode"] = mode
    if allow is not None:
        merged["allow"] = allow
    if ask is not None:
        merged["ask"] = ask
    if deny is not None:
        merged["deny"] = deny
    # 先解析一遍，非法就抛在这里，不写库
    policy = _parse(json.dumps({key: merged[key] for key in ("mode", "allow", "ask", "deny")}))
    storage.set_setting(SETTING_KEY, json.dumps({
        "mode": policy.mode,
        "allow": [rule.text for rule in policy.allow],
        "ask": [rule.text for rule in policy.ask],
        "deny": [rule.text for rule in policy.deny],
    }, ensure_ascii=False))
    return policy


def decide(spec: ToolSpec, args: BaseModel) -> tuple[str, str]:
    """`ToolHub` 的权限门实现（注入，不在 `plugin_api` 里 import 本模块）。"""
    return load().decide(spec, args)


def settings_json() -> dict[str, Any]:
    try:
        return load().to_json()
    except PermissionConfigError as exc:
        return {"mode": "ask", "allow": [], "ask": [], "deny": [],
                "error": str(exc), "precedence": "deny > ask > allow > 默认"}
