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
import urllib.error
import urllib.parse
import urllib.request

from . import accounts, storage

#: 云端地址。默认指向正式环境；`AKSO_VAULT_URL` 可以覆盖（本地联调时用）。
DEFAULT_URL = os.environ.get("AKSO_VAULT_URL") or "https://www.dragonrain.top:8443"

_SETTING_TOKEN = "vault_session_token"      # 密文
_SETTING_PROFILE = "vault_profile"          # 明文 JSON：displayName/avatar 不是凭据
_SETTING_URL = "vault_base_url"

#: 单次请求超时。设备流是**轮询**，所以这个值要小 —— 大超时会让
#: "网络断了"表现为"界面卡住十几秒没有任何反馈"。
TIMEOUT_S = 12


class VaultError(RuntimeError):
    """云端调用失败。消息**面向用户**（会被直接显示在界面上）。"""


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
        raise VaultError(detail or f"云端返回 {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise VaultError(f"连不上云端（{exc.reason}）—— 请检查网络与地址") from exc
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        # 200 但不是 JSON：多半是被中间设备（代理/门户）拦截了。
        # 明说这一条，否则用户会以为是自己密码错了。
        raise VaultError("云端返回的不是 JSON —— 疑似被代理或网络门户拦截") from exc


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


def clear_session() -> None:
    """只清本地。**不调服务端的 /logout** —— 那个动作由 `logout()` 负责，
    分开是因为"清本地"必须是**永远不会失败**的那一步（用户点了退出，
    界面就必须退出，不能因为网络不通而卡在一个半登录状态）。"""
    storage.set_setting(_SETTING_TOKEN, "")
    storage.set_setting(_SETTING_PROFILE, "")


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
        raise VaultError("尚未登录云端账号库")
    try:
        resp = _request("GET", "/api/auth/profile", token=tok)
    except VaultError as exc:
        if "会话" in str(exc) or "401" in str(exc) or "重新登录" in str(exc):
            clear_session()
        raise
    prof = resp.get("profile") or {}
    save_session(tok, prof)
    return prof


def snapshot() -> dict:
    """拉一份完整账号库快照（站点 + 账号 + fernetKey）。"""
    tok = session_token()
    if not tok:
        raise VaultError("尚未登录云端账号库")
    return _request("GET", "/api/vault/snapshot", token=tok)


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
