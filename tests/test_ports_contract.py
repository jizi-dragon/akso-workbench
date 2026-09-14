"""端口协商契约（ADR-0011）：`/api/health` 的身份、桌面端口文件的优先级、控制服务端口单点声明。

存在理由（docs/adr/0011）：改造前 18765/18766/18767 被壳、服务端、浏览器扩展**各自硬编码**，
且壳的就绪探测只看"这个端口上有人回 200"。于是"旧实例占着端口"会表现成
**"代码改了但 UI 没变"**——一次极难排查的真实事故。

本文件钉住三件事：
1. 服务端能**自证身份**（`/api/health` 的 `app`），壳的探测才有东西可核对；
2. 端口值以壳公布的 `desktop-ports.json` 为准，环境变量优先，**缺失时回落默认值**（不炸）；
3. 控制服务端口只有 `config` 一个来源——`routes_update` / `routes_extension` 都读它，
   不能再出现第二个常量。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench import config
from workbench.api import app
from workbench.api import routes_extension as ext
from workbench.api import routes_update as upd


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def no_ports_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """默认场景：没有壳（开发者手起服务端）。"""
    monkeypatch.setattr(config, "DESKTOP_PORTS_PATH", tmp_path / "missing.json")
    monkeypatch.delenv("WORKBENCH_PORT", raising=False)


def _write_ports(tmp_path: Path, **ports: int) -> Path:
    path = tmp_path / "desktop-ports.json"
    path.write_text(json.dumps({
        "app": "AksoWorkbench", "schemaVersion": 1, "pid": 4242, "version": "9.9.9",
        "startedAt": "2026-09-14T10:00:00+08:00",
        "ports": {"server": 18765, "cdp": 18766, "control": 18767, **ports},
    }), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 身份


def test_health_reports_identity(client: TestClient, no_ports_file) -> None:
    """`app` 字段是壳判定"是我们的服务"的唯一依据——不能改名，也不能少。"""
    body = client.get("/api/health").json()
    assert body["app"] == "AksoWorkbench"
    assert body["service"] == "workbench-server"
    assert body["version"]
    assert isinstance(body["pid"], int) and body["pid"] > 0


def test_health_exposes_all_three_ports(client: TestClient, no_ports_file) -> None:
    ports = client.get("/api/health").json()["ports"]
    assert set(ports) == {"server", "cdp", "control"}
    assert all(isinstance(v, int) and 0 < v < 65536 for v in ports.values())


def test_health_defaults_without_shell(client: TestClient, no_ports_file) -> None:
    """没有壳时回落默认端口，并且**如实说明出处**（便于排障时区分"谁定的端口"）。"""
    body = client.get("/api/health").json()
    assert body["portsSource"] == "default"
    assert body["shell"] is None
    assert body["ports"]["server"] == 18765


# ---------------------------------------------------------------- 端口优先级


def test_ports_file_wins_over_default(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WORKBENCH_PORT", raising=False)
    monkeypatch.setattr(config, "DESKTOP_PORTS_PATH", _write_ports(tmp_path, server=18770, cdp=18771, control=18772))
    body = client.get("/api/health").json()
    assert body["ports"] == {"server": 18770, "cdp": 18771, "control": 18772}
    assert body["portsSource"] == "desktop-ports.json"
    assert body["shell"]["pid"] == 4242


def test_env_wins_over_ports_file(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    """壳同时下发环境变量与文件；两者不一致时以**环境变量**为准（那是本进程的实际监听值）。"""
    monkeypatch.setattr(config, "PORT", 18781)
    monkeypatch.setenv("WORKBENCH_PORT", "18781")
    monkeypatch.setattr(config, "DESKTOP_PORTS_PATH", _write_ports(tmp_path, server=18770))
    body = client.get("/api/health").json()
    assert body["ports"]["server"] == 18781
    assert body["portsSource"] == "env"


def test_corrupt_ports_file_does_not_raise(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    """半截 JSON / 非对象内容都不得让健康检查炸掉——探测端点必须永远能应答。"""
    monkeypatch.delenv("WORKBENCH_PORT", raising=False)
    bad = tmp_path / "desktop-ports.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(config, "DESKTOP_PORTS_PATH", bad)
    assert client.get("/api/health").json()["ports"]["server"] == 18765

    bad.write_text("[1, 2, 3]", encoding="utf-8")
    assert client.get("/api/health").json()["ports"]["server"] == 18765


def test_ports_file_with_partial_ports_keeps_defaults(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    """只公布了部分端口时，剩下的用默认值补齐，而不是丢字段（探测方要能无条件读三个）。"""
    monkeypatch.delenv("WORKBENCH_PORT", raising=False)
    path = tmp_path / "desktop-ports.json"
    path.write_text(json.dumps({"app": "AksoWorkbench", "ports": {"server": 19000}}), encoding="utf-8")
    monkeypatch.setattr(config, "DESKTOP_PORTS_PATH", path)
    assert client.get("/api/health").json()["ports"] == {
        "server": 19000, "cdp": config.CDP_PORT, "control": config.CONTROL_PORT,
    }


def test_ports_file_rejects_non_integer_values(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    """被改坏的文件（字符串端口、0、负数）不得变成"监听端口是 nonsense"。"""
    monkeypatch.delenv("WORKBENCH_PORT", raising=False)
    path = tmp_path / "desktop-ports.json"
    path.write_text(json.dumps({"ports": {"server": "18765", "cdp": 0, "control": -1}}), encoding="utf-8")
    monkeypatch.setattr(config, "DESKTOP_PORTS_PATH", path)
    assert client.get("/api/health").json()["ports"] == {
        "server": 18765, "cdp": config.CDP_PORT, "control": config.CONTROL_PORT,
    }


# ---------------------------------------------------------------- 通道前缀


def test_ports_file_lives_in_runtime_dir() -> None:
    """路径规则必须与 desktop/shell-state.js 的 dataDir() 一致，否则壳与服务端读两个目录。"""
    assert config.DESKTOP_PORTS_PATH.parent == config.RUNTIME_DIR
    assert config.DESKTOP_PORTS_PATH.name == "desktop-ports.json"


def test_health_has_no_side_effects(client: TestClient, no_ports_file) -> None:
    """探测端点必须无副作用（不能像 /extension/health 那样写 everConnected 闩锁）。

    壳会每 300ms 打一次探测；若这个端点写库，"扩展开没开过"的判定会被探测本身污染。
    """
    from workbench.services.storage import get_setting

    before = get_setting(ext._EVER_KEY)
    for _ in range(3):
        client.get("/api/health")
    assert get_setting(ext._EVER_KEY) == before


# ---------------------------------------------------------------- 控制服务端口单点


def test_control_base_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由里的壳控制服务地址**不能是模块级常量**：壳会把实际端口下发到环境变量。"""
    monkeypatch.setattr(config, "CONTROL_PORT", 18888)
    assert ext.control_base() == "http://127.0.0.1:18888"
    assert upd.control_base() == "http://127.0.0.1:18888"


