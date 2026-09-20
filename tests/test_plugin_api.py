"""插件 SDK 验收：装配隔离 / 工具契约 / 风险确认门 / 审计 / 载体自检。

对应 `docs/PLUGIN-UPSTREAM-RESEARCH.md` 里 POC 实测过的三条断言
（插件被发现 / 坏插件被隔离 / 插件能力可达），加上本项目新增的两条：
**风险工具无令牌绝不执行**、**令牌一次性**。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel

from workbench import approvals, db
from workbench import plugin_api as pa

# ---------------------------------------------------------------- 夹具


@pytest.fixture()
def hub(monkeypatch):
    """干净的工具中枢：自带一个可观测的探针插件 + 确认门 + 审计。"""
    db.connect()
    approvals.reset_store()
    fresh = pa.ToolHub()
    approvals.install(fresh)          # 审计写入器 + 确认门
    monkeypatch.setattr(pa, "_hub", fresh)
    return fresh


class EchoArgs(BaseModel):
    text: str = pa.Arg("回显内容")
    fail: bool = pa.Arg("是否故意抛异常", default=False)
    sleepy: float = pa.Arg("故意睡多少秒", default=0.0)


CALLS: list[str] = []


async def echo_handler(args: EchoArgs, ctx: pa.ToolContext) -> str:
    CALLS.append(f"echo:{args.text}")
    if args.fail:
        raise RuntimeError("故意失败")
    if args.sleepy:
        await asyncio.sleep(args.sleepy)
    return json.dumps({"ok": True, "echo": args.text})


async def write_handler(args: EchoArgs, ctx: pa.ToolContext) -> str:
    CALLS.append(f"write:{args.text}")
    return "已写入（真的写的话）"


def probe_plugin(risk: pa.Risk = pa.Risk.READ, timeout_s: float = 5.0) -> pa.Plugin:
    return pa.Plugin(
        id="probe", title="探针插件",
        tools=[
            pa.ToolSpec(name="probe_echo", description="回显工具", args_model=EchoArgs,
                        handler=echo_handler, risk=pa.Risk.READ, timeout_s=timeout_s),
            pa.ToolSpec(name="probe_write", description="写工具（过确认门）", args_model=EchoArgs,
                        handler=write_handler, risk=risk, timeout_s=timeout_s),
        ],
    )


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 装配


def test_plugin_registers_and_is_discoverable(hub) -> None:
    hub.add_plugin(probe_plugin())
    names = [s.name for s in hub.specs()]
    assert names == ["probe_echo", "probe_write"]
    assert hub.get("probe_echo").plugin_id == "probe"


def test_bad_plugin_is_isolated_not_fatal(hub) -> None:
    """坏插件只记一条 failure，宿主（其余插件）照常可用。"""

    def broken(inner_hub: pa.ToolHub) -> None:
        raise RuntimeError("插件炸了")

    hub.load(broken)
    assert hub.failures and "插件炸了" in hub.failures[0]["error"]

    hub.add_plugin(probe_plugin())          # 坏插件之后仍能装配
    assert hub.get("probe_echo") is not None


def test_duplicate_plugin_id_rejected(hub) -> None:
    hub.add_plugin(probe_plugin())
    with pytest.raises(ValueError, match="插件 id 重复"):
        hub.add_plugin(probe_plugin())


def test_duplicate_tool_name_rejected(hub) -> None:
    """工具名是全局唯一键：撞名必须装配期就炸，不能静默覆盖。"""
    hub.add_plugin(probe_plugin())
    other = pa.Plugin(id="other", title="撞名插件", tools=[
        pa.ToolSpec(name="probe_echo", description="撞名", args_model=EchoArgs,
                    handler=echo_handler, risk=pa.Risk.READ),
    ])
    with pytest.raises(ValueError, match="已被插件"):
        hub.add_plugin(other)


def test_non_ascii_tool_name_rejected(hub) -> None:
    """function name 只接受 [A-Za-z0-9_-]；中文工具名会在请求侧被拒（本地看不出来）。"""
    bad = pa.Plugin(id="bad", title="中文名插件", tools=[
        pa.ToolSpec(name="洞察_运行", description="中文工具名", args_model=EchoArgs,
                    handler=echo_handler, risk=pa.Risk.READ),
    ])
    with pytest.raises(ValueError, match="工具名"):
        hub.add_plugin(bad)


def test_plugin_without_ids_is_rejected(hub) -> None:
    """plugin_id 不一致 / 缺 description 都必须在装配期暴露。"""
    mismatched = pa.Plugin(id="p1", title="t", tools=[
        pa.ToolSpec(name="p1_x", description="x", args_model=EchoArgs,
                    handler=echo_handler, plugin_id="p2"),
    ])
    with pytest.raises(ValueError, match="plugin_id 不一致"):
        hub.add_plugin(mismatched)


# ---------------------------------------------------------------- schema


def test_tool_api_schema_is_openai_shaped(hub) -> None:
    hub.add_plugin(probe_plugin())
    api = hub.get("probe_echo").to_api()
    assert api["type"] == "function"
    fn = api["function"]
    assert fn["name"] == "probe_echo"
    assert fn["parameters"]["type"] == "object"
    assert set(fn["parameters"]["properties"]) == {"text", "fail", "sleepy"}
    assert fn["parameters"]["required"] == ["text"]
    assert "title" not in fn["parameters"]
    for prop in fn["parameters"]["properties"].values():
        assert "title" not in prop


def test_zero_args_schema_is_object(hub) -> None:
    """无参工具也要是 object schema——不然 OpenAI 侧直接拒。"""
    hub.add_plugin(pa.Plugin(id="z", title="z", tools=[
        pa.ToolSpec(name="z_ping", description="ping", args_model=pa.ZERO_ARGS,
                    handler=echo_handler, risk=pa.Risk.READ),
    ]))
    params = hub.get("z_ping").to_api()["function"]["parameters"]
    assert params["type"] == "object"


# ---------------------------------------------------------------- 执行


def test_execute_ok(hub) -> None:
    hub.add_plugin(probe_plugin())
    inv = run(hub.execute("probe_echo", {"text": "hi"}))
    assert inv.status is pa.ToolStatus.OK
    assert not inv.is_error
    assert json.loads(inv.content)["echo"] == "hi"
    assert inv.audit_id


def test_bad_args_is_error_not_exception(hub) -> None:
    """参数错误必须是**可自我纠正的观测**，不能让循环炸掉。"""
    hub.add_plugin(probe_plugin())
    inv = run(hub.execute("probe_echo", {"nope": 1}))
    assert inv.status is pa.ToolStatus.BAD_ARGS
    assert "参数校验失败" in inv.content


def test_unknown_tool_lists_alternatives(hub) -> None:
    hub.add_plugin(probe_plugin())
    inv = run(hub.execute("nope", {}))
    assert inv.status is pa.ToolStatus.UNKNOWN_TOOL
    assert "probe_echo" in inv.content


def test_handler_exception_becomes_observation(hub) -> None:
    hub.add_plugin(probe_plugin())
    inv = run(hub.execute("probe_echo", {"text": "x", "fail": True}))
    assert inv.status is pa.ToolStatus.ERROR
    assert "RuntimeError" in inv.content


def test_timeout_is_bounded(hub) -> None:
    """工具不许无限期挂着：超时转 timeout 观测。"""
    hub.add_plugin(probe_plugin(timeout_s=0.05))
    inv = run(hub.execute("probe_echo", {"text": "x", "sleepy": 0.5}))
    assert inv.status is pa.ToolStatus.TIMEOUT


def test_long_result_is_truncated(hub) -> None:
    hub.add_plugin(pa.Plugin(id="big", title="big", tools=[
        pa.ToolSpec(name="big_dump", description="超长输出",
                    args_model=pa.ZERO_ARGS,
                    handler=lambda _a, _c: _long(), risk=pa.Risk.READ),
    ]))
    inv = run(hub.execute("big_dump", {}))
    assert len(inv.content) <= pa.MAX_RESULT_CHARS + 100
    assert "已截断" in inv.content


async def _long() -> str:
    return "x" * (pa.MAX_RESULT_CHARS * 2)


# ---------------------------------------------------------------- 风险确认门


def test_write_tool_blocked_without_token(hub) -> None:
    """核心安全属性：风险工具**没有令牌就绝不执行**（不是执行后警告）。"""
    CALLS.clear()
    hub.add_plugin(probe_plugin(risk=pa.Risk.WRITE))
    inv = run(hub.execute("probe_write", {"text": "危险"}))
    assert inv.status is pa.ToolStatus.NEEDS_APPROVAL
    assert inv.pending_id
    assert CALLS == [], "被拦下的工具绝不能被调用"


def test_dangerous_tool_needs_approval_then_runs_with_token(hub) -> None:
    CALLS.clear()
    hub.add_plugin(probe_plugin(risk=pa.Risk.DANGEROUS))
    first = run(hub.execute("probe_write", {"text": "建对象"}))
    assert first.status is pa.ToolStatus.NEEDS_APPROVAL
    assert "dangerous" in first.risk_reason

    token = approvals.pending_store().approve(first.pending_id).token
    second = run(hub.execute(
        "probe_write", {"text": "建对象"}, pa.ToolContext(approval_token=token),
    ))
    assert second.status is pa.ToolStatus.OK
    assert CALLS == ["write:建对象"]


def test_token_is_single_use(hub) -> None:
    """令牌一次性：批准一次不能重放多次（否则确认门形同虚设）。"""
    CALLS.clear()
    hub.add_plugin(probe_plugin(risk=pa.Risk.WRITE))
    first = run(hub.execute("probe_write", {"text": "a"}))
    token = approvals.pending_store().approve(first.pending_id).token

    assert run(hub.execute("probe_write", {"text": "a"},
                           pa.ToolContext(approval_token=token))).status is pa.ToolStatus.OK
    again = run(hub.execute("probe_write", {"text": "a"},
                            pa.ToolContext(approval_token=token)))
    assert again.status is pa.ToolStatus.NEEDS_APPROVAL
    assert CALLS == ["write:a"], "重放用的令牌不能真的再执行一次"


def test_reject_blocks_and_is_visible(hub) -> None:
    CALLS.clear()
    hub.add_plugin(probe_plugin(risk=pa.Risk.WRITE))
    first = run(hub.execute("probe_write", {"text": "b"}))
    item = approvals.pending_store().reject(first.pending_id, "现在别动")
    assert item.status == "rejected"
    assert "用户拒绝" in approvals.pending_store().get(first.pending_id).summary
    assert CALLS == []


def test_pending_request_is_reused_for_same_call(hub) -> None:
    """同一工具+同一参数的重复申请复用同一条，避免界面被刷屏。"""
    hub.add_plugin(probe_plugin(risk=pa.Risk.WRITE))
    a = run(hub.execute("probe_write", {"text": "same"}))
    b = run(hub.execute("probe_write", {"text": "same"}))
    assert a.pending_id == b.pending_id
    c = run(hub.execute("probe_write", {"text": "other"}))
    assert c.pending_id != a.pending_id


def test_read_tool_never_asks(hub) -> None:
    hub.add_plugin(probe_plugin(risk=pa.Risk.READ))
    inv = run(hub.execute("probe_echo", {"text": "ok"}))
    assert inv.status is pa.ToolStatus.OK
    assert approvals.pending_store().list("pending") == []


# ---------------------------------------------------------------- 审计


def test_audit_row_written_with_risk_and_plugin(hub) -> None:
    hub.add_plugin(probe_plugin(risk=pa.Risk.READ))
    inv = run(hub.execute("probe_echo", {"text": "audited"}))
    row = db.query_one("SELECT * FROM agent_audit WHERE id = ?", (inv.audit_id,))
    assert row is not None
    assert row["tool"] == "probe_echo"
    assert row["plugin"] == "probe"
    assert row["risk"] == "read"
    assert row["status"] == "ok"


def test_audit_never_stores_plaintext_credentials(hub) -> None:
    """凭据不落明文：审计里出现口令/令牌键必须被打码。"""
    hub.add_plugin(pa.Plugin(id="sec", title="sec", tools=[
        pa.ToolSpec(name="sec_login", description="带口令的工具", args_model=_SecretArgs,
                    handler=echo_handler, risk=pa.Risk.READ,
                    audit_extra=pa.audit_env_key("username", "password", "token")),
    ]))
    inv = run(hub.execute("sec_login", {"username": "u", "password": "p@ss", "token": "abc"}))
    row = db.query_one("SELECT * FROM agent_audit WHERE id = ?", (inv.audit_id,))
    assert "p@ss" not in row["args"]
    assert "abc" not in row["args"]
    assert "u" in row["args"]


class _SecretArgs(BaseModel):
    username: str = pa.Arg("用户名")
    password: str = pa.Arg("口令")
    token: str = pa.Arg("令牌")


# ---------------------------------------------------------------- 自检


def test_verify_required_tools_reports_missing(hub) -> None:
    hub.add_plugin(probe_plugin())
    missing = pa.verify_required_tools(hub, ("probe_echo", "probe_write", "not_there"))
    assert missing == ["not_there"]
    assert pa.verify_required_tools(hub, ("probe_echo",)) == []


# ---------------------------------------------------------------- 真实装配


def test_real_hub_has_all_plugins_and_no_failures() -> None:
    """真实中枢（builtin + 联网检索 + 三大业务）必须装配干净，且必需工具齐。"""
    from workbench.api.routes_agent import REQUIRED_TOOLS

    hub = pa.get_hub()
    assert {p.id for p in hub.plugins} >= {
        "builtin", "websearch", "insight", "factory", "accounts",
    }
    assert hub.failures == []
    assert pa.verify_required_tools(hub, REQUIRED_TOOLS) == []


def test_plugin_manifest_matches_packaging_manifest() -> None:
    """插件清单必须全部进 server.spec 的 hiddenimports。

    为什么值得一条测试：PyInstaller 看不到 `importlib.import_module` 动态导入的插件，
    而加载器把"没打进去"与"加载失败"都表现成同一条 warning（刻意的隔离设计），
    于是漏收 = 安装包里静默少几个工具。`tools/verify_packaging.py` 的 P7 在构建前拦一道，
    这里在测试期再拦一道——**P7 实测已经拦下过新加的 `websearch` 插件漏登记**。
    """
    from pathlib import Path

    from workbench.plugins import _MODULES

    spec = (Path(__file__).resolve().parent.parent / "workbench" / "server.spec").read_text("utf-8")
    undeclared = [m for m in _MODULES if f'"workbench.plugins.{m}"' not in spec]
    assert not undeclared, f"这些插件没进 server.spec 的 hiddenimports：{undeclared}"


def test_web_search_tool_is_registered() -> None:
    """联网检索必须是 read 级（它只读网页，不该让用户点确认）。"""
    spec = pa.get_hub().get("web_search")
    assert spec is not None, "web_search 没装 —— DeepSeek API 不支持内置联网，这是唯一通路"
    assert spec.risk is pa.Risk.READ
    assert spec.plugin_id == "websearch"


def test_real_hub_risk_grading() -> None:
    """分级是安全属性：写平台/开窗口的必须是 write 以上，只读的必须是 read。"""
    hub = pa.get_hub()
    expected = {
        "insight_read_config": pa.Risk.READ,
        "insight_run": pa.Risk.READ,
        "factory_blueprint_spec": pa.Risk.READ,
        # 预览会**写**运行时目录，但仍然是 read：只写自有工作区、可整体删除、
        # 不碰平台与凭据（与 insight_run 同级）。真正的闸门是 create 的 dangerous 门。
        # 给它加确认门会把"确认"用在暂存上，训练用户习惯性点同意。
        "factory_preview_blueprint": pa.Risk.READ,
        "factory_create": pa.Risk.DANGEROUS,
        "accounts_login": pa.Risk.READ,
        "accounts_browser_open": pa.Risk.WRITE,
        "accounts_browser_close": pa.Risk.WRITE,
    }
    for name, risk in expected.items():
        spec = hub.get(name)
        assert spec is not None, f"工具缺失：{name}"
        assert spec.risk is risk, f"{name} 的风险分级应为 {risk}，实为 {spec.risk}"


def test_real_hub_schema_budget() -> None:
    """工具 schema 是真实的 token 成本：给个预算上限，涨上去要有人知道。"""
    hub = pa.get_hub()
    payload = json.dumps(hub.list_api_for(None), ensure_ascii=False)
    assert len(hub.specs()) <= 24, f"工具数 {len(hub.specs())} 已超预算，考虑拆分插件白名单"
    assert len(payload) < 24_000, f"工具 schema 体积 {len(payload)} 字符，偏大"


def test_plugin_whitelist_filters_tools() -> None:
    hub = pa.get_hub()
    only_factory = {t["function"]["name"] for t in hub.list_api_for(["factory"])}
    assert only_factory and all(n.startswith("factory_") for n in only_factory)
    assert "insight_run" not in only_factory
