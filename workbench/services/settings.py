"""运行时设置（DeepSeek / Embedding）——唯一的设置读写入口。

此前 api.py / embedding.py / 路由各自拼配置 dict，字段名散落三处；
现在集中为 Pydantic 模型：
- 内部代码一律用 snake_case 属性
- 对外 JSON 输出自动转 camelCase（与前端契约保持不变）
- DB 键名与旧版 Tauri 完全一致（apiKey / embeddingApiKey / ...）
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from . import storage
from .deepseek import DEFAULT_MODEL

DEFAULT_EMBEDDING_MODEL = "qwen3.7-text-embedding"
DEFAULT_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_DEEPSEEK_DB_KEYS = ("apiKey", "model", "temperature")
_EMBEDDING_DB_KEYS = ("embeddingApiKey", "embeddingModel", "embeddingBaseUrl")


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


class EmbeddingSettings(BaseModel):
    api_key: str = ""
    model: str = DEFAULT_EMBEDDING_MODEL
    base_url: str = DEFAULT_EMBEDDING_BASE_URL

    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)

    @property
    def ready(self) -> bool:
        return bool(self.api_key)


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(part.title() for part in rest)


def load_deepseek() -> DeepSeekSettings:
    raw = {key: storage.get_setting(key) for key in _DEEPSEEK_DB_KEYS}
    temperature = raw["temperature"]
    return DeepSeekSettings(
        api_key=raw["apiKey"] or "",
        model=raw["model"] or DEFAULT_MODEL,
        temperature=float(temperature) if temperature else 0.7,
    )


def load_embedding() -> EmbeddingSettings:
    raw = {key: storage.get_setting(key) for key in _EMBEDDING_DB_KEYS}
    return EmbeddingSettings(
        api_key=raw["embeddingApiKey"] or "",
        model=raw["embeddingModel"] or DEFAULT_EMBEDDING_MODEL,
        base_url=raw["embeddingBaseUrl"] or DEFAULT_EMBEDDING_BASE_URL,
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


def save_embedding(*, api_key: str | None = None, model: str | None = None,
                   base_url: str | None = None) -> EmbeddingSettings:
    if api_key is not None:
        storage.set_setting("embeddingApiKey", api_key.strip())
    if model:
        storage.set_setting("embeddingModel", model.strip())
    if base_url:
        storage.set_setting("embeddingBaseUrl", base_url.strip())
    return load_embedding()


def all_settings_json() -> dict:
    """bootstrap/保存后的对外 JSON（camelCase，契约不变）。"""
    deepseek = load_deepseek()
    emb = load_embedding()
    return {
        "apiKey": deepseek.api_key,
        "model": deepseek.model,
        "temperature": deepseek.temperature,
        "embedding": emb.model_dump(by_alias=True),
    }
