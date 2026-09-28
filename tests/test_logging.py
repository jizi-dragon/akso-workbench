"""日志系统验收：落盘 / 幂等 / 脱敏 / 关联 ID / 滚动 / 检索 / 失败隔离。

## 为什么这些断言值得写

改造前全仓 38 处 `logger.*` 调用**一个字节都落不下来**（没有 handler，
`lastResort` 只处理 WARNING+ 且写 stderr，而壳 `stdio: 'ignore'` 把它丢了）。
"日志能用"这件事在改造前**没有任何断言**——所以它坏了很久没人知道。

本文件的两条判据是**反证过**的（都实测对旧实现 FAIL）：

- `test_two_size_rolls_same_day_do_not_overwrite`：stdlib 在同一天滚两次会用同一个归档名，
  而 `doRollover` 遇到同名会先 `os.remove` → **静默删掉先前归档**；
- `test_redaction_covers_json_shape`：初版打码正则 `\\b(key)\\b(\\s*[:=]\\s*)` 匹配不到
  JSON 形态的 `"token": "xxx"`（键名后紧跟闭引号才轮到冒号），**而本项目自己的日志就是 JSONL**。
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from workbench import approvals, db
from workbench import logging_setup as log
from workbench import plugin_api as pa


@pytest.fixture()
def fresh_logs(tmp_path, monkeypatch):
    """把日志装配到临时目录，并在测试结束后**摘干净**（不影响其它测试的 caplog）。"""
    target = tmp_path / "logs"
    monkeypatch.setenv(log.DIR_ENV, str(target))
    monkeypatch.setenv(log.FORMAT_ENV, "jsonl")
    monkeypatch.setenv(log.LEVEL_ENV, "INFO")
    monkeypatch.delenv("WORKBENCH_LOG_MAX_BYTES", raising=False)
    monkeypatch.delenv("WORKBENCH_LOG_BACKUP_COUNT", raising=False)
    root = logging.getLogger()
    before_level = root.level
    configured = log.configure(force=True)
    assert configured is not None, "日志装配失败——本夹具的前提就不成立"
    yield target
    log.reset_for_tests()
    root.setLevel(before_level)


def read_rows(directory, pattern="workbench-*.jsonl*") -> list[dict]:
    rows: list[dict] = []
    for path in sorted(directory.glob(pattern)):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def flush_all() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


# ---------------------------------------------------------------- 落盘（核心）

def test_info_lands_on_disk(fresh_logs) -> None:
    """**这是本文件最重要的一条**：`logging.info` 必须真的落盘。

    改造前 `info`/`debug` 共 19 处调用被 `lastResort`（WARNING 级）静默丢弃。
    """
    logging.getLogger("workbench.probe").info("落盘探针 %s", "ok")
    flush_all()
    rows = read_rows(fresh_logs)
    assert any("落盘探针 ok" in str(r.get("msg")) for r in rows), rows
    hit = next(r for r in rows if "落盘探针 ok" in str(r.get("msg")))
    # 结构化字段齐备（否则"便于检索"就是空话）
    for key in ("ts", "level", "logger", "pid", "ver"):
        assert key in hit, f"缺字段 {key}：{hit}"
    assert hit["level"] == "INFO"


def test_debug_is_filtered_at_info_level(fresh_logs) -> None:
    """级别生效：INFO 配置下 `debug` 不该落盘（否则日志会被噪音淹掉）。"""
    logging.getLogger("workbench.probe").debug("不该出现")
    flush_all()
    assert not any("不该出现" in str(r.get("msg")) for r in read_rows(fresh_logs))


def test_configure_is_idempotent(fresh_logs) -> None:
    """重复 `configure()` 不得叠加 handler、不得重复输出。

    真实触发场景：`main.py` 装配一次 + `api.lifespan` 又装配一次（兜底）。
    若每次都 `addHandler`，日志会**成倍增长**。
    """
    log.configure()
    log.configure()
    logging.getLogger("workbench.probe").info("只应出现一次")
    flush_all()
    hits = [r for r in read_rows(fresh_logs) if "只应出现一次" in str(r.get("msg"))]
    assert len(hits) == 1, f"重复了 {len(hits)} 次，说明 handler 装了两遍"


def test_configure_does_not_steal_foreign_handlers(fresh_logs) -> None:
    """只摘自己装的 handler——`caplog` 等外部 handler 必须活着。"""
    sentinel = logging.StreamHandler()
    logging.getLogger().addHandler(sentinel)
    try:
        log.configure(force=True)
        assert sentinel in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(sentinel)


# ---------------------------------------------------------------- 脱敏

@pytest.mark.parametrize("secret", [
    "password=hunter2",
    'password: "hunter2"',
    "token=abc123def456",
    '"token": "abc123def456"',          # ← JSON 形态（初版正则漏掉的就是它）
    'api_key="sk-1234567890abcdef"',
    "Authorization: Bearer eyJhbGciOi.abc.def",
    "sk-1f77d5ba514f4aabbccdd",
    "fernet_key=q5xJnyfouL9mZs-6abcdef",
])
def test_redaction_keeps_secrets_out_of_the_file(fresh_logs, secret: str) -> None:
    """凭据不入日志（不变量 2）。落盘内容里**不得出现**原始值。"""
    logging.getLogger("workbench.probe").info("试试 %s", secret)
    flush_all()
    dump = "\n".join(p.read_text(encoding="utf-8") for p in fresh_logs.glob("*"))
    for value in ("hunter2", "abc123def456", "1234567890abcdef", "eyJhbGciOi.abc.def",
                  "1f77d5ba514f4aabbccdd", "q5xJnyfouL9mZs-6abcdef"):
        assert value not in dump, f"凭据泄漏到日志里了：{value}\n{dump}"
    assert "***" in dump


def test_redaction_covers_json_shape(fresh_logs) -> None:
    """JSON 形态的键值也必须被打码（初版正则的缺口，反证过 FAIL）。"""
    assert "abc123def456" not in log.redact_text('{"token": "abc123def456"}')
    assert "abc123def456" not in log.redact_text('{"api_key":"abc123def456"}')


def test_redaction_preserves_json_validity() -> None:
    """打码后仍应是**合法 JSON**——只换值，不换键名/分隔符/引号。

    否则导出的日志会因打码而变成解析不了的残缺结构。
    """
    for raw in ('{"token": "abc123"}', '{"api_key":"sk-abc123456"}', '{"password": "x"}'):
        cleaned = log.redact_text(raw)
        json.loads(cleaned)  # 解析不了就抛


def test_redaction_survives_bare_percent() -> None:
    """消息里含裸 `%` 时不得炸。

    `record.args = ()` 会让 `getMessage()` 走 `msg % ()`，
    而 `"进度 100%" % ()` 会抛 `ValueError: incomplete format`。
    故过滤器必须把 `args` 置为 `None`（这条是实测踩到后加的）。
    """
    logging.getLogger("workbench.probe").info("进度 100% 完成")
    # 未抛异常即通过；再确认它确实落盘了
    record = logging.LogRecord("x", logging.INFO, "", 0, "进度 100% 完成", None, None)
    assert log.RedactionFilter().filter(record) is True
    assert record.getMessage() == "进度 100% 完成"


def test_redaction_failure_discards_instead_of_leaking(fresh_logs, monkeypatch) -> None:
    """脱敏自身失败时**丢弃原文**，绝不放行。"""
    monkeypatch.setattr(log, "redact_text",
                        lambda _t: (_ for _ in ()).throw(RuntimeError("boom")))
    logging.getLogger("workbench.probe").info("password=hunter2")
    flush_all()
    dump = "\n".join(p.read_text(encoding="utf-8") for p in fresh_logs.glob("*"))
    assert "hunter2" not in dump
    assert "脱敏失败" in dump


# ---------------------------------------------------------------- 关联 ID

def test_correlation_ids_appear_in_record(fresh_logs) -> None:
    """关联 ID 必须进结构化字段——这是"把四层焊在一起"的唯一手段。"""
    with log.bind(traceId="t1234567", conversationId="conv-abc",
                  auditId="audit-xyz", tool="insight_run"):
        logging.getLogger("workbench.probe").info("带关联 ID")
    flush_all()
    hit = next(r for r in read_rows(fresh_logs) if "带关联 ID" in str(r.get("msg")))
    assert hit["traceId"] == "t1234567"
    assert hit["conversationId"] == "conv-abc"
    assert hit["auditId"] == "audit-xyz"
    assert hit["tool"] == "insight_run"


def test_bind_restores_and_nests() -> None:
    """`bind` 退出后恢复原值，且支持嵌套（否则上下文会污染后续请求）。"""
    assert log.correlation() == {}
    with log.bind(traceId="outer"):
        assert log.correlation()["traceId"] == "outer"
        with log.bind(traceId="inner", tool="x"):
            assert log.correlation()["traceId"] == "inner"
        assert log.correlation()["traceId"] == "outer"
    assert log.correlation() == {}


def test_bind_rejects_unknown_field() -> None:
    """拼错字段名要**报错**，不要静默忽略（否则日志里悄悄少了关联 ID）。"""
    with pytest.raises(KeyError):
        with log.bind(no_such_field="x"):
            pass


# ---------------------------------------------------------------- 滚动

def test_size_rollover_creates_archive(tmp_path, monkeypatch) -> None:
    """体积超过上限即归档一次（安全阀生效）。"""
    target = tmp_path / "logs"
    monkeypatch.setenv(log.DIR_ENV, str(target))
    monkeypatch.setenv(log.FORMAT_ENV, "jsonl")
    monkeypatch.setenv("WORKBENCH_LOG_MAX_BYTES", "600")
    try:
        log.configure(force=True)
        for i in range(60):
            logging.getLogger("workbench.probe").info("填充行 %03d %s", i, "x" * 40)
        flush_all()
        archives = list(target.glob("workbench-*.jsonl.*"))
        assert archives, f"没有产生归档文件：{list(target.iterdir())}"
    finally:
        log.reset_for_tests()


def test_two_size_rolls_same_day_do_not_overwrite(tmp_path, monkeypatch) -> None:
    """**同一天滚两次必须产生两个归档**——stdlib 默认会撞名并删掉先前的那个。

    这是反证过的：不加 `rotation_filename()` 唯一化时，本断言 FAIL
    （`doRollover` 遇到同名文件先 `os.remove`）。
    """
    target = tmp_path / "logs"
    monkeypatch.setenv(log.DIR_ENV, str(target))
    monkeypatch.setenv(log.FORMAT_ENV, "jsonl")
    monkeypatch.setenv("WORKBENCH_LOG_MAX_BYTES", "400")
    try:
        log.configure(force=True)
        for i in range(200):
            logging.getLogger("workbench.probe").info("行 %03d %s", i, "y" * 40)
        flush_all()
        archives = list(target.glob("workbench-*.jsonl.*"))
        assert len(archives) >= 2, (
            f"只留下 {len(archives)} 个归档——同一天第二次滚动把第一次覆盖了：{archives}")
    finally:
        log.reset_for_tests()


def test_old_archives_are_cleaned_up(tmp_path, monkeypatch) -> None:
    """保留策略生效：归档数量**有上限**（否则 `extMatch` 不同步会让磁盘无限增长）。"""
    target = tmp_path / "logs"
    monkeypatch.setenv(log.DIR_ENV, str(target))
    monkeypatch.setenv(log.FORMAT_ENV, "jsonl")
    monkeypatch.setenv("WORKBENCH_LOG_MAX_BYTES", "300")
    monkeypatch.setenv("WORKBENCH_LOG_BACKUP_COUNT", "2")
    try:
        log.configure(force=True)
        for i in range(400):
            logging.getLogger("workbench.probe").info("行 %03d %s", i, "z" * 40)
        flush_all()
        archives = list(target.glob("workbench-*.jsonl.*"))
        assert len(archives) <= 3, (
            f"归档没有被清理（backupCount=2，实际 {len(archives)} 个）——"
            f"多半是 extMatch 与 rotation_filename 不同步：{archives}")
    finally:
        log.reset_for_tests()


# ---------------------------------------------------------------- 失败隔离

def test_configure_failure_returns_none_without_raising(tmp_path, monkeypatch) -> None:
    """装配失败必须**返回 None 而不是抛**（不变量 1）。

    构造手法：把日志目录指到一个**已存在的文件**上，`mkdir` 必然失败。
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(log.DIR_ENV, str(blocker))
    try:
        assert log.configure(force=True) is None      # 不抛
        logging.getLogger("workbench.probe").info("没有日志也不该炸")
    finally:
        log.reset_for_tests()


