"""对象分析 SSE 的**终态落库**判据（2026-10-19 补）。

## 它守的是什么

`POST /api/insight/run-sse` 曾经只在生成器**末尾**写 `finish_job`。客户端一断
（刷新 / 关页 / 网络断），`yield` 处收到 `GeneratorExit`，末尾不执行 ⇒
`insight_runs` 里那一行**永远是 `running`**。

对照 `jobs.run_sync()`：它有 `try/except`。两条链路必须是同一个语义 ——
否则"页面跑的"与"工具跑的"会产生两种历史。

## 为什么直接驱动 `run_sse` 而不是起 HTTP 服务

- 判据要的是"生成器被关闭时终态写没写"，HTTP 层只是多一段传输；
- 直接驱动可以在 0.1 秒内覆盖两条出口（正常结束 / 客户端断开），
  不需要真起 uvicorn、不需要等心跳。

## 反证

把 `finally` 里那段 `_finalize` 删掉 ⇒ 第二个用例红（`calls` 为空）。
把正常路径的 `_finalize` 删掉 ⇒ 第一个用例红。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from workbench.api import routes_insight
from workbench.services.egmp.insight import jobs

REQ = jobs.InsightRequest(command="login", account_id="acc-1")


@pytest.fixture(autouse=True)
def _fast_keepalive(monkeypatch: pytest.MonkeyPatch):
    """心跳间隔调到 10ms：判据不验"0.5 秒"，只验"断开后终态有没有落"。"""
    monkeypatch.setattr(routes_insight, "KEEPALIVE_S", 0.01)


def _stub_finish(calls: list[tuple[tuple[Any, ...], dict[str, Any]]]):
    def _finish(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))
    return _finish


def test_sse_normal_run_finishes_the_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(jobs, "start_job", lambda req: ("job-sse-ok", tmp_path))
    monkeypatch.setattr(jobs, "run_job", lambda req, wd: {"status": "succeeded", "workspaces": [str(wd)]})
    monkeypatch.setattr(jobs, "finish_job", _stub_finish(calls))

    async def scenario() -> list[str]:
        response = await routes_insight.run_sse(REQ)
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(scenario())

    assert any('"done"' in chunk for chunk in chunks), "正常路径必须发 done 事件"
    assert len(calls) == 1, f"终态只应写一次，实际 {len(calls)} 次"
    args, kwargs = calls[0]
    assert args == ("job-sse-ok", "succeeded")
    assert kwargs["workspaces"] == [str(tmp_path)]


def test_client_disconnect_still_finishes_the_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """★ 客户端在 worker 结束前断开：终态仍然要落（靠 done callback）。"""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    gate = threading.Event()
    monkeypatch.setattr(routes_insight, "KEEPALIVE_S", 0.01)
    monkeypatch.setattr(jobs, "start_job", lambda req: ("job-sse-cut", tmp_path))

    def slow_run(req, wd):  # noqa: ANN001 —— 模拟一次还在跑的织网
        assert gate.wait(2.0), "worker 没有被放行"
        return {"status": "succeeded", "workspaces": [str(wd)]}

    monkeypatch.setattr(jobs, "run_job", slow_run)
    monkeypatch.setattr(jobs, "finish_job", _stub_finish(calls))

    async def scenario() -> str:
        response = await routes_insight.run_sse(REQ)
        generator = response.body_iterator
        first = await generator.__anext__()  # 至少拿到一帧，证明流已经启动
        await generator.aclose()             # 客户端断开
        gate.set()                           # 放行 worker
        for _ in range(200):                 # 等 done callback 落终态
            if calls:
                break
            await asyncio.sleep(0.01)
        return first

    first = asyncio.run(scenario())

    assert first == ": keep-alive\n\n"
    assert calls, "客户端断开后终态没有落库 —— 这一行会永远停在 running"
    args, _kwargs = calls[0]
    assert args == ("job-sse-cut", "succeeded")


def test_sse_worker_failure_is_recorded_as_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """worker 抛异常（被内部 catch 成 failed）：终态必须是 failed，不是 running。"""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def boom(req, wd):  # noqa: ANN001
        raise RuntimeError("模拟织网崩了")

    monkeypatch.setattr(jobs, "start_job", lambda req: ("job-sse-bad", tmp_path))
    monkeypatch.setattr(jobs, "run_job", boom)
    monkeypatch.setattr(jobs, "finish_job", _stub_finish(calls))

    async def scenario() -> list[str]:
        response = await routes_insight.run_sse(REQ)
        return [chunk async for chunk in response.body_iterator]

    asyncio.run(scenario())

    assert calls and calls[0][0] == ("job-sse-bad", "failed")
