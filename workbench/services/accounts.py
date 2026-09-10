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
_DISABLED_BOXES_KEY = "disabled_boxes"


def list_disabled_boxes() -> list[str]:
    """禁用盒清单（轮盘跳过语义，上游 3.10；settings 表持久化）。"""
    try:
        return json.loads(get_setting(_DISABLED_BOXES_KEY) or "[]")
    except (TypeError, ValueError):
        return []


def set_box_disabled(box: str, disabled: bool) -> list[str]:
    current = list_disabled_boxes()
    if disabled and box not in current:
        current.append(box)
    if not disabled and box in current:
        current.remove(box)
    set_setting(_DISABLED_BOXES_KEY, json.dumps(current, ensure_ascii=False))
    return current


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


# 分配池角色（账号中心「分配池」语义）：池内账号供配置/监听平台取用
POOL_ROLES = ("config", "monitor")


def _normalize_pool(pool: str | list[str] | tuple[str, ...] | None) -> str:
    """规范化池角色：接受 'config' / 'monitor' / 'config,monitor' / 列表，返回逗号串。"""
    if pool is None:
        return ""
    if isinstance(pool, str):
        parts = [p.strip() for p in pool.split(",") if p.strip()]
    else:
        parts = [str(p).strip() for p in pool if str(p).strip()]
    invalid = [p for p in parts if p not in POOL_ROLES]
    if invalid:
        raise AccountError(f"非法池角色：{','.join(invalid)}（可用：{'/'.join(POOL_ROLES)}）")
    ordered = [role for role in POOL_ROLES if role in parts]
    return ",".join(ordered)


def pool_members(role: str) -> list[dict[str, Any]]:
    """取分配池中指定角色的账号（如 config → 洞察/工厂下拉数据源）。

    匹配语义：pool 列为逗号串（'config' / 'monitor' / 'config,monitor'），
    用 ,包夹 LIKE 匹配，保证组合值命中。
    """
    normalized = _normalize_pool(role)
    if not normalized:
        return []
    conditions = " OR ".join("(',' || pool || ',') LIKE ?" for _ in normalized.split(","))
    params = [f"%,{r}%" for r in normalized.split(",")]
    rows = db.query(
        "SELECT a.*, e.name AS env_name, e.base_url AS env_base_url "
        "FROM account a JOIN platform_env e ON e.id = a.env_id "
        f"WHERE {conditions} ORDER BY a.created_at",
        params,
    )
    return [_sanitize(row) for row in rows]


