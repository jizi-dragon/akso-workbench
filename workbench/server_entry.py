"""打包态服务端入口（PyInstaller → dist/WorkbenchServer/WorkbenchServer.exe）。

调用约定：Tauri 壳 spawn `WorkbenchServer.exe --server --host 127.0.0.1 --port <动态端口>`
（见 `src-tauri/src/main.rs` 的 spawn_sidecar）。

与开发态入口 `workbench/main.py` 的区别：**不打开浏览器**——打包态 UI 由 Tauri 主窗承载；
冻结态会把启动信息落到 `DATA_DIR/server.log`，因为壳以文件重定向接收 stdout/stderr，
端口占用/启动崩溃在用户现场需要可查。

⚠ 历史教训：本文件在一次重构中丢失，而 server.spec 仍指向它 → PyInstaller 第 5 步
直接失败，而开发态走 venv + uvicorn 完全不受影响，故长期无人察觉。
改动打包链路后务必真跑一次 `tools/build_tauri.ps1`，不要只看开发态。

⚠ 实现约束：本文件被 PyInstaller 当**顶层脚本**执行（spec 的 Analysis 直接指向它），
因此**只能用绝对导入**（`from workbench import ...`）——相对导入（`from . import ...`）
在 `__main__` 语境下会 ImportError。P2 断言（tools/verify_packaging.py）会守住这点。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import uvicorn

from workbench import __version__, config, logging_setup


def _log_startup(line: str) -> None:
    """**面包屑**：一行启动信息落 `DATA_DIR/server.log`。

    注意它与 `logging_setup` 的分工——**这不是"另一个日志"，是日志系统自身失败时的兜底**：

    - 正常路径：`logging_setup` 把启动横幅与后续一切写进 `logs/workbench-<date>.jsonl`；
    - 兜底路径：若**日志系统都建不起来**（磁盘满、目录不可写、`logging` 初始化异常），
      本函数仍会往 `server.log` 留一行——因为它是裸 `open()`，不依赖任何日志设施。

    保留它还有一个具体理由：`workbench/static/blank.html` 的"服务端没有应答"页面
    **直接把这个文件名告诉了用户**。删掉它会让那句提示指向一个不存在的文件。
    """
    if not config.FROZEN:
        return
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (config.DATA_DIR / "server.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
    except Exception:  # noqa: BLE001 —— 日志失败绝不影响启动
        pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    host, port = config.HOST, config.PORT
    i = 0
    while i < len(args):
        # `--server` 是桌面壳的调用约定（"只跑服务、不开浏览器"）；本入口本就如此，
        # 故仅识别不分支——保留它是为了兼容壳的固定命令行，并让参数表自解释。
        if args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
            continue
        if args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
            continue
        i += 1

    # 日志必须**在 uvicorn 之前**装配：壳以 `stdio: 'ignore'` 启动本进程，
    # 装配前发生的任何失败（PyInstaller 引导、import 期异常）都无处可查。
    logging_setup.configure()
    logging_setup.install_excepthooks()

    line = (
        f"WorkbenchServer v{__version__} pid={os.getpid()} -> http://{host}:{port} "
        f"(frozen={config.FROZEN})"
    )
    print(line, flush=True)
    _log_startup(line)
    # 启动横幅由 `api.lifespan` 记（所有启动路径的唯一交集，见 logging_setup.banner 的文档串）
    # 见 `main.py` 同一处的说明：日志配置所有权归 `logging_setup`。
    uvicorn.run("workbench.api:app", host=host, port=port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
