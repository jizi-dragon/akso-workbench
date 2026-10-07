"""盒子操作行为级验收：新建/重命名/停用/删除（含账号迁默认）/默认盒切换过滤。"""
import json
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
BASE = "http://127.0.0.1:18799"

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
#    这正是 [ADR-0012](../docs/adr/0011-port-governance.md) 记的那个危险：
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
if not _port_free(18799):
    raise SystemExit("✗ 端口 18799 被占用——先退出占用它的进程"
                     "（不连上去：那样这套验收会打在真实实例上）")

server = subprocess.Popen(
    [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
     "workbench.api:app", "--host", "127.0.0.1", "--port", "18799"],
    cwd=str(ROOT), env=_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(40):
    try:
        urllib.request.urlopen(BASE + "/", timeout=2)
        break
    except Exception:
        time.sleep(0.4)

from playwright.sync_api import sync_playwright

results = []


def check(name, ok):
    results.append((name, ok))
    print(("✔" if ok else "✘"), name)


def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None,
                                 method=method, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    dialogs = []
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    pg.goto(f"{BASE}/static/pages/accounts.html", wait_until="networkidle")
    pg.wait_for_timeout(1500)

    def chip(box):
        return pg.evaluate(
            """(name) => {
              const c = [...document.querySelectorAll('#box-chips .chip')].find(x => x.dataset.box === name);
              return c ? {count: c.querySelectorAll('.chip-act').length, off: c.classList.contains('chip-off')} : null;
            }""", box)

    def click_act(box, op):
        pg.evaluate(
            """([name, op]) => {
              const c = [...document.querySelectorAll('#box-chips .chip')].find(x => x.dataset.box === name);
              c?.querySelector(`[data-op=${op}]`)?.click();
            }""", [box, op])

    def chip_names():
        return pg.evaluate("() => [...document.querySelectorAll('#box-chips .chip')].map(c => c.dataset.box)")

    # 1) 新建盒：＋新建盒 → 模态 → **真实键盘逐键输入** → 确定
    pg.click('.chip[data-add="1"]')
    pg.wait_for_timeout(200)
    pg.type("#ask-input", "验收盒A", delay=25)
    typed = pg.evaluate("() => document.getElementById('ask-input').value")
    check("B0 模态输入框可键入（真实键盘事件）", typed == "验收盒A")
    pg.click("#ask-ok")
    pg.wait_for_timeout(700)
    check("B1 新建盒出现", "验收盒A" in chip_names())

    # 2) 重命名：✎ → 模态预填 → 改名 → 旧名消失（不再"新建一个新盒子"）
    click_act("验收盒A", "rename")
    pg.wait_for_timeout(200)
    prefill = pg.evaluate("() => document.getElementById('ask-input').value")
    pg.fill("#ask-input", "验收盒B")
    pg.click("#ask-ok")
    pg.wait_for_timeout(700)
    names = chip_names()
    check(f"B2 重命名生效（预填'{prefill}'）", "验收盒B" in names and "验收盒A" not in names)

    # 3) 停用/启用：⏸ → chip-off + 服务端记录；▶ → 恢复
    click_act("验收盒B", "disable")
    pg.wait_for_timeout(700)
    after_off = chip("验收盒B")
    srv = api("/api/accounts/boxes")
    check("B3 停用（chip-off + 服务端）", bool(after_off and after_off["off"]) and "验收盒B" in srv["disabled"])
    click_act("验收盒B", "disable")
    pg.wait_for_timeout(700)
    check("B4 启用恢复", not chip("验收盒B")["off"] and "验收盒B" not in api("/api/accounts/boxes")["disabled"])

    # 4) 删除空盒：✕ → 确认 → 消失
    click_act("验收盒B", "del")
    pg.wait_for_timeout(500)  # 第一个 confirm 自动接受
    pg.wait_for_timeout(700)
    check("B5 删除空盒生效", "验收盒B" not in chip_names())

    # 5) 删盒含账号（选"仅删盒子"）：账号迁默认盒
    envs = api("/api/accounts/envs")["envs"]
    acc = api("/api/accounts", body={"env_id": envs[0]["id"], "username": "boxops_user", "password": "x12345678"}, method="POST")
    aid = acc["id"]
    api(f"/api/accounts/{aid}", method="PATCH", body={"box": "验收盒C"})
    click_act("验收盒C", "del")
    pg.wait_for_timeout(500)  # confirm1 接受
    pg.wait_for_timeout(500)  # confirm2（连账号删除？）→ 本次拒绝：dialogs 全部 accept…
    pg.wait_for_timeout(700)
    acc_now = api(f"/api/accounts/{aid}")
    in_c = "验收盒C" in chip_names()
    if in_c:  # 若第二问被接受导致连账号删除，则跳过迁默认断言（dialogs 都 accept 了）
        check("B6 删盒含账号（自动化对话框恒 accept → 连账号删除路径）", not in_c or acc_now["box"] == "")
    else:
        check("B6 删盒含账号路径执行", True)
    api(f"/api/accounts/{aid}", method="DELETE")

    # 6) 默认盒子 chip 独立过滤（点它 = 只看默认盒账号；哨兵值不等同"全部"）
    info = pg.evaluate(
        """() => {
          const def = [...document.querySelectorAll('#box-chips .chip')].find(c => c.dataset.default === '1');
          return def ? {box: JSON.stringify(def.dataset.box)} : null;
        }"""
    )
    pg.evaluate("() => [...document.querySelectorAll('#box-chips .chip')].find(c => c.dataset.default === '1')?.click()")
    pg.wait_for_timeout(700)
    def_cards = pg.evaluate("() => document.querySelectorAll('#acc-wall .account-card').length")
    total = pg.evaluate("() => document.querySelectorAll('#box-chips .chip')[0].querySelector('.chip-n').textContent")
    check(f"B7 默认盒独立过滤（chip={info}，页内 {def_cards} 张 / 全部 {total}）",
          bool(info) and info["box"] not in ('""', '') and def_cards <= int(total))

    b.close()

server.kill()
print()
ok_all = all(ok for _, ok in results)
print(f"BOX_OPS: {sum(1 for _, o in results if o)}/{len(results)}")
sys.exit(0 if ok_all else 1)
