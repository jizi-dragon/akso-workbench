"""统一账号库：平台环境 + 账号 CRUD + Fernet 凭据加密。

知识迁移映射：
- quick-login `storage/db.ts` 的 ParallelAccount（siteHost/tabName/box 分组）→
  platform_env（平台环境，含 baseUrl）+ account（env_id/username/role/tags）。
- quick-login `background/core/credentials.ts` 的「种子 + 派生密钥 + 随机 IV 加密」思路
  → Fernet（密钥为随机 32 字节 urlsafe，存 settings 表，seed 机制沿用 mogul storage）。

安全纪律：明文密码只在内存/内联参数中出现；绝不入库、绝不写原项目 env 文件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .. import db
from .storage import get_setting, new_id, now_ms, set_setting

_KEY_SETTING = "account_fernet_key"


class AccountError(ValueError):
    """账号库参数/状态错误。"""


def _fernet() -> Fernet:
    key = get_setting(_KEY_SETTING)
    if not key:
        key = Fernet.generate_key().decode("ascii")
        set_setting(_KEY_SETTING, key)
    return Fernet(key.encode("ascii"))


def encrypt_password(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_password(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise AccountError("凭据解密失败：密钥轮换或数据损坏") from exc


# ---------------------------------------------------------------- platform env


def list_envs() -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT e.*, (SELECT COUNT(*) FROM account a WHERE a.env_id = e.id) AS account_count "
        "FROM platform_env e ORDER BY e.created_at"
    )
    for row in rows:
        row["account_count"] = int(row.get("account_count") or 0)
    return rows


def get_env(env_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM platform_env WHERE id = ?", (env_id,))


def create_env(*, name: str, base_url: str = "", note: str = "") -> dict[str, Any]:
    if not name.strip():
        raise AccountError("环境名称不能为空")
    env = {
        "id": new_id(),
        "name": name.strip(),
        "base_url": (base_url or "").strip().rstrip("/"),
        "note": note or "",
        "created_at": now_ms(),
        "updated_at": now_ms(),
    }
    db.execute(
        "INSERT INTO platform_env (id, name, base_url, note, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        tuple(env.values()),
    )
    return env


def update_env(env_id: str, *, name: str | None = None, base_url: str | None = None,
               note: str | None = None) -> dict[str, Any] | None:
    env = get_env(env_id)
    if not env:
        return None
    if name is not None:
        if not name.strip():
            raise AccountError("环境名称不能为空")
        env["name"] = name.strip()
    if base_url is not None:
        env["base_url"] = base_url.strip().rstrip("/")
    if note is not None:
        env["note"] = note
    env["updated_at"] = now_ms()
    db.execute(
        "UPDATE platform_env SET name = ?, base_url = ?, note = ?, updated_at = ? WHERE id = ?",
        (env["name"], env["base_url"], env["note"], env["updated_at"], env_id),
    )
    return env


def delete_env(env_id: str) -> bool:
    return db.execute("DELETE FROM platform_env WHERE id = ?", (env_id,)) > 0


# -------------------------------------------------------------------- accounts


def list_accounts(env_id: str | None = None) -> list[dict[str, Any]]:
    """账号列表（密码不回传，只回传 has_password 与脱敏摘要）。"""
    if env_id:
        rows = db.query(
            "SELECT a.*, e.name AS env_name, e.base_url AS env_base_url "
            "FROM account a JOIN platform_env e ON e.id = a.env_id "
            "WHERE a.env_id = ? ORDER BY a.created_at",
            (env_id,),
        )
    else:
        rows = db.query(
            "SELECT a.*, e.name AS env_name, e.base_url AS env_base_url "
            "FROM account a JOIN platform_env e ON e.id = a.env_id "
            "ORDER BY a.created_at"
        )
    return [_sanitize(row) for row in rows]


def get_account(account_id: str) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT a.*, e.name AS env_name, e.base_url AS env_base_url "
        "FROM account a JOIN platform_env e ON e.id = a.env_id WHERE a.id = ?",
        (account_id,),
    )
    return _sanitize(row) if row else None


def _sanitize(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    enc = row.pop("password_enc", None)
    row["has_password"] = bool(enc)
    try:
        row["tags"] = json.loads(row.get("tags") or "[]")
    except ValueError:
        row["tags"] = []
    return row


def create_account(*, env_id: str, username: str, password: str, role: str = "",
                   tags: list[str] | None = None, note: str = "") -> dict[str, Any]:
    if not get_env(env_id):
        raise AccountError(f"平台环境不存在：{env_id}")
    if not username.strip():
        raise AccountError("用户名不能为空")
    if not password:
        raise AccountError("密码不能为空（自动登录依赖凭据）")
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    account_id = new_id()
    ts = now_ms()
    db.execute(
        "INSERT INTO account (id, env_id, username, password_enc, role, tags, note, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?)",
        (account_id, env_id, username.strip(), encrypt_password(password), role.strip(),
         tags_json, note, ts, ts),
    )
    assert get_account(account_id)
    return get_account(account_id)  # type: ignore[return-value]


def update_account(account_id: str, *, username: str | None = None, password: str | None = None,
                   role: str | None = None, tags: list[str] | None = None,
                   note: str | None = None, status: str | None = None) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM account WHERE id = ?", (account_id,))
    if not row:
        return None
    sets: list[str] = []
    params: list[Any] = []
    if username is not None:
        if not username.strip():
            raise AccountError("用户名不能为空")
        sets.append("username = ?")
        params.append(username.strip())
    if password is not None and password != "":
        sets.append("password_enc = ?")
        params.append(encrypt_password(password))
    if role is not None:
        sets.append("role = ?")
        params.append(role.strip())
    if tags is not None:
        sets.append("tags = ?")
        params.append(json.dumps(tags, ensure_ascii=False))
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if not sets:
        return get_account(account_id)
    sets.append("updated_at = ?")
    params.append(now_ms())
    params.append(account_id)
    db.execute(f"UPDATE account SET {', '.join(sets)} WHERE id = ?", params)
    return get_account(account_id)


def delete_account(account_id: str) -> bool:
    return db.execute("DELETE FROM account WHERE id = ?", (account_id,)) > 0


def reveal_password(account_id: str) -> str:
    """取回明文密码（仅内存使用：自动登录 / 内联注入）。绝不入库/入日志。"""
    row = db.query_one("SELECT password_enc FROM account WHERE id = ?", (account_id,))
    if not row or not row["password_enc"]:
        raise AccountError(f"账号 {account_id} 无凭据")
    return decrypt_password(row["password_enc"])


def account_env_base_url(account_id: str) -> str:
    account = get_account(account_id)
    if not account:
        raise AccountError(f"账号不存在：{account_id}")
    return account.get("env_base_url") or ""


# ------------------------------------------------- 原项目 env 一键导入（只读）


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def scan_original_env_files() -> list[dict]:
    """扫描三个原项目的 env 文件（只读，绝不修改）。

    - akso-cc:      env.json         {environments:[{id,baseUrl,username,password}]}
    - akso-auto:    resources/env/env.json（同构）
    """
    import os

    candidates = [
        ("akso-cc", os.environ.get("AKSO_CC_REPO", r"D:\ai_assistant\akso-cc"), Path("env.json")),
        ("akso-auto", os.environ.get("AKSO_AUTO_REPO", r"D:\ai_assistant\akso-auto"),
         Path("resources") / "env" / "env.json"),
    ]
    found: list[dict] = []
    for module, repo, rel in candidates:
        path = Path(repo) / rel
        data = _read_json(path)
        if not data or not isinstance(data.get("environments"), list):
            found.append({"module": module, "path": str(path), "exists": False, "envs": []})
            continue
        envs = [
            {"env_id": str(e.get("id") or e.get("name") or ""), "name": str(e.get("name") or e.get("id") or ""),
             "base_url": str(e.get("baseUrl") or ""), "username": str(e.get("username") or ""),
             "password": str(e.get("password") or "")}
            for e in data["environments"] if isinstance(e, dict)
        ]
        found.append({"module": module, "path": str(path), "exists": True, "envs": envs})
    return found


def import_from_original_projects(dry_run: bool = False) -> list[dict]:
    """把原项目 env 的环境+账号导入统一账号库（同 baseUrl+username 去重）。"""
    lines: list[dict] = []
    imported = skipped = 0
    for src in scan_original_env_files():
        if not src["exists"]:
            lines.append({"text": f"[{src['module']}] 未找到 {src['path']}，跳过", "cls": "sys"})
            continue
        lines.append({"text": f"[{src['module']}] {src['path']}：{len(src['envs'])} 个环境", "cls": ""})
        for env in src["envs"]:
            if not env["base_url"] or not env["username"]:
                lines.append({"text": "  - 字段不全（缺 baseUrl/username），跳过", "cls": "warn"})
                continue
            existing = db.query_one(
                "SELECT a.id FROM account a JOIN platform_env e ON e.id = a.env_id "
                "WHERE e.base_url = ? AND a.username = ?",
                (env["base_url"].rstrip("/"), env["username"]),
            )
            if existing:
                skipped += 1
                lines.append({"text": f"  - {env['username']} @ {env['base_url']} 已存在，跳过", "cls": "sys"})
                continue
            if dry_run:
                imported += 1
                lines.append({"text": f"  + 将导入 {env['username']} @ {env['base_url']}", "cls": "ok"})
                continue
            target = db.query_one("SELECT id FROM platform_env WHERE base_url = ?", (env["base_url"].rstrip("/"),))
            env_id = target["id"] if target else create_env(
                name=env["name"] or env["env_id"] or env["base_url"], base_url=env["base_url"],
                note=f"导入自 {src['module']}",
            )["id"]
            create_account(
                env_id=env_id, username=env["username"], password=env["password"],
                note=f"导入自 {src['module']}（{env['env_id'] or '默认环境'}）",
            )
            imported += 1
            lines.append({"text": f"  + 已导入 {env['username']} @ {env['base_url']}", "cls": "ok"})
    lines.append({"text": f"■ 完成：导入 {imported}，去重跳过 {skipped}",
                  "cls": "ok" if imported else "sys"})
    return lines
