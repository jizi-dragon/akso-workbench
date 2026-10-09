"""更新面测试：版本比较、壳状态读取（TTL/损坏/缺失）、更新路由。

覆盖的都是「静默失败会变成用户困惑」的路径：壳没跑 / 状态文件半截。

> 2026-10-09：原先还覆盖"扩展版本一致性"（`/extension/health` 的 `extStale` 判定）。
> 浏览器扩展已整体移出本仓库，那一节与它依赖的 `/extension/state` 端点一并删除。
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from workbench import config
from workbench.api import app, routes_update
from workbench.api.routes_update import read_shell_state, version_tuple


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


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
    # 缺字段时按"自动更新开着"回落：旧壳没这个键，不能因为读不到就断言它被关了
    assert state["autoUpdate"] is True


def test_disabled_auto_update_reaches_the_ui() -> None:
    """自动更新关闭时，**界面必须拿得到这个事实**，而不是靠点一下才知道。

    2026-09-21 实测：壳写下的 `message="自动更新已关闭"` 在 API 边界被丢掉，
    角标于是显示「未检查更新」（看起来像"点我可以检查"），用户点一下吃到一个
    「已关闭」弹窗，当场问"我不是关了吗"。

    这条钉两件事：① 壳给的口径不再被本地映射**重算**掉（重算会丢掉理由）；
    ② `autoUpdate` 这个**独立字段**一路透到 UI（phase 分不出"关掉了"和"还没检查"，
    两者的 phase 都是 idle）。
    """
    now = int(time.time() * 1000)
    _write_shell_state({
        "at": now,
        "phase": "idle",
        "label": "未检查更新",
        "message": "自动更新已关闭",
        "autoUpdate": False,
        "version": "0.3.12",
        "supported": False,
    })
    state = read_shell_state()
    assert state["live"] is True
    assert state["autoUpdate"] is False, "关闭这个事实没有透到界面"
    assert state["message"] == "自动更新已关闭"


def test_shell_label_wins_over_the_local_mapping() -> None:
    """壳写的 `label` 优先于本地 `_PHASE_LABEL` 的重算。

    本地那份只是**按 phase 重算的近似**——壳知道得更多（它才知道自己是"被关掉了"
    还是"还没检查"）。重算 = 把壳的判断扔掉，这是本仓最忌讳的"两个事实源"。
    """
    now = int(time.time() * 1000)
    _write_shell_state({"at": now, "phase": "idle", "label": "壳自己的说法"})
    assert read_shell_state()["label"] == "壳自己的说法"

    # 壳没给 label 时才回落本地映射（老壳 / 手写文件）
    _write_shell_state({"at": now, "phase": "ready"})
    assert read_shell_state()["label"] == "更新已就绪，退出时安装"
    # 连映射里都没有的 phase：如实回显，不要编
    _write_shell_state({"at": now, "phase": "未来相位"})
    assert read_shell_state()["label"] == "未来相位"


def test_phase_label_covers_every_phase_the_shell_can_return() -> None:
    """本地映射必须覆盖壳可能给出的每个 phase。

    缺一档的后果不是报错，而是界面上**原样显示英文内部枚举值**
    （`_PHASE_LABEL.get(phase, phase)` 的兜底）——`checkAndReport()` 在关闭时
    返回的正是 `disabled`，曾经就不在这张表里。
    """
    from workbench.api.routes_update import _PHASE_LABEL

    for phase in ("idle", "checking", "latest", "downloading", "ready", "error",
                  "disabled", "inactive", "unsupported"):
        assert _PHASE_LABEL.get(phase), f"{phase} 没有中文说明，界面会显示英文枚举值"


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


def test_update_check_without_shell_degrades(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """壳没跑时不能抛 500：UI 要拿到 ok=false 才能退化为文字提示。

    ⚠️ **"壳没跑"必须被注入进来，不能靠"这台机器上 18767 正好没人听"**
    （2026-09-21 实测事故）。本函数原来只有一句 `client.post("/api/update/check")`，
    而那条路由会**真的**去 POST `127.0.0.1:18767` —— 于是**壳开着的时候**
    （开发机上大多数时候都开着）它会：

      ① 真的打到壳的控制服务；
      ② 让壳弹出「自动更新已关闭」的**模态框**（就在开发者屏幕上）；
      ③ 而 `ok is False` **照样成立**——关闭分支返回的就是 `ok=false`。

    也就是一条**带真实副作用、却始终显示为绿的测试**：用户三次报告"弹窗自己冒出来"，
    凶手就是跑测试这件事本身（证据：壳侧收到一条**无 `Origin`** 的 `/update-check`，
    而产品日志里一条都没有——TestClient 在 pytest 进程内处理，日志不进应用日志）。
    """
    def _refused(*_args: object, **_kwargs: object) -> object:
        raise ConnectionRefusedError("测试注入：本机没有壳在监听控制端口")

    monkeypatch.setattr(routes_update.urllib.request, "urlopen", _refused)
    resp = client.post("/api/update/check")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    # 顺带钉住"降级"这句话本身：失败理由要能到达界面，不能是空串
    assert resp.json().get("error")

