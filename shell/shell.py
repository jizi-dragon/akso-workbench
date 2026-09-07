"""pywebview 桌面壳：起 uvicorn 子线程 + 开窗口 + 依赖体检首屏。

用法：.venv/Scripts/python shell/shell.py
（无 pywebview 时自动降级为纯服务器模式 + 打开系统浏览器）
"""

from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

from workbench import config


def _start_server() -> None:
    uvicorn.run("workbench.api:app", host=config.HOST, port=config.PORT,
                log_level="warning", factory=False)


def _wait_ready(timeout_s: float = 20.0) -> bool:
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
    server = threading.Thread(target=_start_server, daemon=True)
    server.start()
    if not _wait_ready():
        print("服务启动超时，退出")
        raise SystemExit(1)

    url = f"http://{config.HOST}:{config.PORT}"
    try:
        import webview  # pywebview

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
                        f'alert("以下模块依赖未就绪：{names}。\\n详情见「设置」或 /api/modules。")'
                    )
            except Exception:  # noqa: BLE001 —— 体检失败不影响窗口
                pass

        threading.Thread(target=_health_check, daemon=True).start()
        webview.start()
        return
    except ImportError:
        print("pywebview 未安装（pip install pywebview），降级为浏览器模式")
    webbrowser.open(url)

    print(f"Akso Workbench 运行中：{url}（Ctrl+C 退出）")
    try:
        while server.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
