"""托管浏览器池：一账号 = 一个 Playwright BrowserContext（原生隔离）。

quick-login 能力的全家桶内化（本模块即该扩展行为的唯一归宿）：
- 「账号↔页签」模型 → 「账号↔context」：六平面隔离（DNR/Cookie 袋/命名空间…）
  由 context 原生隔离 + storage_state 持久化等价替代，不迁清单见迁移台账。
- 登录态持久化：autologin 成功 / 会话正常关闭时落盘 storage_state；
  再次打开免密直达首页（对应扩展的「有 token 直达 /」体验，且跨进程重启有效）。
- 会话自愈：持久会话被踢回登录页时自动重跑节奏门控引擎（对应扩展的会话失效处理）。
- URL 决策（parallel-session.ts open() 语义的原生等价）：有持久会话 → 首页；
  无 → /login + autologin。
- tab-title.ts 不迁：标题由状态墙轮询 page.title() 呈现。

线程模型：Playwright sync API。FastAPI 同步路由跑在线程池里，
BrowserPool 内部用锁串行化对 Playwright 对象的访问。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
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
    restored: bool = False  # 本次打开是否来自持久会话
    headful: bool = False  # 该会话是否为可见窗口
    heal_count: int = 0  # 运行中自愈重登次数（上限 2，防死循环，对齐 TokenRenewer）
    autologin_state: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "status": self.status,
            "title": self.title,
            "detail": self.detail,
            "started_at": self.started_at,
            "restored": self.restored,
            "headful": self.headful,
            "heal_count": self.heal_count,
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
        self._browsers: dict[bool, Any] = {}  # headful → browser（两种模式各自惰性创建）
        self._sessions: dict[str, SessionEntry] = {}

    # ------------------------------------------------------------- 持久会话

    @staticmethod
    def _state_path(account_id: str) -> Path:
        from .. import config

        directory = config.RUNTIME_DIR / "browser-states"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{account_id}.json"

    @classmethod
    def has_saved_session(cls, account_id: str) -> bool:
        return cls._state_path(account_id).exists()

    @classmethod
    def forget_session(cls, account_id: str) -> bool:
        """清除持久会话（登出语义；活动中的 context 不受影响，关闭后即回到登录态）。"""
        path = cls._state_path(account_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def _save_state(self, entry: SessionEntry) -> None:
        """落盘登录态（storage_state：cookies + localStorage，Cookie 袋的原生等价物）。"""
        try:
            if entry.context is not None:
                entry.context.storage_state(path=str(self._state_path(entry.account_id)))
        except Exception:  # noqa: BLE001 —— 落盘失败不影响会话本身
            pass

    @staticmethod
    def _looks_like_login(page: Any) -> bool:
        """会话自愈判定：被踢回登录页 = URL 含 /login 或页面上有用户名输入框。"""
        try:
            url = str(page.url or "")
            if "/login" in url:
                return True
            return page.locator('input[placeholder="请输入用户名"]').count() > 0
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------- 生命周期

    def _ensure_browser(self, *, headful: bool = False) -> Any:
        """按模式惰性启动浏览器（可见窗口交付"以该身份操作"的实际效果）。"""
        if self._browsers.get(headful) is not None:
            return self._browsers[headful]
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError("playwright 未安装：pip install playwright") from exc
        if self._pw is None:
            self._pw = sync_playwright().start()
        try:
            self._browsers[headful] = self._pw.chromium.launch(headless=not headful)
        except Exception as exc:  # noqa: BLE001 —— 浏览器未安装等
            raise BrowserError(
                f"chromium 启动失败（先执行 playwright install chromium）：{exc}"
            ) from exc
        return self._browsers[headful]

    def close_all(self) -> None:
        with self._lock:
            for entry in list(self._sessions.values()):
                self._close_entry(entry)
            for browser in list(self._browsers.values()):
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
            self._browsers.clear()
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._pw = None

    def _close_entry(self, entry: SessionEntry) -> None:
        # 正常关闭前固化登录态（对应扩展的会话卫生：下次免密直达）
        if entry.status == "online":
            self._save_state(entry)
        try:
            if entry.context is not None:
                entry.context.close()
        except Exception:  # noqa: BLE001
            pass
        entry.context = None
        entry.page = None
        entry.status = "stopped"
        entry.detail = "已关闭（登录态已保存）" if entry.restored or entry.status == "online" else "已关闭"

    # ------------------------------------------------------------- 会话管理

    def open_account(self, account_id: str, *, headful: bool = True) -> dict[str, Any]:
        """一键启动账号会话：持久会话恢复 → 失效自愈 / 全新自动登录。

        默认有头（可见窗口）——交付"以该身份操作"的实际效果；
        纯后端自动化场景显式传 headful=False。
        已在线的会话重复打开 = 聚焦其窗口（对齐扩展"切换身份"手感）。
        """
        with self._lock:
            account = accounts.get_account(account_id)
            if not account:
                raise BrowserError(f"账号不存在：{account_id}")
            base_url = (account.get("env_base_url") or "").strip()
            if not base_url:
                raise BrowserError(f"账号 {account['username']} 的平台环境未配置 baseUrl")

            old = self._sessions.get(account_id)
            if old is not None and old.context is not None:
                if old.status == "online":
                    self._focus(old)  # 切换身份 = 聚焦已有窗口
                return {**old.snapshot(), "reused": True}

            username = account["username"]
            password = accounts.reveal_password(account_id)
            browser = self._ensure_browser(headful=headful)

            state_path = self._state_path(account_id)
            has_saved = state_path.exists()
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                storage_state=str(state_path) if has_saved else None,
            )
            entry = SessionEntry(account_id=account_id, context=context,
                                 started_at=now_ms(), restored=has_saved, headful=headful)
            entry.token_capture = autologin.TokenCapture(account_id)
            entry.token_capture.attach(context)
            self._sessions[account_id] = entry

            page = context.new_page()
            entry.page = page
            autologin.install(page)

            # URL 决策：有持久会话 → 免密直达首页；无 → /login 自动填充
            entry.status = "online" if has_saved else "logging_in"
            entry.detail = "恢复持久会话，免密直达首页" if has_saved else "进入登录页，自动填充中"
            url = base_url

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if has_saved and self._looks_like_login(page):
                    # 会话自愈：持久态被平台踢回登录页 → 自动重登
                    entry.status = "logging_in"
                    entry.restored = False
                    entry.detail = "持久会话已失效，自动重新登录中"
                    if not autologin.start(page, username, password):
                        entry.status = "error"
                        entry.detail = "自动登录引擎启动失败（页面无登录表单？）"
                elif not has_saved:
                    if not autologin.start(page, username, password):
                        entry.status = "error"
                        entry.detail = "自动登录引擎启动失败（页面无登录表单？）"
                else:
                    self._focus(entry)  # 免密直达后窗口置前
            except Exception as exc:  # noqa: BLE001
                entry.status = "error"
                entry.detail = f"页面加载失败：{exc}"
            return entry.snapshot()

    def _focus(self, entry: SessionEntry) -> None:
        """把该账号的窗口带到前台（尽力而为；headless 下为无害空操作）。"""
        try:
            if entry.page is not None:
                entry.page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass

    def focus_account(self, account_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._sessions.get(account_id)
            if entry is None or entry.context is None:
                return None
            self._focus(entry)
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
                        entry.detail = ("自动登录完成（登录态已持久化）"
                                        if entry.heal_count == 0
                                        else f"自愈成功（第 {entry.heal_count} 次重登）")
                        self._save_state(entry)  # 登录成功即落盘
                    elif entry.status == "logging_in" and st.get("phase") in {"gave_up", "stopped"}:
                        entry.status = "error"
                        entry.detail = f"自动登录未完成：{st.get('reason', st.get('phase'))}"

                # 运行中自愈（对齐扩展 TokenRenewer）：在线会话被踢回登录页 → 自动重登
                if (entry.status == "online" and entry.page is not None
                        and self._looks_like_login(entry.page)):
                    if entry.heal_count >= 2:
                        entry.status = "error"
                        entry.detail = "会话反复失效（自愈 2 次未成功），请检查凭据"
                    else:
                        try:
                            account = accounts.get_account(entry.account_id)
                            username = account["username"] if account else ""
                            password = accounts.reveal_password(entry.account_id)
                        except Exception:  # noqa: BLE001
                            username, password = "", ""
                        if username and password:
                            entry.heal_count += 1
                            entry.status = "logging_in"
                            entry.detail = f"会话已失效，自动重新登录中（第 {entry.heal_count} 次）"
                            entry.restored = False
                            autologin.install(entry.page)
                            if not autologin.start(entry.page, username, password):
                                entry.status = "error"
                                entry.detail = "自愈失败：页面无登录表单"
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
