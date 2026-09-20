"""启动入口：python -m workbench.main（自动打开浏览器）。

日志在此**最先装配**：装配之前发生的任何事都只会进 stderr，而打包态的 stderr 是黑洞。
"""

from __future__ import annotations

import threading
import webbrowser

import uvicorn

from . import config, logging_setup


def main() -> None:
    logging_setup.configure()
    logging_setup.install_excepthooks()

    url = f"http://{config.HOST}:{config.PORT}"
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f"Workbench 启动中：{url}（Ctrl+C 退出）")
    # 启动横幅由 `api.lifespan` 记（它是**所有启动路径的唯一交集**——
    # 入口脚本漏掉了"桌面壳直接 spawn uvicorn"那条路，见 logging_setup.banner 的文档串）。
    # `log_config=None`：把日志配置的所有权收归 `logging_setup`。
    # 否则 uvicorn 会在 run() 里用自己的 dictConfig 覆盖日志器，
    # 它的输出就**只进它自己的 handler、不进我们的文件**。
    uvicorn.run("workbench.api:app", host=config.HOST, port=config.PORT, log_config=None)


if __name__ == "__main__":
    main()
