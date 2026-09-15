"""验证：`factory.js` 的 renderOutcome 能读懂 `run_full_workflow` 的真实返回。

为什么需要它：2026-09-15 前的 factory.js 读的是 `stdout_tail` / `stderr_tail` /
`code` / `duration_s`——**这四个字段 `/api/factory/run` 从来不返回**（它们是
`services/proc.py` 的进程结果形状，原生化后就没有进程了）。后果是"执行日志区一片空白、
收尾行写着 exit=undefined"，而页面不报错、测试也不报错，所以潜伏了很久。

本脚本把**真实后端返回**喂给**真实前端渲染函数**，逐一断言渲染结果里有应有的内容。
两侧任何一边改字段名，这里立刻红。前端函数用 DOM 桩驱动（本机无浏览器环境）。

用法：
    .venv\\Scripts\\python.exe tools\\verify_factory_page.py
（需要 node 在 PATH 上——只用它跑 DOM 桩，不需要装任何 npm 包）
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fakes import FakeEgmpClient  # noqa: E402

from workbench.services.egmp import orchestrate  # noqa: E402

BLUEPRINT = {
    "description": "验证用对象",
    "objects": [{
        "name": "培训管理", "code": "training_mgr__c",
        "fields": [{"name": "名称", "code": "title", "dataType": 1}],
    }],
}

# 前端脚本用 DOM 桩跑：只关心 renderOutcome 往日志里塞了什么文本
HARNESS = r"""
import fs from "node:fs";
const lines = [];
const fakeEl = () => ({
  innerHTML: "", textContent: "", className: "", style: {}, dataset: {},
  classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  appendChild() {}, querySelector: fakeEl, querySelectorAll: () => [],
  addEventListener() {}, remove() {}, children: [],
});
globalThis.document = {
  getElementById: fakeEl, createElement: fakeEl,
  querySelector: fakeEl, querySelectorAll: () => [],
};
globalThis.window = globalThis;
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

const src = fs.readFileSync(process.argv[2], "utf8");
const body = src.replace(/^import .*$/gm, "")
               .replace(/^el\('btn-fac-upload'\)[\s\S]*$/m, "");
const fn = new Function("logLine", "badgeFor", "api", "el", "fmtBytes", body +
  "\n; return { renderOutcome };");
const logLine = (_log, text, kind) => lines.push(`${kind || "out"}|${text}`);
const { renderOutcome } = fn(logLine, () => "ok", async () => ({}), fakeEl, () => "");
const log = { innerHTML: "" };
renderOutcome(log, JSON.parse(fs.readFileSync(process.argv[3], "utf8")));
console.log(JSON.stringify(lines));
"""


def run_frontend(payload: dict) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        payload_path = Path(tmp) / "payload.json"
        harness_path = Path(tmp) / "harness.mjs"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        harness_path.write_text(HARNESS, encoding="utf-8")
        proc = subprocess.run(
            ["node", str(harness_path), str(ROOT / "workbench" / "static" / "js" / "factory.js"),
             str(payload_path)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        )
    if proc.returncode != 0:
        raise RuntimeError(f"前端桩执行失败：{proc.stderr[:600]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def backend_payload(ok: bool) -> dict:
    """造出与 `routes_factory.run` **同形状**的响应体。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = orchestrate.run_full_workflow(
            FakeEgmpClient(), BLUEPRINT, artifact_dir=Path(tmp),
        )
    status = "succeeded" if ok else "failed"
    return {"job_id": "abc123def456", "status": status, "result": result, "log_tail": ["① 开始"]}


def main() -> int:
    rendered = run_frontend(backend_payload(ok=True))
    joined = "\n".join(rendered)
    print("renderOutcome 输出：")
    for line in rendered:
        print("   ", line)

    checks = [
        ("渲染出了『成功』", "|■ 成功" in joined),
        ("渲染出了对象名与步骤", "training_mgr__c" in joined and "verify" in joined),
        ("渲染出了断点（来自 checkpoint.completedCount）", "断点" in joined and "5/5" in joined),
        ("渲染出了任务 id", "abc123def456" in joined),
        ("**没有** undefined（旧版读错字段就会印出它）", "undefined" not in joined),
        ("**没有** NaN", "NaN" not in joined),
    ]
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    # 前端源码里不该再出现那四个幽灵字段（**只看代码，不看注释**：注释里
    # 特意写了它们的名字来解释这个缺陷，把注释也算进来会永远红）
    src = (ROOT / "workbench" / "static" / "js" / "factory.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*[\s\S]*?\*/", "", src)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    ghosts = [f for f in ("stdout_tail", "stderr_tail", "duration_s") if f in code]
    print(f"  {'PASS' if not ghosts else 'FAIL'}  代码里不再引用幽灵字段 {ghosts or '（无）'}")
    if ghosts:
        failed.append(f"幽灵字段 {ghosts}")

    # 上游事实核对：接口确实不返回那些字段
    payload_keys = set(backend_payload(ok=True))
    assert not ({"stdout_tail", "stderr_tail", "code", "duration_s"} & payload_keys), \
        "接口返回了幽灵字段？那这条验证的前提变了，请重新核对 API.md §7"

    if failed:
        print(f"\nFACTORY_FRONTEND_FAILED: {failed}")
        return 1
    print("\nFACTORY_FRONTEND_OK  真实后端返回 → 真实前端渲染，字段完全对齐")
    assert re.search(r"renderOutcome", src), "factory.js 里应有 renderOutcome"
    return 0


sys.exit(main())
