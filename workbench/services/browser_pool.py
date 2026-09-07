"""托管浏览器池：一账号 = 一个 Playwright BrowserContext（原生隔离）。

知识迁移映射（quick-login parallel-session.ts）：
- 「账号↔页签」模型 → 简化为「账号↔context」：DNR 回放 / Cookie 袋 / 命名空间隔离
  全部不迁（context 天然隔离，见 docs/迁移台账.md 不迁清单）。
- URL 决策：有登录态（token 快照存在）→ 直达 baseUrl；无登录态 → baseUrl + /login
  自动登录。autologin.py 承担节奏门控。
- tab-title.ts 的页签标题改写不迁：页签标识由 context/账号状态墙承载。

线程模型：Playwright sync API。FastAPI 同步路由跑在线程池里，
BrowserPool 内部用锁串行化对 Playwright 对象的访问。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from . import accounts, autologin
from .storage import now_ms


@dataclass
class SessionEntry:
    account_id: str
    context: Any = None
    page: Any = None
    token_capture: autologin.TokenCapture | None = None
    status: str = "launching"  # launching | logging_in | online | stopped | error
    title: str = ""
    detail: str = ""
    started_at: int = 0
    autologin_state: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "status": self.status,
            "title": self.title,
            "detail": self.detail,
            "started_at": self.started_at,
            "autologin": self.autologin_state,
            "has_token": bool(self.token_capture and self.token_capture.latest()),
        }


class BrowserError(RuntimeError):
    """托管浏览器操作失败。"""


class BrowserPool:
    """进程级单例（由 get_pool() 获取）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pw: Any = None
        self._browser: Any = None
        self._sessions: dict[str, SessionEntry] = {}

    # ------------------------------------------------------------- 生命周期

    def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError("playwright 未安装：pip install playwright") from exc
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 —— 浏览器未安装等
            self._pw.stop()
            self._pw = None
            raise BrowserError(
                f"chromium 启动失败（先执行 playwright install chromium）：{exc}"
            ) from exc
        return self._browser

    def close_all(self) -> None:
        with self._lock:
            for entry in list(self._sessions.values()):
                self._close_entry(entry)
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:  # noqa: BLE001
                    pass
                self._browser = None
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._pw = None

    def _close_entry(self, entry: SessionEntry) -> None:
        try:
            if entry.context is not None:
                entry.context.close()
        except Exception:  # noqa: BLE001
            pass
        entry.context = None
        entry.page = None
        entry.status = "stopped"
        entry.detail = "已关闭"

    # ------------------------------------------------------------- 会话管理

    def open_account(self, account_id: str, *, headful: bool = False) -> dict[str, Any]:
        """一键启动账号会话：建 context → 自动登录 → 返回会话快照。"""
        with self._lock:
            account = accounts.get_account(account_id)
            if not account:
                raise BrowserError(f"账号不存在：{account_id}")
            base_url = (account.get("env_base_url") or "").strip()
            if not base_url:
                raise BrowserError(f"账号 {account['username']} 的平台环境未配置 baseUrl")

            old = self._sessions.get(account_id)
            if old is not None and old.context is not None:
                return {**old.snapshot(), "reused": True}

            username = account["username"]
            password = accounts.reveal_password(account_id)
            browser = self._ensure_browser()
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            entry = SessionEntry(account_id=account_id, context=context, started_at=now_ms())
            entry.token_capture = autologin.TokenCapture(account_id)
            entry.token_capture.attach(context)
            self._sessions[account_id] = entry

            page = context.new_page()
            entry.page = page
            autologin.install(page)

            # URL 决策（parallel-session.ts open() 语义）：有 token 快照直达，否则进登录页
            has_session = entry.token_capture.latest() is not None
            url = base_url if has_session else base_url.rstrip("/") + "/login"
            entry.status = "logging_in" if not has_session else "online"
            entry.detail = "已有登录态，直达首页" if has_session else "进入登录页，自动填充中"

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if not has_session:
                    started = autologin.start(page, username, password)
                    if not started:
                        entry.status = "error"
                        entry.detail = "自动登录引擎启动失败（页面无登录表单？）"
            except Exception as exc:  # noqa: BLE001
                entry.status = "error"
                entry.detail = f"页面加载失败：{exc}"
            return entry.snapshot()

    def close_account(self, account_id: str) -> bool:
        with self._lock:
            entry = self._sessions.get(account_id)
            if entry is None:
                return False
            self._close_entry(entry)
            return True

    def session(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._sessions.get(account_id)
            if entry is None:
                return None
            # 刷新标题与 autologin 状态（尽力而为）
            if entry.page is not None:
                try:
                    entry.title = entry.page.title()
                except Exception:  # noqa: BLE001
                    pass
                st = autologin.status(entry.page)
                if st:
                    entry.autologin_state = st
                    if entry.status == "logging_in" and st.get("phase") == "success":
                        entry.status = "online"
                        entry.detail = "自动登录完成"
                    elif entry.status == "logging_in" and st.get("phase") in {"gave_up", "stopped"}:
                        entry.status = "error"
                        entry.detail = f"自动登录未完成：{st.get('reason', st.get('phase'))}"
            return entry.snapshot()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.session(account_id) or entry.snapshot()
                    for account_id, entry in self._sessions.items()]

    def token_of(self, account_id: str) -> str | None:
        with self._lock:
            entry = self._sessions.get(account_id)
            return entry.token_capture.latest() if entry and entry.token_capture else None


_pool: BrowserPool | None = None
_pool_lock = threading.Lock()


def get_pool() -> BrowserPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = BrowserPool()
        return _pool


def warmup_check() -> dict[str, Any]:
    """依赖体检：chromium 可执行文件是否存在（不真正启动）。"""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = p.chromium.executable_path
            from pathlib import Path

            ok = Path(path).exists()
        return {"ok": ok, "detail": str(path) if ok else "chromium 未安装"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"检测失败：{exc}"}
