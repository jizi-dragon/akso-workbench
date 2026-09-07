"""pytest 全局夹具：所有测试用隔离的临时数据目录/数据库。

必须在导入 workbench 之前设置环境变量（config 在导入期读取）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="akso-workbench-test-"))
os.environ["WORKBENCH_DATA"] = str(_TMP / "data")
os.environ["WORKBENCH_DB"] = str(_TMP / "data" / "test.db")
os.environ["WORKBENCH_ADAPTERS"] = str(Path(__file__).resolve().parent.parent / "adapters")
