"""数据目录隔离：显式 `WORKBENCH_DATA` 时**不接管**遗留库。

## 为什么需要它（2026-10-22 实测）

`config.bootstrap_database()` 原先在目标库不存在时**无条件**按 `legacy_db_candidates()`
复制旧库。测试夹具用 `WORKBENCH_DATA` 指向临时目录，于是**每次 pytest 都把用户的真实库
复制一份**进 `%TEMP%`：本机实测 210 个目录 / 327 MB / 102 份可解密的凭据库
（含 `account_fernet_key` 与 9 条 `password_enc`）。

修复后的语义：**显式指定目录 = 用这个目录，别自己找**；接管只服务"用户首次启动"。

## 为什么用子进程

`config` 在导入期读取环境变量、`db` 在进程内缓存连接。用子进程才能真正验证
"同一份代码在指定环境变量下的行为"，而不是在已导入的模块上做近似。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PROBE = """
import json
from workbench import db
db.connect()
rows = db.query("select name from sqlite_master where type='table'")
print(json.dumps({"tables": sorted(r["name"] for r in rows)}))
"""


def _make_legacy_db(appdata: Path) -> Path:
    """造一个带标记表的"旧库"，放在 `%APPDATA%/AksoWorkbench/` 下。"""
    legacy = appdata / "AksoWorkbench" / "workbench.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(legacy)
    try:
        conn.execute("CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO legacy_marker (note) VALUES ('real user data')")
        conn.commit()
    finally:
        conn.close()
    return legacy


def _probe_env(tmp_path: Path, *, explicit: bool) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("WORKBENCH_DATA", "WORKBENCH_DB", "MOGUL_DATA", "MOGUL_DB"):
        env.pop(key, None)
    env["APPDATA"] = str(tmp_path / "appdata")
    env["PYTHONUTF8"] = "1"
    if explicit:
        env["WORKBENCH_DATA"] = str(tmp_path / "data")
        env["WORKBENCH_DB"] = str(tmp_path / "data" / "test.db")
    return env


def _run_probe(tmp_path: Path, *, explicit: bool) -> set[str]:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(ROOT),
        env=_probe_env(tmp_path, explicit=explicit),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    assert proc.returncode == 0, f"探针失败：{proc.stderr[-2000:]}"
    return set(json.loads(proc.stdout.strip().splitlines()[-1])["tables"])


def test_explicit_data_dir_does_not_adopt_the_legacy_database(tmp_path: Path) -> None:
    """显式 `WORKBENCH_DATA` ⇒ 新库是空的，旧库里的标记表**不得**出现。"""
    _make_legacy_db(tmp_path / "appdata")
    tables = _run_probe(tmp_path, explicit=True)
    assert "legacy_marker" not in tables, (
        "显式指定数据目录时仍然接管了遗留库 —— 测试/验收脚本会读到用户的真实数据。"
        f"实际表：{sorted(tables)}"
    )
    assert "_sqlx_migrations" in tables, "迁移没跑：这个用例没有验证到真实启动路径"


def test_without_explicit_data_dir_the_legacy_database_is_still_adopted(tmp_path: Path) -> None:
    """没有显式目录时，旧库接管仍然生效（这是"用户首次启动"的承诺）。"""
    _make_legacy_db(tmp_path / "appdata")
    tables = _run_probe(tmp_path, explicit=False)
    assert "legacy_marker" in tables, (
        "未显式指定目录时没有接管旧库 —— 真实用户升级后会看到「账号全没了」。"
        f"实际表：{sorted(tables)}"
    )


def test_tests_themselves_run_in_an_isolated_temp_home() -> None:
    """本进程（pytest）的数据目录必须在临时目录里，且不是用户的 `%APPDATA%`。"""
    from workbench import config

    data_dir = Path(os.environ["WORKBENCH_DATA"])
    assert data_dir == config.DATA_DIR
    assert "workbench-test-" in data_dir.parent.name
    assert data_dir != Path(os.environ.get("APPDATA", "")) / "Workbench"
    assert Path(os.environ["WORKBENCH_TEST_TMP"]).is_dir()
    assert Path(os.environ["WORKBENCH_DB"]).parent == data_dir
