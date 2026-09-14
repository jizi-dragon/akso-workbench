"""扩展启动冒烟（隔离 profile 装载 dist）：SW 能否启动、命令清单、storage 键、数据面同步。

为什么需要：扩展端没有单测框架，typecheck 只能保类型、保不了运行期——
删除模块/改 imports 后 SW 启动即崩的情况只有真起一个 SW 才能发现。

用法：
    .venv\\Scripts\\python.exe tools\\verify_extension_boot.py

要点（踩过的坑）：
- **必须 headful**：MV3 扩展在旧 headless 下不加载（实测 `ctx.service_workers` 为空）；
  窗口用 `--window-position` 挪到屏幕外，避免打扰使用者。
- SW 需先开一个页面才暴露目标（Playwright 1.62 语义）。
- 断言的 storage 键均来自扩展自身写入；`akso:acctMap` 出现即证明桌面数据面同步成功。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "extensions" / "quick-login" / "dist"
PROFILE = Path(os.environ.get("TEMP", "/tmp")) / "ql_boot_profile"
# 用桌面服务自带的静态页做宿主页（不必额外起服务；不可达时退化为 about:blank）
HOST_URL = "http://127.0.0.1:18765/static/blank.html"

# 期望：manifest **不再注册任何快捷键**——Alt+Q 归桌面壳全局热键独占。
# 桌面未运行时扩展若自行响应 Alt+Q，会在浏览器里弹出浏览器版轮盘（用户实测 bug）：
# 扩展是执行面，它的轮盘本来只应由桌面经 /extension/commands 的 wheel.toggle 指令触发。
EXPECT_COMMANDS: list[str] = []
# 同步桥的复活闹钟名（sync.ts `startDesktopSync()` 创建）：SW 顶层跑到最后一行的证据
EXPECT_ALARM = "akso:sync"
# 反向断言：Alt+Q 绝不能再出现在扩展 manifest 里（防止上游同步把它带回来）
FORBIDDEN_MANIFEST_TEXT = "Alt+Q"
FORBIDDEN_LOCAL_KEYS = ["ql:recentPages"]
FORBIDDEN_SESSION_KEYS = ["ql:pageNames"]


def main() -> int:
    if not (DIST / "manifest.json").exists():
        print(f"未找到构建产物：{DIST}（先跑 `cd extensions/quick-login && npm run build`）")
        return 2
    if PROFILE.exists():
        shutil.rmtree(PROFILE, ignore_errors=True)

    from playwright.sync_api import sync_playwright

    sw_info: dict = {}
    fetch_patched = None
    errors: list[str] = []

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=False,  # MV3 扩展在 headless 下不加载
            args=[
                f"--disable-extensions-except={DIST}",
                f"--load-extension={DIST}",
                "--window-position=-2400,-2400",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.goto(HOST_URL, wait_until="load", timeout=8000)
        except Exception:  # noqa: BLE001 服务未起时不影响 SW 断言
            # 兜底 goto 也可能抛：Chromium 在导航被 chrome-error:// 页面打断时会抛
            # "Navigation to about:blank is interrupted by another navigation" ——
            # 之前这行没保护，服务未起时整个脚本直接崩、连结果都不打印（实测 flake）。
            # SW 断言不依赖宿主页内容，拿不到页面就继续跑。
            try:
                page.goto("about:blank", wait_until="load", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(3000)  # 等 SW 起来 + 内容脚本注入

        fetch_patched = page.evaluate(
            "() => { try { return String(window.fetch).includes('sniffFetch'); } catch { return null; } }"
        )

        sws = list(ctx.service_workers)
        if sws:
            sw_info = sws[0].evaluate(
                """async () => {
                     const m = chrome.runtime.getManifest();
                     // chrome.commands 在未声明任何命令时可能整体不存在——防御性读取，
                     // 否则断言脚本会因为"扩展没注册快捷键"这个正确状态而报 TypeError
                     const cmds = chrome.commands ? await chrome.commands.getAll() : [];
                     // B9 的证据：startDesktopSync() 里 create 的复活闹钟。
                     // 它是"SW 顶层脚本跑到最后一行"的唯一可靠证据——2026-09-14 的 SW 顶层
                     // TypeError 事故里，B1~B8 全绿（进程活着、宿主页零错误），只有它变红。
                     const alarms = chrome.alarms ? (await chrome.alarms.getAll()).map((a) => a.name) : [];
                     const local = await chrome.storage.local.get(null);
                     const session = await chrome.storage.session.get(null);
                     return {
                       version: m.version,
                       manifestCommands: Object.keys(m.commands || {}),
                       commandIds: cmds.map((c) => c.name),
                       alarmNames: alarms,
                       hasCommandsApi: !!chrome.commands,
                       localKeys: Object.keys(local),
                       sessionKeys: Object.keys(session),
                     };
                   }"""
            )
        ctx.close()

    local_keys = sw_info.get("localKeys") or []
    session_keys = sw_info.get("sessionKeys") or []
    alarm_names = sw_info.get("alarmNames") or []
    # 反向断言（用户实测 bug 的回归点）：Alt+Q 归桌面壳独占。
    # 若上游同步把 commands.quick-wheel（suggested_key: Alt+Q）带回来，这里立刻红。
    dist_manifest_text = (DIST / "manifest.json").read_text(encoding="utf-8")
    checks = {
        "B1 SW 已启动": bool(sw_info),
        "B2 SW 未崩（读到 manifest 版本）": bool(sw_info.get("version")),
        f"B3 manifest 命令 = {EXPECT_COMMANDS}": sw_info.get("manifestCommands") == EXPECT_COMMANDS,
        "B4 commands.getAll 与 manifest 一致": set(sw_info.get("commandIds") or []) - {"_execute_action"} == set(EXPECT_COMMANDS),
        f"B5 storage 无 {FORBIDDEN_LOCAL_KEYS}": not set(FORBIDDEN_LOCAL_KEYS) & set(local_keys),
        f"B6 session 无 {FORBIDDEN_SESSION_KEYS}": not set(FORBIDDEN_SESSION_KEYS) & set(session_keys),
        "B7 宿主页零 JS 错误": not errors,
        f"B8 manifest 不含 {FORBIDDEN_MANIFEST_TEXT}（Alt+Q 归桌面独占）": FORBIDDEN_MANIFEST_TEXT not in dist_manifest_text,
        # B9 是 2026-09-14 事故的回归点：SW 顶层脚本未跑到底时，B1~B8 全绿而功能全废
        # （进程活着、宿主页零错误，只有「同步桥没启动」这一事实无处体现）。
        # `akso:sync` 闹钟由 startDesktopSync() 创建，是「顶层跑到最后一行」的可靠证据。
        "B9 桌面同步桥已启动（akso:sync 闹钟存在）": EXPECT_ALARM in alarm_names,
        # B10：manifest 未声明命令时 Chrome 不提供 chrome.commands（值为 undefined）——
        # 任何模块在 SW 顶层无条件访问它都会让整个 SW 崩在那一行。
        # 这里钉住「命令清单为空」与「同步桥已启动」必须同时成立。
        "B10 无 commands 声明时 SW 仍跑到底（同步桥已启动）": (
            sw_info.get("manifestCommands") == [] and EXPECT_ALARM in alarm_names
        ),
    }
    print(json.dumps(sw_info, ensure_ascii=False, indent=2))
    print(f"MAIN 壳 fetch patch: {fetch_patched}（信息性：未绑定页签不激活壳，故通常为 False）")
    if errors:
        print(f"pageErrors: {errors[:3]}")
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    passed = sum(1 for ok in checks.values() if ok)
    print(f"\nBOOT_CHECKS: {passed}/{len(checks)}")
    shutil.rmtree(PROFILE, ignore_errors=True)
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
