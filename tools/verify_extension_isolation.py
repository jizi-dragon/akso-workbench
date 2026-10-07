"""扩展隔离面 + 自动填表的定向回归（自建 fixture 页，装载 dist 内容脚本产物）。

覆盖两个**曾在真机上静默失效**的缺陷（2026-09-11 修复）：
  A. MAIN 壳的 `Storage.prototype.clear` 补丁未区分存储实例 → 页面调 `sessionStorage.clear()`
     会把住在 localStorage 命名空间的「虚拟 Cookie 袋」（含 token）一并清空 =
     无关操作销毁账号登录态；
  B. `auto-login.fillPasswordInIframes` 在第一个可访问 iframe 上就 `return Boolean(field)` →
     多 iframe 页面（第一个 iframe 不含密码框）自动填表静默失效，永不点提交。

本脚本用「红/绿」方式工作：断言里带 ★ 的两条就是上述缺陷的回归点——
修复前必 FAIL，修复后应全绿。

用法：
    .venv\\Scripts\\python.exe tools\\verify_extension_isolation.py
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socketserver
import sys
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "extensions" / "quick-login" / "dist" / "content"
FIX = Path(os.environ.get("TEMP", "/tmp")) / "ql_isolation_fixture"
PORT = int(os.environ.get("QL_FIXTURE_PORT", "18997"))

AUTOLOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>fixture</title>
<script>
// background 桩：auto-login 经 sb:autoLoginRequest 拉取本次凭证
window.chrome = {
  runtime: {
    sendMessage: async (m) => (m && m.type === 'sb:autoLoginRequest' ? { username: 'u1', password: 'p1' } : undefined),
    onMessage: { addListener: () => {} },
  },
};
</script></head><body>
  <input id="user" type="text" placeholder="请输入用户名">
  <!-- 提交按钮必须在顶层：findSubmit() 只扫顶层文档 -->
  <button id="loginBtn" onclick="window.__clicked=(window.__clicked||0)+1">登录</button>
  <!-- iframe A：可访问但没有密码框（缺陷 B 的触发点） -->
  <iframe id="decoy" srcdoc="&lt;input type='text' placeholder='搜索'&gt;"></iframe>
  <!-- iframe B：真正的登录表单 -->
  <iframe id="real" srcdoc="&lt;input type='password' id='pw'&gt;"></iframe>
  <script src="auto-login.js"></script>
</body></html>
"""

SHIELD_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>fixture</title>
<script>
// document_start：在任何补丁之前抓住原始 Storage API，并驱动壳激活
(() => {
  const origGet = Storage.prototype.getItem;
  window.__probe = { bagRaw: () => origGet.call(localStorage, '__ql_ns_acc1____ql_cookies__') };
  window.__activated = () => window.__probe.bagRaw() !== null;
  // 模拟桥下行 bind（含袋权威视图）：每 250ms 重发直到激活（跨越 BOOT_GUARD 的一次 reload）
  let n = 0;
  const t = setInterval(() => {
    window.postMessage({ src: 'QL_BRIDGE_TO_PAGE', payload: {
      op: 'bind', accountId: 'acc1', tabId: 7,
      seed: { '__auth_token__': 'T0K' }, bag: { sid: 'BAGVAL' },
    } }, '*');
    if (++n > 40 || window.__activated()) clearInterval(t);
  }, 250);
})();
</script></head><body>
  <h1>shield fixture</h1>
  <script src="shield-main.js"></script>
</body></html>
"""


#: 缺陷②的夹具：**提交按钮文案不在词表里**（`findSubmit()` 只认 登录/login/submit），
#: 但页面有口令框 ⇒ 修复后应「照填、不自动提交」；修复前**一个字都不填**。
SUBMIT_LABEL_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>fixture</title>
<script>
window.chrome = {
  runtime: {
    sendMessage: async (m) => (m && m.type === 'sb:autoLoginRequest' ? { username: 'u1', password: 'p1' } : undefined),
    onMessage: { addListener: () => {} },
  },
};
</script></head><body>
  <!-- 没有任何 <form>：与"改密码页 / 二次校验页"的形态一致 -->
  <input id="user" type="text" placeholder="请输入用户名">
  <input id="pw" type="password">
  <button id="submitBtn" onclick="window.__clicked=(window.__clicked||0)+1">提交</button>
  <script src="auto-login.js"></script>
</body></html>
"""

