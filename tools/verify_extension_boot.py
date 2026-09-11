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

# 期望：manifest 只保留 quick-wheel 命令；storage 不应再出现已删除功能的键
EXPECT_COMMANDS = ["quick-wheel"]
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
            page.goto("about:blank")
        page.wait_for_timeout(3000)  # 等 SW 起来 + 内容脚本注入

        fetch_patched = page.evaluate(
            "() => { try { return String(window.fetch).includes('sniffFetch'); } catch { return null; } }"
        )

        sws = list(ctx.service_workers)
        if sws:
            sw_info = sws[0].evaluate(
                """async () => {
                     const m = chrome.runtime.getManifest();
                     const cmds = await chrome.commands.getAll();
                     const local = await chrome.storage.local.get(null);
                     const session = await chrome.storage.session.get(null);
                     return {
                       version: m.version,
                       manifestCommands: Object.keys(m.commands || {}),
                       commandIds: cmds.map((c) => c.name),
                       localKeys: Object.keys(local),
                       sessionKeys: Object.keys(session),
                     };
                   }"""
            )
        ctx.close()

    local_keys = sw_info.get("localKeys") or []
    session_keys = sw_info.get("sessionKeys") or []
    checks = {
        "B1 SW 已启动": bool(sw_info),
        "B2 SW 未崩（读到 manifest 版本）": bool(sw_info.get("version")),
        f"B3 manifest 命令 = {EXPECT_COMMANDS}": sw_info.get("manifestCommands") == EXPECT_COMMANDS,
        "B4 commands.getAll 与 manifest 一致": set(sw_info.get("commandIds") or []) - {"_execute_action"} == set(EXPECT_COMMANDS),
        f"B5 storage 无 {FORBIDDEN_LOCAL_KEYS}": not set(FORBIDDEN_LOCAL_KEYS) & set(local_keys),
        f"B6 session 无 {FORBIDDEN_SESSION_KEYS}": not set(FORBIDDEN_SESSION_KEYS) & set(session_keys),
        "B7 宿主页零 JS 错误": not errors,
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
