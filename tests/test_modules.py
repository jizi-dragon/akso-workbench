"""modules.py 单测：adapters 只读声明加载 + 模块体检。"""

from __future__ import annotations

from workbench.services import modules


def test_adapters_loaded() -> None:
    adapters = modules.load_adapters()
    ids = {a["id"] for a in adapters}
    # 模块 id 是本工作台自己的名字（`insight` / `factory`）；
    # **文件名**才是"对应哪个来源项目"的引用真源，故不随改名而改
    assert {"insight", "factory"} <= ids


def test_adapter_ids_never_leak_upstream_project_names() -> None:
    """界面上/接口上不得再出现上游项目名。

    这条守的是"改名改彻底"：`insight_runs.module` 一列历史上出现过三个值
    （`akso-cc` / `egmp-native` / `insight`），`blueprint_jobs.module` 出现过两个
    （`akso-auto` / `egmp-native`）——都是改名只改一半留下的。改名就一次改干净。
    """
    ids = {a["id"] for a in modules.load_adapters()}
    leaked = {i for i in ids if i.startswith("akso-")}
    assert not leaked, f"模块 id 里还有上游项目名：{leaked}"


def test_adapter_id_matches_its_filename_reference() -> None:
    """存档的 `id` 与文件名**刻意不同**，但 `repoPathEnv` 必须仍指向同一来源项目。

    这条守的是"改名不改引用"：`insight` 模块的存档是 `akso-cc.json`、
    `factory` 模块的存档是 `akso-auto.json`。若有人顺手把文件名或 `repoPathEnv`
    也改掉，只读引用的口径就断了——`AKSO_AUTO_REPO` 这个名字的语义就是
    "指向 akso-auto 那个仓库"，与模块叫什么无关。
    """
    by_file = {a["_file"]: a for a in modules.load_adapters()}

    insight = by_file.get("akso-cc.json")
    assert insight is not None, "adapters/akso-cc.json 必须存在（只读存档）"
    assert insight["id"] == "insight"
    assert insight["repoPathEnv"] == "AKSO_CC_REPO"
    assert "akso-cc" in str(insight["repoPath"]).lower()

    factory = by_file.get("akso-auto.json")
    assert factory is not None, "adapters/akso-auto.json 必须存在（只读存档）"
    assert factory["id"] == "factory"
    assert factory["repoPathEnv"] == "AKSO_AUTO_REPO"
    assert "akso-auto" in str(factory["repoPath"]).lower()


def test_frozen_adapters_do_not_require_the_upstream_repo() -> None:
    """只读存档**不该**要求原仓库存在，但必须能明确报告它在哪里。

    为什么改掉"repoPath 必须 exists"：`adapters/*.json` 的 `mode` 已是
    `native-python-internalized` + `frozen: true`——能力全部内化到 Python，
    运行时**零依赖**原仓库。把"原仓库目录存在"当断言，等于给一个已经不需要它的
    系统加一道环境门禁：本机没克隆那四个仓库时测试就红，而**功能完全正常**
    （2026-09-13 起 `pytest` 一直挂着一个这样的失败，正是本节要根治的）。
    真正该守的是"路径口径没坏"——即解析得出、且与存档声明一致。
    """
    for adapter in modules.load_adapters():
        assert adapter.get("frozen") is True, f"{adapter['id']} 未标记 frozen"
        assert adapter.get("mode") == "native-python-internalized", adapter["id"]
        repo = modules.resolve_repo_path(adapter)
        assert repo is not None, f"{adapter['id']} 的 repoPath 解析为空"
        declared = adapter.get("repoPath")
        if declared:
            assert str(repo) == declared, (
                f"{adapter['id']} 的 repoPath 解析结果与存档声明不一致：{repo} != {declared}"
            )


def test_missing_upstream_repo_is_reported_as_info_not_failure() -> None:
    """原仓库在不在，都不得让模块状态变红——它是**信息项**。

    这是"只读存档不该要求原仓库存在"的接口侧对应：`_check_adapter` 把
    `source-reference` 记为 `ok=True`（无论路径是否可达），文案在两种情况下都要说清
    "功能已原生化、不影响使用"。

    ⚠️ 这条测试**刻意不去断言"原仓库存在/不存在"**：那是环境事实，不是契约。
    本机 `D:\ai_assistant\akso-auto` 恰好存在，CI/同事机器上可能没有——早先
    那条 `assert repo.exists()` 就是因此常驻红的（2026-09-13 起挂着，见
    CODEBASE_OVERVIEW 的"pytest 唯一失败"一节）。
    """
    status = modules.module_status("factory")
    ref = next(c for c in status["checks"] if c["name"] == "source-reference")
    assert ref["ok"] is True, "只读存档的可达性不该是门禁"
    repo = modules.resolve_repo_path(modules.get_adapter("factory"))
    if repo is not None and repo.exists():
        assert str(repo) in ref["detail"], "可达时应报出路径"
    else:
        assert "原生化" in ref["detail"] and "不影响使用" in ref["detail"], ref["detail"]
    assert status["status"] == "ok", f"原仓库不在本机不该让模块状态变红：{status['status']}"


def test_module_status_resolves_adapter_without_being_handed_one() -> None:
    """`module_status(id)` 必须自己找到存档——这是改名不再变成静默故障的关键。

    早先它是 `mod = adapter or {}`：调用方不显式传 adapter 时，任何**适配器模块**
    都会得到空清单 + `status=missing`。2026-09-15 把 `akso-auto` 改名 `factory` 时
    正是这样暴露的——`GET /api/modules/factory` 返回了 missing，而代码一处都没错。
    """
    status = modules.module_status("factory")
    assert status["checks"], "适配器模块不带 adapter 调用也必须有体检项"
    assert status["status"] != "missing"
    assert status["title"] != "factory", "title 应来自存档而不是回落成 id"
    assert status["commands"], "命令清单应来自存档"


def test_builtin_modules_status() -> None:
    for mod in modules.list_modules():
        assert mod["status"] in {"ok", "degraded", "missing"}
        assert mod["checks"], f"{mod['id']} 应有体检项"


def test_factory_module_is_renamed_and_reachable() -> None:
    """工厂模块的 id 是 `factory`，旧 id 必须 404（不留"还能用"的第二名字）。"""
    from fastapi.testclient import TestClient

    from workbench.api import app

    client = TestClient(app)
    assert client.get("/api/modules/factory").status_code == 200
    assert client.post("/api/modules/factory/check").status_code == 200
    assert client.get("/api/modules/akso-auto").status_code == 404


def test_workbench_self_check_ok() -> None:
    status = modules.module_status("workbench")
    ok_items = [c for c in status["checks"] if c["name"].startswith("python:") and c["ok"]]
    assert {"python:fastapi", "python:httpx"} <= {c["name"] for c in ok_items}

