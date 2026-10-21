"""洞察产物预览验证：应用内预览 markdown 与 drawio（真渲染断言，不靠肉眼）。

为什么要这个工具：静态 JS 没有单测框架（同扩展侧），而"预览"这件事的失败形态是
**静默的**——弹窗照开、内容是空白或源码。所以这里用真浏览器断言渲染结果：
markdown 要出表格/标题/引用块，drawio 要出 SVG 图形与页签，且全程零 JS 错误。

自足性：临时数据目录 + 临时库 + 专用端口，不碰用户的真实数据；夹具用**本仓自己的
drawio 生成器**产出，保证测的就是生产里同一种形状。

跑法：.venv\\Scripts\\python.exe tools\\verify_insight_preview.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def _port_free(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0

PORT = 18791
BASE = f"http://127.0.0.1:{PORT}"
JOB_ID = "previewcheck1"

MD = """# 蜘蛛计划 · 第 2 步：生命周期状态明细

> 列口径：状态名称 / 进入动作 / 用户动作 / 进入条件。归类按 `executeType`。

## 变更计划表

| 生命周期状态名称 | 进入动作 | 用户动作 | 进入条件 |
|---|---|---|---|
| 草稿 | — | 提交申请（发起工作流） | 风险评估不为空 |
| 审批中 | 自动指派 | 审批→通过 | — |

