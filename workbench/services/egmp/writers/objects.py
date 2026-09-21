"""对象创建（akso-auto create-object.js 的 Python 化）。"""

from __future__ import annotations

from typing import Any

from ..queries import GaiaQueries, invalidate_object_cache
from .endpoints import SAVE_BASIC_OBJECT
from .idempotency import Idempotency
from .responses import created_id


def _refetch_object(client: Any, code: str) -> dict[str, Any] | None:
    """建完之后按 code 回读，**先失效缓存**。

    为什么必须失效：`GaiaQueries.get_object_info` 走进程内缓存（`cache.default_cache`），
    而"这个 code 存在吗"这个问题在**创建之前**已经被幂等层问过一次了 ——
    问出来的否定答案（或上一次运行留下的旧记录）会被缓存命中，
    于是回读拿到的是**创建前**的世界。真机同一进程内连续跑两份蓝图就会撞上，
    单测里两个用例用同一个 code 也会。`orchestrate._object()` 本来就会失效，
    但它做在**建完之后**，救不了这次回读。
    """
    invalidate_object_cache(code)
    return Idempotency(GaiaQueries(client)).object_exists(code)


def _object_body(*, name: str, code: str, enable_life_cycle: bool,
                 enable_signatures: bool) -> dict[str, Any]:
    """建对象请求体。**真机逐键核对过**（2026-09-21 演示）：
    平台界面建自定义对象时发的就是这 8 个键、`source/status/objectClass` 就是 3/1/1。"""
    return {
        "name": name, "code": code, "source": 3, "status": 1, "objectClass": 1,
        "enableLifeCycle": enable_life_cycle, "enableSignatures": enable_signatures,
        "summaryFields": [],
    }


def create_object(client: Any, *, name: str, code: str, enable_life_cycle: bool = True,
                  enable_signatures: bool = False) -> dict[str, Any]:
    """建对象并取回真实 id。

    ⚠️ **真机实证（2026-09-21）**：`SaveBasicObject` 只回 `{code:0, data:true}`，
    **不回 id**。所以：
    - id 必须**回读**（旧写法把布尔 `True` 当成了 id，见 `writers/responses.py` 的模块文档）；
    - 回读也拿不到时**如实返回 `success: False`** —— 旧实现无条件 `success: True`，
      会让空 id 一路流到建字段那一步，症状变成"平台报了个看不懂的错"。

    判据：`python -m pytest tests/test_native.py -o addopts="" -k write_responses`。
    """
    body = _object_body(name=name, code=code, enable_life_cycle=enable_life_cycle,
                        enable_signatures=enable_signatures)
    object_id = created_id(
        client, path=SAVE_BASIC_OBJECT, body=body,
        refetch=lambda: _refetch_object(client, code),
    )
    if not object_id:
        return {"success": False, "objectId": None, "code": code,
                "message": f"对象[{name}]已创建但回读不到 id，后续步骤中止"}
    return {"success": True, "objectId": object_id, "code": code, "message": "对象创建成功"}
