"""modules.py 单测：adapters 只读声明加载 + 模块体检。"""

from __future__ import annotations

from workbench.services import modules


def test_adapters_loaded() -> None:
    adapters = modules.load_adapters()
    ids = {a["id"] for a in adapters}
    # `insight` 来自 adapters/akso-cc.json（文件名是"对应哪个来源项目"的引用真源，故不改名）
    assert {"insight", "akso-auto"} <= ids


def test_adapter_id_matches_its_filename_reference() -> None:
    """存档的 `id` 与文件名可以不同，但 `repoPathEnv` 必须仍指向同一来源项目。

    这条守的是"改名不改引用"：`insight` 模块的存档是 `akso-cc.json`，
    若有人顺手把 `repoPathEnv` 也改成 `AKSO_INSIGHT_REPO`，只读引用的口径就断了。
    """
    by_file = {a["_file"]: a for a in modules.load_adapters()}
    insight = by_file.get("akso-cc.json")
    assert insight is not None, "adapters/akso-cc.json 必须存在（只读存档）"
    assert insight["id"] == "insight"
    assert insight["repoPathEnv"] == "AKSO_CC_REPO"
    assert "akso-cc" in str(insight["repoPath"]).lower()


def test_adapter_paths_exist() -> None:
    """只读引用的原仓库必须真实存在（不写入，只校验）。"""
    for adapter in modules.load_adapters():
        repo = modules.resolve_repo_path(adapter)
        assert repo is not None and repo.exists(), f"{adapter['id']} repoPath 无效：{repo}"


def test_builtin_modules_status() -> None:
    for mod in modules.list_modules():
        assert mod["status"] in {"ok", "degraded", "missing"}
        assert mod["checks"], f"{mod['id']} 应有体检项"


def test_workbench_self_check_ok() -> None:
    status = modules.module_status("workbench")
    ok_items = [c for c in status["checks"] if c["name"].startswith("python:") and c["ok"]]
    assert {"python:fastapi", "python:httpx"} <= {c["name"] for c in ok_items}
