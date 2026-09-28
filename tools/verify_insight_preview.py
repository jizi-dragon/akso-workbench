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
    # 这正是 [ADR-0012](../docs/adr/0012-fixed-ports.md) 记的那个危险。
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
            wait("#ins-artifacts table.mtable tbody tr", "产物列表")
            names = page.eval_on_selector_all(
                "#ins-artifacts tbody tr td:first-child", "els => els.map(e => e.textContent)")
            expect = {"step1-relations.md", "step2-status-table.md", "step3-attribution.md",
                      "step4-workflows.drawio", "spider-network.md", "spider-network.json",
                      "spider-network.drawio", "multi-page-probe.drawio"}
            checks.append((f"P1 产物列表（{len(names)} 件）", expect <= set(names)))

            # —— markdown 预览（真产物：step2 四列表） ——
            page.click("#ins-artifacts tbody tr:has-text('step2-status-table.md') a:has-text('预览')")
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
            page.click("#ins-artifacts tbody tr:has-text('step1-relations.md') a:has-text('预览')")
            wait("#artifact-preview[open] .apv-md table.apv-table", "step1 关联表")
            step1_heads = page.eval_on_selector_all(
                ".apv-md table.apv-table th", "els => els.map(e => e.textContent)")
            bullets = page.eval_on_selector_all(".apv-md li", "els => els.map(e => e.textContent)")
            checks.append((f"P8 step1 关联表列（{step1_heads[:3]}）",
                           step1_heads[:3] == ["来源对象", "关联对象", "经由字段"]))
            checks.append((f"P9 列表项渲染（{bullets[:1]}）",
                           any("未展开" in b or "自环" in b for b in bullets)))
            page.click("#apv-close")
            page.wait_for_timeout(300)

            # —— drawio 预览（真产物：spider 生成的工作流流程图） ——
            page.click("#ins-artifacts tbody tr:has-text('step4-workflows.drawio') a:has-text('预览')")
            wait("#artifact-preview[open] .apv-canvas svg", "drawio 画布")
            shapes = page.eval_on_selector_all(
                ".apv-canvas:not([hidden]) svg .apv-node", "els => els.length")
            nodes = page.eval_on_selector_all(
                ".apv-canvas:not([hidden]) svg text tspan", "els => els.map(e => e.textContent)")
            paths = page.eval_on_selector_all(".apv-canvas:not([hidden]) svg .apv-edge", "els => els.length")
            checks.append((f"P10 drawio 节点 {shapes} 个 / 连线 {paths} 条", shapes == 4 and paths == 3))
            checks.append((f"P11 节点文字（{nodes[:3]}）", "开始" in nodes and "审批" in nodes))
            page.click("#apv-close")
            page.wait_for_timeout(300)

            # —— 多页页签 ——
            page.click("#ins-artifacts tbody tr:has-text('multi-page-probe.drawio') a:has-text('预览')")
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
