"""pywebview 桌面壳：uvicorn 子进程 + 原生窗口（关闭窗口即退出）。

用法：
- 源码态：.venv/Scripts/pythonw.exe shell/shell.py（无控制台；python.exe 亦可）
- 打包态：dist 里的 AksoWorkbench.exe（PyInstaller onedir；服务以 --server
  参数重入自身 exe，保持"窗口与服务的进程隔离"架构）

- 无 pywebview 时自动降级：打开系统浏览器 + 保持服务直至退出。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser

import uvicorn

from workbench import config

# pythonw 模式下 stdout/stderr 为 None：print 与 uvicorn 日志会静默崩溃，先兜底
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

FROZEN = getattr(sys, "frozen", False)

# 轮盘单例窗口（全局热键 toggle 目标；js_api close/move 也操作它）
_wheel_window: Any = None


def _python_exe() -> str:
    """服务子进程解释器：源码态 = venv 的 python.exe（pythonw 的兄弟文件）。"""
    candidate = Path(sys.executable).with_name("python.exe")
    return str(candidate) if candidate.exists() else sys.executable


def _run_server_blocking() -> None:
    uvicorn.run("workbench.api:app", host=config.HOST, port=config.PORT,
                log_level="warning", factory=False)


def _start_server_process() -> subprocess.Popen:
    """服务子进程（独立于窗口生命周期；stdout/stderr 丢弃）。

    打包态：自身 exe 以 --server 参数重入（onedir 内含完整运行时）。
    源码态：venv 的 python -m uvicorn。
    """
    if FROZEN:
        args = [sys.executable, "--server"]
        cwd = str(Path(sys.executable).parent)
    else:
        args = [
            _python_exe(), "-m", "uvicorn", "workbench.api:app",
            "--host", config.HOST, "--port", str(config.PORT),
        ]
        cwd = str(Path(__file__).resolve().parent.parent)
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
    )


from pathlib import Path  # noqa: E402 —— 置顶导入下方使用


def _wait_ready(timeout_s: float = 30.0) -> bool:
    import urllib.request

    url = f"http://{config.HOST}:{config.PORT}/"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


class WheelApi:
    """轮盘窗口的 js_api：拖动移动 / 程序化关闭（window.close 不可靠）。"""

    def close(self) -> None:
        global _wheel_window
        w = _wheel_window
        if w is not None:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
            _wheel_window = None

    def move(self, dx: int, dy: int) -> None:
        w = _wheel_window
        if w is None:
            return
        try:
            x = getattr(w, "x", None) or 100
            y = getattr(w, "y", None) or 100
            w.move(int(x) + int(dx), int(y) + int(dy))
        except Exception:  # noqa: BLE001
            pass


def _hotkey_open_wheel() -> None:
    """轮盘单例 toggle：未开则建（frameless + 置顶 + js_api），已开则销毁。"""
    import webview  # noqa: PLC0415 —— 热键线程内延迟导入

    global _wheel_window
    # 单例守卫：按标题+引用清扫（WebView2 可能把窗口标题改成页面标题，两种都匹配）
    for w in list(webview.windows):
        if w is _wheel_window or getattr(w, "title", "") in {"Akso 轮盘", "Akso Workbench · 账号轮盘"}:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
            if w is _wheel_window:
                _wheel_window = None
            return  # toggle：本次按键 = 关闭
    try:
        _wheel_window = webview.create_window(
            "Akso 轮盘",
            f"http://{config.HOST}:{config.PORT}/static/pages/wheel-picker.html",
            width=560, height=640,
            on_top=True, focus=True, frameless=True,
            background_color="#121C2E",
            js_api=WheelApi(),
        )
    except Exception:
        try:
            webbrowser.open(f"http://{config.HOST}:{config.PORT}/static/pages/wheel-picker.html")
        except OSError:
            pass


def _global_hotkey_loop() -> None:
    """全局热键 Alt+Q：任何应用/页面下呼出账号轮盘（系统级注册，按键被本应用接管）。"""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    MOD_ALT = 0x0001
    MOD_NOREPEAT = 0x4000
    VK_Q = 0x51
    WM_HOTKEY = 0x0312
    if not user32.RegisterHotKey(None, 1, MOD_ALT | MOD_NOREPEAT, VK_Q):
        print("全局热键 Alt+Q 注册失败（可能被其他程序占用）")
        return
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            _hotkey_open_wheel()


def main() -> None:
    if "--server" in sys.argv:
        # 打包态服务重入：运行 API 后阻塞（由父进程 kill_tree 回收）
        _run_server_blocking()
        return

    server = _start_server_process()
    try:
        if not _wait_ready():
            print("服务启动超时，退出")
            raise SystemExit(1)

        url = f"http://{config.HOST}:{config.PORT}"
        try:
            import webview

            window = webview.create_window(
                "Akso Workbench", url, width=1440, height=920, min_size=(1100, 700),
            )

            def _health_check() -> None:
                """首屏体检：模块依赖缺失时弹提示。"""
                import httpx

                time.sleep(2)
                try:
                    data = httpx.get(f"{url}/api/modules", timeout=10).json()
                    bad = [m for m in data.get("modules", []) if m["status"] != "ok"]
                    if bad:
                        names = "、".join(m["id"] for m in bad)
                        window.evaluate_js(
                            f'alert("以下模块依赖未就绪：{names}。\\n详情见 /api/modules。")'
                        )
                except Exception:  # noqa: BLE001 —— 体检失败不影响窗口
                    pass

            threading.Thread(target=_health_check, daemon=True).start()
            if sys.platform == "win32":
                threading.Thread(target=_global_hotkey_loop, daemon=True).start()
            webview.start()  # 阻塞至窗口关闭
            return
        except ImportError:
            print("pywebview 未安装（uv sync --extra desktop），降级为浏览器模式")
        webbrowser.open(url)
        print(f"Akso Workbench 运行中：{url}（关闭本进程即退出）")
        try:
            while server.poll() is None:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        # 按进程树终止（uvicorn → playwright 驱动 → chromium 全链路），
        # 避免 TerminateProcess 只杀直接子进程导致托管浏览器变孤儿
        kill_tree(server.pid)
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def kill_tree(pid: int) -> None:
    """Windows：taskkill /T /F 杀整棵进程树；其他平台退化为 terminate。"""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, check=False,
        )
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


if __name__ == "__main__":
    main()