def test_control_port_reads_env() -> None:
    """`WORKBENCH_CONTROL_PORT` 是壳下发端口的入口（ADR-0011 §D-2），必须被 config 认。"""
    import importlib
    import os

    old = os.environ.get("WORKBENCH_CONTROL_PORT")
    os.environ["WORKBENCH_CONTROL_PORT"] = "18799"
    try:
        reloaded = importlib.reload(config)
        assert reloaded.CONTROL_PORT == 18799
    finally:
        if old is None:
            os.environ.pop("WORKBENCH_CONTROL_PORT", None)
        else:
            os.environ["WORKBENCH_CONTROL_PORT"] = old
        importlib.reload(config)


def test_no_hardcoded_shell_ports_outside_config() -> None:
    """源码扫描：壳的两个端口除了 config，别处不得再写下会**参与运行**的字面量。

    这条守的是本 ADR 的**唯一真相来源**原则——一旦有人图省事在别处写下 "18767"，
    端口顺延之后那处就会静默指错（改造前正是如此）。

    只扫代码，不扫注释与**字符串字面量内部**（文档字符串里写示例端口是正常的，
    例如"常量是 18767、实际壳在 18768"这句解释本身就该留在注释里）。
    """
    import re

    root = Path(__file__).resolve().parent.parent / "workbench"
    allowed = {root / "config.py"}
    string_literal = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        # 去掉字符串字面量与注释后，代码里就不该再有这两个数字
        code = string_literal.sub('""', text)
        for lineno, line in enumerate(code.splitlines(), 1):
            bare = line.split("#", 1)[0]
            if "18766" in bare or "18767" in bare:
                offenders.append(f"{path.relative_to(root.parent)}:{lineno} {bare.strip()[:90]}")
    assert not offenders, "壳端口字面量必须只在 config 声明：\n" + "\n".join(offenders)