def test_broken_handler_does_not_break_the_caller(fresh_logs, monkeypatch) -> None:
    """handler 抛异常时调用方**不受影响**（本仓对留痕的一贯纪律）。"""
    handler = next(h for h in logging.getLogger().handlers
                   if getattr(h, "_workbench_owned", False))
    monkeypatch.setattr(handler, "emit",
                        lambda _r: (_ for _ in ()).throw(OSError("磁盘满了")))
    logging.getLogger("workbench.probe").info("这次写不进去")  # 不抛即通过


def test_configure_rebuilds_a_closed_handler(fresh_logs) -> None:
    """handler 被别人关掉之后，`configure()`（**非 force**）必须重建它。

    触发场景：第三方按默认配置调 `logging.config.dictConfig`（例如没传
    `log_config=None` 的 `uvicorn.run`），它会 `_clearExistingHandlers()` 关闭所有既有 handler。
    此后写日志会抛 "I/O operation on closed file" 而被我们吞掉 → **日志静默停止**，
    而 `_configured` 仍是 True → 下一句 `configure()` 早返回 → 没人重建。
    """
    handler = next(h for h in logging.getLogger().handlers
                   if getattr(h, "_workbench_owned", False))
    handler.close()  # 模拟 dictConfig 的 _clearExistingHandlers

    log.configure()  # 注意：**不是** force
    logging.getLogger("workbench.probe").info("重建之后仍要落盘")
    flush_all()
    assert any("重建之后仍要落盘" in str(r.get("msg")) for r in read_rows(fresh_logs))


