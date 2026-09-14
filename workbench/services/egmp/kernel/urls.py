"""平台地址归一化（**唯一**事实来源，读写两侧共用）。

为什么需要它（2026-09-14 真机实证）：把浏览器地址栏里的**登录页**地址整段粘成环境的
baseUrl（`https://tonbridge-config.aksoegmp.com/login`）后，AUTH 端点会被拼成
`.../login/api/openapi/v1.0/Auth`，网关直接回 **405 Not Allowed**（nginx），
界面上的表现就是「login 连通测试无法连通」。同主机的根地址 POST 同名端点返回的是
平台信封 `{"code":500,...}` 而不是 405 —— 这就是「根地址 vs 登录页地址」的判据。

归一化规则（**保守**，只处理有实证的形态）：

- 去首尾空白、去末尾 ``/``（历史行为，保持兼容）；
- 路径**恰为** ``/login``（大小写不敏感）时剥掉——那是登录页，不是 API 根；
- 其余带路径的地址（真实的子路径挂载部署）**原样保留**，不猜、不截断；
- query / fragment 一律丢弃（baseUrl 的语义就是「根」，带它们只会污染拼接结果）。

`host` 为空或非绝对地址时只做保守清理并原样返回，让上层按「未配置 baseUrl」报错，
而不是在这里悄悄造出一个更奇怪的地址。
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

#: 实证的登录页路径——只有它会被剥掉（见模块 docstring）
_LOGIN_PATH = "/login"


def normalize_base_url(url: str) -> str:
    """把用户填的平台地址归一化成「API 根」。

    幂等：``normalize_base_url(normalize_base_url(x)) == normalize_base_url(x)``。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        # 不是绝对地址（如用户只填了 "10.100.0.105"）：不猜协议，保守去尾斜杠
        return raw.rstrip("/")
    path = parts.path.rstrip("/")
    if path.lower() == _LOGIN_PATH:
        path = ""
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")
