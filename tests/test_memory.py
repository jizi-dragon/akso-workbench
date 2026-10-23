"""长期记忆 v1：事实存储 / 检索 / 过期 / 工具声明。

## 边界

- 只存事实，不存指令；不自动注入（system 前缀每轮变一次 = 缓存全 miss）。
- 检索是"词元重叠"的保守排序，不是语义搜索；`score=0` 的行不会返回（空查询除外）。
- `expires_at` 是硬边界：过期行不参与 `search`，但 `list` 仍可见（便于清理）。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from workbench import db, memory
from workbench.api import routes_memory
from workbench.hub import get_hub


@pytest.fixture(autouse=True)
def _clean():
    db.connect()
    db.execute("DELETE FROM agent_memory")
    yield
    db.execute("DELETE FROM agent_memory")


def _add(**kw):
    base = {"title": "发布分支叫 release/2026.10", "body": "发版前从 main 切出，只接 hotfix。",
            "scope": "project", "type": "project"}
    base.update(kw)
    return memory.add(**base)


def test_add_and_get_round_trip() -> None:
    row = _add(keywords="release branch 发布")
    assert row["id"] and row["revision"] == 1
    assert row["scope"] == "project" and row["title"].startswith("发布分支")
    assert memory.get(row["id"])["body"].startswith("发版前")
    assert memory.count() == 1


def test_search_ranks_title_and_keyword_hits() -> None:
    hit = _add(title="发布分支叫 release/2026.10", keywords="release 发布")
    _add(title="账号中心只展示云端库", body="本地账号库只作迁移输入。", scope="global",
         type="reference")
    rows = memory.search("发布分支")
    assert rows and rows[0]["id"] == hit["id"]
    assert rows[0]["score"] > 0
    assert all(item["score"] > 0 for item in rows)


def test_expired_memory_is_not_searched_but_is_listed() -> None:
    row = _add(expires_at=1)
    assert memory.search("发布") == []
    assert any(item["id"] == row["id"] for item in memory.list_memories())


def test_scope_filter() -> None:
    project = _add()
    global_row = _add(title="用户偏好：先给结论", scope="global", type="user")
    assert [r["id"] for r in memory.search("发布", scope="project")] == [project["id"]]
    assert [r["id"] for r in memory.search("结论", scope="global")] == [global_row["id"]]


def test_validation_rejects_bad_input() -> None:
    with pytest.raises(memory.MemoryError, match="scope"):
        _add(scope="nope")
    with pytest.raises(memory.MemoryError, match="title"):
        _add(title="")
    with pytest.raises(memory.MemoryError, match="type"):
        _add(type="nope")


def test_delete() -> None:
    row = _add()
    assert memory.delete(row["id"]) is True
    assert memory.get(row["id"]) is None
    assert memory.delete(row["id"]) is False


def test_api_routes() -> None:
    created = routes_memory.add_memory(routes_memory.MemoryIn(
        title="服务约束：周五不发布", body="除非 P0 安全修复。", scope="project", type="project"))
    assert created["id"]
    listed = routes_memory.list_memories(scope="project")
    assert listed["count"] == 1
    assert routes_memory.delete_memory(created["id"])["ok"] is True

    with pytest.raises(HTTPException):
        routes_memory.add_memory(routes_memory.MemoryIn(
            title="", body="x", scope="project", type="project"))


def test_tools_are_registered_with_safe_risk() -> None:
    hub = get_hub()
    search = hub.get("memory_search")
    add = hub.get("memory_add")
    delete = hub.get("memory_delete")
    assert search.risk.value == "read"
    assert add.risk.value == "write" and add.reversibility.value == "irreversible"
    assert delete.risk.value == "write" and delete.reversibility.value == "irreversible"
    assert all(spec.trust.value == "local" for spec in (search, add, delete))
