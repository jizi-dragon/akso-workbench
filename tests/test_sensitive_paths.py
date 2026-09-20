"""敏感路径**只有一张表**，且只读工具真的按它拦（P0-B 的回归）。

## 缺陷原状（2026-09-20 实测复现，见 docs/AGENT-TOOL-AUDIT.md P0-B）

仓里有**两张**敏感路径表：

| 表 | 位置 | 含 `browser-states` / `egmp-tokens` / `workbench.db` / `accounts.json`？ |
|---|---|---|
| 沙箱的 | `sandbox/policy.py` | ✅ 全含 |
| 只读工具的 | `plugins/builtins.py`（`_BLOCKED_PARTS`） | ❌ **一个都不含** |

而 `builtins._guard_path()` 的顺序是「先查自己那张残表 → 再 `is_inside_app` 就 `return`」，
`is_inside_app()` 的根包含 `DATA_DIR` 与 `RUNTIME_DIR`——**凭据恰好都在那里**，
且 `return` 之后连"文本后缀 + 2MB 上限"两道也不生效。

实测结果（`tmp/probe_sensitive_paths.py`）：三个真实形态的产物被**完整读出**：

    runtime/browser-states/acct-1.json   → cookie + localStorage token
    runtime/egmp-tokens/token-env1.json  → 平台 JWT
    runtime/accounts-backup/accounts.json → **Fernet 密钥 + 密文口令**

主库则返回 **1,056,412 字符**（本次密钥落在 16k 截断之外，**但那不是控制，是运气**）。

## 修法

1. 表收归 `config.SENSITIVE_PATH_PARTS`（唯一事实源），两个消费者都从它读；
2. `_guard_path` 把敏感表判定放在 `is_inside_app` **之前**（与位置无关）；
3. `sandbox/policy.py` 只做再导出，`_BLOCKED_PARTS` 删除。

## 本文件是**反证过**的

`tools/verify_approval_tests.py` 会把 `_guard_path` 的顺序改回缺陷版，期望本文件变红。
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from workbench import config
from workbench import plugin_api as pa
from workbench.plugins.builtins import ReadLocalFileArgs, read_local_file

#: 真实形态的凭据产物（键名/形状照本仓实际落盘写）
CREDENTIAL_ARTIFACTS = [
    "runtime/browser-states/acct-1.json",
    "runtime/egmp-tokens/token-env1.json",
    "runtime/accounts-backup/accounts.json",
    "runtime/desktop-ports.json",
    "shell-state.json",
    "workbench.db",
    "workbench.db-wal",
    ".ssh/id_rsa",
]


def read(path) -> str:
    return asyncio.run(read_local_file(ReadLocalFileArgs(path=str(path)), pa.ToolContext()))


# ---------------------------------------------------------------- 唯一事实源

def test_sensitive_table_has_exactly_one_definition_site() -> None:
    """表在**源码里只能有一处定义**——直接查源文件，而不是比对象 identity。

    为什么不用 `policy.SENSITIVE_PATH_PARTS is config.SENSITIVE_PATH_PARTS`：
    `tests/test_ports_contract.py` 会 `importlib.reload(config)`，
    重载后 `config` 的属性被换成**新对象**，而 `sandbox/policy.py` 还握着旧引用——
    于是 identity 断言变成**测试顺序相关**的（本文件第一版就是这么写错的）。
    真正要守的不变量是"**只有一处定义**"，那就直接查定义处。
    """
    import re

    from workbench import __file__ as pkg_file

    root = pathlib.Path(pkg_file).parent
    definers = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if re.search(r"^SENSITIVE_PATH_PARTS\s*[:=]", path.read_text(encoding="utf-8"), re.M)
    ]
    assert definers == ["config.py"], (
        f"敏感表被多处定义（或定义处不是 config.py）：{definers}")


def test_policy_reexports_the_same_values() -> None:
    """再导出必须**同值**（identity 见上一条的说明）。"""
    from workbench.sandbox import policy

    assert tuple(policy.SENSITIVE_PATH_PARTS) == tuple(config.SENSITIVE_PATH_PARTS)
    assert policy.is_sensitive_path("C:/x/browser-states/y") is True


def test_second_table_is_gone() -> None:
    """`plugins/builtins.py` 里那张残表必须**不存在**（回来了就是又开了一个事实源）。"""
    from workbench.plugins import builtins

    assert not hasattr(builtins, "_BLOCKED_PARTS"), (
        "第二张敏感表又回来了——见 docs/AGENT-TOOL-AUDIT.md P0-B")


@pytest.mark.parametrize("name", [
    "browser-states", "egmp-tokens", "workbench.db", "workbench.db-wal",
    "accounts.json", "shell-state.json", "desktop-ports.json", "pending_action",
])
def test_paths_the_weak_table_missed_are_now_sensitive(name: str) -> None:
    """残表漏掉的那批名字，现在必须命中（这是 P0-B 的判据本身）。"""
    assert config.is_sensitive_path(f"C:/Users/x/AppData/Roaming/Workbench/{name}/f.json")
    assert config.is_sensitive_path(f"C:/Users/x/AppData/Roaming/Workbench/{name}")


def test_posix_and_windows_separators_are_equivalent() -> None:
    """判定必须对两种分隔符一致——本仓路径来自两处（Python 与扩展/平台）。"""
    assert config.is_sensitive_path("C:\\Users\\x\\Workbench\\browser-states\\a.json")
    assert config.is_sensitive_path("C:/Users/x/Workbench/browser-states/a.json")


# ---------------------------------------------------------------- 只读工具真的按它拦

@pytest.mark.parametrize("rel", CREDENTIAL_ARTIFACTS)
def test_read_local_file_rejects_credential_artifacts(tmp_path, rel: str) -> None:
    """**P0-B 的核心断言**：应用目录内的凭据产物必须被拒。

    放在 `config.DATA_DIR` 下（即"应用目录内"）—— 修复前这一条恰好是被
    `is_inside_app` 那条 `return` 放行的路径。
    """
    target = config.DATA_DIR / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"fernetKey": "REAL-FERNET-KEY==", "token": "REAL-JWT"}',
                      encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="凭据"):
            read(target)
    finally:
        if target.exists():
            target.unlink()


def test_read_local_file_rejects_the_real_database() -> None:
    """主库（含 `account_fernet_key` 与全部 `password_enc`）必须被拒。

    修复前实测：它被返回 1,056,412 字符（截断后模型仍能看到前 16k）。
    """
    db_path = config.DATA_DIR / "workbench.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    created = not db_path.exists()
    if created:
        db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    try:
        with pytest.raises(ValueError, match="凭据"):
            read(db_path)
    finally:
        if created:
            db_path.unlink()


def test_normal_file_inside_data_dir_is_still_readable() -> None:
    """**收紧不能把正常用途一起打死**：应用目录内的普通文本仍要可读。

    这是本仓反复踩到的一类回归——"按形态设边界"变成了"什么都不给"。
    """
    target = config.DATA_DIR / "notes" / "readme.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("正常的笔记内容", encoding="utf-8")
    try:
        assert read(target) == "正常的笔记内容"
    finally:
        target.unlink()


def test_insight_workspace_artifacts_are_still_readable() -> None:
    """洞察工作区产物（`.md`）仍要可读——那是 `read_local_file` 的**主要合法用途**。"""
    target = config.DATA_DIR / "insight-workspace" / "capa_plan__c" / "spider-network.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# 关系网\n", encoding="utf-8")
    try:
        assert "关系网" in read(target)
    finally:
        target.unlink()


def test_outside_app_dir_rules_still_apply(tmp_path) -> None:
    """应用目录**之外**的两道（文本后缀 + 体积上限）不能被这次改动碰坏。"""
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="仅允许文本类"):
        read(exe)

    big = tmp_path / "big.txt"
    big.write_text("x" * (2 * 1024 * 1024 + 10), encoding="utf-8")
    with pytest.raises(ValueError, match="过大"):
        read(big)


def test_directory_is_still_rejected() -> None:
    with pytest.raises(ValueError, match="目录"):
        read(config.DATA_DIR)
