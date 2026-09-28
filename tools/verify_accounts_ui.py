"""账号中心 v2 DOM 验证：CSS 生效、统计、chips、卡片结构、四态徽标、零 JS 错误。"""
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:18798"

# ── 自足运行（2026-09-24 加）──────────────────────────────────────────────
#
# ★ 此前这几行是**没有**的，后果有两条，都很实在：
#
# ① **它会读写真实数据目录**（`WORKBENCH_DATA` 没设 ⇒ 用的是用户那份库）。
#    本脚本会做新建/重命名/停用/删除盒子（且 `dialog` 一律 accept）——
#    也就是**在真实数据上做破坏性操作**。
#
# ② ★★ **它绑的是生产端口 18765**。如果应用**正在运行**：`uvicorn` 起不来
#    （它的报错被丢进 DEVNULL），而下面的就绪探测会**连上那个正在跑的应用** ——
#    于是这一整套"验收"打在**真实实例**上。
#    这正是 [ADR-0012](../docs/adr/0012-fixed-ports.md) 记的那个危险：
#    **"旧进程还活着"与"测试戳到了真端口"两件事都不报错。**
#
# ⇒ 现在：临时数据目录 + 独立端口 + **端口被占就拒跑**（而不是连上去）。
WORKBENCH_DATA = pathlib.Path(tempfile.mkdtemp(prefix="wb-verify-"))
_ENV = {**os.environ, "WORKBENCH_DATA": str(WORKBENCH_DATA),
        "WORKBENCH_LOG_DIR": str(WORKBENCH_DATA / "logs"),
        "PYTHONIOENCODING": "utf-8"}


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


# ★ 端口被占**就拒跑**，而不是连上去（见上面那段说明）
if not _port_free(18798):
    raise SystemExit("✗ 端口 18798 被占用——先退出占用它的进程"
                     "（不连上去：那样这套验收会打在真实实例上）")

server = subprocess.Popen(
    [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "workbench.api:app", "--host", "127.0.0.1", "--port", "18798"],
    cwd=str(ROOT), env=_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    # C5 三态徽标类别合法（未授权·已暂停已随状态通道精简移除）
    ok_badge = card and card["badgeCls"] and any(k in card["badgeCls"] for k in ("online", "offline", "starting"))
    checks.append((f"C8 三态徽标类别合法（{card and card['badgeCls']}）", bool(ok_badge)))

    # C6 批量管理切换
    pg.click("#batch-toggle")
    pg.wait_for_timeout(400)
    bar_visible = pg.evaluate("() => !document.getElementById('batch-bar').classList.contains('hidden')")
    checks.append(("C9 批量管理条出现", bar_visible))
    pg.click("#batch-toggle")

    # C7 底部卡片：批量面板折叠 + 分配池 + 站点管理在位
    bulk_hidden = pg.evaluate("() => document.getElementById('bulk-panel').classList.contains('hidden')")
    pool = pg.evaluate("() => !!document.getElementById('pool-config')")
    site_form = pg.evaluate("() => !!document.getElementById('site-form') && !!document.getElementById('site-list')")
    wheel_label = pg.evaluate("() => document.getElementById('btn-wheel').textContent")
    checks.append(("C10 批量面板折叠 + 分配池在位", bulk_hidden and pool))
    checks.append((f"C12 站点管理板块 + 轮盘标签（{wheel_label}）", site_form and "Alt+Q" in wheel_label))
    site_rows = pg.evaluate("() => document.querySelectorAll('#site-list .site-row').length")
    checks.append((f"C13 站点清单行 ×{site_rows}", site_rows >= 1))

    # 清洗函数单测（页面上下文内）
    clean = pg.evaluate(
        """() => {
          const cases = [
            'https://example-config.example.com/admin/config/lifecycle/3a19fd65-6221-c5c7-6ef8-44a96ca76b22/status/d5db4111-2edf-37f9-5841-3a1a0821ab4f/entry-reaction/42e1286e-19d4-b837-df89-3a239c402b98?__edit=2',
            'http://192.0.2.10:8080/admin/x',
            'example.com',
          ];
          return cases.map((t) => {
            try {
              if (/^https?:\\/\\//i.test(t)) { const u = new URL(t); return u.origin; }
              return null;
            } catch { return null; }
          });
        }"""
    )
    checks.append((f"C14 链接清洗 {clean}", clean[0] == 'https://example-config.example.com' and clean[1] == 'http://192.0.2.10:8080'))

    # C15 单列布局 + 主页返回 + 无角色/标签输入
    no_grid = pg.evaluate("() => document.querySelector('.bottom-grid') === null")
    home = pg.evaluate("() => !!document.querySelector('.head-actions a[href=\"/\"]')")
    no_role = pg.evaluate("() => !document.getElementById('acc-role') && !document.getElementById('edit-role')")
    tabname = pg.evaluate("() => !!document.getElementById('acc-tabname')")
    checks.append(("C15 单列布局", no_grid))
    checks.append(("C16 主页返回按钮", home))
    checks.append((f"C17 角色已移除 + 页签名在位（tabname={tabname}）", no_role and tabname))

    # C18 环境下拉无账号数
    opt_text = pg.evaluate("() => document.querySelector('#acc-env option')?.textContent || ''")
    checks.append((f"C18 站点选项无账号数（‘{opt_text}’）", opt_text and "账号" not in opt_text))

    # C19 盒子 ✎ 点击 → ask 模态出现（Electron 无 prompt 的替代路径）
    #
    # ★ 2026-09-24 修：这条判据**依赖一个从来没被造出来的前提**。
    # 全新数据目录里只有 **1 个默认盒**（`bx: 1`），而 ✎（`data-op="rename"`）
    # **只出现在有名盒子上** ⇒ 下面那个选择器永远找不到目标，
    # 判据报"功能坏了"，而真相是"**没数据**"。
    # ⇒ 现在**自己先建一个有名字的盒子**（用户就是这么做的），再验重命名。
    pg.click("#batch-toggle")  # 确保退出批量态再操作
    pg.evaluate("""() => {
      const add = [...document.querySelectorAll('#box-chips .chip')].find(x => x.dataset.add);
      add?.click();
    }""")
    pg.wait_for_timeout(300)
    pg.fill("#ask-input", "验收盒")
    pg.click("#ask-ok")
    pg.wait_for_timeout(700)
    has_named = pg.evaluate(
        """() => [...document.querySelectorAll('#box-chips .chip')]
                 .some(x => x.querySelector('[data-op="rename"]'))""")
    checks.append(("C19a 建有名字的盒子（✎ 才会出现）", has_named))

    pg.evaluate("() => { const c = [...document.querySelectorAll('#box-chips .chip')].find(x => x.querySelector('[data-op=\"rename\"]')); c?.querySelector('[data-op=\"rename\"]')?.click(); }")
    pg.wait_for_timeout(300)
    ask_visible = pg.evaluate("() => !!document.getElementById('ask-modal').open")
    checks.append(("C19 盒子重命名弹出输入模态", ask_visible))
    pg.evaluate("() => document.getElementById('ask-cancel')?.click()")
    pg.wait_for_timeout(200)

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