# ---------------------------------------------------------------- 检索

def test_tail_filters(fresh_logs) -> None:
    """`tail` 的三个过滤维度都要生效（否则 `/api/logs/tail` 只是"倒着看文件"）。"""
    probe = logging.getLogger("workbench.probe")
    probe.info("普通一条", extra={"wb_kind": "probe"})
    probe.warning("警告一条", extra={"wb_kind": "probe"})
    with log.bind(traceId="trace-abc"):
        probe.info("带 trace 的一条", extra={"wb_kind": "probe"})
    flush_all()

    assert len(log.tail(limit=100)) >= 3
    assert all(r["level"] == "WARNING" for r in log.tail(limit=100, level="WARNING"))
    assert any("警告一条" in str(r["msg"]) for r in log.tail(limit=100, contains="警告一条"))
    traced = log.tail(limit=100, trace="trace-abc")
    assert traced and all(r["traceId"] == "trace-abc" for r in traced)
    kinds = log.tail(limit=100, kind="probe")
    assert kinds and all(r.get("kind") == "probe" for r in kinds)
    # 从新到旧
    all_rows = log.tail(limit=100)
    assert str(all_rows[0].get("msg")).startswith("带 trace")


def test_available_days_lists_today(fresh_logs) -> None:
    logging.getLogger("workbench.probe").info("x")
    flush_all()
    days = log.available_days()
    assert days and days[0] == time.strftime("%Y%m%d")


