"""打包态服务端入口（PyInstaller → dist/AksoServer/AksoServer.exe）。

调用约定：桌面壳 spawn `AksoServer.exe --server`（见 desktop/main.js 的 spawnServer）。

与开发态入口 `workbench/main.py` 的区别：**不打开浏览器**——打包态 UI 由 Electron 主窗承载，
sidecar 只提供服务本身；另外冻结态会把启动信息落到 `DATA_DIR/server.log`，
因为壳以 `stdio: 'ignore'` 启动本进程，端口占用/启动崩溃在用户现场是"零输出"的。

⚠ 历史教训：本文件在一次重构中丢失，而 server.spec 仍指向它 → `tools/build.ps1` 第 5 步
（PyInstaller）直接失败，而开发态走 venv + uvicorn 完全不受影响，故长期无人察觉。
改动打包链路后务必真跑一次 build.ps1，不要只看开发态。

⚠ 实现约束：本文件被 PyInstaller 当**顶层脚本**执行（spec 的 Analysis 直接指向它），
因此**只能用绝对导入**（`from workbench import ...`）——相对导入（`from . import ...`）
在 `__main__` 语境下会 ImportError。第 4 条断言（tools/verify_packaging.py）会守住这点。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import uvicorn

from workbench import __version__, config


def _log_startup(line: str) -> None:
    """冻结态把启动信息落盘（壳 spawn 时 stdio 被忽略，现场无输出可用）。"""
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

    line = (
        f"AksoServer v{__version__} pid={os.getpid()} → http://{host}:{port} "
        f"(frozen={config.FROZEN})"
    )
    print(line, flush=True)
    _log_startup(line)
    uvicorn.run("workbench.api:app", host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
