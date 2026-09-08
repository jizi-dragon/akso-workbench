"""运行时设置（DeepSeek）——唯一的设置读写入口。

集中为 Pydantic 模型：
- 内部代码一律用 snake_case 属性
- 对外 JSON 输出自动转 camelCase（与前端契约保持不变）
- DB 键名与旧版 Tauri 完全一致（apiKey / model / temperature）

（Embedding 配置已随知识库功能下线移除；历史 DB 键留存不清理。）
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from . import storage
from .deepseek import DEFAULT_MODEL

_DEEPSEEK_DB_KEYS = ("apiKey", "model", "temperature")


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
    raw = {key: storage.get_setting(key) for key in _DEEPSEEK_DB_KEYS}
    temperature = raw["temperature"]
    return DeepSeekSettings(
        api_key=raw["apiKey"] or "",
        model=raw["model"] or DEFAULT_MODEL,
        temperature=float(temperature) if temperature else 0.7,
    )


def save_deepseek(*, api_key: str | None = None, model: str | None = None,
                  temperature: float | None = None) -> DeepSeekSettings:
    if api_key is not None:
        storage.set_setting("apiKey", api_key.strip())
    if model:
        storage.set_setting("model", model.strip())
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
    }