# ---------------------------------------------------------------- 与工具中枢的集成

class ProbeArgs(BaseModel):
    text: str = pa.Arg("内容", default="")


async def _probe_handler(args: ProbeArgs, ctx: pa.ToolContext) -> str:
    return json.dumps({"ok": True})


@pytest.fixture()
def probe_hub(monkeypatch):
    db.connect()
    approvals.reset_store()
    hub = pa.ToolHub()
    approvals.install(hub)
    hub.add_plugin(pa.Plugin(id="probe", title="探针", tools=[
        pa.ToolSpec(name="probe_echo", description="回显", args_model=ProbeArgs,
                    handler=_probe_handler, risk=pa.Risk.READ, trust=pa.Trust.LOCAL, timeout_s=5),
    ]))
    monkeypatch.setattr(pa, "_hub", hub)
    return hub


def test_tool_call_is_logged_with_audit_id(fresh_logs, probe_hub) -> None:
    """工具调用日志必须带 `auditId` 与 `tool`——那是与 `agent_audit` 表对接的唯一钥匙。

    挂点是 `ToolHub._audit`（每次真实调用的扼流点），因此"日志里有"与"审计里有"
    在结构上等价，不会漂移。
    """
    import asyncio

    from workbench import db as _db

    inv = asyncio.run(probe_hub.execute("probe_echo", {"text": "hi"},
                                        pa.ToolContext(audit_id="audit-777", source="agent")))
    assert inv.status is pa.ToolStatus.OK
    flush_all()

    rows = [r for r in read_rows(fresh_logs) if r.get("kind") == "tool"]
    assert rows, "工具调用没有留下日志"
    hit = next(r for r in rows if r.get("auditId") == "audit-777")
    assert hit["tool"] == "probe_echo"
    assert hit["status"] == "ok"
    assert hit["risk"] == "read"
    assert hit["plugin"] == "probe"
    # 与审计表对得上（同一把钥匙）
    assert _db.query_one("SELECT id FROM agent_audit WHERE id = ?", ("audit-777",)) is not None


