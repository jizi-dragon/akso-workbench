"""多账号并行托管的机器验收（P0）。

验证效果链：两个账号同时启动 → 各自独立 context 自动登录 → 同时在线互不干扰
→ 关闭后登录态落盘 → 重开免密直达（引擎 phase=idle）→ 服务端会话失效 + 页面导航
→ 运行中自愈重登（heal_count=1，对齐扩展 TokenRenewer 语义）。

载体：本地 http.server 模拟平台（服务端会话 cookie；首页/登录页由会话状态决定，
对齐真实平台"免密直达 vs 踢回登录页"的行为）。需要 chromium，否则整组 skip。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest

from workbench.services import accounts as accounts_svc
from workbench.services.browser_pool import BrowserPool

PW123 = "pw123"

_PAGE = """<!doctype html>
<html><body id="b"><div class="chat-loading"><span class="spin">◌</span> boot…</div></body>
<script>
function showLogin() {
  document.getElementById('b').innerHTML = `
    <form onsubmit="return false">
      <input placeholder="请输入用户名" type="text" />
      <iframe id="pwbox" srcdoc="<input type='password' placeholder='请输入密码'>"></iframe>
      <button type="button">登 录</button>
    </form>`;
  document.querySelector('button').addEventListener('click', () => {
    const u = document.querySelector('input[type=text]').value;
    const f = document.querySelector('#pwbox').contentDocument.querySelector('input');
    const p = f ? f.value : '';
    fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({u, p})}).then(r => r.json()).then(j => {
        if (j.code === 0) showHome(); else {
          const d = document.createElement('div');
          d.className = 'ant-message-error'; d.textContent = '登录失败';
          document.body.appendChild(d);
        }
      });
  });
}
function showHome() {
  document.getElementById('b').innerHTML = '<h1 id="home">平台首页</h1>';
}
async function boot() {
  try {
    const j = await (await fetch('/api/whoami')).json();
    if (j.ok) { showHome(); return; }
  } catch (e) { /* fallthrough */ }
  showLogin();
}
boot();
</script></html>"""


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sessions: dict[str, str] = {}  # sid → username


STATE = _State()


def _sid_of(handler: BaseHTTPRequestHandler) -> str:
    for part in (handler.headers.get("Cookie") or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == "sid":
            return value
    return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # noqa: N802
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              cookie: str | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/login"}:
            self._send(200, _PAGE.encode("utf-8"))
        elif path == "/api/whoami":
            sid = _sid_of(self)
            with STATE.lock:
                ok = sid in STATE.sessions
            self._send(200, json.dumps({"ok": ok}).encode(), "application/json")
        elif path == "/api/expire":
            self._send(200, b'{"ok": true}', "application/json")
        else:
            self._send(404, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/login":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                data = {}
            if str(data.get("p")) != PW123 or not str(data.get("u", "")).strip():
                self._send(401, b'{"code":401}', "application/json")
                return
            sid = uuid.uuid4().hex
            with STATE.lock:
                STATE.sessions[sid] = str(data["u"])
            self._send(200, b'{"code":0}', "application/json", cookie=f"sid={sid}; Path=/")
        elif path == "/api/expire":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                data = {}
            username = str(data.get("username") or "")
            with STATE.lock:
                STATE.sessions = {k: v for k, v in STATE.sessions.items() if v != username}
            self._send(200, b'{"ok": true}', "application/json")
        else:
            self._send(404, b"not found")


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def pool_two(server):
    """两个账号的托管池：a/b 各自独立 context，自动登录到模拟平台。

    注意：Pool 自持 sync Playwright（与生产一致）；同线程只允许一个
    sync 实例，因此本文件不再单独起 chromium 夹具。
    """
    env = accounts_svc.create_env(name=f"并行验收-{uuid.uuid4().hex[:6]}", base_url=server)
    accounts_svc.create_account(env_id=env["id"], username="par-a", password=PW123)
    accounts_svc.create_account(env_id=env["id"], username="par-b", password=PW123)
    ids = [a["id"] for a in accounts_svc.list_accounts(env_id=env["id"])]
    pool = BrowserPool()
    yield pool, ids, server
    pool.close_all()


def _wait_online(pool: BrowserPool, account_id: str, timeout_s: float = 45.0) -> dict:
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = pool.session(account_id) or {}
        if last.get("status") == "online":
            return last
        if last.get("status") == "error":
            break
        time.sleep(0.4)
    return last


def test_parallel_two_accounts_online_independently(pool_two) -> None:
    """P0 核心：两个账号同时在线、context 独立、cookie 互不串号。"""
    pool, ids, _server = pool_two
    pool.open_account(ids[0], headful=False)
    pool.open_account(ids[1], headful=False)
    snap_a = _wait_online(pool, ids[0])
    snap_b = _wait_online(pool, ids[1])
    assert snap_a["status"] == "online", snap_a
    assert snap_b["status"] == "online", snap_b
    assert snap_a["headful"] is False and snap_b["headful"] is False
    # 同时在线：两个 context 均存活
    assert pool._sessions[ids[0]].context is not None
    assert pool._sessions[ids[1]].context is not None

    cookies_a = {(c["name"], c["value"]) for c in pool._sessions[ids[0]].context.cookies()}
    cookies_b = {(c["name"], c["value"]) for c in pool._sessions[ids[1]].context.cookies()}
    sids_a = {v for k, v in cookies_a if k == "sid"}
    sids_b = {v for k, v in cookies_b if k == "sid"}
    assert sids_a and sids_b and sids_a != sids_b, "两个账号必须持有不同会话（不串号）"


def test_focus_online_session(pool_two) -> None:
    """在线会话重复打开/聚焦：返回 reused 快照且不报错（headless 下 bring_to_front 为无害空操作）。"""
    pool, ids, _server = pool_two
    snap = pool.open_account(ids[0], headful=False)
    assert snap.get("reused") is True and snap["status"] == "online"
    focused = pool.focus_account(ids[0])
    assert focused is not None and focused["account_id"] == ids[0]


def test_persistence_reopen_without_autologin(pool_two) -> None:
    """关闭重开：storage_state 恢复 → 免密直达（引擎 phase 保持 idle）。"""
    pool, ids, _server = pool_two
    for account_id in ids:
        pool.close_account(account_id)
    for account_id in ids:
        assert BrowserPool.has_saved_session(account_id), "关闭时应已落盘登录态"

    for account_id in ids:
        snap = pool.open_account(account_id, headful=False)
        assert snap["restored"] is True, snap
        deadline = time.monotonic() + 15
        last: dict = {}
        while time.monotonic() < deadline:
            last = pool.session(account_id) or {}
            if last.get("status") == "online":
                break
            time.sleep(0.3)
        assert last.get("status") == "online", last
        al = last.get("autologin") or {}
        assert al.get("phase") in {"idle", None}, f"重开不应再走登录引擎：{al}"
        assert "免密" in (last.get("detail") or ""), last


def test_running_session_self_heal(pool_two) -> None:
    """运行中自愈：服务端会话清空 + 页面导航到登录页 → 轮询自动重登（heal_count=1）。"""
    pool, ids, server = pool_two
    account_id = ids[0]
    username = accounts_svc.get_account(account_id)["username"]

    # 模拟平台侧会话过期
    pool._sessions[account_id].context.request.post(
        f"{server}/api/expire", data=json.dumps({"username": username}),
        headers={"Content-Type": "application/json"},
    )
    # 用户下一次点击 → 导航 → 平台判定未登录 → 登录页
    entry = pool._sessions[account_id]
    entry.page.goto(f"{server}/", wait_until="domcontentloaded")

    deadline = time.monotonic() + 45
    snap: dict = {}
    healed = False
    while time.monotonic() < deadline:
        snap = pool.session(account_id) or {}
        if snap.get("status") == "online" and snap.get("heal_count", 0) >= 1:
            healed = True
            break
        if snap.get("status") == "error" and "反复失效" in (snap.get("detail") or ""):
            break
        time.sleep(0.5)
    assert healed, f"运行中自愈未完成：{snap}"
