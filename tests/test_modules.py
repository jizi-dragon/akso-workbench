"""modules.py 单测：adapters 只读声明加载 + 模块体检。"""

from __future__ import annotations

from workbench.services import modules


def test_adapters_loaded() -> None:
    adapters = modules.load_adapters()
    ids = {a["id"] for a in adapters}
    assert {"akso-cc", "akso-auto"} <= ids


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