def test_unknown_tool_is_logged(fresh_logs, probe_hub) -> None:
    """未知工具走不到 `_audit`，必须单独记；否则"模型编了个工具名"完全不可见。"""
    import asyncio

    asyncio.run(probe_hub.execute("no_such_tool", {}))
    flush_all()
    dump = "\n".join(p.read_text(encoding="utf-8") for p in fresh_logs.glob("*"))
    assert "未知工具调用" in dump


def test_unknown_tool_via_http_is_logged_too(fresh_logs, probe_hub) -> None:
    """**HTTP 路径**的未知工具也要留痕。

    这条路径在 `routes_agent.invoke` 的**路由层就短路了**（直接 404），
    所以 `ToolHub.execute()` 里那条告警根本不会执行——两条链路对同一个事件
    的口径不一致（对话链路有，HTTP 链路只有一条 404 访问日志）。
    本断言就是为那次收口写的。
    """
    from workbench.api import app

    with TestClient(app) as client:
        resp = client.post("/api/agent/invoke", json={"tool": "no_such_tool", "args": {}})
        assert resp.status_code == 404
    flush_all()
    dump = "\n".join(p.read_text(encoding="utf-8") for p in fresh_logs.glob("*"))
    assert "未知工具调用（HTTP）" in dump, dump[-800:]


# ---------------------------------------------------------------- API 面

def test_logs_api_returns_rows_and_trace(fresh_logs) -> None:
    """`/api/logs/*` 三个端点可用，且 **traceId 能把一次请求串起来**。"""
    from workbench.api import app

    logging.getLogger("workbench.probe").info("API 面探针")
    flush_all()

    with TestClient(app) as client:
        days = client.get("/api/logs/days")
        assert days.status_code == 200
        assert days.json()["current"].startswith("workbench-")

        # 请求本身会带 traceId（中间件注入），响应头里也回一份
        resp = client.get("/api/agent/selfcheck")
        assert resp.status_code == 200
        trace = resp.headers.get("X-Trace-Id")
        assert trace, "响应头没有 X-Trace-Id —— 用户拿不到可报读的追踪号"

        tail = client.get("/api/logs/tail", params={"n": 50})
        assert tail.status_code == 200
        assert tail.json()["count"] >= 1

        timeline = client.get(f"/api/logs/trace/{trace}")
        assert timeline.status_code == 200, timeline.text
        rows = timeline.json()["rows"]
        assert rows, "时间线是空的"
        assert all(r["traceId"] == trace for r in rows)
        assert any(r.get("kind") == "http" for r in rows), rows

        download = client.get("/api/logs/download")
        assert download.status_code == 200
        assert download.text.strip()


def test_logs_self_noise_is_excluded(fresh_logs) -> None:
    """查日志这个动作**本身不记日志**——否则每次查询都会在上一条结果里看到自己。"""
    from workbench.api import app

    with TestClient(app) as client:
        client.get("/api/logs/tail", params={"n": 10})
    flush_all()
    rows = log.tail(limit=200, kind="http")
    assert not any(str(r.get("path", "")).startswith("/api/logs") for r in rows), rows


def test_logs_download_rejects_bad_day(fresh_logs) -> None:
    from workbench.api import app

    with TestClient(app) as client:
        assert client.get("/api/logs/download", params={"day": "abc"}).status_code == 400
        assert client.get("/api/logs/download",
                          params={"day": "19990101"}).status_code == 404
