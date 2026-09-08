"""accounts.py 单测：Fernet 加密闭环 + 环境/账号 CRUD + 原项目 env 扫描（只读）。"""

from __future__ import annotations

import pytest

from workbench.services import accounts


@pytest.fixture()
def env() -> dict:
    return accounts.create_env(name="测试环境", base_url="https://test.example.com/")


def test_fernet_roundtrip(env: dict) -> None:
    account = accounts.create_account(
        env_id=env["id"], username="tester", password="pw123", role="管理员", tags=["a", "b"],
    )
    assert account["has_password"] is True
    assert "password_enc" not in account  # 脱敏：明文/密文都不回传
    assert accounts.reveal_password(account["id"]) == "pw123"


def test_fernet_unique_ciphertexts(env: dict) -> None:
    a = accounts.encrypt_password("same-pw")
    b = accounts.encrypt_password("same-pw")
    assert a != b  # Fernet 每次随机 IV（对应 quick-login 每次随机 IV 语义）
    assert accounts.decrypt_password(a) == accounts.decrypt_password(b) == "same-pw"


def test_account_crud(env: dict) -> None:
    account = accounts.create_account(env_id=env["id"], username="u1", password="p1")
    updated = accounts.update_account(account["id"], role="顾问", status="online")
    assert updated is not None
    assert updated["role"] == "顾问" and updated["status"] == "online"
    assert accounts.delete_account(account["id"]) is True
    assert accounts.get_account(account["id"]) is None


def test_create_account_validations(env: dict) -> None:
    with pytest.raises(accounts.AccountError):
        accounts.create_account(env_id="no-such-env", username="x", password="y")
    with pytest.raises(accounts.AccountError):
        accounts.create_account(env_id=env["id"], username=" ", password="y")
    with pytest.raises(accounts.AccountError):
        accounts.create_account(env_id=env["id"], username="x", password="")


def test_scan_original_env_files_readonly() -> None:
    """只读扫描：不修改原项目文件，只报告发现。"""
    results = accounts.scan_original_env_files()
    assert {r["module"] for r in results} >= {"akso-cc", "akso-auto"}
    for result in results:
        assert isinstance(result["path"], str) and result["path"]
        assert isinstance(result["envs"], list)


def test_import_dry_run() -> None:
    lines = accounts.import_from_original_projects(dry_run=True)
    assert any("完成" in line["text"] for line in lines)


def test_pool_assign_and_members(env: dict) -> None:
    """分配池：加入/组合/移出 + 按角色取用（账号中心合并语义）。"""
    a = accounts.create_account(env_id=env["id"], username="pool-a", password="p1")
    b = accounts.create_account(env_id=env["id"], username="pool-b", password="p2")

    accounts.update_account(a["id"], pool="config")
    accounts.update_account(b["id"], pool=["config", "monitor"])

    config_members = accounts.pool_members("config")
    monitor_members = accounts.pool_members("monitor")
    assert {x["username"] for x in config_members} >= {"pool-a", "pool-b"}
    assert {x["username"] for x in monitor_members} == {"pool-b"}

    accounts.update_account(a["id"], pool="")
    assert accounts.pool_members("config") == [] or \
        all(x["username"] != "pool-a" for x in accounts.pool_members("config"))

    with pytest.raises(accounts.AccountError):
        accounts.update_account(a["id"], pool="bad-role")
