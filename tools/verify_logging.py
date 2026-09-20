"""日志系统**端到端**完善性检查（真起服务 + 真发请求 + 真读日志文件）。

## 为什么需要它

`tests/test_logging.py` 验的是**组件**（handler、过滤器、格式化器、tail 读口）。
它**证明不了**"整套系统在真实进程里跑起来是完整的"——那需要：

1. 起一个**真的** sidecar（走真实入口 `workbench.server_entry`，不是 TestClient）；
2. 发**真的** HTTP 请求（含工具调用、确认门、未知工具、错误路径）；
3. 读**真的**落盘文件，逐条断言结构、关联 ID、脱敏、自指噪声。

本仓对此有明确教训：PITFALLS #42「函数级测过了 ≠ 链路用上了」。
组件测试全绿而链路是空的，正是那一条的形态。

## 用法

    .\\.venv\\Scripts\\python.exe tools\\verify_logging.py

自起服务、用**临时数据目录与专用端口**（18793），跑完自动清理，
**不影响**你正在开着的那个工作台实例。

退出码：`0` = 全部检查通过；`1` = 有检查失败（逐条打印失败原因）。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
PORT = 18793
BASE = f"http://127.0.0.1:{PORT}"

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —— {detail}" if detail and not ok else ""))


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def read_jsonl(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    """返回 (解析成功的行, 解析失败的行)。**解析失败本身就是一项检查**。"""
    rows: list[dict] = []
    bad: list[str] = []
    if not path.exists():
        return rows, ["<文件不存在>"]
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            bad.append(line[:120])
    return rows, bad


def boot(command: list[str], env: dict, port: int, stdio_log: pathlib.Path):
    """起一个 sidecar 并等它就绪。返回 (进程, 是否就绪)。stdout/stderr 落文件（不是 PIPE）。"""
    import httpx

    handle = stdio_log.open("w", encoding="utf-8")
    proc = subprocess.Popen(command, cwd=str(ROOT), env=env,
                            stdout=handle, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1.5).status_code == 200:
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

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="wb-logverify-"))
    data_dir = workdir / "data"
    log_dir = workdir / "logs"
    stdio_log = workdir / "server-stdio.log"
    env = {
        **os.environ,
        "WORKBENCH_DATA": str(data_dir),
        "WORKBENCH_LOG_DIR": str(log_dir),
        "WORKBENCH_LOG_FORMAT": "jsonl",
        "WORKBENCH_LOG_LEVEL": "INFO",
        "WORKBENCH_PORT": str(PORT),
        "PYTHONIOENCODING": "utf-8",
    }
    print(f"临时数据目录 = {workdir}\n端口 = {PORT}（生产入口）+ {PORT + 1}（开发态入口）\n")

    # 生产入口：server_entry（打包态跑的就是它）
    proc, handle, ready = boot(
        [PY, "-m", "workbench.server_entry", "--port", str(PORT)], env, PORT, stdio_log)

    try:
        check("sidecar 起服并就绪（/api/health 200，生产入口 server_entry）", ready,
              f"进程已退出={proc.poll()} stderr={stdio_log.read_text(encoding='utf-8')[-400:]}")
        if not ready:
            return 1

        client = httpx.Client(base_url=BASE, timeout=20.0)

        # ── 驱动场景：正常请求 / 工具目录 / 自检 / 未知工具 / 危险工具 / 日志面
        client.get("/api/health")
        tools = client.get("/api/agent/tools")
        client.get("/api/agent/selfcheck")
        trace_of_tools = tools.headers.get("X-Trace-Id", "")

        # 只读工具：正常路径
        client.post("/api/agent/invoke", json={"tool": "get_current_time", "args": {}})
        # 未知工具：错误路径（路由层短路 → 由 routes_agent 单独记）
        client.post("/api/agent/invoke", json={"tool": "no_such_tool", "args": {}})
        # 危险工具：确认门（**必须给合法参数**，否则先撞 bad_args 而不是 needs_approval）
        client.post("/api/agent/invoke",
                    json={"tool": "factory_create",
                          "args": {"blueprint_id": "probe-bp", "account_id": "probe-acc"}})

        # 日志面（**自身不该产生 http 行**）
        client.get("/api/logs/days")
        client.get("/api/logs/tail", params={"n": 5})

        log_file = log_dir / f"workbench-{time.strftime('%Y%m%d')}.jsonl"
        rows, bad_lines = read_jsonl(log_file)
        direct = client.get("/api/logs/tail", params={"n": 2000}).json()["rows"]

        print("\n=== A. 落盘与结构 ===")
        check("A1 日志文件存在且非空", log_file.exists() and log_file.stat().st_size > 0,
              str(log_file))
        check("A2 每一行都是合法 JSON（JSONL 完整性）", not bad_lines,
              f"{len(bad_lines)} 行解析失败，首条：{bad_lines[:1]}")
        required = {"ts", "level", "logger", "msg", "pid", "ver"}
        missing = [r for r in rows if not required <= set(r)]
        check("A3 每条记录都有必填字段（ts/level/logger/msg/pid/ver）", not missing,
              f"{len(missing)} 条缺字段：{missing[:1]}")

        print("\n=== B. 启动链路的留痕 ===")
        dump = json.dumps(direct, ensure_ascii=False)
        check("B1 启动横幅（版本 + pid + frozen + 端口 + 数据目录 + 日志路径）",
              "Workbench v" in dump and "log=" in dump)
        check("B2 路由注册逐条留痕（应有 12 条）",
              len([r for r in direct if "路由已注册" in str(r.get("msg"))]) >= 12,
              f"实际 {len([r for r in direct if '路由已注册' in str(r.get('msg'))])} 条")
        check("B3 工具中枢自检（插件数 / 工具数）",
              any("工具中枢：" in str(r.get("msg")) for r in direct))
        check("B4 应用就绪（lifespan 装配完成）",
              any("应用就绪" in str(r.get("msg")) for r in direct))
        check("B5 沙箱与 Skill 装配留痕",
              any("沙箱已装配" in str(r.get("msg")) for r in direct)
              or any("Skill 已装配" in str(r.get("msg")) for r in direct))

        print("\n=== C. L1 请求日志与 traceId 贯通 ===")
        http_rows = [r for r in direct if r.get("kind") == "http"]
        check("C1 存在 kind=http 的请求日志", bool(http_rows), f"{len(http_rows)} 条")
        check("C2 响应头带 X-Trace-Id", bool(trace_of_tools), repr(trace_of_tools))
        check("C3 请求行含 method/path/status/durationMs",
              all({"method", "path", "status", "durationMs"} <= set(r) for r in http_rows))
        check("C4 请求行带 traceId（且与响应头一致）",
              any(r.get("traceId") == trace_of_tools for r in http_rows),
              f"header={trace_of_tools}")
        traced = client.get(f"/api/logs/trace/{trace_of_tools}")
        check("C5 /api/logs/trace 能取回同一 trace 的时间线",
              traced.status_code == 200 and traced.json()["count"] >= 1,
              f"HTTP {traced.status_code}")
        if traced.status_code == 200:
            tl = traced.json()["rows"]
            check("C6 时间线按时间**正序**（旧→新）",
                  all(tl[i]["ts"] <= tl[i + 1]["ts"] for i in range(len(tl) - 1)))

        print("\n=== D. 工具调用与审计的对接 ===")
        tool_rows = [r for r in direct if r.get("kind") == "tool"]
        ok_row = next((r for r in tool_rows if r.get("tool") == "get_current_time"), None)
        check("D1 工具调用有日志（kind=tool）", ok_row is not None,
              f"日志里的工具：{[r.get('tool') for r in tool_rows]}")
        if ok_row:
            check("D2 工具日志含 auditId/status/plugin/risk/durationMs",
                  {"auditId", "status", "plugin", "risk", "durationMs"} <= set(ok_row))
            audit_id = ok_row["auditId"]
            detail = client.get(f"/api/agent/audit/{audit_id}")
            check("D3 日志里的 auditId 能在 agent_audit 里查到同一行（两处对得上）",
                  detail.status_code == 200 and detail.json().get("tool") == "get_current_time",
                  f"HTTP {detail.status_code}")
        check("D4 未知工具被单独记下（HTTP 路径在路由层短路，故由 routes_agent 记）",
              any("未知工具调用" in str(r.get("msg")) for r in direct))
        check("D5 危险工具被确认门拦下并留痕",
              any("确认门拦下" in str(r.get("msg")) for r in direct)
              or any(r.get("tool") == "factory_create" and r.get("status") == "needs_approval"
                     for r in tool_rows),
              f"factory_create 的日志："
              f"{[r.get('status') for r in tool_rows if r.get('tool') == 'factory_create']}")
        check("D6 被拦后的工具日志状态是 needs_approval",
              any(r.get("tool") == "factory_create" and r.get("status") == "needs_approval"
                  for r in tool_rows))

        print("\n=== E. 检索面与自指噪声 ===")
        days = client.get("/api/logs/days").json()
        check("E1 /api/logs/days 列出当天", time.strftime("%Y%m%d") in days["days"])
        only_err = client.get("/api/logs/tail", params={"level": "ERROR", "n": 50}).json()["rows"]
        check("E2 级别过滤生效（ERROR 里没有 INFO）",
              all(r.get("level") == "ERROR" for r in only_err))
        check("E3 关键字过滤生效",
              client.get("/api/logs/tail",
                         params={"contains": "工具中枢", "n": 20}).json()["count"] >= 1)
        check("E4 /api/logs/* 自身**不产生** http 日志（无自指噪声）",
              not [r for r in http_rows if str(r.get("path", "")).startswith("/api/logs")],
              f"混入：{[r.get('path') for r in http_rows if str(r.get('path','')).startswith('/api/logs')]}")
        dl = client.get("/api/logs/download")
        dl_ok = False
        if dl.status_code == 200:
            try:
                [json.loads(ln) for ln in dl.text.splitlines() if ln.strip()]
                dl_ok = True
            except ValueError:
                dl_ok = False
        check("E5 /api/logs/download 导出的是可解析的 JSONL", dl_ok,
              f"HTTP {dl.status_code}")

        print("\n=== F. 脱敏与失败隔离 ===")
        raw = log_file.read_text(encoding="utf-8", errors="replace")
        check("F1 落盘内容里没有 `sk-` 形状的 API Key",
              not re.search(r"sk-[A-Za-z0-9._\-]{16,}", raw),
              (re.search(r"sk-[A-Za-z0-9._\-]{16,}", raw) or [""])[0][:40])
        check("F2 落盘内容里没有 `password=` 原文",
              not re.search(r"(?i)password\s*[:=]\s*(?!\*\*\*)[^\s,;\"']{3,}", raw))
        check("F3 进程存活（日志写入没有把服务搞崩）", proc.poll() is None)
        check("F4 stdout/stderr 里没有「日志装配失败」",
              "日志装配失败" not in stdio_log.read_text(encoding="utf-8", errors="replace"))

        print("\n=== G. 滚动配置 ===")
        check("G1 日志目录下只有本系统的文件",
              all(p.name.startswith("workbench-") for p in log_dir.iterdir()),
              str([p.name for p in log_dir.iterdir()]))
        check("G2 未到阈值时**不产生**多余归档（正常一天一个文件）",
              len(list(log_dir.glob("workbench-*.jsonl.*"))) == 0,
              str([p.name for p in log_dir.glob('workbench-*.jsonl.*')]))

        client.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            with __import__("contextlib").suppress(Exception):
                proc.kill()
        handle.close()

    # ── H. 开发态启动路径（**桌面壳 `npm start` 走的就是这条**）
    #
    # 为什么单列一组：`desktop/main.js::spawnServer` 的**开发态回退分支**是直接 spawn
    # `python -m uvicorn workbench.api:app`——**不经过任何入口脚本**。
    # 于是"启动横幅"如果只在 `main.py` / `server_entry.py` 里记，日常开发测试时日志就是残缺的
    # （2026-09-20 实测确认过这个缺口，随后把横幅移到 `api.lifespan`）。
    print("\n=== H. 开发态启动路径（桌面壳 npm start 的 Python 侧）===")
    dev_port = PORT + 1
    dev_dir = workdir / "dev"
    dev_env = {**env, "WORKBENCH_DATA": str(dev_dir), "WORKBENCH_LOG_DIR": str(dev_dir / "logs"),
               "WORKBENCH_PORT": str(dev_port)}
    dev_proc, dev_handle, dev_ready = boot(
        [PY, "-m", "uvicorn", "workbench.api:app",
         "--host", "127.0.0.1", "--port", str(dev_port)],
        dev_env, dev_port, workdir / "dev-stdio.log")
    try:
        check("H1 开发态（裸 uvicorn）起服并就绪", dev_ready,
              f"进程已退出={dev_proc.poll()}")
        if dev_ready:
            dev_file = dev_dir / "logs" / f"workbench-{time.strftime('%Y%m%d')}.jsonl"
            dev_rows, _ = read_jsonl(dev_file)
            dv = json.dumps(dev_rows, ensure_ascii=False)
            check("H2 **开发态也记启动横幅**（版本/pid/frozen/端口/数据目录/日志路径）",
                  "Workbench v" in dv and "frozen=" in dv and "log=" in dv,
                  f"日志行数={len(dev_rows)}")
            check("H3 开发态记「应用就绪」", "应用就绪" in dv)
            counts = [r for r in dev_rows if "工具中枢：" in str(r.get("msg"))]
            check("H4 工具自检**只记一次**（横幅与自检不重复报同一件事）",
                  len(counts) == 1, f"出现 {len(counts)} 次")
    finally:
        try:
            dev_proc.terminate()
            dev_proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            with __import__("contextlib").suppress(Exception):
                dev_proc.kill()
        dev_handle.close()
        shutil.rmtree(workdir, ignore_errors=True)

    failed = [c for c in CHECKS if not c[1]]
    print(f"\n{'=' * 62}")
    print(f"LOGGING_E2E: {len(CHECKS) - len(failed)}/{len(CHECKS)} PASS")
    for label, _, detail in failed:
        print(f"  ✗ {label}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
