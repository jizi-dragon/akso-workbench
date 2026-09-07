"""自动登录引擎机器验收（quick-login tmp/verify-fillrhythm.mjs 的 9 项断言等价迁移）。

台架：本地 http.server 模拟 eGMP 登录页（antd 风格 + srcdoc iframe 密码框），
场景由用户名首字母区分（与原验收一致）：
  A 齐备门槛    密码 iframe 延迟 2.5s 挂载
  B 清值自愈    首击强制 401；4s 后页面脚本清空密码
  C 用户接管    首击强制 401；观察期内测试代码做 trusted 点击接管
  D 失败让位    连续 2 次强制 401 → errorSeen 达 2 → 停

9 项断言：
  A1 首 POST > 密码挂载时刻   A2 提交时密码完整     A3 成功且无重复提交
  B1 清值后重填再提交成功     B2 重试克制（≤3）
  C1 用户接管有效（恰 2 次 [false,true]）           C2 接管后无追加自动重试
  D1 失败感知停止（≤2）       D2 停止后 4s 内不再重试

需要 playwright chromium（未安装则整组 skip）。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from workbench.services import autologin

PW123 = "pw123"

_LOGIN_PAGE = """<!doctype html>
<html><body>
<form onsubmit="return false">
  <input placeholder="请输入用户名" type="text" />
  <iframe id="pwbox" srcdoc="<span>loading</span>"></iframe>
  <button type="button">登 录</button>
