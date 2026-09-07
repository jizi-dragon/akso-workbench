"""启动入口：python -m workbench.main（自动打开浏览器）。"""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

from . import config


def main() -> None:
    url = f"http://{config.HOST}:{config.PORT}"
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"Akso Workbench 启动中：{url}（Ctrl+C 退出）")
    uvicorn.run("workbench.api:app", host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
