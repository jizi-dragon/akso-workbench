"""写路径的响应解析：**创建类端点怎么取回新建记录的 id**。

## 为什么单独一个模块（2026-09-21 真机实证）

`SaveBasicObject` 与 `SaveField` 的响应**都只回 `{code:0, data:true}`** ——
那个 `True` 是"成功"标志，**不是 id**。而旧写法是：

    object_id = resp.get("id") if isinstance(resp, dict) else resp

`True` 不是 dict，于是走 `else resp` 分支，`object_id` 变成布尔 `True`；再经
`orchestrate._object()` 的 `str(outcome.get("objectId") or ... or "")` 归一化成
字符串 `"True"`，被后续建字段 / 建表单布局 / 建列表布局 / 建工作流一路当成
`objectId` 发出去。

## 它为什么能长期藏着（两处夹具都不忠实）

1. `tests/fakes.py` 把这两个端点建模成**返回 `{"id": ...}`** —— 那正是真实平台
   **不**做的事。于是测试里走的是 `isinstance(resp, dict)` 那条**正确**分支，
   `else resp` 那条**从未被执行**，`test_run_full_workflow_idempotent` 一路绿灯。
2. **分析侧的合成样例**把响应信封**手写**成 `{code, message, success}`，而真机是
   `{code, errorCode, message, data, ...}` —— 分析代码读的 `data` 键在样例里根本不存在，
   于是"平台真的返回了数据"这条路径同样从未被走过。

**夹具的形状与真机不一致时，回归测试反而守着错误行为**（同
[docs/PITFALLS.md](../../../../../docs/PITFALLS.md) #29 / #62）。

## 取 id 的三条路

1. **dict 且带 `id`** → 直接用（部分端点确实这样回）；
2. **裸字符串** → 直接用（`Status/Create` 这类，作者见过），但**排除布尔与
   `"true"`/`"false"`** 字样 —— 它们只可能是成功标志；
3. **其余一律视为"没拿到"** → 由调用方回读（按 code / 编码查一次）；
   仍然拿不到就返回 `None`，由调用方**如实报失败**。

第 3 条是本次修复的要点：旧实现把"没拿到"当成了"拿到了一个布尔"。
"""

from __future__ import annotations

from typing import Any, Callable

#: 这些字符串只可能是成功标志，不可能是 id
_NOT_IDS = frozenset({"true", "false"})


def created_id(client: Any, *, path: str, body: dict[str, Any],
               refetch: Callable[[], dict[str, Any] | None]) -> str | None:
    """发创建请求并取回新建记录的 id；**取不到返回 `None`，绝不返回布尔**。

    `refetch`：按 code/编码回读一次的函数（幂等层本来就在用这条通道，
    见 `writers/idempotency.py`）。回读失败不抛异常，返回 `None` 即可 ——
    调用方据此把这一步记成失败，而不是让一个坏 id 继续往下流。
    """
    resp = client.post(path, body)

    if isinstance(resp, dict) and resp.get("id"):
        return str(resp["id"])

    if isinstance(resp, str):
        text = resp.strip()
        if text and text.lower() not in _NOT_IDS:
            return text

    try:
        meta = refetch() or {}
    except Exception:  # noqa: BLE001 —— 回读失败按"没拿到"处理，交由调用方报失败
        return None
    got = meta.get("id")
    return str(got) if got else None
