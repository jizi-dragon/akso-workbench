"""定时任务调度器：每 30 秒查一次到期任务，在独立线程里跑只读 Agent 回合。

## 为什么是线程而不是 asyncio task

`run_agent_loop` 是异步的，但调度器本身是同步 DB 轮询；用 `asyncio.run()` 在
worker 线程里跑一个完整回合，和 CLI/脚本消费同一套循环，不依赖 FastAPI 事件循环。
`to_thread` 在这里不适用（它是给 async 调用方用的），我们**就是**那个线程。

## 无人值守的安全边界

- `plan_mode=True`：模型只看见 READ 工具；
- `allowed_tools` 再收窄到**当前 core 的 READ 工具**（不把 MCP/用户 Skill 的
  未知写能力带进定时任务）；
- 没有确认门可点，所以**不允许**写工具；这是产品约束，不是提示词建议。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading

from . import schedules
from .plugin_api import Risk

logger = logging.getLogger("workbench.scheduler")

TICK_INTERVAL_S = 30.0


class Scheduler:
    def __init__(self, interval_s: float = TICK_INTERVAL_S) -> None:
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if os.environ.get("WORKBENCH_DISABLE_SCHEDULER") == "1":
            logger.info("定时任务调度器被 WORKBENCH_DISABLE_SCHEDULER=1 禁用")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 —— 调度器坏掉不能拖垮应用
                logger.warning("定时任务轮询失败", exc_info=True)

    def tick(self, now: int | None = None) -> int:
        started = 0
        for task in schedules.due(now):
            with self._lock:
                if task["id"] in self._running:
                    continue
                self._running.add(task["id"])
            threading.Thread(target=self._run_one, args=(task,),
                             name=f"schedule-{task['id'][:8]}", daemon=True).start()
            started += 1
        return started

    def _run_one(self, task: dict) -> None:
        try:
            status, error, conversation_id = execute(task)
        except Exception as exc:  # noqa: BLE001 —— 单个任务失败只记该任务
            logger.warning("定时任务执行失败：%s", task.get("id"), exc_info=True)
            status, error, conversation_id = "error", f"{type(exc).__name__}: {exc}", ""
        finally:
            with self._lock:
                self._running.discard(task["id"])
        try:
            schedules.mark_run(task["id"], status=status, error=error,
                               conversation_id=conversation_id)
        except Exception:  # noqa: BLE001
            logger.warning("定时任务状态回写失败：%s", task.get("id"), exc_info=True)


def _read_only_tools() -> tuple[str, ...]:
    from .harness.tools import default_registry

    return tuple(spec.name for spec in default_registry().core_specs()
                 if spec.risk is Risk.READ)


def execute(task: dict) -> tuple[str, str, str]:
    """执行一次任务，返回 `(status, error, conversation_id)`。不做状态回写。"""
    from .harness.loop import run_agent_loop
    from .harness.prompts import build_system_prompt
    from .harness.tools import default_registry
    from .harness.types import AgentConfig
    from .services import storage
    from .services.deepseek import DeepSeekClient
    from .services.settings import load_deepseek, load_max_steps

    cfg = load_deepseek()
    if not cfg.api_key:
        raise RuntimeError("未配置 DeepSeek API Key，定时任务无法执行")

    conversation = storage.create_conversation(task["title"][:60] or "定时任务")
    conv_id = conversation["id"]
    storage.insert_message(conv_id, "user", task["prompt"])
    client = DeepSeekClient(cfg.api_key, model=cfg.model)
    config = AgentConfig(
        temperature=cfg.temperature,
        system_prompt=build_system_prompt() + (
            "\n\n## 定时任务\n这是一次无人值守的只读任务：只允许 READ 工具，"
            "不要请求确认、不要尝试写操作；把结论写成可直接阅读的报告。"
        ),
        max_steps=load_max_steps(),
        plan_mode=True,
        allowed_tools=_read_only_tools(),
        approval_wait_s=0.0,
    )
    content = ""
    error = ""
    async def _run() -> None:
        nonlocal content, error
        async for event in run_agent_loop(
            [{"role": "user", "content": task["prompt"]}],
            client, default_registry(), config, conversation_id=conv_id,
        ):
            if event["type"] == "token":
                content += event["text"]
            elif event["type"] == "final":
                content = event["content"]
            elif event["type"] == "error":
                error = str(event.get("message") or "Agent 回合失败")

    asyncio.run(_run())
    if content:
        storage.insert_message(conv_id, "assistant", content)
    if error:
        return "error", error, conv_id
    return "ok", "", conv_id