def _sanitize(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    enc = row.pop("password_enc", None)
    row["has_password"] = bool(enc)
    row.setdefault("pool", "")
    try:
        row["tags"] = json.loads(row.get("tags") or "[]")
    except ValueError:
        row["tags"] = []
    return row


def create_account(*, env_id: str, username: str, password: str, role: str = "",
                   tags: list[str] | None = None, note: str = "",
                   pool: str | list[str] | tuple[str, ...] | None = None,
                   box: str = "", tab_name: str = "") -> dict[str, Any]:
    if not get_env(env_id):
        raise AccountError(f"平台环境不存在：{env_id}")
    if not username.strip():
        raise AccountError("用户名不能为空")
    if not password:
        raise AccountError("密码不能为空（自动登录依赖凭据）")
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    pool_value = _normalize_pool(pool)
    account_id = new_id()
    ts = now_ms()
    db.execute(
        "INSERT INTO account (id, env_id, username, password_enc, role, tags, note, status, "
        "pool, box, tab_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', ?, ?, ?, ?, ?)",
        (account_id, env_id, username.strip(), encrypt_password(password), role.strip(),
         tags_json, note, pool_value, box.strip(), tab_name.strip(), ts, ts),
    )
    assert get_account(account_id)
    return get_account(account_id)  # type: ignore[return-value]


def update_account(account_id: str, *, username: str | None = None, password: str | None = None,
                   role: str | None = None, tags: list[str] | None = None,
                   note: str | None = None, status: str | None = None,
                   pool: str | list[str] | tuple[str, ...] | None = None,
                   box: str | None = None, tab_name: str | None = None,
                   env_id: str | None = None) -> dict[str, Any] | None:
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
    if pool is not None:
        sets.append("pool = ?")
        params.append(_normalize_pool(pool))
    if box is not None:
        sets.append("box = ?")
        params.append(box.strip())
    if tab_name is not None:
        sets.append("tab_name = ?")
        params.append(tab_name.strip())
    if env_id is not None:
        if not get_env(env_id):
            raise AccountError(f"目标平台环境不存在：{env_id}")
        sets.append("env_id = ?")
        params.append(env_id)
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


# ------------------------------------------------- 盒子管理（quick-login 盒子语义）

DEFAULT_BOX = ""  # 空 = 默认盒子
_REMEMBERED_KEY = "rememberedBoxes"
_DEFAULT_BOX_NAME_KEY = "defaultBoxName"


def _box_display(name: str) -> str:
    """盒子的展示名（空串 = 默认盒子，可自定义其显示名）。"""
    if name.strip():
        return name.strip()
    custom = get_setting(_DEFAULT_BOX_NAME_KEY)
    return custom or "默认盒子"


def list_boxes() -> list[dict[str, Any]]:
    """记忆的盒子清单（空盒也保留，对齐原 ql:boxes 语义）+ 每盒账号数。"""
    remembered: list[str] = []
    raw = get_setting(_REMEMBERED_KEY)
    if raw:
        try:
            remembered = [str(x) for x in json.loads(raw)]
        except ValueError:
            remembered = []
    rows = db.query(
        "SELECT box, COUNT(*) AS n FROM account WHERE box != '' GROUP BY box ORDER BY box"
    )
    counts = {str(r["box"]): int(r["n"]) for r in rows}
    ordered: list[str] = []
    for name in remembered:
        if name not in ordered:
            ordered.append(name)
    for name in counts:
        if name not in ordered:
            ordered.append(name)
    default_count = int(
        (db.query_one("SELECT COUNT(*) AS n FROM account WHERE box = ''") or {}).get("n") or 0
    )
    return [
        {"box": name, "displayName": _box_display(name), "count": counts.get(name, 0)}
        for name in ordered
    ] + [{"box": DEFAULT_BOX, "displayName": _box_display(DEFAULT_BOX), "count": default_count}]


def _remember_box(name: str) -> None:
    name = name.strip()
    if not name:
        return
    remembered: list[str] = []
    raw = get_setting(_REMEMBERED_KEY)
    if raw:
        try:
            remembered = [str(x) for x in json.loads(raw)]
        except ValueError:
            remembered = []
    if name not in remembered:
        remembered.append(name)
        set_setting(_REMEMBERED_KEY, json.dumps(remembered, ensure_ascii=False))


def _forget_box(name: str) -> None:
    """从记忆清单移除盒子（删除/重命名后旧名不再保留——曾遗留空盒 chip，0.2.13 修复）。"""
    name = name.strip()
    if not name:
        return
    raw = get_setting(_REMEMBERED_KEY)
    if not raw:
        return
    try:
        remembered = [str(x) for x in json.loads(raw)]
    except ValueError:
        return
    if name in remembered:
        remembered = [x for x in remembered if x != name]
        set_setting(_REMEMBERED_KEY, json.dumps(remembered, ensure_ascii=False))


def rename_box(from_name: str, to_name: str) -> int:
    """盒子重命名 / 移动账号（to 为空 = 并入默认盒子）。返回随迁账号数。

    记忆清单**原位替换**（用户实测 0.2.16：追加+删除会让重命名后的盒子排到末尾）。
    """
    from_name = from_name.strip()
    to_name = to_name.strip()
    if not from_name:
        raise AccountError("来源盒子不能为空")
    if to_name == from_name:
        return 0
    count = db.execute(
        "UPDATE account SET box = ?, updated_at = ? WHERE box = ?",
        (to_name, now_ms(), from_name),
    )
    raw = get_setting(_REMEMBERED_KEY)
    remembered: list[str] = []
    if raw:
        try:
            remembered = [str(x) for x in json.loads(raw)]
        except ValueError:
            remembered = []
    if from_name in remembered:
        # 原位替换 / 移除（to 为空 = 并入默认盒，位置移除）
        remembered = [to_name if x == from_name else x for x in remembered] if to_name \
            else [x for x in remembered if x != from_name]
        set_setting(_REMEMBERED_KEY, json.dumps(remembered, ensure_ascii=False))
    elif to_name:
        _remember_box(to_name)
    return count


def create_box(name: str) -> None:
    """新建一个（可为空的）记忆盒子（原 ql:boxes 空盒保留语义）。"""
    _remember_box(name.strip())


def delete_box(name: str) -> int:
    """删除盒子 = 账号并入默认盒子 + 移除记忆盒名。"""
    return rename_box(name, DEFAULT_BOX)


def set_default_box_name(name: str) -> None:
    set_setting(_DEFAULT_BOX_NAME_KEY, name.strip())


# ------------------------------------------------- 备份导出 / 导入（DataBackup v1 语义）


def export_backup() -> dict[str, Any]:
    """导出备份：加密凭据 + Fernet 密钥随文件走（原 cryptoSeed 语义）。

    ⚠ 文件本身即凭据（含密钥），交付给用户自行保管。
    """
    key = get_setting(_KEY_SETTING) or ""
    return {
        "format": "akso-workbench-backup",
        "version": 1,
        "exportedAt": now_ms(),
        "fernetKey": key,
        "envs": [
            {"id": e["id"], "name": e["name"], "baseUrl": e["base_url"], "note": e["note"]}
            for e in list_envs()
        ],
        "boxes": {
            "remembered": json.loads(get_setting(_REMEMBERED_KEY) or "[]"),
            "defaultName": get_setting(_DEFAULT_BOX_NAME_KEY) or "",
        },
        "accounts": [
            {
                "id": a["id"],
                "envBaseUrl": a.get("env_base_url") or "",
                "username": a["username"],
                "passwordEnc": db.query_one(
                    "SELECT password_enc FROM account WHERE id = ?", (a["id"],)
                )["password_enc"],
                "box": a.get("box") or "",
                "role": a.get("role") or "",
                "tags": a.get("tags") or [],
                "tabName": (a.get("tab_name") or "").strip(),
            }
            for a in list_accounts()
        ],
    }


def import_backup(data: dict[str, Any]) -> dict[str, Any]:
    """导入备份：用文件内密钥解密 → 本地密钥重加密 → 同站同名去重。"""
    from cryptography.fernet import Fernet as _F

    if data.get("format") != "akso-workbench-backup" or data.get("version") != 1:
        raise AccountError("不是有效的 Akso Workbench 备份文件（format/version 不符）")
    file_key = str(data.get("fernetKey") or "")
    if not file_key:
        raise AccountError("备份缺少密钥（fernetKey）")
    file_fernet = _F(file_key.encode("ascii"))

    env_by_url = {e["base_url"]: e["id"] for e in list_envs()}
    existing = {(a.get("env_base_url") or "", a["username"]) for a in list_accounts()}
    created = skipped = 0
    for item in data.get("accounts") or []:
        base_url = str(item.get("envBaseUrl") or "").strip().rstrip("/")
        enc = str(item.get("passwordEnc") or "")
        username = str(item.get("username") or "").strip()
        if not base_url or not username or not enc:
            skipped += 1
            continue
        try:
            password = file_fernet.decrypt(enc.encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001 —— 无法用文件密钥解开（损坏/篡改）→ 跳过
            skipped += 1
            continue
        if (base_url, username) in existing:
            skipped += 1
            continue
        env_id = env_by_url.get(base_url)
        if not env_id:
            env = create_env(name=base_url, base_url=base_url, note="导入自备份文件")
            env_by_url[base_url] = env["id"]
            env_id = env["id"]
        create_account(
            env_id=env_id, username=username, password=password,
            role=str(item.get("role") or ""), tags=list(item.get("tags") or []),
            note="导入自备份文件",
        )
        accounts_rows = db.query(
            "SELECT id FROM account WHERE env_id = ? AND username = ?", (env_id, username)
        )
        if accounts_rows and item.get("box"):
            db.execute("UPDATE account SET box = ? WHERE id = ?",
                       (str(item["box"]).strip(), accounts_rows[0]["id"]))
            _remember_box(str(item["box"]))
        existing.add((base_url, username))
        created += 1
    # 盒子配置以文件为准覆盖（记忆盒/默认盒名）
    boxes = data.get("boxes") or {}
    if isinstance(boxes.get("remembered"), list):
        set_setting(_REMEMBERED_KEY, json.dumps(
            [str(x) for x in boxes["remembered"]], ensure_ascii=False))
    if boxes.get("defaultName"):
        set_setting(_DEFAULT_BOX_NAME_KEY, str(boxes["defaultName"]).strip())
    return {"created": created, "skipped": skipped}


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
