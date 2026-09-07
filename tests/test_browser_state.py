"""browser_pool 持久会话逻辑单测（不启动真实浏览器：验证决策与状态文件语义）。"""

from __future__ import annotations

from typing import Any

from workbench.services.browser_pool import BrowserPool


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
