"""pywebview 桌面壳：uvicorn 子进程 + 原生窗口（关闭窗口即退出）。

用法：.venv/Scripts/pythonw.exe shell/shell.py   （无控制台；python.exe 亦可）
- 服务以子进程运行（与窗口线程解耦：pythonw 的 stdout 限制、uvicorn 日志互不干扰）；
- 无 pywebview 时自动降级：打开系统浏览器 + 保持服务直至 Ctrl+C。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser

from workbench import config

# pythonw 模式下 stdout/stderr 为 None：本模块的 print 兜底
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _python_exe() -> str:
    """venv 里与当前解释器同源的 python.exe（pythonw 的兄弟文件）。"""
    candidate = Path(sys.executable).with_name("python.exe")
    return str(candidate) if candidate.exists() else sys.executable


from pathlib import Path  # noqa: E402 —— 置顶导入下方使用


def _start_server_process() -> subprocess.Popen:
    """uvicorn 子进程（独立于窗口生命周期；stdout/stderr 丢弃）。"""
    return subprocess.Popen(
        [
            _python_exe(), "-m", "uvicorn", "workbench.api:app",
            "--host", config.HOST, "--port", str(config.PORT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parent.parent),
    )


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


def main() -> None:
    server = _start_server_process()
    try:
        if not _wait_ready():
            print("服务启动超时，退出")
            raise SystemExit(1)

        url = f"http://{config.HOST}:{config.PORT}"
        try:
            import webview

            webview.create_window(
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
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
