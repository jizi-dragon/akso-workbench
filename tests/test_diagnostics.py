"""能力诊断：稳定 code、静态默认、路径/凭据脱敏。

## 为什么单列

诊断最容易变成"又一个日志页"：输出一堆绝对路径和异常原文，用户还是不知道
哪个能力没了。这份测试钉三件事：

1. **code 稳定**（`mcp.config_invalid` / `permissions.invalid_config`），调用方可分支；
2. **默认静态**（不启动 MCP、不联网），`live` 也只读内存注册表；
3. **脱敏**：不出现 `<data>` 之外的绝对路径，不输出异常原文/凭据值。
"""

from __future__ import annotations

import json

import pytest

from workbench import config, db, diagnostics, permissions
from workbench.api import routes_diagnostics
from workbench.services import mcp, storage


@pytest.fixture(autouse=True)
def _ready():
    db.connect()
    cfg = mcp.config_path()
    backup = cfg.read_bytes() if cfg.exists() else None
    if cfg.exists():
        cfg.unlink()
    storage.set_setting(permissions.SETTING_KEY, "")
    yield
    if cfg.exists():
        cfg.unlink()
    if backup is not None:
        cfg.write_bytes(backup)
    storage.set_setting(permissions.SETTING_KEY, "")


def _codes(report: dict) -> set[str]:
    return {item["code"] for item in report["issues"]}


def test_static_report_has_stable_schema_and_is_redacted() -> None:
    report = diagnostics.collect()
    assert report["schemaVersion"] == diagnostics.SCHEMA_VERSION
    assert set(report["summary"]) == {"error", "warning", "info"}
    assert report["summary"]["error"] == 0, report["issues"]
    assert report["ok"] is True

    text = json.dumps(report, ensure_ascii=False)
    assert str(config.DATA_DIR) not in text, "诊断泄露了数据目录绝对路径"
    assert "C:\\Users\\" not in text, "诊断泄露了用户目录绝对路径"
    # 路径只以 <data>/<repo>/<external> 形式出现
    assert report["modules"]["mcp"]["configPath"].startswith("<data>/")
    assert report["modules"]["config"]["dataDir"].startswith("<data>")


def test_invalid_mcp_config_is_a_stable_warning() -> None:
    mcp.config_path().write_text("{ not json", encoding="utf-8")
    report = diagnostics.collect()
    assert "mcp.config_invalid" in _codes(report)
    item = next(i for i in report["issues"] if i["code"] == "mcp.config_invalid")
    assert item["severity"] == "warning"
    assert item["facts"]["count"] >= 1


def test_invalid_permissions_config_is_an_error_and_fails_closed() -> None:
    storage.set_setting(permissions.SETTING_KEY, '{"mode": "ask", "deny": [')
    report = diagnostics.collect()
    assert report["ok"] is False
    assert "permissions.invalid_config" in _codes(report)


def test_live_reads_registry_but_never_starts_servers() -> None:
    report = diagnostics.collect(live=True)
    registry = report["modules"]["mcp"].get("registry")
    assert isinstance(registry, dict)
    assert set(registry) == {"loaded", "healthy", "failing", "tools"}


def test_endpoint_returns_the_same_schema() -> None:
    report = routes_diagnostics.diagnostics(live=False)
    assert report["schemaVersion"] == diagnostics.SCHEMA_VERSION
    assert "issues" in report
