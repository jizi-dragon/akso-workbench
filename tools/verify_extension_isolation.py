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
    for name, html in (("autologin.html", AUTOLOGIN_HTML), ("shield.html", SHIELD_HTML)):
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
