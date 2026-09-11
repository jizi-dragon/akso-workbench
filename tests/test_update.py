"""更新面测试：版本比较、壳状态读取（TTL/损坏/缺失）、更新路由与扩展版本一致性判定。

覆盖的都是「静默失败会变成用户困惑」的路径：壳没跑 / 状态文件半截 / 扩展旧版判定。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from workbench import config
from workbench.api import app
from workbench.api import routes_extension as ext
from workbench.api.routes_update import read_shell_state, version_tuple


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def fresh_ext_meta(monkeypatch: pytest.MonkeyPatch):
    """每个用例从空元信息开始（模块级 _ext_meta 是进程态，会跨用例串味）。"""

    def reset() -> None:
        monkeypatch.setitem(ext._ext_meta, "extVersion", "")
        monkeypatch.setitem(ext._ext_meta, "at", 0.0)

    reset()
    return monkeypatch


def _write_shell_state(payload: dict) -> None:
    path = config.DATA_DIR / "shell-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ------------------------------------------------------------------ 版本比较


def test_version_tuple_parsing() -> None:
    assert version_tuple("0.3.2") == (0, 3, 2)
    assert version_tuple("0.3.10") > version_tuple("0.3.9")  # 字符串比较会在这里翻车
    assert version_tuple("1.0") == (1, 0, 0)  # 缺段补零
    assert version_tuple("") == (0, 0, 0)  # 缺失按最小版本
    assert version_tuple(None) == (0, 0, 0)
    assert version_tuple("0.3.2-beta.1") == (0, 3, 2)  # 预发布后缀不影响数值比较


# ------------------------------------------------------------------ 壳状态读取


def test_shell_state_absent() -> None:
    (config.DATA_DIR / "shell-state.json").unlink(missing_ok=True)
    state = read_shell_state()
    assert state["live"] is False
    assert state["label"] == "桌面壳未运行"


def test_shell_state_corrupt_is_not_fatal() -> None:
    path = config.DATA_DIR / "shell-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 半截 JSON", encoding="utf-8")
    assert read_shell_state()["live"] is False


def test_shell_state_live_and_stale() -> None:
    now = int(time.time() * 1000)
    _write_shell_state({"at": now, "phase": "ready", "version": "0.3.3", "availableVersion": "0.3.4", "percent": 100})
    live = read_shell_state()
    assert live["live"] is True
    assert live["phase"] == "ready"
    assert live["availableVersion"] == "0.3.4"

    # 陈旧（壳已退出）→ 不再沿用旧结论，UI 不该提示「更新已就绪」
    _write_shell_state({"at": now - 10 * 60 * 1000, "phase": "ready", "availableVersion": "0.3.4"})
    stale = read_shell_state()
    assert stale["live"] is False
    assert stale["phase"] == "inactive"
    assert stale["updatePending"] is False


# ------------------------------------------------------------------ 更新路由


def test_update_info_shape(client: TestClient) -> None:
    (config.DATA_DIR / "shell-state.json").unlink(missing_ok=True)
    data = client.get("/api/update").json()
    assert data["version"]
    assert data["shell"]["live"] is False
    assert data["updateAvailable"] is False


def test_update_info_flags_newer_version(client: TestClient) -> None:
    _write_shell_state(
        {"at": int(time.time() * 1000), "phase": "downloading", "version": "0.3.2",
         "availableVersion": "0.9.9", "percent": 42, "updatePending": True}
    )
    data = client.get("/api/update").json()
    assert data["updateAvailable"] is True
    assert data["shell"]["percent"] == 42


def test_update_action_rejects_unknown(client: TestClient) -> None:
    assert client.post("/api/update/boom").json()["ok"] is False


def test_update_check_without_shell_degrades(client: TestClient) -> None:
    """壳没跑时不能抛 500：UI 要拿到 ok=false 才能退化为文字提示。"""
    resp = client.post("/api/update/check")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ------------------------------------------------------------------ 扩展版本一致性


def _report(client: TestClient, ext_version: str) -> None:
    body: dict = {"items": []}
    if ext_version:
        body["extVersion"] = ext_version
    resp = client.post("/extension/state", json=body)
    assert resp.status_code == 200


def test_health_reports_versions(client: TestClient, fresh_ext_meta) -> None:
    _report(client, "0.0.1")  # 故意旧版
    data = client.get("/extension/health").json()
    assert data["extVersion"] == "0.0.1"
    assert data["desktopVersion"]
    assert data["extStale"] is True


def test_health_not_stale_when_versions_match(client: TestClient, fresh_ext_meta) -> None:
    desktop = client.get("/extension/health").json()["desktopVersion"]
    _report(client, desktop)
    data = client.get("/extension/health").json()
    assert data["extVersion"] == desktop
    assert data["extStale"] is False


def test_health_unknown_version_is_not_stale(client: TestClient, fresh_ext_meta) -> None:
    """旧版扩展根本不上报版本 → 不能判定为旧版（否则每次升级都误报打扰用户）。"""
    data = client.get("/extension/health").json()
    assert data["extVersion"] == ""
    assert data["extStale"] is False


def test_state_report_without_accounts_still_reports_version(client: TestClient, fresh_ext_meta) -> None:
    """映射为空（还没同步过账号）时也必须能上报版本——否则旧版扩展永远提示不到。"""
    _report(client, "9.9.9")
    assert client.get("/extension/health").json()["extVersion"] == "9.9.9"


def test_version_only_report_counts_as_connected(client: TestClient, fresh_ext_meta) -> None:
    """「只有版本、没有账号映射」的上报同样算已连接。

    否则用户还没同步过账号时，执行面明明活着、UI 却在说「未连接」——
    与 everConnected 闩锁（收到上报即置位）自相矛盾。
    """
    _report(client, "9.9.9")
    data = client.get("/extension/health").json()
    assert data["connected"] is True
    assert data["reportedAccounts"] == 0


def test_snapshot_carries_desktop_version(client: TestClient) -> None:
    snap = client.get("/extension/snapshot").json()
    assert snap["desktopVersion"] == client.get("/extension/health").json()["desktopVersion"]
