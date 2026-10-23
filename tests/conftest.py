"""pytest 全局夹具：所有测试用隔离的临时数据目录/数据库。

必须在导入 workbench 之前设置环境变量（config 在导入期读取）。

★ 2026-10-22：临时目录改为 `TemporaryDirectory`，会话结束即删（`WORKBENCH_TEST_KEEP=1`
可保留现场）。配合 `config.has_explicit_data_dir()`：显式数据目录**不接管遗留库**，
所以测试不再把用户的真实账号库复制进 `%TEMP%`。

★ 清理不能只靠 `TemporaryDirectory` 的退出 finalizer：Windows 上 SQLite 连接/子进程
可能短暂占住文件，finalizer 失败后不会重试，于是留下一个几百 MB 的
`workbench-test-*`。这里显式做 **close → retry rmtree**，并在失败时把路径打出来。
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

#: 现场保留开关：排查挂住的用例时设 `WORKBENCH_TEST_KEEP=1`，产物留在 `%TEMP%`。
_KEEP = os.environ.get("WORKBENCH_TEST_KEEP") == "1"
_DEBUG_CLEANUP = os.environ.get("WORKBENCH_TEST_CLEANUP_DEBUG") == "1"

#: 目录生命周期 = 本进程；`delete=False` 时由用户自行清理。
_TMP_OBJ = tempfile.TemporaryDirectory(prefix="workbench-test-", delete=not _KEEP)
_TMP = Path(_TMP_OBJ.name)

os.environ["WORKBENCH_DATA"] = str(_TMP / "data")
os.environ["WORKBENCH_DB"] = str(_TMP / "data" / "test.db")
os.environ["WORKBENCH_ADAPTERS"] = str(Path(__file__).resolve().parent.parent / "adapters")
#: 供少数需要知道"隔离目录在哪"的用例读取（调试用，不作为产品契约）。
os.environ["WORKBENCH_TEST_TMP"] = str(_TMP)


def _cleanup_temp() -> None:
    """关连接 + 重试删除。失败时**明说路径**，不静默留垃圾。"""
    if _KEEP:
        return
    for attempt in range(4):
        try:
            from workbench import db

            db.close()
        except Exception:  # noqa: BLE001 —— 关连接失败不能让清理直接放弃
            pass
        # 日志 handler 也占着 `data/logs/*.jsonl`：不关就删不掉（Windows 文件锁）。
        logging.shutdown()
        gc.collect()
        try:
            shutil.rmtree(_TMP)
            if _DEBUG_CLEANUP:
                print(f"[conftest] cleaned {_TMP} on attempt {attempt}", flush=True)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if _DEBUG_CLEANUP:
                print(f"[conftest] attempt {attempt} failed: {type(exc).__name__}: {exc}",
                      flush=True)
            # 常见原因：daemon 线程/子进程还没退完，文件仍被占用。
            time.sleep(0.25 * (attempt + 1))
    print(f"[conftest] WARNING 临时目录未能清理：{_TMP}", flush=True)


def pytest_sessionfinish(session, exitstatus) -> None:
    """会话结束立刻清理；不把几百 MB 的测试数据留到下一次运行。"""
    _cleanup_temp()
