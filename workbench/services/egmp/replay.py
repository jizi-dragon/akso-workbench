"""离线回放台架：把真实平台的响应录成**夹具**，之后无需联网即可跑通洞察全链路。

用途见 docs/INSIGHT-SPEC.md §6.2「离线回放夹具」：

- :class:`Recorder` —— 包住真实 ``EgmpClient``，把每次只读调用的请求/响应落成夹具
  （由 ``tools/record_insight_fixture.py`` 驱动，对着真实环境录一次即可长期复用）；
- :class:`ReplayClient` —— 按夹具回放，只读面与 ``EgmpClient`` 一致；
- 回放时若请求不在夹具里 → 抛 :class:`ReplayMiss` 并列出可用键 —— **夹具的覆盖度由此可证**，
  而不是靠人肉清点。

**记录粒度**：录制发生在 ``get`` / ``post`` / ``paged_post`` 这一层，也就是**逻辑调用**粒度。
``paged_post`` 内部的翻页循环看不到——分页正确性由 client 层自己的测试负责，本台架不重复覆盖。

这不是生产运行时代码，是离线开发与验收的支撑模块（同 Monitor 之于监听）。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from .kernel import endpoints

FIXTURE_FORMAT_VERSION = 1


class ReplayMiss(RuntimeError):
    """回放时请求不在夹具里——说明夹具覆盖度不足（或代码新增了平台调用）。"""

    def __init__(self, key: str, available: list[str]) -> None:
        self.key = key
        self.available = available
        preview = "\n".join(f"    - {k}" for k in sorted(available)[:40])
        more = "" if len(available) <= 40 else f"\n    …（共 {len(available)} 条）"
        super().__init__(
            f"夹具缺少这条交互：{key}\n  夹具内已有：\n{preview}{more}"
        )


class ReplayRecordedError(RuntimeError):
    """回放一条**当时就失败**的交互（平台上真实发生的错误）。"""


# ---------------------------------------------------------------- 夹具读写


def match_key(method: str, path: str, params: Any = None, body: Any = None) -> str:
    """交互匹配键：方法 + 路径 + 规范化后的载荷。参数与请求体互斥（见 EgmpClient 的用法）。"""
    payload = params if params else body
    canon = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str)
    return f"{method.upper()} {path} {canon}"


def make_fixture(name: str, *, base_url: str = "", env_name: str = "", note: str = "") -> dict:
    return {
        "formatVersion": FIXTURE_FORMAT_VERSION,
        "name": name,
        "baseUrl": base_url,
        "envName": env_name,
        "note": note,
        "interactions": [],
    }


def load_fixture(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("formatVersion")
    if version != FIXTURE_FORMAT_VERSION:
        raise ValueError(
            f"夹具格式版本不匹配：文件为 {version}，本代码支持 {FIXTURE_FORMAT_VERSION}"
        )
    if not isinstance(data.get("interactions"), list):
        raise ValueError("夹具缺少 interactions 列表")
    return data


def save_fixture(fixture: dict, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _index(fixture: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in fixture.get("interactions", []):
        key = match_key(item.get("method", ""), item.get("path", ""),
                        item.get("params"), item.get("body"))
        out[key] = item
    return out


# ---------------------------------------------------------------- 录制


class Recorder:
    """包住真实客户端，记录每次只读调用。接口与 ``EgmpClient`` 一致。"""

    def __init__(self, inner: Any, fixture: dict | None = None, *, name: str = "recording") -> None:
        self._inner = inner
        self.fixture = fixture if fixture is not None else make_fixture(name)

    @property
    def scope(self) -> str:
        return str(getattr(self._inner, "scope", "") or "")

    def _record(self, method: str, path: str, params: Any, body: Any,
                call: Callable[[], Any]) -> Any:
        entry: dict[str, Any] = {"method": method.upper(), "path": path}
        if params:
            entry["params"] = params
        if body:
            entry["body"] = body
        try:
            result = call()
        except Exception as exc:  # noqa: BLE001 —— 失败也要录，回放时才能复现失败语义
            entry["ok"] = False
            entry["error"] = str(exc)
            entry["errorType"] = type(exc).__name__
            self.fixture["interactions"].append(entry)
            raise
        entry["ok"] = True
        entry["response"] = result
        self.fixture["interactions"].append(entry)
        return result

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._record("GET", path, params, None, lambda: self._inner.get(path, params))

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._record("POST", path, None, body, lambda: self._inner.post(path, body))

    def paged_post(self, path: str, body: dict | None = None, page_size: int = 1000) -> list[dict]:
        return self._record("POST", path, None, body,
                            lambda: self._inner.paged_post(path, body, page_size))

    # ---- 便捷只读方法：与 EgmpClient 同形（录制的是逻辑调用，不展开内部翻页）----

    def get_object(self, code: str) -> dict:
        return self.get(endpoints.render(endpoints.OBJECT, code=code)) or {}

    def get_object_fields(self, code: str) -> list:
        return self.get(endpoints.render(endpoints.OBJECT_FIELD, code=code)) or []

    def get_object_statuses(self, code: str) -> list:
        return self.get(endpoints.render(endpoints.OBJECT_LIFECYCLE, code=code)) or []

    def page_fields(self, object_id: str) -> list[dict]:
        return self.paged_post(endpoints.FIELD_PAGE, {"objectId": object_id})

    def list_workflows(self) -> list[dict]:
        return self.paged_post(
            endpoints.WORKFLOW_LIST, {"filters": []}
        )

    def list_menus(self) -> list:
        return self.get(endpoints.MENU_LIST) or []

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


# ---------------------------------------------------------------- 回放


class ReplayClient:
    """按夹具回放的客户端（只读面与 ``EgmpClient`` 一致）。

    返回值一律**深拷贝**：调用方可能就地修改（如给动作栏行打 ``_consumed`` 标记），
    共用同一份对象会让两次回放结果不同。
    """

    def __init__(self, fixture: dict, *, strict: bool = True) -> None:
        self._index = _index(fixture)
        self._fixture = fixture
        self._strict = strict
        self.calls: list[str] = []
        self.misses: list[str] = []
        self._base_url = str(fixture.get("baseUrl") or "replay://fixture")

    @property
    def scope(self) -> str:
        """回放也有作用域——否则缓存隔离测试在回放下失去意义。"""
        return self._base_url

    @property
    def interaction_count(self) -> int:
        return len(self._index)

    def _replay(self, method: str, path: str, params: Any = None, body: Any = None) -> Any:
        key = match_key(method, path, params, body)
        self.calls.append(key)
        entry = self._index.get(key)
        if entry is None:
            self.misses.append(key)
            if self._strict:
                raise ReplayMiss(key, list(self._index))
            return None
        if not entry.get("ok", True):
            raise ReplayRecordedError(entry.get("error") or f"录制时失败：{key}")
        return copy.deepcopy(entry.get("response"))

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._replay("GET", path, params=params)

    def post(self, path: str, body: dict | None = None) -> Any:
        return self._replay("POST", path, body=body)

    def paged_post(self, path: str, body: dict | None = None, page_size: int = 1000) -> list[dict]:
        return self._replay("POST", path, body=body) or []

    # ---- 便捷只读方法 ----

    def get_object(self, code: str) -> dict:
        return self.get(endpoints.render(endpoints.OBJECT, code=code)) or {}

    def get_object_fields(self, code: str) -> list:
        return self.get(endpoints.render(endpoints.OBJECT_FIELD, code=code)) or []

    def get_object_statuses(self, code: str) -> list:
        return self.get(endpoints.render(endpoints.OBJECT_LIFECYCLE, code=code)) or []

    def page_fields(self, object_id: str) -> list[dict]:
        return self.paged_post(endpoints.FIELD_PAGE, {"objectId": object_id})

    def list_workflows(self) -> list[dict]:
        return self.paged_post(
            endpoints.WORKFLOW_LIST, {"filters": []}
        )

    def list_menus(self) -> list:
        return self.get(endpoints.MENU_LIST) or []

    def __enter__(self) -> "ReplayClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


__all__ = [
    "FIXTURE_FORMAT_VERSION",
    "Recorder",
    "ReplayClient",
    "ReplayMiss",
    "ReplayRecordedError",
    "load_fixture",
    "make_fixture",
    "match_key",
    "save_fixture",
]