- 未展开：用户对象（只有 id，平台未给编码）
- **平台未配置**的项会如实写进报告
"""


def build_artifacts(workspace: Path) -> list[str]:
    """把预览要用到的产物写进临时工作区。

    主体**不是手写夹具**，而是拿回放夹具离线跑一遍真 spider —— 预览要面对的正是生产
    产物的形状（四列表、引用块、嵌套列表、多页 drawio、长表格）。手写 md 只能证明
    "渲染器能渲染我以为的东西"，证明不了"能渲染真实产物"。
    另加一个两页 drawio 专测多页页签（真夹具只引用了 1 个工作流，只有 1 页）。
    """
    sys.path.insert(0, str(ROOT))
    from workbench.services.egmp import replay
    from workbench.services.egmp.insight import drawio as drawio_mod
    from workbench.services.egmp.insight import run_spider

    fixture = replay.load_fixture(ROOT / "tests" / "fixtures" / "egmp" / "spider-training-mgr.json")
    # 不关心返回值：要的就是它落盘的产物（预览渲染器面对的是这些文件）
    run_spider(replay.ReplayClient(fixture), ["training_mgr__c"], output_dir=workspace)

    # ★ 手写探针产物：**专测**列表 / 有序列表 / 加粗 ——
    # 真产物里今天一条列表都没有（`step2-status-table.md` 是 0 条），
    # 所以"列表渲染"这件事只能靠一份带列表的产物才验得到。
    # 名字直说它是探针，免得下次有人以为它是产品产物。
    (workspace / "markdown-probe.md").write_text(MD, encoding="utf-8")

    graph = {
        "name": "两页探针",
        "nodes": [
            {"id": "s1", "name": "开始", "stepType": 10, "behaviors": [{"summary": "开始"}]},
            {"id": "s2", "name": "审批", "stepType": 30, "behaviors": [{"summary": "审批任务"}]},
            {"id": "s3", "name": "结束", "stepType": 20, "behaviors": [{"summary": "结束"}]},
        ],
        "edges": [{"source": "s1", "target": "s2", "label": ""},
                  {"source": "s2", "target": "s3", "label": "通过"}],
    }
    page = "".join(drawio_mod.workflow_cells(graph, axis="col"))
    (workspace / "multi-page-probe.drawio").write_text(
        drawio_mod.mxfile([("第一页", page), ("第二页", page)], host="verify"), encoding="utf-8")
    return [p.name for p in sorted(workspace.iterdir())]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="insight-preview-"))
    workspace = tmp / "workspace" / "training_mgr__c" / "20260915-000000"
    workspace.mkdir(parents=True)
    files = build_artifacts(workspace)
    print(f"夹具产物 {len(files)} 件：{files}\n")

    env = {
        **os.environ,
        "WORKBENCH_DATA": str(tmp / "data"),
        "WORKBENCH_DB": str(tmp / "data" / "verify.db"),
        "WORKBENCH_PORT": str(PORT),
        "WORKBENCH_HOST": "127.0.0.1",
    }
    # ★ 端口被占**就拒跑**，而不是连上去（2026-09-24 加）。
    #
    # 此前没有这一道：`uvicorn` 绑不上端口时只是**退出**（报错被丢进 DEVNULL），
    # 而下面的就绪探测会**连上那个正在运行的应用** —— 于是这套验收打在
    # **真实实例**上，且**没有任何一步会报错**。
    # 这正是 [ADR-0012](../docs/adr/0011-port-governance.md) 记的那个危险。
    if _port_free(PORT):
        pass
    else:
        raise SystemExit(f"✗ 端口 {PORT} 被占用——先退出占用它的进程"
                         "（不连上去：那样这套验收会打在真实实例上）")

    server = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn",
         "workbench.api:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    checks: list[tuple[str, bool]] = []
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/api/health", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.4)

        db_path = env["WORKBENCH_DB"]
        now = int(time.time() * 1000)
        con = sqlite3.connect(db_path)
        con.execute(
            "INSERT INTO insight_runs (id, module, command, objects, env_id, status, log_path,"
            " artifact_dir, created_at, updated_at, workspace_dir, coverage_status)"
            " VALUES (?, 'insight', 'spider', 'change_plan__c', '', 'succeeded', ?, ?, ?, ?, ?, 'complete')",
            (JOB_ID, str(workspace / "proc.log"), str(workspace), now, now, str(workspace)))
        con.commit()
        con.close()

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            consoles: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: consoles.append(f"{m.type}: {m.text}"))

            def wait(selector: str, what: str) -> None:
                """等元素；超时就把弹窗现场打出来——预览的失败形态是静默的。"""
                try:
                    page.wait_for_selector(selector, timeout=8000)
                except Exception:  # noqa: BLE001
                    meta = page.eval_on_selector("#apv-meta", "e => e.textContent") \
                        if page.query_selector("#apv-meta") else "(无 meta)"
                    body = page.eval_on_selector("#apv-body", "e => e.innerHTML.slice(0, 400)") \
                        if page.query_selector("#apv-body") else "(无 body)"
                    print(f"!! 等待「{what}」失败\n   selector={selector}\n   meta={meta}\n"
                          f"   body={body}\n   控制台={consoles[-5:]}\n   JS 错误={errors}")
                    raise
            page.goto(f"{BASE}/static/pages/insight.html", wait_until="networkidle")
            page.wait_for_timeout(600)

            page.click("#ins-runs tbody tr:first-child a")          # 「产物」→ 列产物
            wait("#ins-artifacts .artifact-card", "产物列表")
            names = page.eval_on_selector_all(
                "#ins-artifacts .artifact-card .artifact-name", "els => els.map(e => e.textContent)")
            expect = {"markdown-probe.md",
                      "step1-relations.md", "step2-status-table.md", "step3-attribution.md",
                      "step4-workflows.drawio", "spider-network.md", "spider-network.json",
                      "spider-network.drawio", "multi-page-probe.drawio"}
            checks.append((f"P1 产物列表（{len(names)} 件）", expect <= set(names)))
            shot = Path(tempfile.gettempdir()) / "artifact-panel-preview.png"
            page.locator("#ins-artifacts").scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            page.screenshot(path=str(shot), full_page=False)
            print(f"  产物面板截图：{shot}")

            # —— markdown 预览（真产物：step2 四列表） ——
            page.click('.artifact-card[data-name="step2-status-table.md"] button[title="预览"]')
            wait("#artifact-preview[open] .apv-md table.apv-table", "markdown 表格")
            headers = page.eval_on_selector_all(
                ".apv-md table.apv-table th", "els => els.map(e => e.textContent)")
            rows = page.eval_on_selector_all(".apv-md table.apv-table tbody tr", "els => els.length")
            heads = page.eval_on_selector_all(".apv-md h2, .apv-md h3", "els => els.map(e => e.textContent)")
            quote = page.eval_on_selector(".apv-md blockquote", "e => e.textContent")
            cells = page.eval_on_selector_all(".apv-md table.apv-table tbody td", "els => els.length")
            checks.append((f"P2 markdown 表格 4 列（{headers}）",
                           headers == ["生命周期状态名称", "进入动作", "用户动作", "进入条件"]))
            checks.append((f"P3 表体 {rows} 行 / {cells} 格（夹具 3 状态）", rows == 3 and cells == 12))
            checks.append((f"P4 markdown 标题（{heads}）", any("第 2 步" in h for h in heads)))
            checks.append((f"P5 引用块照渲染（{quote[:16]}…）", "列口径" in quote))
            checks.append(("P6 未渲染成源码（无 <pre> 直出）",
                           page.eval_on_selector_all(".apv-md pre.apv-code", "els => els.length") == 0))

            page.click("#apv-raw")                                   # 原始/渲染切换
            wait("#apv-body pre.apv-source", "原始文本")
            raw_ok = "| 生命周期状态名称 |" in page.eval_on_selector("#apv-body pre", "e => e.textContent")
            checks.append(("P7 「原始」切到源文本", raw_ok))
            page.click("#apv-close")
            page.wait_for_timeout(300)

            # —— 另一份真产物（step1 关联对象表，含未展开边界列表） ——
            page.click('.artifact-card[data-name="step1-relations.md"] button[title="预览"]')
            wait("#artifact-preview[open] .apv-md table.apv-table", "step1 关联表")
            step1_heads = page.eval_on_selector_all(
                ".apv-md table.apv-table th", "els => els.map(e => e.textContent)")
            bullets = page.eval_on_selector_all(".apv-md li", "els => els.map(e => e.textContent)")

            # ★ 2026-09-24 修：这两条**写死了产品的文案**，于是产品换过说法之后它们
            # 静默过期（判据写的是 `关联对象/经由字段`，而产物今天是 `相关对象/经由关系`）。
            # ⇒ 改成断言「**渲染出来的 == 源文件里的**」——
            #    这个**关系**不会随文案变化而过期，而那正是这两条要买的东西。
            src_md = (workspace / "step1-relations.md").read_text(encoding="utf-8")
            src_heads = []
            for line in src_md.splitlines():
                if line.lstrip().startswith("|") and "---" not in line:
                    src_heads = [c.strip() for c in line.strip().strip("|").split("|")]
                    break
            src_bullets = [ln.strip()[1:].strip() for ln in src_md.splitlines()
                           if ln.lstrip().startswith("- ")]

            checks.append((f"P8 关联表列 == 源文件（{step1_heads[:3]}）",
                           bool(src_heads) and step1_heads[:len(src_heads)] == src_heads))
            # ⚠️ 这一条今天两边都是 **0** ⇒ 它是"**不多不少**"的一致性判据，
            # **不**证明渲染器会渲染列表（那一条在上面的 P7a，那份产物里真有列表）。
            checks.append((f"P9 列表项数 == 源文件（渲染 {len(bullets)} / 源 {len(src_bullets)}）",
                           len(bullets) == len(src_bullets)))
            page.click("#apv-close")
            page.wait_for_timeout(300)

            # —— 手写探针产物：列表 / 有序列表 / 加粗（真产物里没有列表） ——
            #
            # ★ 为什么单要一份探针：`step2-status-table.md` 今天**一条 `- ` 都没有**
            # ⇒ 在它上面断言"渲染出的 `<li>` 数 == 源里的 `- ` 数"永远是 **0 == 0**，
            # 一个把列表整个吞掉的渲染器同样通过。**那种断言比没有更坏**：
            # 它占着"这里验过了"的位置（PITFALLS #112 那一族）。
            page.click('.artifact-card[data-name="markdown-probe.md"] button[title="预览"]')
            wait("#artifact-preview[open] .apv-md", "markdown 探针")
            li_texts = page.eval_on_selector_all(
                ".apv-md li", "els => els.map(e => e.textContent)")
            src_bullets = [ln.strip()[2:].strip() for ln in MD.splitlines()
                           if ln.lstrip().startswith("- ")]
            checks.append((f"P9a 无序列表项（渲染 {len(li_texts)} / 源 {len(src_bullets)}）",
                           len(src_bullets) >= 2 and len(li_texts) == len(src_bullets)))
            checks.append((f"P9b 列表文字对得上（{li_texts[:1]}）",
                           bool(li_texts) and "未展开" in li_texts[0]))
            checks.append(("P9c 加粗照渲染（不是星号原文）",
                           page.eval_on_selector_all(".apv-md li strong", "els => els.length") >= 1))
            # ★ ool(li_texts) and 是承重的：ll(... for x in []) 在空列表上**恒真**，
            # 而"渲染器把列表整个吞掉"恰恰会让它是空的 —— 那时这条判据
            # 会**在缺陷版上通过**（反证实测：拆掉列表分支后它是唯一还绿的 P9*）。
            checks.append(("P9d 列表里没有残留的星号原文",
                           bool(li_texts) and all("**" not in x for x in li_texts)))
            page.click("#apv-close")
            page.wait_for_timeout(300)

            # —— drawio 预览（真产物：spider 生成的工作流流程图） ——
            page.click('.artifact-card[data-name="step4-workflows.drawio"] button[title="预览"]')
            wait("#artifact-preview[open] .apv-canvas svg", "drawio 画布")
            shapes = page.eval_on_selector_all(
                ".apv-canvas:not([hidden]) svg .apv-node", "els => els.length")
            nodes = page.eval_on_selector_all(
                ".apv-canvas:not([hidden]) svg text tspan", "els => els.map(e => e.textContent)")
            paths = page.eval_on_selector_all(".apv-canvas:not([hidden]) svg .apv-edge", "els => els.length")
            checks.append((f"P10 drawio 节点 {shapes} 个 / 连线 {paths} 条", shapes == 4 and paths == 3))
            # ★ 2026-09-24 修：`"审批" in nodes` 是**列表精确匹配**，而 drawio 的单元格
            # 是 `审批任务`（`&#xa;` 把内容切成多个 tspan）⇒ 它永远不成立。
            # 本意显然是**子串**判断 —— 一个"看起来像在验文字、其实在验整格相等"的判据。
            checks.append((f"P11 节点文字（{nodes[:3]}）",
                           any("开始" in n for n in nodes) and any("审批" in n for n in nodes)))
            page.click("#apv-close")
            page.wait_for_timeout(300)

            # —— 多页页签 ——
            page.click('.artifact-card[data-name="multi-page-probe.drawio"] button[title="预览"]')
            wait("#artifact-preview[open] .apv-canvas svg", "多页画布")
            tabs = page.eval_on_selector_all(".apv-tab", "els => els.map(e => e.textContent)")
            first = page.eval_on_selector_all(".apv-canvas:not([hidden]) svg .apv-node", "els => els.length")
            page.click(".apv-tab:nth-child(2)")
            page.wait_for_timeout(200)
            second = page.eval_on_selector_all(".apv-canvas:not([hidden]) svg .apv-node", "els => els.length")
            checks.append((f"P12 多页页签（{tabs}）", tabs == ["第一页", "第二页"]))
            checks.append((f"P13 切页有效（{first} → {second} 个图形）", first == 3 and second == 3))

            checks.append((f"P14 零 JS 错误（{errors[:2]}）", not errors))
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    for label, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"\nINSIGHT_PREVIEW_CHECKS: {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
