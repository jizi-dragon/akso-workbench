"""托管浏览器池：一账号 = 一个 Playwright BrowserContext（原生隔离）。

线程模型（关键）：Playwright sync API 绑定创建它的线程且非线程安全，
而 FastAPI 会把并发请求派到不同线程。因此本池持有**专职工作线程**，
Playwright/浏览器对象只在该线程内存活，所有操作以 Future 提交串行执行——
彻底消除跨线程 greenlet 错误（"启动失败"的历史根因）。

quick-login 能力的全家桶内化：
- 多账号共用一个浏览器应用（同一次 chromium launch，任务栏归组），每账号
  一个独立窗口（context 原生隔离；同一窗口多标签会共享 cookie 必然串号，
  故窗口制是唯一正确形态）。
- 登录态持久化：autologin 成功 / 正常关闭时落盘 storage_state，重开免密直达。
- 会话自愈：持久会话被踢回登录页（打开时）或运行中失效（轮询发现）→
  自动重跑节奏门控（heal_count 上限 2，对齐扩展 TokenRenewer）。
- 用户手动关闭浏览器窗口 → 检测并标记 stopped，下次启动自动重建。
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import accounts, autologin
from .storage import now_ms

# —— Electron 壳复用模式（WORKBENCH_SESSION_MODE=cdp）——
# 会话窗由 Electron 壳创建（每账号 persist 分区：cookie/存储隔离 + 内建持久化），
# Playwright 经 CDP(18766) 反向接管页面；开户窗/聚焦/关窗走壳的控制服务(18767)。
# local 模式 = 原生 playwright chromium（开发/无壳环境）。
SESSION_MODE = os.environ.get("WORKBENCH_SESSION_MODE", "local")
_CONTROL_PORT = int(os.environ.get("WORKBENCH_CONTROL_PORT", "18767"))
_CDP_PORT = int(os.environ.get("WORKBENCH_CDP_PORT", "18766"))


def _control_post(path: str, payload: dict[str, Any], timeout: float = 10.0) -> dict[str, Any] | None:
    """调用 Electron 壳的会话控制服务。壳未运行时返回 None（调用方转为 BrowserError）。"""
    import json as _json
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{_CONTROL_PORT}{path}",
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


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
    restored: bool = False
    headful: bool = False
    heal_count: int = 0
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
    """进程级单例（由 get_pool() 获取）。所有 Playwright 操作经专职线程串行执行。"""

    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Future, Callable[[], Any]]] = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, name="browser-pool-worker", daemon=True
        )
        self._thread.start()

    # --------------------------------------------------- 专职线程（Playwright 家）

    def _worker(self) -> None:
        # 打包态：优先使用随包分发的 chromium（须在 sync_playwright 启动前设置）
        if getattr(sys, "frozen", False):
            bundled = Path(getattr(sys, "_MEIPASS", ".")) / "ms-playwright"
            if bundled.exists():
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled)
        pw: Any = None
        browsers: dict[bool, Any] = {}
        sessions: dict[str, SessionEntry] = {}
        while True:
            future, job = self._jobs.get()
            try:
                future.set_result(job(pw, browsers, sessions))
            except Exception as exc:  # noqa: BLE001 —— 异常回传给提交方
                future.set_exception(exc)

    def _submit(self, job: Callable[[Any, dict[bool, Any], dict[str, SessionEntry]], Any],
                timeout: float | None = None) -> Any:
        future: Future = Future()
        self._jobs.put((future, job))
        return future.result(timeout=timeout)

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

    # ------------------------------------------------- 工作线程内的私有操作

    @staticmethod
    def _save_state(entry: SessionEntry) -> None:
        try:
            if entry.context is not None:
                entry.context.storage_state(path=str(BrowserPool._state_path(entry.account_id)))
        except Exception:  # noqa: BLE001 —— 落盘失败不影响会话本身
            pass

    @staticmethod
    def _looks_like_login(page: Any) -> bool:
        try:
            url = str(page.url or "")
            if "/login" in url:
                return True
            return page.locator('input[placeholder="请输入用户名"]').count() > 0
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _ensure_browser(pw: Any, browsers: dict[bool, Any], headful: bool) -> Any:
        if SESSION_MODE == "cdp":
            # 复用 Electron 壳的 Chromium：CDP 反向接管（headful/headless 共用壳实例）
            if browsers.get(True) is not None:
                return browsers[True]
            if pw is None:
                try:
                    from playwright.sync_api import sync_playwright
                except ImportError as exc:
                    raise BrowserError("playwright 未安装：pip install playwright") from exc
                pw = sync_playwright().start()
            try:
                browsers[True] = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{_CDP_PORT}")
            except Exception as exc:  # noqa: BLE001
                raise BrowserError(
                    f"CDP 连接失败（Electron 壳未运行或调试端口 {_CDP_PORT} 未开）：{exc}"
                ) from exc
            return browsers[True]
        if browsers.get(headful) is not None:
            return browsers[headful]
        if pw is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise BrowserError("playwright 未安装：pip install playwright") from exc
            pw = sync_playwright().start()
        try:
            browsers[headful] = pw.chromium.launch(headless=not headful)
        except Exception as exc:  # noqa: BLE001 —— 浏览器未安装等
            raise BrowserError(
                f"chromium 启动失败（先执行 playwright install chromium）：{exc}"
            ) from exc
        return browsers[headful]

    @staticmethod
    def _focus(entry: SessionEntry) -> None:
        try:
            if SESSION_MODE == "cdp":
                # CDP bring_to_front 抬不 OS 窗口 → 走壳控制服务
                _control_post("/windows/focus", {"windowId": f"acc-{entry.account_id}"})
                return
            if entry.page is not None:
                entry.page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _close_entry(entry: SessionEntry) -> None:
        if entry.status == "online":
            BrowserPool._save_state(entry)
        if SESSION_MODE == "cdp":
            # 关壳的会话窗（分区持久化由 Electron 自管）
            _control_post("/windows/close", {"windowId": f"acc-{entry.account_id}"})
        else:
            try:
                if entry.context is not None:
                    entry.context.close()
            except Exception:  # noqa: BLE001
                pass
        entry.context = None
        entry.page = None
        entry.status = "stopped"
        entry.detail = "已关闭（登录态已保存）" if entry.restored or entry.status == "online" else "已关闭"

    def _do_open(self, pw: Any, browsers: dict[bool, Any], sessions: dict[str, SessionEntry],
                 account_id: str, headful: bool) -> dict[str, Any]:
        account = accounts.get_account(account_id)
        if not account:
            raise BrowserError(f"账号不存在：{account_id}")
        base_url = (account.get("env_base_url") or "").strip()
        if not base_url:
            raise BrowserError(f"账号 {account['username']} 的平台环境未配置 baseUrl")

        old = sessions.get(account_id)
        if old is not None and old.context is not None:
            if old.status == "online":
                self._focus(old)  # 切换身份 = 聚焦已有窗口
            return {**old.snapshot(), "reused": True}

        username = account["username"]
        password = accounts.reveal_password(account_id)
        browser = self._ensure_browser(pw, browsers, headful)

        if SESSION_MODE == "cdp":
            return self._do_open_cdp(browser, sessions, account, account_id)

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
        sessions[account_id] = entry

        page = context.new_page()
        entry.page = page
        autologin.install(page)

        entry.status = "online" if has_saved else "logging_in"
        entry.detail = "恢复持久会话，免密直达首页" if has_saved else "进入登录页，自动填充中"

        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            if has_saved and self._looks_like_login(page):
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
                self._focus(entry)
        except Exception as exc:  # noqa: BLE001
            entry.status = "error"
            entry.detail = f"页面加载失败：{exc}"
        return entry.snapshot()

    def _do_open_cdp(self, browser: Any, sessions: dict[str, SessionEntry],
                     account: dict[str, Any], account_id: str) -> dict[str, Any]:
        """cdp 模式开户：壳建分区窗（persist:acc-<id>）→ CDP 定位 blank 标记页 → 导航平台。"""
        base_url = (account.get("env_base_url") or "").strip()
        window_id = f"acc-{account_id}"
        resp = _control_post("/windows", {"windowId": window_id})
        if not resp or not resp.get("ok"):
            raise BrowserError("会话窗口创建失败（Electron 壳控制服务 18767 不可达）")

        marker = f"/static/blank.html?w={window_id}"
        page: Any = None
        deadline = time.time() + 10
        while page is None and time.time() < deadline:
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    if marker in str(pg.url):
                        page = pg
                        break
                if page is not None:
                    break
            if page is None:
                time.sleep(0.2)
        if page is None:
            raise BrowserError("CDP 中未定位到会话窗口（blank 标记页未出现）")

        username = account["username"]
        password = accounts.reveal_password(account_id)
        entry = SessionEntry(account_id=account_id, context=page.context, page=page,
                             started_at=now_ms(), restored=True, headful=True)
        entry.token_capture = autologin.TokenCapture(account_id)
        entry.token_capture.attach(page.context)
        sessions[account_id] = entry
        autologin.install(page)

        # 分区内建持久化：有历史分区即视为"恢复"（无需 storage_state 往返）
        entry.status = "online"
        entry.detail = "恢复持久会话，免密直达首页"
        try:
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            if self._looks_like_login(page):
                entry.status = "logging_in"
                entry.restored = False
                entry.detail = "分区无有效登录态，自动登录中"
                if not autologin.start(page, username, password):
                    entry.status = "error"
                    entry.detail = "自动登录引擎启动失败（页面无登录表单？）"
            else:
                self._focus(entry)
        except Exception as exc:  # noqa: BLE001
            entry.status = "error"
            entry.detail = f"页面加载失败：{exc}"
        return entry.snapshot()

    def _do_session(self, sessions: dict[str, SessionEntry], account_id: str) -> dict[str, Any] | None:
        entry = sessions.get(account_id)
        if entry is None:
            return None
        if entry.page is not None:
            try:
                if entry.page.is_closed():
                    raise RuntimeError("窗口已关闭")
                entry.title = entry.page.title()
            except Exception:  # noqa: BLE001 —— 用户手动关窗 / 崩溃
                BrowserPool._save_state(entry)
                entry.context = None
                entry.page = None
                if entry.status != "stopped":
                    entry.status = "stopped"
                    entry.detail = "窗口已被手动关闭（登录态已保存）"
                return entry.snapshot()
            st = autologin.status(entry.page)
            if st:
                entry.autologin_state = st
                if entry.status == "logging_in" and st.get("phase") == "success":
                    entry.status = "online"
                    entry.detail = ("自动登录完成（登录态已持久化）"
                                    if entry.heal_count == 0
                                    else f"自愈成功（第 {entry.heal_count} 次重登）")
                    self._save_state(entry)
                elif entry.status == "logging_in" and st.get("phase") in {"gave_up", "stopped"}:
                    entry.status = "error"
                    entry.detail = f"自动登录未完成：{st.get('reason', st.get('phase'))}"

            # 运行中自愈（对齐扩展 TokenRenewer）
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

    # ------------------------------------------------------------- 公共 API

    def open_account(self, account_id: str, *, headful: bool = True) -> dict[str, Any]:
        """启动账号会话（默认可见窗口）。已在线会话重复调用 = 聚焦其窗口。"""
        return self._submit(
            lambda pw, browsers, sessions: self._do_open(pw, browsers, sessions, account_id, headful)
        )

    def focus_account(self, account_id: str) -> dict[str, Any] | None:
        return self._submit(
            lambda pw, browsers, sessions: (
                (self._focus(entry) or entry.snapshot())
                if (entry := sessions.get(account_id)) is not None and entry.context is not None
                else None
            )
        )

    def close_account(self, account_id: str) -> bool:
        def job(_pw: Any, _browsers: dict[bool, Any], sessions: dict[str, SessionEntry]) -> bool:
            entry = sessions.get(account_id)
            if entry is None:
                return False
            self._close_entry(entry)
            return True

        return self._submit(job)

    def session(self, account_id: str) -> dict[str, Any] | None:
        return self._submit(
            lambda pw, browsers, sessions: self._do_session(sessions, account_id)
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        def job(_pw: Any, _browsers: dict[bool, Any], sessions: dict[str, SessionEntry]) -> list:
            result = []
            for account_id in list(sessions):
                snap = self._do_session(sessions, account_id)
                if snap:
                    result.append(snap)
            return result

        return self._submit(job)

    def context_cookies(self, account_id: str) -> list[dict[str, Any]]:
        """读取某账号 context 的 cookies（串行线程内执行，供验收用）。"""

        def job(_pw: Any, _browsers: dict[bool, Any], sessions: dict[str, SessionEntry]) -> list:
            entry = sessions.get(account_id)
            if entry is None or entry.context is None:
                return []
            return list(entry.context.cookies())

        return self._submit(job)

    def navigate(self, account_id: str, url: str) -> None:
        """驱动某账号会话的页面导航（验收/自愈触发用）。"""

        def job(_pw: Any, _browsers: dict[bool, Any], sessions: dict[str, SessionEntry]) -> None:
            entry = sessions.get(account_id)
            if entry is not None and entry.page is not None:
                entry.page.goto(url, wait_until="domcontentloaded", timeout=30000)

        self._submit(job)

    def token_of(self, account_id: str) -> str | None:
        def job(_pw: Any, _browsers: dict[bool, Any], sessions: dict[str, SessionEntry]) -> str | None:
            entry = sessions.get(account_id)
            return entry.token_capture.latest() if entry and entry.token_capture else None

        return self._submit(job)

    def close_all(self) -> None:
        def job(_pw: Any, browsers: dict[bool, Any], sessions: dict[str, SessionEntry]) -> None:
            for entry in list(sessions.values()):
                self._close_entry(entry)
            sessions.clear()
            for browser in list(browsers.values()):
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
            browsers.clear()

        self._submit(job)


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
