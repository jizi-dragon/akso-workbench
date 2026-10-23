"""定时任务/目标推进的**执行路径**：不联网，用假循环验证落会话与状态。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from workbench import scheduler
from workbench.services import storage


@pytest.fixture()
def fake_loop(monkeypatch: pytest.MonkeyPatch):
    async def fake_run(_messages, _client, _hub, _config, **_kwargs):
        yield {"type": "token", "text": "定时结果"}
        yield {"type": "final", "content": "定时结果"}

    monkeypatch.setattr("workbench.harness.loop.run_agent_loop", fake_run)
    monkeypatch.setattr("workbench.services.settings.load_deepseek",
                        lambda: SimpleNamespace(api_key="sk-test", model="fake",
                                                temperature=0.2))
    monkeypatch.setattr("workbench.services.settings.load_max_steps", lambda: 5)
    monkeypatch.setattr("workbench.services.deepseek.DeepSeekClient",
                        lambda *args, **kwargs: object())
    return fake_run


def test_scheduler_execute_persists_result(fake_loop) -> None:
    task = {"id": "sch-test", "title": "测试任务", "prompt": "只读检查"}
    status, error, conversation_id = scheduler.execute(task)
    assert status == "ok"
    assert error == ""
    messages = storage.list_messages(conversation_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert "定时结果" in messages[-1]["content"]