#: 缺陷③的夹具：`sb:autoLoginRequest` **第一次返回 null**、第二次才给凭据。
#: `par.open` 把待取凭据写进 storage 是在 `tabs.create` 之后（中间隔着 PBKDF2 十万轮解密），
#: 而本机页几毫秒就到 `document_idle` ⇒ 真机上那一次索取就是拿到 null。
NULL_FIRST_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>fixture</title>
<script>
let n = 0;
window.__claims = 0;
window.chrome = {
  runtime: {
    sendMessage: async (m) => {
      if (!m || m.type !== 'sb:autoLoginRequest') return undefined;
      window.__claims = ++n;
      return n === 1 ? null : { username: 'u1', password: 'p1' };
    },
    onMessage: { addListener: () => {} },
  },
};
</script></head><body>
  <input id="user" type="text" placeholder="请输入用户名">
  <button id="loginBtn" onclick="window.__clicked=(window.__clicked||0)+1">登录</button>
  <iframe id="real" srcdoc="&lt;input type='password' id='pw'&gt;"></iframe>
  <script src="auto-login.js"></script>
</body></html>
"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: ANN002, ANN003
        pass


def _serve() -> socketserver.TCPServer:
    handler = functools.partial(_Quiet, directory=str(FIX))
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    if not (DIST / "shield-main.js").exists() or not (DIST / "auto-login.js").exists():
        print(f"未找到构建产物：{DIST}（先跑 `cd extensions/quick-login && npm run build`）")
        return 2

    FIX.mkdir(parents=True, exist_ok=True)
    for name, html in (
        ("autologin.html", AUTOLOGIN_HTML),
        ("shield.html", SHIELD_HTML),
        ("submitlabel.html", SUBMIT_LABEL_HTML),
        ("nullfirst.html", NULL_FIRST_HTML),
    ):
        (FIX / name).write_text(html, encoding="utf-8")
    for art in ("auto-login.js", "shield-main.js"):
        shutil.copy2(DIST / art, FIX / art)

    httpd = _serve()
    from playwright.sync_api import sync_playwright

    results: list[tuple[str, bool, str]] = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)

        # ---------- A：Cookie 袋不得被 sessionStorage.clear() 清空 ----------
        pg = b.new_page()
        pg.goto(f"http://127.0.0.1:{PORT}/shield.html", wait_until="load")
        pg.wait_for_function("() => window.__activated && window.__activated()", timeout=15000)
        bag0 = pg.evaluate("() => window.__probe.bagRaw()")
        results.append(("A1 壳激活且袋按 bag 视图落盘", bag0 is not None, str(bag0)[:60]))
        results.append(("A2 袋内含种子值 BAGVAL", "BAGVAL" in (bag0 or ""), ""))
        pg.evaluate("() => sessionStorage.clear()")
        bag1 = pg.evaluate("() => window.__probe.bagRaw()")
        results.append((
            "A3 ★ sessionStorage.clear() 后袋仍在（缺陷 A 回归点）",
            bag1 is not None and "BAGVAL" in bag1,
            f"袋={str(bag1)[:60]}",
        ))
        pg.evaluate("() => localStorage.clear()")
        bag2 = pg.evaluate("() => window.__probe.bagRaw()")
        results.append((
            "A4 localStorage.clear() 仍清空袋（原语义未变）",
            bag2 is None or json.loads(bag2) == {},
            str(bag2)[:40],
        ))
        pg.close()

        # ---------- B：多 iframe 下密码必须填进真正的表单 ----------
        pg2 = b.new_page()
        errs: list[str] = []
        pg2.on("pageerror", lambda e: errs.append(str(e)))
        pg2.goto(f"http://127.0.0.1:{PORT}/autologin.html", wait_until="load")
        pg2.wait_for_timeout(3000)  # requestCredentials → start → attempt（轮询 800ms）
        got = pg2.evaluate(
            """() => {
                 const real = document.getElementById('real');
                 const pw = real.contentDocument.querySelector("input[type='password']");
                 const decoy = document.getElementById('decoy');
                 return {
                   user: document.getElementById('user').value,
                   pw: pw ? pw.value : null,
                   decoyHasPw: !!decoy.contentDocument.querySelector("input[type='password']"),
                 };
               }"""
        )
        results.append(("B1 用户名已填（对照组）", got["user"] == "u1", str(got)))
        results.append(("B2 诱饵 iframe 确无密码框（构造成立）", got["decoyHasPw"] is False, ""))
        results.append((
            "B3 ★ 第 2 个 iframe 的密码已填（缺陷 B 回归点）",
            got["pw"] == "p1",
            f"实际={got['pw']!r}",
        ))
        results.append(("B4 零 JS 错误", not errs, str(errs[:2])))
        pg2.close()

        # ---------- C：提交按钮识别不出时「照填但不自动提交」（缺陷 ② 的回归点）----------
        #
        # ★ 修复前：`attempt()` 在 `if (!submit) { … return; }` 处**在填表之前**返回
        #   ⇒ 这一页一个字都不填，而且**不报任何错**（用户看到"点了登录没反应"）。
        # ★ 修复后：只要页面有口令框就照填；识别不出按钮 ⇒ 走不到点击那一步。
        pg3 = b.new_page()
        errs3: list[str] = []
        pg3.on("pageerror", lambda e: errs3.append(str(e)))
        pg3.goto(f"http://127.0.0.1:{PORT}/submitlabel.html", wait_until="load")
        pg3.wait_for_timeout(3000)  # requestCredentials → 800ms 轮询
        got3 = pg3.evaluate(
            """() => ({
                 user: document.getElementById('user').value,
                 pw: document.getElementById('pw').value,
                 clicked: window.__clicked || 0,
               })"""
        )
        results.append((
            "C1 ★ 按钮文案不在词表里也照填用户名（缺陷 ② 回归点）",
            got3["user"] == "u1",
            f"实际={got3['user']!r}",
        ))
        # ★ 这里**不是**产品缺陷，是它的边界：没有 `<form>` 时 `ownPasswordField(uField)`
        #   找不到"哪个口令框属于这个用户名" ⇒ **刻意不猜**（与 A9「两个无 form 的口令框
        #   ⇒ 口令不猜」同一条纪律）。第一版判据把它写成"口令也该填"，**是判据错了**。
        results.append((
            "C2 无 form 时**刻意不猜口令**（用户名照填、口令留空）",
            got3["user"] == "u1" and got3["pw"] == "",
            f"用户名={got3['user']!r} 口令={got3['pw']!r}",
        ))
        results.append((
            "C3 识别不出提交按钮 ⇒ **不自动提交**（填了但把最后一下留给用户）",
            got3["clicked"] == 0,
            f"点击次数={got3['clicked']}",
        ))
        results.append(("C4 零 JS 错误", not errs3, str(errs3[:2])))
        pg3.close()

        # ---------- D：`null` 响应必须重试（缺陷 ③ 的回归点）----------
        #
        # ★ 修复前：只在**抛异常**时重试 ⇒ 第一次拿到 null 就结束 ⇒ 这一页永远不填。
        pg4 = b.new_page()
        errs4: list[str] = []
        pg4.on("pageerror", lambda e: errs4.append(str(e)))
        pg4.goto(f"http://127.0.0.1:{PORT}/nullfirst.html", wait_until="load")
        pg4.wait_for_timeout(4000)  # 第一次 null → 等重试（常量 400ms）
        got4 = pg4.evaluate(
            """() => {
                 const real = document.getElementById('real');
                 const pw = real.contentDocument.querySelector("input[type='password']");
                 return {
                   user: document.getElementById('user').value,
                   pw: pw ? pw.value : null,
                   claims: window.__claims,
                 };
               }"""
        )
        results.append((
            "D1 ★ 第一次索取拿到 null 也会重试（缺陷 ③ 回归点）",
            got4["claims"] >= 2,
            f"索取次数={got4['claims']}",
        ))
        results.append((
            "D2 ★ 重试后凭据到手 ⇒ 用户名被填",
            got4["user"] == "u1",
            f"实际={got4['user']!r}",
        ))
        results.append((
            "D3 重试后口令也填进 iframe",
            got4["pw"] == "p1",
            f"实际={got4['pw']!r}",
        ))
        results.append(("D4 零 JS 错误", not errs4, str(errs4[:2])))
        pg4.close()
        b.close()
    httpd.shutdown()
    shutil.rmtree(FIX, ignore_errors=True)

    print()
    passed = 0
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))
        passed += bool(ok)
    print(f"\nISOLATION_CHECKS: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
