"""账号中心 v2 DOM 验证：CSS 生效、统计、chips、卡片结构、四态徽标、零 JS 错误。"""
import json
import subprocess
import sys
import time
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

checks = []
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1050})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{BASE}/static/pages/accounts.html", wait_until="networkidle")
    pg.wait_for_timeout(2000)

    # C1 上游 CSS 已生效：品牌彩环是 conic-gradient，卡片有左边条
    ring = pg.evaluate("() => getComputedStyle(document.querySelector('.brand-ring')).backgroundImage")
    checks.append(("C1 ql-theme/ql-parallel CSS 生效（conic-gradient）", "conic-gradient" in (ring or "")))
    before_bar = pg.evaluate("() => { const c = document.querySelector('.account-card'); return c ? getComputedStyle(c, '::before').width : 'none'; }")
    checks.append(("C2 卡片左侧色条 ::before", before_bar not in ("none", "")))

    # C2 统计数字已填充
    stats = pg.evaluate("() => ({a: document.getElementById('stat-accounts').textContent, o: document.getElementById('stat-online').textContent, bx: document.getElementById('stat-boxes').textContent, v: document.getElementById('ver-chip').textContent})")
    checks.append((f"C3 顶栏统计 {stats}", stats["a"] != "0" or stats["bx"] != "0"))
    checks.append((f"C4 版本 chip {stats['v']}", stats["v"].startswith("v0.")))

    # C3 盒子 chips 与账号卡片
    chips = pg.evaluate("() => document.querySelectorAll('#box-chips .chip').length")
    cards = pg.evaluate("() => document.querySelectorAll('#acc-wall .account-card').length")
    checks.append((f"C5 盒子 chips ×{chips}", chips >= 2))
    checks.append((f"C6 账号卡片 ×{cards}", cards >= 1))

    # C4 卡片结构完整性（头像/徽标/主按钮/次操作行）
    card = pg.evaluate(
        """() => {
          const c = document.querySelector('#acc-wall .account-card');
          if (!c) return null;
          return {
            avatar: !!c.querySelector('.ac-avatar'),
            badge: c.querySelector('.badge')?.textContent || '',
            badgeCls: c.querySelector('.badge')?.className || '',
            primary: !!c.querySelector('.btn-primary'),
            acts: c.querySelectorAll('.ac-actions button').length,
            meta: c.querySelector('.alias')?.textContent || '',
          };
        }"""
    )
    checks.append((f"C7 卡片结构 头像={card and card['avatar']} 徽标=‘{card and card['badge']}’ 主按钮={card and card['primary']}", bool(card and card["avatar"] and card["badge"] and card["primary"] and card["acts"] >= 5)))

    # C5 四态徽标类别合法
    ok_badge = card and card["badgeCls"] and any(k in card["badgeCls"] for k in ("online", "offline", "starting", "login_failed"))
    checks.append((f"C8 四态徽标类别合法（{card and card['badgeCls']}）", bool(ok_badge)))

    # C6 批量管理切换
    pg.click("#batch-toggle")
    pg.wait_for_timeout(400)
    bar_visible = pg.evaluate("() => !document.getElementById('batch-bar').classList.contains('hidden')")
    checks.append(("C9 批量管理条出现", bar_visible))
    pg.click("#batch-toggle")

    # C7 底部双卡 + 批量面板折叠
    bulk_hidden = pg.evaluate("() => document.getElementById('bulk-panel').classList.contains('hidden')")
    pool = pg.evaluate("() => !!document.getElementById('pool-config')")
    checks.append(("C10 批量面板默认折叠 + 分配池在位", bulk_hidden and pool))

    checks.append(("C11 零 JS 页面错误", not errors))
    if errors:
        print("JS 错误:", errors[:3])
    b.close()

server.kill()
print()
for name, ok in checks:
    print(("✔" if ok else "✘"), name)
passed = sum(1 for _, ok in checks if ok)
print(f"\nUI_CHECKS: {passed}/{len(checks)}")
sys.exit(0 if passed == len(checks) else 1)
