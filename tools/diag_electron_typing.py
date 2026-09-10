"""Electron 真机复现：连 CDP → 账号中心 → 新建盒模态 → 真实键盘输入。"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:18766")
    ctx = b.contexts[0]
    pages = [p for p in ctx.pages if "accounts.html" in p.url]
    print("候选页面:", [p.url[:60] for p in ctx.pages])
    if pages:
        pg = pages[0]
    else:
        pg = next((p for p in ctx.pages if p.url.startswith("http://127.0.0.1:18765")), ctx.pages[0])
        pg.goto("http://127.0.0.1:18765/static/pages/accounts.html", wait_until="networkidle")
    pg.bring_to_front()
    pg.wait_for_timeout(1200)

    # 打开新建盒模态
    pg.click('.chip[data-add="1"]')
    pg.wait_for_timeout(400)
    visible = pg.evaluate("() => !document.getElementById('ask-modal').classList.contains('hidden')")
    print("模态可见:", visible)

    # 真实键盘输入（CDP key events，非 fill）
    pg.focus("#ask-input")
    pg.keyboard.type("真机键盘", delay=40)
    pg.wait_for_timeout(300)
    value = pg.evaluate("() => document.getElementById('ask-input').value")
    active = pg.evaluate("() => document.activeElement?.id || document.activeElement?.tagName")
    print(f"输入值: {value!r} | 焦点: {active}")

    # 若输入成功则走完整流程建盒再清理
    if value == "真机键盘":
        pg.click("#ask-ok")
        pg.wait_for_timeout(800)
        chips = pg.evaluate("() => [...document.querySelectorAll('#box-chips .chip')].map(c => c.dataset.box)")
        print("建盒后 chips:", chips)
        # 清理：删除该盒
        pg.evaluate(
            """() => new Promise((res) => {
              window.__dlg = [];
              page_dialog = null;
              res(1);
            })"""
        )
        pg.on("dialog", lambda d: d.accept())
        pg.evaluate(
            """() => {
              const c = [...document.querySelectorAll('#box-chips .chip')].find(x => x.dataset.box === '真机键盘');
              c?.querySelector('[data-op="del"]')?.click();
            }"""
        )
        pg.wait_for_timeout(500)
        pg.evaluate("() => document.querySelector('#ask-modal')?.classList.add('hidden')")
    b.close()
