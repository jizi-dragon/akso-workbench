"""Akso Workbench 云端账号库客户端 —— 设备授权登录 + 会话保持。

## 它解决什么

桌面端原先只有一个"登录 / 云端账号库"的链接，点开浏览器就到此为止 ——
桌面端**不知道自己是谁**。这个模块补上那半条链路：用标准设备流（RFC 8628）
把会话拿到本地，之后就能代表用户去读写云端账号库。

## 三条纪律（都是本仓已有的）

**① 凭据不落明文。** 会话令牌经 `accounts.encrypt_password` 加密后存 settings
（与平台口令同一把 `account_fernet_key`）。令牌的**权限比单个平台口令更大**
——它一次能读全部账号，所以它没有任何理由比口令存得松。

**② 同步阻塞一律挪线程。** `requests` 是同步的，调用方（路由）是 `async def`
⇒ 必须走 `asyncio.to_thread`，否则表现是**整个服务卡住**而不是报错（不变量 I11）。
本模块自身保持**同步**，把"要不要挪线程"的决定留给调用方 —— 这样它也能被
同步的测试直接调用。

**③ 设备流不抄码。** `device_start` 拿到的 `deviceCode` 只在本模块内存里活着，
**不入库、不入日志**；`userCode` 才是给人看的。两者混用的后果见 vault 服务端
`routers/device.py` 的注释。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import accounts, storage

#: 云端地址。默认指向正式环境；`AKSO_VAULT_URL` 可以覆盖（本地联调时用）。
#:
#: ★ 2026-10-19：默认端口从 `:8443` 改为标准的 **443**。
#: 服务端 2026-10-13 已完成迁移（备案通过），`:8443` 现在只是一段
#: `iptables REDIRECT` 垫片；垫片按云端 README 的约定会在两端客户端升级后撤掉。
#: 继续把新安装指向旧端口 = 给将来预埋一次"登录突然打不开"。
DEFAULT_URL = os.environ.get("AKSO_VAULT_URL") or "https://www.dragonrain.top"

_SETTING_TOKEN = "vault_session_token"      # 密文
_SETTING_PROFILE = "vault_profile"          # 明文 JSON：displayName/avatar 不是凭据
_SETTING_URL = "vault_base_url"
_SETTING_LIBRARY = "vault_library_cache"    # **脱敏后**的展示快照（不含 fernetKey/passwordEnc）
_SETTING_LIBRARY_AT = "vault_library_cache_at"
_SETTING_REVISION = "vault_revision"        # 上次拉取/推送的云端 revision（乐观并发）

#: 展示缓存的保鲜期。云控台改一条账号后，桌面端最多 5 分钟内看到旧数据；
#: 用户点「同步数据」会绕过它（`library(refresh=True)`）。
LIBRARY_TTL_MS = 5 * 60 * 1000

#: 云端快照合法的 `format` 值。★ 2026-10-11 服务端改过名，但形状没变；
#: 新客户端**两个都接受**（见 akso-cloud `docs/SYNC-CONTRACT.md` §3.1）。
SNAPSHOT_FORMATS = frozenset({"akso-cloud-snapshot", "akso-vault-snapshot"})

#: 单次请求超时。设备流是**轮询**，所以这个值要小 —— 大超时会让
#: "网络断了"表现为"界面卡住十几秒没有任何反馈"。
TIMEOUT_S = 12


class VaultError(RuntimeError):
    """云端调用失败。消息**面向用户**（会被直接显示在界面上）。

    ★ `kind` 是**机器可读**分类，调用方只准读它，不许拿消息文本猜
    （旧实现用 `"401" in str(exc)` / `"会话" in text`：云端换一句措辞，
    401 就退化成 502，界面会把"请重新登录"显示成"上游挂了"）。
    `status` 是 HTTP 状态码（没有则 0），只用于展示/诊断。
    """

    KINDS = frozenset({"auth", "network", "format", "validation", "upstream"})

    def __init__(self, message: str, status: int = 0, kind: str = "upstream") -> None:
        super().__init__(message)
        if kind not in self.KINDS:
            raise ValueError(f"未知的 VaultError.kind：{kind!r}")
        self.status = status
        self.kind = kind


def base_url() -> str:
    return (storage.get_setting(_SETTING_URL) or "").strip() or DEFAULT_URL


def _request(method: str, path: str, *, body: dict | None = None, token: str | None = None) -> dict:
    """发一次请求。失败一律转成**可读的** `VaultError`。

    ★ 这里刻意**不区分**"服务端返回的错误"与"网络不通"以外的情形：
    调用方（界面）需要知道的是"能不能用"和"为什么不能"，而不是 HTTP 细节。
    但两者必须**分开说** —— 混成一句"请求失败"会让用户去查错方向。
    """
    url = base_url().rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:  # noqa: BLE001 —— 错误体不是 JSON 也不该让这里炸
            pass
        raise VaultError(
            detail or f"云端返回 {exc.code}",
            status=exc.code,
            kind="auth" if exc.code == 401 else "upstream",
        ) from exc
    except urllib.error.URLError as exc:
        raise VaultError(
            f"连不上云端（{exc.reason}）—— 请检查网络与地址", kind="network") from exc
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        # 200 但不是 JSON：多半是被中间设备（代理/门户）拦截了。
        # 明说这一条，否则用户会以为是自己密码错了。
        raise VaultError(
            "云端返回的不是 JSON —— 疑似被代理或网络门户拦截", kind="format") from exc


# ---------------------------------------------------------------- 会话

def session_token() -> str:
    """取当前会话令牌（解密后）。没有会话就返回空串。"""
    blob = storage.get_setting(_SETTING_TOKEN) or ""
    if not blob:
        return ""
    try:
        return accounts.decrypt_password(blob)
    except Exception:  # noqa: BLE001 —— 密钥换了/密文坏了，都当作"没有会话"
        return ""


def save_session(token: str, profile: dict) -> None:
    storage.set_setting(_SETTING_TOKEN, accounts.encrypt_password(token))
    storage.set_setting(_SETTING_PROFILE, json.dumps(profile, ensure_ascii=False))
    # 换了一个会话 ⇒ 上一份展示缓存属于**上一个身份**，必须丢。
    clear_library_cache()


def clear_session() -> None:
    """只清本地。**不调服务端的 /logout** —— 那个动作由 `logout()` 负责，
    分开是因为"清本地"必须是**永远不会失败**的那一步（用户点了退出，
    界面就必须退出，不能因为网络不通而卡在一个半登录状态）。"""
    storage.set_setting(_SETTING_TOKEN, "")
    storage.set_setting(_SETTING_PROFILE, "")
    clear_library_cache()


def profile() -> dict:
    raw = storage.get_setting(_SETTING_PROFILE) or ""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def status() -> dict:
    """界面首屏用：当前登没登录、登的是谁、连的是哪个地址。

    ★ **不在这里验证令牌**。它会被页面频繁调用，而每次验证都是一次网络往返；
    真正的失效由 `fetch_profile()` 在需要时暴露（401 → 调用方 `clear_session()`）。
    """
    prof = profile()
    return {
        "loggedIn": bool(session_token()),
        "baseUrl": base_url(),
        "profile": prof,
        "displayName": prof.get("displayName") or prof.get("email") or "",
        "avatar": prof.get("avatar") or "",
        "email": prof.get("email") or "",
    }


# ---------------------------------------------------------------- 设备流

def device_start(client_name: str = "桌面工作台") -> dict:
    """第一拍：申请一对码。**无需登录**。

    返回里 `verificationUrl` 是服务端拼好的完整地址 —— 界面**直接打开它**，
    不要自己拼（拼错的表现是"打开了网页却发现要手输码"，那时很难归因）。
    """
    return _request("POST", "/api/auth/device-start", body={"clientName": client_name})


def device_poll(device_code: str) -> dict:
    """第二拍：问"批准了吗"。`status` 见服务端 `routers/device.py`。"""
    return _request("POST", "/api/auth/device-poll", body={"deviceCode": device_code})


def device_login(client_name: str = "桌面工作台") -> dict:
    """**给测试与命令行用的一步式登录**：开局 → 返回给调用方去开浏览器。

    真正的轮询在界面侧（它要能显示"等待中…"并能取消），所以这里只做第一拍。
    保留这个薄封装是为了让"设备流"这件事在代码里有一个**可读的入口名**。
    """
    return device_start(client_name)


# ---------------------------------------------------------------- 账号库

def fetch_profile(token: str | None = None) -> dict:
    """校验会话并取回最新个人信息。

    ★ 令牌失效（401）时**顺手清掉本地会话**：不清的话，界面会一直显示
    "已登录"，而每个动作都失败 —— 用户面对的是一个自相矛盾的界面。
    """
    tok = token or session_token()
    if not tok:
        raise VaultError("尚未登录云端账号库", kind="auth")
    try:
        resp = _request("GET", "/api/auth/profile", token=tok)
    except VaultError as exc:
        if exc.kind == "auth":
            clear_session()
        raise
    prof = resp.get("profile") or {}
    save_session(tok, prof)
    return prof


#: 账号库同步（`GET /api/vault/snapshot` / `POST /api/vault/merge`）**刻意未实现**：
#: 云端契约要求 `snapshotId` 幂等、空快照不清本地、`revision` 乐观并发（S1–S4），
#: 而本仓只做了设备流登录。下面只做**只读展示**：拉快照 → 脱敏 → 缓存。


def _now_ms() -> int:
    return int(time.time() * 1000)


def is_auth_error(exc: VaultError) -> bool:
    """会话失效的**唯一**判据是 `kind`，不是消息里有没有"401/会话/重新登录"。"""
    return exc.kind == "auth"


def clear_library_cache() -> None:
    """清掉展示缓存。退出 / 换会话时调用（缓存里没有凭据，但它是**上一个身份的数据**）。"""
    storage.set_setting(_SETTING_LIBRARY, "")
    storage.set_setting(_SETTING_LIBRARY_AT, "")


def _load_library_cache() -> dict | None:
    raw = storage.get_setting(_SETTING_LIBRARY) or ""
    if not raw:
        return None
    try:
        view = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(view, dict):
        return None
    try:
        view["_cachedAt"] = int(storage.get_setting(_SETTING_LIBRARY_AT) or 0)
    except (TypeError, ValueError):
        view["_cachedAt"] = 0
    return view


def _store_library_cache(view: dict) -> None:
    storage.set_setting(_SETTING_LIBRARY, json.dumps(view, ensure_ascii=False))
    storage.set_setting(_SETTING_LIBRARY_AT, str(_now_ms()))


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _str(value: object, default: str = "") -> str:
    return str(value) if value is not None else default


def _tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _snapshot() -> dict:
    """拉云端全量快照并做**形状校验**（不校验的后果是页面静默空白）。"""
    token = session_token()
    if not token:
        raise VaultError("尚未登录云端账号库", kind="auth")
    data = _request("GET", "/api/vault/snapshot", token=token)
    if not isinstance(data, dict):
        raise VaultError("云端快照形状不对：顶层不是对象", kind="format")
    if data.get("format") not in SNAPSHOT_FORMATS:
        raise VaultError(f"云端快照格式不认识：{data.get('format')!r}", kind="format")
    if _int(data.get("version"), 0) != 1:
        raise VaultError(f"云端快照版本不支持：{data.get('version')!r}", kind="format")
    for key in ("sites", "accounts", "boxes"):
        if not isinstance(data.get(key), list):
            raise VaultError(f"云端快照缺少数组字段：{key}", kind="format")
    return data


def _safe_library(data: dict) -> dict:
    """把云端快照压成**可以给浏览器看**的只读视图。

    ★ **绝不透传** `fernetKey` 与 `passwordEnc`：前者能解开全部密码，后者是密文。
    页面需要的"有没有密码"由 `hasPassword` 表达，不需要密文本体。
    """
    sites = [
        {
            "id": _str(site.get("id")),
            "name": _str(site.get("name")),
            "baseUrl": _str(site.get("baseUrl")),
            "note": _str(site.get("note")),
            "createdAt": _int(site.get("createdAt")),
            "updatedAt": _int(site.get("updatedAt")),
            "accountCount": _int(site.get("accountCount")),
        }
        for site in data.get("sites") or [] if isinstance(site, dict)
    ]
    accounts = [
        {
            "id": _str(account.get("id")),
            "siteId": _str(account.get("siteId")),
            "siteName": _str(account.get("siteName")),
            "baseUrl": _str(account.get("baseUrl")),
            "username": _str(account.get("username")),
            "tabName": _str(account.get("tabName")) or _str(account.get("username")),
            "role": _str(account.get("role")),
            "tags": _tags(account.get("tags")),
            "note": _str(account.get("note")),
            "status": _str(account.get("status"), "idle"),
            "scheme": _str(account.get("scheme")),
            "box": _str(account.get("box")),
            "createdAt": _int(account.get("createdAt")),
            "updatedAt": _int(account.get("updatedAt")),
            "hasPassword": bool(account.get("passwordEnc")),
        }
        for account in data.get("accounts") or [] if isinstance(account, dict)
    ]
    boxes = [
        {
            "name": _str(box.get("name")),
            "isDefault": bool(box.get("isDefault")),
            "disabled": bool(box.get("disabled")),
            "autoDisabled": bool(box.get("autoDisabled")),
            "count": _int(box.get("count")),
            "deletable": bool(box.get("deletable")),
            "renameable": bool(box.get("renameable")),
            "bindable": bool(box.get("bindable")),
            "siteId": _str(box.get("siteId")),
            "siteName": _str(box.get("siteName")),
            "sortOrder": _int(box.get("sortOrder"), 999),
        }
        for box in data.get("boxes") or [] if isinstance(box, dict)
    ]
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    return {
        "format": _str(data.get("format")),
        "version": _int(data.get("version"), 1),
        "snapshotId": _str(data.get("snapshotId")),
        "revision": _int(data.get("revision")),
        "generatedAt": _int(data.get("generatedAt")),
        "fetchedAt": _now_ms(),
        "defaultBox": _str(data.get("defaultBox"), "默认盒子"),
        "counts": {
            "sites": _int(counts.get("sites"), len(sites)),
            "accounts": _int(counts.get("accounts"), len(accounts)),
            "boxes": _int(counts.get("boxes"), len(boxes)),
        },
        "sites": sites,
        "accounts": accounts,
        "boxes": boxes,
    }


def library(refresh: bool = False) -> dict:
    """云端账号库的**只读展示视图**（脱敏 + 5 分钟缓存 + 断网回落）。

    - `refresh=True` 绕过 TTL（用户点「同步数据」）；
    - 断网/5xx 时若有缓存，返回 `stale=True` + `error`，页面显示"上次同步的数据"；
    - **401 不回落**：会话失效必须让用户重新登录（返回旧数据会假装还登录着）。
    """
    cached = _load_library_cache()
    if not refresh and cached and _now_ms() - int(cached.get("_cachedAt") or 0) < LIBRARY_TTL_MS:
        view = dict(cached)
        view.pop("_cachedAt", None)
        view.update(stale=False, fromCache=True)
        _remember_revision(view)
        return view
    try:
        view = _safe_library(_snapshot())
        _store_library_cache(view)
        _remember_revision(view)
        return {**view, "stale": False, "fromCache": False}
    except VaultError as exc:
        if is_auth_error(exc):
            clear_session()
            raise
        if cached:
            view = dict(cached)
            view.pop("_cachedAt", None)
            view.update(stale=True, fromCache=True, error=str(exc))
            _remember_revision(view)
            return view
        raise


def _remember_revision(view: dict) -> None:
    revision = _int(view.get("revision"))
    if revision > 0:
        storage.set_setting(_SETTING_REVISION, str(revision))


def _host_of(base_url: str) -> tuple[str, str]:
    raw = (base_url or "").strip()
    if not raw:
        return "", ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urllib.parse.urlsplit(raw)
    return (parsed.hostname or "").lower(), parsed.scheme or "https"


def _local_merge_payload() -> dict:
    """把本机账号库整理成云端 `/api/vault/merge` 的请求体。

    ★ 只作为“本地→云端”的一次性迁移/推送：账号中心不再提供本地增删改。
    密码在内存里解密后随 HTTPS 请求发出，不落日志；没有密码的账号发空串。
    """
    sites_by_host: dict[str, dict] = {}
    rows: list[dict] = []
    for item in accounts.list_accounts():
        host, scheme = _host_of(str(item.get("env_base_url") or ""))
        if not host:
            continue
        sites_by_host.setdefault(host, {
            "host": host,
            "name": str(item.get("env_name") or host),
            "scheme": scheme,
            "note": "",
        })
        try:
            password = accounts.reveal_password(str(item["id"])) if item.get("has_password") else ""
        except Exception:  # noqa: BLE001 —— 单条坏密文不拖垮整次推送
            password = ""
        rows.append({
            "siteHost": host,
            "username": str(item.get("username") or ""),
            "password": password,
            "tabName": str(item.get("tab_name") or ""),
            "box": str(item.get("box") or ""),
            "scheme": "",
            "updatedAt": _int(item.get("updated_at")),
        })
    payload: dict = {
        "sites": list(sites_by_host.values()),
        "accounts": rows,
        "clientAccountCount": len(rows),
    }
    revision = _int(storage.get_setting(_SETTING_REVISION))
    if revision > 0:
        payload["baseRevision"] = revision
    return payload


def push_local() -> dict:
    """把本机账号库并上云端（`POST /api/vault/merge`）。

    - 仅在已登录时可用；请求走与拉取相同的会话令牌；
    - 带上上次拉取的 `revision`，云端 409 时**不重试**，把“重新拉取后再推”
      交给用户/界面（M3：拿旧 revision 反复重试会永远 409）；
    - 成功后记住新的 `revision`，供下一次推送使用。
    """
    token = session_token()
    if not token:
        raise VaultError("请先登录云端账号库", kind="auth")
    payload = _local_merge_payload()
    if not payload["accounts"]:
        raise VaultError("本机没有可推送的账号", kind="validation")
    resp = _request("POST", "/api/vault/merge", body=payload, token=token)
    revision = _int(resp.get("revision"))
    if revision > 0:
        storage.set_setting(_SETTING_REVISION, str(revision))
    return {
        "ok": bool(resp.get("ok", True)),
        "sent": len(payload["accounts"]),
        "sites": len(payload["sites"]),
        "result": resp,
    }


def logout() -> None:
    """退出：**先告诉服务端吊销**，再清本地。

    顺序不能反。先清本地的话，服务端那个会话仍然活着 ——
    而"我点了退出"在用户心里等于"那个令牌已经没用了"。
    但即使服务端调用失败，本地也一定要清干净（见 `clear_session` 的注释）。
    """
    tok = session_token()
    try:
        if tok:
            _request("POST", "/api/auth/logout", token=tok)
    except VaultError:
        pass
    finally:
        clear_session()
