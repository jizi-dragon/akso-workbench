"""browser_pool 持久会话逻辑单测（不启动真实浏览器：验证决策与状态文件语义）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from workbench.services.browser_pool import BrowserPool

ROOT = Path(__file__).resolve().parent.parent


class _FakePage:
    """最小 page 桩：_looks_like_login 的两种信号。"""

    def __init__(self, url: str = "", username_fields: int = 0) -> None:
        self.url = url
        self._fields = username_fields

    def locator(self, _selector: str) -> Any:
        class _L:
            def __init__(self, n: int) -> None:
                self._n = n

            def count(self) -> int:
                return self._n

        return _L(self._fields)


def test_state_path_isolated_per_account() -> None:
    path_a = BrowserPool._state_path("acc-a")
    path_b = BrowserPool._state_path("acc-b")
    assert path_a != path_b
    assert path_a.name == "acc-a.json"
    assert "browser-states" in str(path_a)


def test_has_saved_and_forget_roundtrip() -> None:
    account_id = "acc-roundtrip"
    assert BrowserPool.has_saved_session(account_id) is False
    path = BrowserPool._state_path(account_id)
    path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    assert BrowserPool.has_saved_session(account_id) is True
    assert BrowserPool.forget_session(account_id) is True
    assert BrowserPool.has_saved_session(account_id) is False
    assert BrowserPool.forget_session(account_id) is False  # 幂等


def test_looks_like_login_by_url() -> None:
    assert BrowserPool._looks_like_login(_FakePage(url="https://x.example.com/login")) is True
    assert BrowserPool._looks_like_login(_FakePage(url="https://x.example.com/web/home")) is False


def test_looks_like_login_by_field_fallback() -> None:
    """URL 不含 /login 但页面存在用户名输入框（SPA 路由场景）→ 视为登录页。"""
    assert BrowserPool._looks_like_login(_FakePage(url="https://x.example.com/", username_fields=1)) is True
    assert BrowserPool._looks_like_login(_FakePage(url="https://x.example.com/", username_fields=0)) is False


def test_looks_like_login_never_raises() -> None:
    class _Boom:
        url = property(lambda self: (_ for _ in ()).throw(RuntimeError("closed")))

        def locator(self, *_a: object) -> Any:
            raise RuntimeError("closed")

    assert BrowserPool._looks_like_login(_Boom()) is False  # 自愈判定永不拖垮会话


# ─────────────────────────────────────────────────────────────────────────────
# C1 回归：关窗时的状态判断曾经恒假 → "登录态已保存"永远传不到 UI
# ─────────────────────────────────────────────────────────────────────────────


class _FakeContext:
    """最小 context 桩：只记录 storage_state 被写到了哪。"""

    def __init__(self) -> None:
        self.saved_to: list[str] = []
        self.closed = False

    def storage_state(self, path: str) -> None:
        self.saved_to.append(path)

    def close(self) -> None:
        self.closed = True


def _entry(status: str, restored: bool = False) -> Any:
    from workbench.services.browser_pool import SessionEntry

    return SessionEntry(account_id="acc-c1", context=_FakeContext(), status=status, restored=restored)


def test_close_entry_reports_saved_state_when_session_was_online() -> None:
    """**C1 回归**（2026-09-17 修）。

    旧实现先 `entry.status = "stopped"` 再判断 `entry.status == "online"` ——
    赋值之后才比较，该分支**恒假**，于是"关窗时登录态已落盘"这件事永远传不到 UI，
    用户只看到含糊的「已关闭」。本用例在修复前于 `detail` 断言上 FAIL。
    """
    entry = _entry("online")
    BrowserPool._close_entry(entry)

    assert entry.status == "stopped"
    assert entry.detail == "已关闭（登录态已保存）", "在线会话关窗必须说明登录态已保存"
    assert entry.context is None and entry.page is None


def test_close_entry_saves_storage_state_before_clearing_context() -> None:
    """落盘必须发生在 `context = None` **之前**，否则免密直达的持久化会静默消失。"""
    entry = _entry("online")
    context = entry.context
    BrowserPool._close_entry(entry)
    assert len(context.saved_to) == 1, "在线会话关窗时必须落盘一次 storage_state"
    assert context.saved_to[0].endswith("acc-c1.json")


def test_close_entry_offline_session_has_plain_detail() -> None:
    """没登录过（launching/stopped）的会话关窗不该谎称"登录态已保存"。"""
    entry = _entry("launching")
    BrowserPool._close_entry(entry)
    assert entry.status == "stopped"
    assert entry.detail == "已关闭"


def test_close_entry_restored_session_reports_saved_state() -> None:
    """`restored`（从磁盘恢复的持久会话）同样应报"已保存"——旧实现里这条分支是唯一活的。"""
    entry = _entry("logging_in", restored=True)
    BrowserPool._close_entry(entry)
    assert entry.detail == "已关闭（登录态已保存）"


# ─────────────────────────────────────────────────────────────────────────────
# 体检探测：不许在日志里留噪声
# ─────────────────────────────────────────────────────────────────────────────


def test_warmup_check_leaves_no_asyncio_noise() -> None:
    """`warmup_check()` 跑完，进程退出时 **stderr 必须干净**。

    实测：`sync_playwright()` 的起停会留下一个 pending 的 connection task，
    解释器退出/GC 时打印
    `Task was destroyed but it is pending!` 与
    `TargetClosedError: Target page, context or browser has been closed`（never retrieved）。
    而 `warmup_check()` 是**体检**入口——`/api/modules`（前端模块体检页）与
    `/api/browser/check`（账号中心）都会反复调用它，sync 版等于往日志里持续灌噪声；
    噪声会掩盖真错误，而"看得见的失败"正是本仓的纪律。

    **为什么要在子进程里测**：那两句是在**解释器退出阶段**打印的，
    同进程里断言看不到——只有真跑完一个进程才现形。
    """
    proc = subprocess.run(
        [sys.executable, "-c",
         "from workbench.services.browser_pool import warmup_check;"
         "print('probe-ok', warmup_check()['ok'])"],
        capture_output=True, text=True, timeout=300, cwd=str(ROOT),
    )
    assert "probe-ok" in proc.stdout, f"子进程没跑起来：{proc.stdout!r} {proc.stderr!r}"
    assert "Task was destroyed" not in proc.stderr, (
        f"体检留下了 pending task（把 sync_playwright 换回 async 版）：{proc.stderr}"
    )
    assert "TargetClosedError" not in proc.stderr, (
        f"体检泄漏了未取回的异常：{proc.stderr}"
    )

