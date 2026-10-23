"""权限策略 v1：`deny > ask > allow`，模式 `ask` / `auto`，配置非法 Fail-Closed。

## 为什么需要这份测试

`Risk` 只回答"能不能改状态"，不回答"这个工作区允不允许"。没有策略层时，
用户只能用"卸载工具"表达"这个工具别用"——而卸载是永久的、且模型会看到工具消失。
策略层要买的正是：**同一个工具，在不同工作区/不同模式下有不同边界**。

反证重点：
- `deny` 必须压过 `allow`（否则一条宽 allow 就能把硬阻断架空）；
- 策略配置写错（少一个括号）时**不能静默忽略**，必须拒绝执行。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from workbench import db, permissions
from workbench import plugin_api as pa
from workbench.services import storage


class _Args(BaseModel):
    path: str = ""
    account_id: str = ""


CALLS: list[str] = []


async def _handler(_args: _Args, _ctx: pa.ToolContext) -> str:
    CALLS.append("ran")
    return "ok"


def _spec(*, risk: pa.Risk = pa.Risk.READ) -> pa.ToolSpec:
    return pa.ToolSpec(
        name="probe_read",
        description="探针",
        args_model=_Args,
        handler=_handler,
        risk=risk,
        trust=pa.Trust.LOCAL,
        reversibility=None if risk is pa.Risk.READ else pa.Reversibility.IRREVERSIBLE,
    )


def _hub() -> pa.ToolHub:
    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[_spec()]))
    hub.set_permission_gate(permissions.decide)
    return hub


@pytest.fixture(autouse=True)
def _ready():
    db.connect()
    storage.set_setting(permissions.SETTING_KEY, "")
    CALLS.clear()
    yield
    storage.set_setting(permissions.SETTING_KEY, "")


def test_deny_beats_allow_and_blocks_before_the_handler() -> None:
    permissions.save(allow=["probe_read"], deny=["probe_read"])
    inv = asyncio.run(_hub().execute("probe_read", {"path": "x"}))
    assert inv.status is pa.ToolStatus.REJECTED
    assert "deny" in (inv.error or "")
    assert CALLS == [], "deny 规则命中了，handler 仍然执行 —— 硬阻断没生效"


def test_ask_on_a_read_tool_needs_a_token_and_fails_closed_without_a_gate() -> None:
    permissions.save(ask=["probe_read"])
    inv = asyncio.run(_hub().execute("probe_read", {"path": "x"}))
    assert inv.status is pa.ToolStatus.REJECTED
    assert CALLS == []

    hub = _hub()
    hub.set_approval_gate(lambda *_a, **_k: (False, "p-1", "权限策略要求确认"))
    inv2 = asyncio.run(hub.execute("probe_read", {"path": "x"}))
    assert inv2.status is pa.ToolStatus.NEEDS_APPROVAL
    assert inv2.pending_id == "p-1"
    assert CALLS == []


def test_allow_does_not_bypass_the_risk_gate() -> None:
    """显式 allow 只表示"策略层不额外拦"，写工具仍要过风险门。"""
    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[_spec(risk=pa.Risk.WRITE)]))
    hub.set_permission_gate(permissions.decide)
    permissions.save(allow=["probe_read"])
    inv = asyncio.run(hub.execute("probe_read", {"path": "x"}))
    assert inv.status is pa.ToolStatus.REJECTED, "写工具绕过了风险门"
    assert CALLS == []


def test_auto_mode_allows_writers_but_deny_still_wins() -> None:
    hub = pa.ToolHub()
    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[_spec(risk=pa.Risk.WRITE)]))
    hub.set_permission_gate(permissions.decide)
    hub.set_approval_gate(lambda *_a, **_k: (True, "", ""))
    permissions.save(mode="auto")
    inv = asyncio.run(hub.execute("probe_read", {"path": "x"}))
    assert inv.status is pa.ToolStatus.OK
    assert CALLS == ["ran"]

    CALLS.clear()
    permissions.save(mode="auto", deny=["probe_read"])
    inv2 = asyncio.run(hub.execute("probe_read", {"path": "x"}))
    assert inv2.status is pa.ToolStatus.REJECTED
    assert CALLS == []


def test_subject_rules_match_the_argument() -> None:
    permissions.save(deny=["probe_read(*/secrets/*)"])
    pol = permissions.load()
    spec = _spec()
    assert pol.decide(spec, _Args(path="C:/work/secrets/key.txt"))[0] == "deny"
    assert pol.decide(spec, _Args(path="C:/work/src/app.py"))[0] == "allow"


def test_invalid_rule_is_rejected_at_save_time() -> None:
    with pytest.raises(permissions.PermissionConfigError, match="括号"):
        permissions.save(ask=["probe_read(path:*/x"])


def test_invalid_stored_config_fails_closed() -> None:
    storage.set_setting(permissions.SETTING_KEY, '{"mode": "ask", "deny": [')
    inv = asyncio.run(_hub().execute("probe_read", {"path": "x"}))
    assert inv.status is pa.ToolStatus.REJECTED
    assert "fail-closed" in (inv.error or "")
    assert CALLS == []


def test_permissions_endpoint_returns_policy_and_rejects_bad_rules() -> None:
    """HTTP 面：合法更新返回新策略；非法规则 400（不静默忽略）。"""
    from fastapi import HTTPException

    from workbench.api import routes_settings

    body = routes_settings.PermissionsBody(mode="auto", deny=["probe_read"])
    out = routes_settings.put_permissions(body)
    assert out["mode"] == "auto"
    assert out["deny"] == ["probe_read"]

    with pytest.raises(HTTPException) as ei:
        routes_settings.put_permissions(
            routes_settings.PermissionsBody(ask=["probe_read(path"]))
    assert ei.value.status_code == 400
