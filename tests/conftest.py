"""pytest 全局夹具：所有测试用隔离的临时数据目录/数据库。

必须在导入 workbench 之前设置环境变量（config 在导入期读取）。
"""

from __future__ import annotations

import os
import socket as _socket
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="workbench-test-"))
os.environ["WORKBENCH_DATA"] = str(_TMP / "data")
os.environ["WORKBENCH_DB"] = str(_TMP / "data" / "test.db")
os.environ["WORKBENCH_ADAPTERS"] = str(Path(__file__).resolve().parent.parent / "adapters")


# ---------------------------------------------------------------- 测试不得驱动真实的壳
#
# ## 事故原状（2026-09-21，用户三次报告"自动更新弹窗自己冒出来"）—— 见 PITFALLS #71
#
# `tests/test_update.py::test_update_check_without_shell_degrades` 直接
# `client.post("/api/update/check")`。这条路由（`routes_update.update_action`）
# 会**真的**去 POST `http://127.0.0.1:18767` —— 而那个端口上跑着**开发机自己的壳**。
# 于是壳正开着的时候（开发机上大多数时候都开着）这条测试会：
#
#   ① 真的打到壳的控制服务；
#   ② 让壳弹出「自动更新已关闭」的**模态框**（就在开发者的屏幕上）；
#   ③ 而断言 `ok is False` **照样成立** —— 关闭分支返回的就是 `ok=false`。
#
# ⇒ **一条带真实副作用、却始终显示为绿的测试**。它会一路活下去，因为没有任何
# 门禁看得见"测试在别人的屏幕上弹了个框"。
#
# ## 为什么是全局闸门而不是只修那一条
#
# 数据目录是隔离的（见上），**但端口不是**：壳、CDP、控制服务的三个端口都是
# 固定常量（ADR-0012）。所以"某个测试会不会碰到真机器"这件事，只能靠**端口**兜。
# 这也符合本仓的口径：**能变成硬闸门的东西，不要靠人记得**。
#
# ⚠️ 想验"壳没跑"的降级路径，必须像那条测试现在这样**注入**失败
# （monkeypatch urlopen 抛 ConnectionRefusedError），
# **不要**指望"这台机器上 18767 正好没人听"——那是环境事实，会变。
@pytest.fixture(autouse=True)
def _block_real_control_port(monkeypatch: pytest.MonkeyPatch):
    from workbench import config

    target_port = int(config.CONTROL_PORT)
    real_connect = _socket.socket.connect

    def guarded(self, address):  # noqa: ANN001 —— 复刻 socket.connect 的签名
        host = address[0] if isinstance(address, tuple) and address else ""
        port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
        if port == target_port and host in {"127.0.0.1", "localhost", "::1", ""}:
            raise ConnectionRefusedError(
                f"测试不许连真实控制端口 {host}:{port}——那是开发机上正在跑的壳，"
                "驱动它会在用户屏幕上弹真实弹窗。要验降级路径请注入失败，"
                "别指望这个端口正好没人听（见 conftest 顶部的事故记录）"
            )
        return real_connect(self, address)

    monkeypatch.setattr(_socket.socket, "connect", guarded)
    yield