</form>
<script>
const scenario = new URLSearchParams(location.search).get('scenario') || 'a';
const iframe = document.getElementById('pwbox');
function mountPw() {
  iframe.srcdoc = '<input type="password" placeholder="请输入密码">';
  fetch('/api/pw-mounted?u=' + encodeURIComponent(scenario));
  if (scenario === 'b') setTimeout(() => {
    const f = iframe.contentDocument.querySelector('input');
    if (f) f.value = '';
  }, 4000);
}
if (scenario === 'a') setTimeout(mountPw, 2500); else mountPw();
document.querySelector('button').addEventListener('click', () => {
  const u = document.querySelector('input[type=text]').value;
  const f = iframe.contentDocument.querySelector('input');
  const p = f ? f.value : '';
  fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({u: u, p: p})}).then(r => {
    if (r.status === 200) { document.querySelector('button').remove(); }
    else {
      const d = document.createElement('div');
      d.className = 'ant-message-error'; d.textContent = '账号或密码错误';
      document.body.appendChild(d);
    }
  });
});
</script>
</body></html>"""


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.log: list[dict] = []          # {u,p,t,ok}
        self.pw_mounted: dict[str, float] = {}
        self.attempts: dict[str, int] = {}

    def reset(self) -> None:
        with self.lock:
            self.log.clear()
            self.pw_mounted.clear()
            self.attempts.clear()


STATE = _State()


def _decide(scenario: str) -> int:
    """返回 HTTP 状态码（服务端拒绝策略，与原验收一致）。"""
    with STATE.lock:
        n = STATE.attempts.get(scenario, 0)
        STATE.attempts[scenario] = n + 1
    if scenario == "d" and n < 2:
        return 401
    if scenario in {"b", "c"} and n < 1:
        return 401
    return 200


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # noqa: N802 —— 静默
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            self._send(200, _LOGIN_PAGE.encode("utf-8"))
        elif parsed.path == "/api/pw-mounted":
            scenario = (parse_qs(parsed.query).get("u") or ["a"])[0]
            with STATE.lock:
                STATE.pw_mounted[scenario] = time.time()
            self._send(200, b"{}")
        elif parsed.path == "/__log":
            with STATE.lock:
                payload = {"log": list(STATE.log), "pwMounted": dict(STATE.pw_mounted)}
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
        else:
            self._send(404, b"not found")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/login":
            self._send(404, b"not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = {}
        scenario = str(data.get("u", "?"))[:1]
        code = _decide(scenario)
        if code == 200 and str(data.get("p")) != PW123:
            code = 401
        with STATE.lock:
            STATE.log.append({"u": data.get("u"), "p": data.get("p"),
                              "t": time.time(), "ok": code == 200})
        self._send(code, b'{"code":0}' if code == 200 else b'{"code":401}',
                   "application/json")


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def chromium():
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
    except ImportError:
        pytest.skip("playwright 未安装")
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:  # noqa: BLE001
        pw.stop()
        pytest.skip(f"chromium 未安装（playwright install chromium）：{exc}")
    yield browser
    browser.close()
    pw.stop()


def _fetch_log(page) -> dict:
    return page.evaluate("async () => await (await fetch('/__log')).json()")


def _run_scenario(chromium, base_url: str, scenario: str, timeout_s: float = 45.0):
    STATE.reset()
    context = chromium.new_context()
    page = context.new_page()
    autologin.install(page)
    page.goto(f"{base_url}/login?scenario={scenario}", wait_until="domcontentloaded")
    assert autologin.start(page, f"{scenario}-user", PW123)
    final = autologin.wait_terminal(page, timeout_s=timeout_s)
    return context, page, final


def test_a_readiness_gate(chromium, server) -> None:
    """A1/A2/A3：密码未就绪绝不提交；提交时密码完整；成功且无重复提交。"""
    context, page, final = _run_scenario(chromium, server, "a")
    try:
        data = _fetch_log(page)
        log, mounted = data["log"], data["pwMounted"].get("a")
        assert log, "应有提交记录"
        first = log[0]
        assert mounted is not None and first["t"] > mounted, "A1: 首 POST 必须晚于密码挂载"
        assert first["p"] == PW123, "A2: 提交时密码必须完整"
        assert len(log) == 1 and log[0]["ok"], "A3: 登录成功且无重复提交"
        assert final["phase"] == "success"
    finally:
        context.close()


def test_b_self_heal_after_clear(chromium, server) -> None:
    """B1/B2：中途清值后重填再提交成功；重试克制（≤3）。"""
    context, page, _ = _run_scenario(chromium, server, "b")
    try:
        data = _fetch_log(page)
        log = data["log"]
        assert len(log) >= 2, "B: 应经历 401 → 清值 → 重填 → 成功"
        assert not log[0]["ok"], "B: 首击应被 401"
        assert log[-1]["ok"] and log[-1]["p"] == PW123, "B1: 末次提交成功且密码完整"
        assert len(log) <= 3, "B2: 重试克制"
    finally:
        context.close()


def test_c_user_takeover(chromium, server) -> None:
    """C1/C2：用户 trusted 点击接管有效；接管后引擎让位无追加自动重试。

    注意：不能先等引擎终态再接管——观察期后引擎会自己二次点击并成功。
    必须在检测到首击（401）后、3500ms 观察期内完成 trusted 点击。
    """
    STATE.reset()
    context = chromium.new_context()
    page = context.new_page()
    try:
        autologin.install(page)
        page.goto(f"{server}/login?scenario=c", wait_until="domcontentloaded")
        assert autologin.start(page, "c-user", PW123)

        deadline = time.monotonic() + 20
        data = {"log": [], "pwMounted": {}}
        while time.monotonic() < deadline:
            data = _fetch_log(page)
            if data["log"]:
                break
            time.sleep(0.08)
        assert data["log"] and not data["log"][0]["ok"], "C: 引擎首击应被 401"

        page.click("button", force=True)  # trusted 点击 → 接管
        final = autologin.wait_terminal(page, timeout_s=10.0)
        assert final["phase"] == "stopped" and final["userTouched"], "C: 引擎应让位"
        time.sleep(4.0)  # C2：观察 4s，确认无追加自动重试
        data = _fetch_log(page)
        log = data["log"]
        assert len(log) == 2, f"C1/C2: 应恰 2 次提交（实测 {len(log)}）"
        assert [entry["ok"] for entry in log] == [False, True], "C1: 结果序列 [false, true]"
    finally:
        context.close()


def test_d_give_up_on_errors(chromium, server) -> None:
    """D1/D2：失败感知让位（2 次 401 后停止）；停止后不再重试。"""
    context, page, final = _run_scenario(chromium, server, "d")
    try:
        assert final["phase"] == "gave_up" and final["reason"] == "errors_limit"
        data = _fetch_log(page)
        log = data["log"]
        assert len(log) <= 2, f"D1: 失败感知停止（实测 {len(log)}）"
        time.sleep(4.0)
        data = _fetch_log(page)
        assert len(data["log"]) == len(log), "D2: 停止后不再重试"
    finally:
        context.close()
