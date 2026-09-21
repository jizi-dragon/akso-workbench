"""壳控制服务的**跨源边界**（2026-09-21 实测收口）。

## 缺陷原状（用户报告："自动更新提示自己弹出来，而且你一拉起服务就复现"）

排查结论：

    壳控制服务 `127.0.0.1:18767` **无认证**，`json()` 又给每个响应都加
    `Access-Control-Allow-Origin: *`；而那几个 POST 都是**简单请求**
    （无自定义头、无 JSON body）⇒ 浏览器**不预检**，跨源直接放行。
    ⇒ 本机任何一个网页都能：弹更新框、让应用退出时装更新、开出可见窗口。

**证据链是两条互相独立的记录**（缺一不可，单看任何一条都会误判）：

1. 壳侧日志出现 `[akso-shell] 控制服务请求检查更新`；
2. Python 代理侧（`routes_update.update_action` 的 referer 日志）**一次都没有**
   收到 `/api/update/check` ⇒ 请求是**直接打到控制端口**的，绕过了整个后端。

「壳没跑时静默失败、壳一起来就复现」正是它的指纹——端口没人听，那个外部请求就发不出去。

## 为什么这里是静态断言

真正跑起来验需要 Electron 壳（pytest 起不了它）。所以本文件只钉**边界本身还在**；
活的验证由人工跑这三条（2026-09-21 实测：403 / 200 / 200）：

```powershell
# 1) 跨源触发更新检查 —— 必须 403
Invoke-WebRequest http://127.0.0.1:18767/update-check -Method POST `
  -Headers @{Origin='https://evil.example'} -UseBasicParsing
# 2) 扩展的身份自证 —— 必须仍然 200（挡过头就把扩展打断了）
Invoke-WebRequest http://127.0.0.1:18767/whoami `
  -Headers @{Origin='chrome-extension://abcdef'} -UseBasicParsing
# 3) 本机进程调用（不带 Origin）—— 必须仍然 200
Invoke-WebRequest http://127.0.0.1:18767/windows/close -Method POST `
  -ContentType application/json -Body '{}' -UseBasicParsing
```

⚠️ **改这个文件之前先读这段**：控制服务新增**任何**会改状态 / 弹窗 / 开窗 / 拉起安装器
的端点时，必须同时把它加进 `CHANGING_PATHS`。漏一个就是一条新的"网页可驱动"入口，
而且它**不会报错**——表现只是"某个弹窗/窗口偶尔自己冒出来"。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN_JS = ROOT / "desktop" / "main.js"

#: 会改状态 / 弹窗 / 开窗 / 拉起安装器的端点 —— 必须挡跨源
MUST_BLOCK_CROSS_ORIGIN = (
    "/windows",
    "/windows/focus",
    "/windows/close",
    "/update-check",
    "/update-install",
    "/extension-setup",
)

#: 只读、且**必须**允许跨源的端点。
#: `/whoami` 是扩展的端口与身份发现入口（ADR-0011 §D-5）——挡掉它扩展就连不上壳。
MUST_ALLOW_CROSS_ORIGIN = ("/health", "/whoami", "/update-state")


def _src() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


def _guarded_paths() -> set[str]:
    block = re.search(r"const CHANGING_PATHS = new Set\(\[(.*?)\]\)", _src(), re.S)
    assert block, (
        "desktop/main.js 里找不到 CHANGING_PATHS——跨源闸门被删了？"
        "删掉它等于把控制服务重新开放给本机上的任意网页"
    )
    return set(re.findall(r"'(/[^']*)'", block.group(1)))


def test_every_state_changing_control_endpoint_is_guarded() -> None:
    listed = _guarded_paths()
    for path in MUST_BLOCK_CROSS_ORIGIN:
        assert path in listed, f"{path} 不在 CHANGING_PATHS 里：它又能被网页驱动了"


def test_read_only_endpoints_stay_reachable_cross_origin() -> None:
    """挡过头会把扩展打断——这是这道闸门唯一的"误伤"方向，单独钉一条。"""
    listed = _guarded_paths()
    for path in MUST_ALLOW_CROSS_ORIGIN:
        assert path not in listed, f"{path} 是只读发现端点，挡掉它扩展就连不上壳了"


def test_the_guard_decides_on_the_origin_header() -> None:
    """判据必须是"有没有 `Origin`"，不是来源 IP、也不是白名单。

    我们自己的调用方（Python sidecar 的 `urllib`）**不发 Origin**；浏览器跨源 POST
    **一定发**。所以这一条闸门只挡网页、不挡本机进程，且不需要维护任何名单
    ——名单会漂，而"浏览器会不会带这个头"是规范行为。
    """
    src = _src()
    assert "req.headers.origin" in src, "闸门没有看 Origin 头——那它挡不住网页"
    assert "403" in src, "闸门没有拒绝码"


def test_cors_wildcard_is_kept_for_the_read_only_endpoints() -> None:
    """`Access-Control-Allow-Origin: *` **不能**顺手删掉。

    它是有意的：只读端点要给扩展跨源读（`/whoami`）。挡住写端点靠的是上面那道
    "带 Origin 就拒"的闸门——**不是靠 CORS**：简单请求本来就不受 CORS 管辖，
    这正是本缺陷的成因。删掉 ACAO 只会打断扩展，并不会让写端点更安全。
    """
    assert "Access-Control-Allow-Origin" in _src()
