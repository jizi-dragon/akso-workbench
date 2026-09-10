"""Electron 轮盘页渲染验证：SVG 扇区、Hub、动画、指纹防重绘、零 JS 错误。"""
import subprocess
import sys
import time
import json
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:18765"

server = subprocess.Popen(
    [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "workbench.api:app", "--host", "127.0.0.1", "--port", "18765"],
    cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(40):
    try:
        urllib.request.urlopen(BASE + "/", timeout=2)
        break
    except Exception:
        time.sleep(0.4)

from playwright.sync_api import sync_playwright

responses = []

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 560, "height": 640})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("response", lambda r: responses.append({"url": r.url[-40:], "status": r.status}) if "/api/" in r.url else None)
    pg.goto(f"{BASE}/static/pages/wheel-picker.html", wait_until="networkidle")
    pg.wait_for_timeout(1500)

    checks = []
    svg = pg.evaluate("() => !!document.querySelector('#wheel-root svg')")
    sectors = pg.evaluate("() => document.querySelectorAll('g.sector').length")
    labels = pg.evaluate("() => [...document.querySelectorAll('text.sector-label')].map((t) => t.textContent)")
    hub = pg.evaluate("() => document.querySelector('text.hub-page')?.textContent || ''")
    anim = pg.evaluate("() => { const g = document.querySelector('g.sector'); return g ? getComputedStyle(g).animationName : 'none'; }")
    checks.append(("W1 SVG 渲染", svg))
    checks.append((f"W2 扇区 {sectors} 个（{labels}）", sectors >= 1))
    checks.append((f"W3 Hub 页码 '{hub}'", bool(hub)))
    checks.append((f"W4 入场动画生效（{anim}）", anim == "sector-in"))

    root_dump = pg.evaluate("() => document.getElementById('wheel-root').innerHTML.slice(0, 200)")

    # 指纹防重绘：标记当前首个扇区节点，等 4s（跨一次 3s 轮询）后确认节点未被重建
    marked = pg.evaluate("() => { const g = document.querySelector('g.sector'); if (!g) return false; g.dataset.mark = 'keep'; return true; }")
    pg.wait_for_timeout(4000)
    same = pg.evaluate("() => document.querySelector('g.sector')?.dataset.mark === 'keep'")
    resp_dump = json.dumps(responses[-2:], ensure_ascii=False)[:200]
    checks.append((f"W4b root 内容: {root_dump[:80]}", bool(marked)))
    checks.append((f"W4c 网络响应: {resp_dump}", True))
    checks.append(("W5 指纹防重绘（3s 轮询未重建 DOM）", bool(marked and same)))

    # W7 视觉优化：无外圈描边盘 + 右侧 150° 分段渐变轨道 + 极淡投影
    ring = pg.evaluate("() => !!document.querySelector('#wheel-root svg circle[r=\"262\"]')")
    segs = pg.evaluate(
        """() => {
          const paths = [...document.querySelectorAll('#wheel-root svg path.box-track')];
          if (!paths.length) return {n: 0};
          const strokes = paths.map((p) => p.getAttribute('stroke'));
          const xs = paths.map((p) => +p.getAttribute('d').match(/M (-?[\\d.]+)/)[1]);
          return {n: paths.length, grad: strokes[0] !== strokes[strokes.length - 1],
                  allRight: xs.every((x) => x >= 260), sw: paths[0].getAttribute('stroke-width')};
        }"""
    )
    shadow = pg.evaluate("() => getComputedStyle(document.querySelector('.sector-svg')).filter")
    checks.append(("W7a 外圈描边盘已移除", not ring))
    checks.append((f"W7b 轨道分段渐变 {segs}", segs.get("n", 0) >= 24 and segs.get("grad") and segs.get("allRight")))
    checks.append((f"W7c 轨道线宽 {segs.get('sw')} + 极淡投影（{shadow[:44]}）",
                   segs.get("sw") == "9" and "drop-shadow" in shadow))

    checks.append(("W6 零 JS 错误", not errs))
    if errs:
        print("JS 错误:", errs[:3])
    b.close()

server.kill()
print()
for name, ok in checks:
    print(("✔" if ok else "✘"), name)
passed = sum(1 for _, ok in checks if ok)
print(f"\nWHEEL_CHECKS: {passed}/{len(checks)}")
sys.exit(0 if passed == len(checks) else 1)
