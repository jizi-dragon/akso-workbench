"""分页必须有硬上限，且正常结束不能被上限误伤。

## 为什么单列

`paged_post` 的 `while True` 只认 `hasNext`。平台/代理返回恒 `true` 时，旧实现
会一直请求（审实测桩打出 **501 页**）—— 表现是"对象分析跑不完"，不是报错。
"""

from __future__ import annotations

import pytest

from workbench.services.egmp.client import MAX_PAGES, ApiError, EgmpClient


class _Paged(EgmpClient):
    def __init__(self, *, forever: bool = True, stop_after: int = 2) -> None:
        self.calls: list[int] = []
        self._forever = forever
        self._stop_after = stop_after

    def _envelope(self, _method: str, _path: str, body: dict) -> dict:  # type: ignore[override]
        page = int(body["pageIndex"])
        self.calls.append(page)
        return {"datas": [{"page": page}], "hasNext": self._forever or page < self._stop_after}


def test_paged_post_stops_at_the_hard_cap() -> None:
    client = _Paged(forever=True)
    with pytest.raises(ApiError) as ei:
        client.paged_post("/api/openapi/v1.0/FieldPage")
    assert ei.value.code == "pagination"
    assert len(client.calls) == MAX_PAGES, (
        f"实际请求了 {len(client.calls)} 页，上限是 {MAX_PAGES} —— 上限没生效"
    )


def test_paged_post_returns_all_rows_when_has_next_turns_false() -> None:
    client = _Paged(forever=False, stop_after=2)
    rows = client.paged_post("/api/openapi/v1.0/FieldPage")
    assert client.calls == [1, 2]
    assert [row["page"] for row in rows] == [1, 2]
