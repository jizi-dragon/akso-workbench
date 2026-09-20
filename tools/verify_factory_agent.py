"""配置工厂 × Agent 的端到端核验：**真起服务、真发 HTTP、真看落盘文件**。

## 它证明什么（组件测试证明不了的那一段）

`tests/test_factory_agent.py` 用的是 `TestClient` 与直接 `hub.execute()`——它证明得了**逻辑**，
证明不了**链路**。这一条补的正是 PITFALLS #53 的教训：一个只在新分支里出现的 `NameError`
能让几百个单测全绿而真实请求 500。所以这里坚持：

- 用**生产入口** `python -m workbench.server_entry` 起服（打包态跑的就是它）；
- 走**真实 HTTP**，不 import 应用；
- **真去磁盘上看那份暂存的蓝图在不在**——"返回了个 id 但没落盘"是本条链路最可能的假通过；
- 临时数据目录（账号表为空），所以任何"真去连平台"的步骤都会失败在"账号不存在"上，
  **不会碰到真实环境**；
- 跑完即清。

## 用法

    .\\.venv\\Scripts\\python.exe tools\\verify_factory_agent.py

退出码：`0` = 全过；`1` = 有失败或起不来。**手动诊断用**，不进 CI（它占固定端口）。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

PORT = 18796
BASE = f"http://127.0.0.1:{PORT}"

GOOD = {
    "description": "端到端核验用：一个对象 + 两个字段",
    "objects": [{
        "name": "核验对象",
        "code": "verify_probe__c",
        "fields": [
            {"name": "主题", "code": "topic__c", "dataType": 1, "isRequired": True},
            {"name": "数量", "code": "count__c", "dataType": 2, "min": 0, "max": 99},
        ],
    }],
}
BAD = {"objects": [{"name": "坏的", "code": "BAD", "fields": [
    {"name": "选项", "code": "opt__c", "dataType": 4}]}]}

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —— {detail}" if detail and not ok else ""))


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def boot(env: dict, stdio_log: pathlib.Path):
    import httpx

    handle = stdio_log.open("w", encoding="utf-8")
    proc = subprocess.Popen([PY, "-m", "workbench.server_entry", "--port", str(PORT)],
                            cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            if httpx.get(f"{BASE}/api/health", timeout=1.5).status_code == 200:
                return proc, handle, True
        except Exception:  # noqa: BLE001 —— 还没起来
            time.sleep(0.5)
    return proc, handle, False


def main() -> int:
    if not port_free(PORT):
        print(f"✗ 端口 {PORT} 被占用——请先退出占用它的进程")
        return 1
    if not pathlib.Path(PY).exists():
        print(f"✗ 找不到解释器 {PY}")
        return 1

    import httpx

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="wb-factoryverify-"))
    data_dir = workdir / "data"
    env = {
        **os.environ,
        "WORKBENCH_DATA": str(data_dir),
        "WORKBENCH_LOG_DIR": str(workdir / "logs"),
        "WORKBENCH_PORT": str(PORT),
        "PYTHONIOENCODING": "utf-8",
    }
    print(f"临时数据目录 = {workdir}\n端口 = {PORT}（生产入口 server_entry）\n")

    proc, handle, ready = boot(env, workdir / "server-stdio.log")
    try:
        check("sidecar 起服并就绪（生产入口）", ready,
              (workdir / "server-stdio.log").read_text(encoding="utf-8", errors="replace")[-400:])
        if not ready:
            return 1
        client = httpx.Client(base_url=BASE, timeout=60.0)

        print("=== A. 工具面（旧工具必须消失，新工具必须出现）===")
        names = {t["name"] for t in client.get("/api/agent/tools").json()["tools"]}
        check("A1 factory_blueprint_spec 已装配", "factory_blueprint_spec" in names)
        check("A2 factory_preview_blueprint 已装配", "factory_preview_blueprint" in names)
        check("A3 旧的 factory_validate_blueprint 已不存在",
              "factory_validate_blueprint" not in names)

        print("=== B. 规格真的送到模型手里了 ===")
        spec_res = client.post("/api/agent/invoke",
                               json={"tool": "factory_blueprint_spec", "args": {}})
        check("B1 规格调用成功", spec_res.status_code == 200, f"status={spec_res.status_code}")
        spec_text = json.dumps(spec_res.json().get("result") or {}, ensure_ascii=False)
        for key in ("referenceObjectCode", "picklistCode", "bindToStatusCode", "engine_pipeline"):
            check(f"B 规格含 {key}", key in spec_text)
        check("B2 规格没有在返回路上被截断",
              "截断" not in spec_text and len(spec_text) > 5000,
              f"len={len(spec_text)}")

        print("=== C. 闭环：一份蓝图进来，一个 blueprint_id 出去，且真的落盘 ===")
        res = client.post("/api/agent/invoke", json={
            "tool": "factory_preview_blueprint",
            "args": {"blueprint": json.dumps(GOOD, ensure_ascii=False)},
        })
        check("C1 预览调用成功", res.status_code == 200, f"status={res.status_code}")
        payload = res.json().get("result") or {}
        blueprint_id = payload.get("blueprint_id", "")
        check("C2 返回了 blueprint_id", bool(blueprint_id), str(payload)[:200])
        check("C3 返回了给用户看的审阅清单",
              "核验对象" in str(payload.get("review_markdown", "")),
              str(payload.get("review_markdown", ""))[:120])
        check("C4 返回了蓝图要点（确认摘要要用它）",
              (payload.get("highlights") or {}).get("fieldCount") == 2,
              str(payload.get("highlights")))

        staged = data_dir / "runtime" / "factory" / blueprint_id / "blueprint.json"
        check("C5 **蓝图真的落盘了**（不是只回了个字符串）", staged.is_file(), str(staged))
        for name in ("spec.md", "checklist.md"):
            check(f"C5 {name} 也在（审阅材料在审批之前就生成）",
                  (staged.parent / name).is_file(), str(staged.parent / name))

        listed = client.get("/api/factory/blueprints").json()["blueprints"]
        check("C6 配置工厂页面的列表里能看到它（两条路共用同一个暂存区）",
              blueprint_id in [b["blueprint_id"] for b in listed], str(listed)[:200])

        print("=== D. 校验不过就不暂存（半成品不许进暂存区）===")
        bad = client.post("/api/agent/invoke", json={
            "tool": "factory_preview_blueprint",
            "args": {"blueprint": json.dumps(BAD, ensure_ascii=False)},
        })
        bad_payload = bad.json().get("result") or {}
        check("D1 返回 valid=false", bad_payload.get("valid") is False, str(bad_payload)[:200])
        check("D2 **没有**给 blueprint_id", "blueprint_id" not in bad_payload)
        check("D3 问题清单里附了相关键的写法",
              bool(bad_payload.get("field_hints")), str(bad_payload.get("field_hints")))

        print("=== E. 旧的「一句话→蓝图」路径已下线且指明改用什么 ===")
        legacy = client.post("/api/factory/run", json={
            "blueprint_id": blueprint_id, "account_id": "ghost",
            "command": "orchestrate", "confirmed": True,
        })
        check("E1 command=orchestrate → 400", legacy.status_code == 400,
              f"status={legacy.status_code}")
        detail = str(legacy.json().get("detail", ""))
        check("E2 错误信息里说清了改用对话", "对话" in detail and "create" in detail, detail[:160])

        print("=== F. 创建仍然被两道闸门挡住 ===")
        unconfirmed = client.post("/api/factory/run", json={
            "blueprint_id": blueprint_id, "account_id": "ghost",
            "command": "create", "confirmed": False,
        })
        check("F1 未勾选「环境强制确认」→ 409", unconfirmed.status_code == 409,
              f"status={unconfirmed.status_code}")

        client.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
        handle.close()
        shutil.rmtree(workdir, ignore_errors=True)

    failed = [label for label, ok, _ in CHECKS if not ok]
    print()
    print("=" * 62)
    if failed:
        print(f"FACTORY_AGENT_E2E: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS")
        for label in failed:
            print(f"  ✗ {label}")
        return 1
    print(f"FACTORY_AGENT_E2E: {len(CHECKS)}/{len(CHECKS)} PASS")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
