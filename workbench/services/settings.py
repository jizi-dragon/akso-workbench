"""运行时设置（DeepSeek 与 Agent 运行限制）——唯一的设置读写入口。

集中为 Pydantic 模型：
- 内部代码一律用 snake_case 属性
- 对外 JSON 输出自动转 camelCase（与前端契约保持不变）
- DB 键名与旧版 Tauri 完全一致（apiKey / model / temperature）

（Embedding 配置已随知识库功能下线移除；历史 DB 键留存不清理。）
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..harness.types import DEFAULT_MAX_STEPS, MAX_MAX_STEPS, MIN_MAX_STEPS
from . import storage
from .deepseek import DEFAULT_MODEL, normalize_model

_DEEPSEEK_DB_KEYS = ("apiKey", "model", "temperature")

MAX_STEPS_KEY = "maxSteps"
"""步数预算的 DB 键（与前端 camelCase 同名，省一次转换）。

**空 / 非法 / 越界一律回默认值，不报错**：这是"给用户调大的旋钮"，
不是安全参数——它的失效方向是"回到原来那个偏小的值"，而不是"变成无穷大"。
"""


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(part.title() for part in rest)


class DeepSeekSettings(BaseModel):
    api_key: str = ""
    model: str = DEFAULT_MODEL
    temperature: float = 0.7

    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)

    @property
    def ready(self) -> bool:
        return bool(self.api_key)


def load_deepseek() -> DeepSeekSettings:
    """读设置。

    **模型名在这里就归一**：库里可能存着 2026-09-10 已退役的 `deepseek-v4-flash`
    （官方为兼容把它路由到 V4.1-Flash，"temporarily"）。归一的收益是双重的——
    接口回给设置页的是现行名（模型卡片能正确高亮），请求里发的也是现行名
    （不依赖一条随时可能撤掉的兼容路由）。行为不变：同一个模型、同一个价格。
    """
    raw = {key: storage.get_setting(key) for key in _DEEPSEEK_DB_KEYS}
    temperature = raw["temperature"]
    return DeepSeekSettings(
        api_key=raw["apiKey"] or "",
        model=normalize_model(raw["model"]),
        temperature=float(temperature) if temperature else 0.7,
    )


def save_deepseek(*, api_key: str | None = None, model: str | None = None,
                  temperature: float | None = None) -> DeepSeekSettings:
    if api_key is not None:
        storage.set_setting("apiKey", api_key.strip())
    if model:
        storage.set_setting("model", normalize_model(model))
    if temperature is not None:
        storage.set_setting("temperature", str(temperature))
    return load_deepseek()


def all_settings_json() -> dict:
    """bootstrap/保存后的对外 JSON（camelCase，契约不变）。"""
    deepseek = load_deepseek()
    return {
        "apiKey": deepseek.api_key,
        "model": deepseek.model,
        "temperature": deepseek.temperature,
        "maxSteps": load_max_steps(),
        "maxStepsRange": [MIN_MAX_STEPS, MAX_MAX_STEPS],
        "maxStepsDefault": DEFAULT_MAX_STEPS,
    }


# ---------------------------------------------------------------- Agent 运行限制


def load_max_steps() -> int:
    """读步数预算。**任何异常都回默认值**（见 `MAX_STEPS_KEY` 的说明）。"""
    try:
        raw = storage.get_setting(MAX_STEPS_KEY)
    except Exception:  # noqa: BLE001 —— 读设置失败不该让对话起不来
        return DEFAULT_MAX_STEPS
    if not raw:
        return DEFAULT_MAX_STEPS
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_MAX_STEPS
    if not (MIN_MAX_STEPS <= value <= MAX_MAX_STEPS):
        return DEFAULT_MAX_STEPS
    return value


def save_max_steps(value: int | None) -> int:
    """写步数预算。`None` = 不动；越界值**夹到范围内**而不是拒（旋钮语义，不是校验语义）。"""
    if value is None:
        return load_max_steps()
    try:
        clamped = max(MIN_MAX_STEPS, min(int(value), MAX_MAX_STEPS))
    except (TypeError, ValueError):
        return load_max_steps()
    storage.set_setting(MAX_STEPS_KEY, str(clamped))
    return clamped
