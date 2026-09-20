"""脱敏规则 —— **唯一事实源**（`ingest.py` 与 `scan_secrets.py` 共用同一套）。

## 为什么必须在这里做，而不是"提交前补"

平台流量里带着 JWT / 会话令牌 / 口令。这个仓库推到 GitHub 之后，
原始流量一旦进了 git 历史**很难真正删掉**——那既是安全事故，
也直接违反本仓铁律 19「凭据不落明文」与 26「凭据永不入日志」。

所以本模块在 **写文件之前** 就被调用：磁盘上从头到尾没有一份原始流量。

## 保留什么、丢什么（这个取舍决定了分析还有没有用）

| 丢 | 留 |
|---|---|
| 令牌 / 口令 / 会话 id / 签名 | **对象编码、字段编码、字段名、选项值** |
| 主机名（不记录你用的是哪个环境） | 请求路径（`/api/platform/BasicObject/SaveField`） |
| 账号 GUID、本机绝对路径 | 请求体里的**结构**（键名 + 非敏感值） |
| 请求头（Monitor 本来就不录） | 页面上的点击/输入文本（最多 60 字，见 `monitor.py`） |

要分析的是"平台实际接受什么样的请求体"，那部分**一个字都不能丢**；
要防的是"这串东西能拿去冒充你"，那部分**一个字节都不留**。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

PLACEHOLDER = "***"

#: 键名命中即整值替换。**分两档，因为一刀切会把业务字段一起打掉**。
#:
#: ⚠️ 第一版把 `sign(ature)?` 放进子串匹配里，实测立刻打掉了 `enableSignatures`
#: ——那是 `ObjectDef` 的**真实业务字段**（"是否启用电子签名"），
#: 而它被打掉的后果不是"安全了一点"，是**采集到的结构缺了一块**，
#: 分析会得出"平台不发这个键"的错误结论。过度脱敏与脱敏不足一样是缺陷。
#:
#: 分档口径：
#: - **子串命中**（第一档）：这些词出现在任何位置都说明它是个凭据容器
#:   （`accessToken`、`userPassword`、`apiKey`…）；
#: - **整键命中**（第二档）：这些词单独出现才可疑，做前缀/后缀都会误伤
#:   （`signature` vs `enableSignatures`、`session` vs `sessionCount`）。
SENSITIVE_KEY_SUBSTR_RE = re.compile(
    r"(?i)(pass(word|wd)?|pwd|secret|token|jwt|bearer|authorization|cookie|"
    r"credential|private[_-]?key|api[_-]?key|access[_-]?key|refresh[_-]?token|"
    r"fernet|csrf|xsrf|nonce|salt)"
)

#: 整键命中（大小写、`-`/`_` 归一后比较）：`{"signature": "…"}` 要打，
#: `{"enableSignatures": true}` 不能打。
SENSITIVE_KEY_EXACT: frozenset[str] = frozenset({
    "sign", "signed", "signstr", "signature",
    "auth", "session", "sessionid", "ticket",
    "enc", "encrypted", "key", "secretkey",
})


def is_sensitive_key(name: object) -> bool:
    """键名是否指向凭据。**这是脱敏的第一道也是主力防线**（见模块文档）。"""
    key = str(name or "").strip()
    if not key:
        return False
    if key.lower().replace("-", "").replace("_", "") in SENSITIVE_KEY_EXACT:
        return True
    return bool(SENSITIVE_KEY_SUBSTR_RE.search(key))

#: 值形状命中即替换（不管键名叫什么）。
#:
#: ⚠️ **这组规则的首要指标是精度，不是召回。** 第一版把 `/` 放进 base64 字符类、
#: 长度门槛 44，结果**每一个端点路径都命中**（`api/platform/BasicObject/SaveFieldDefaultValue`
#: 正好 47 字符）——18 处"疑似凭据"全是误报。而一个动辄误报的扫描器等于没有：
#: 人会学会忽略它，然后真正的泄漏就从眼皮底下过去（同 PITFALLS #43：
#: 恒红的门禁价值为负）。
#:
#: 所以分工是：**键名规则做主力**（平台响应里的令牌几乎必然挂在 `token`/`accessToken`
#: 这类名下），值形状规则只兜"没有提示性键名"的少数情况，因此宁可窄。
SENSITIVE_VALUE_RES: tuple[re.Pattern[str], ...] = (
    # JWT：`eyJ` 开头的三段 base64url。几乎不可能误报。
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # 显式的 Bearer 头
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    # 标准 base64 长串：**必须带 `=` 填充**才算。
    #
    # 这条的 `=` 是整个规则的关键，不是装饰：不加它，`[A-Za-z0-9+/]{40,}` 会把
    # **整条 URL 路径**吃进去（`/` 在那个字符类里，而路径全是这些字符）——
    # 第二版实测就是这样，32 处"疑似凭据"全是端点路径。
    # Fernet 密钥（32 字节 → 44 字符 + 1 个 `=`）永远带填充，所以这条依然抓得到它。
    re.compile(r"\b[A-Za-z0-9+/]{40,}={1,2}"),
    # 32 位以上连续 hex：MD5/SHA 片段（GUID 带连字符，不走这条）
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
)

#: 兜底启发式：`secrets.token_urlsafe(24)` 那种"24 字符以上、带 `-`/`_`、且含数字"
#: 的串。**用 Python 判定而不是一条正则**，因为条件是两个（有连接符 + 有数字），
#: 塞进一条正则会变成没人敢改的天书。
_TOKENISH_RE = re.compile(r"[A-Za-z0-9_-]{24,}")

#: GUID：**不是**凭据，但它是账号/环境标识，按"假名化"处理而不是丢弃
GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

#: 本机绝对路径（Windows 与 POSIX 两种写法）
PATH_RE = re.compile(r"(?i)\b[A-Z]:\\(?:[^\\\s\"']+\\)*[^\\\s\"']*|/(?:home|Users|var|tmp)/[^\s\"']+")


def pseudonym(raw: str, *, prefix: str = "acct") -> str:
    """稳定假名：同一输入永远同一输出，但**不可反推**。

    为什么不用"账号 1 / 账号 2"：那样两次采集之间对不上号，
    而"同一个人两次配置行为的差异"恰恰是要看的东西之一。
    """
    digest = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def is_tokenish(blob: str) -> bool:
    """这个串像不像"生成出来的令牌"（而不是一个正常标识符或路径片段）。

    条件**两个都要**：含 `-` 或 `_`，且含数字。
    - 端点路径段（`BasicObject`、`SaveField`）没有连接符；
    - 长 CamelCase 标识符同样没有；
    - `snake_case` 的键名有 `_` 但通常没有数字，也短于 24。
    """
    return (bool(re.search(r"[-_]", blob))
            and any(ch.isdigit() for ch in blob)
            and len(blob) >= 24)


def redact_text(text: str) -> str:
    """把一段自由文本里的令牌形状串、GUID、绝对路径替换掉。"""
    if not text:
        return text
    out = text
    for pattern in SENSITIVE_VALUE_RES:
        out = pattern.sub(PLACEHOLDER, out)
    out = _TOKENISH_RE.sub(lambda m: PLACEHOLDER if is_tokenish(m.group(0)) else m.group(0), out)
    out = GUID_RE.sub(lambda m: pseudonym(m.group(0), prefix="id"), out)
    out = PATH_RE.sub("<path>", out)
    return out


def redact_url(url: str) -> str:
    """URL → **只留路径**（丢掉主机与查询值）。

    丢主机是刻意的：报告只需要"打了哪个端点"，而主机名会暴露你用的是哪个平台环境
    （生产/测试/UAT），那属于不该进仓库的信息。查询串按键名脱敏后保留键。
    """
    if not url:
        return ""
    rest = url.split("://", 1)[-1]
    slash = rest.find("/")
    path_and_query = rest[slash:] if slash >= 0 else "/"
    path, _, query = path_and_query.partition("?")
    if not query:
        return path
    parts = []
    for pair in query.split("&"):
        key, sep, value = pair.partition("=")
        parts.append(f"{key}{sep}{redact_query_value(key, value)}")
    return f"{path}?{'&'.join(parts)}"


def redact_query_value(key: str, value: str) -> str:
    if is_sensitive_key(key):
        return PLACEHOLDER
    return redact_text(value)


def redact_obj(value: Any, *, depth: int = 0) -> Any:
    """递归脱敏一个 JSON 结构。

    `depth` 有上限：一个自引用或极深的响应体不该让采集脚本栈溢出——
    那会把"采集失败"变成"采集脚本崩了"，而失败可见是本仓的硬要求。
    """
    if depth > 32:
        return "<深度超限>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                out[key] = PLACEHOLDER
            else:
                out[key] = redact_obj(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact_obj(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_body(body: Any) -> Any:
    """请求体 / 响应体：可能是 JSON 字符串，也可能是已被裁剪过的短串。

    字符串先试 JSON 解析——**能解析就按结构脱敏**（保住键名，那正是要分析的），
    解析不了才按自由文本处理。
    """
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        return redact_obj(body)
    if not isinstance(body, str):
        return body
    text = body.strip()
    if text[:1] in "[{" and text[-1:] in "]}":
        try:
            return redact_obj(json.loads(text))
        except ValueError:
            pass
    return redact_text(body)


def find_secrets(text: str, *, limit: int = 8) -> list[str]:
    """扫出文本里**像凭据**的片段（`scan_secrets.py` 用）。

    刻意与 `redact_text` 共用同一组规则：**扫描规则与脱敏规则必须是同一份**，
    否则会出现"脱敏漏掉的形状，扫描也认不出来"这种最坏的组合
    ——那时你会以为"扫过了就安全"。
    """
    hits: list[str] = []
    for pattern in SENSITIVE_VALUE_RES:
        for match in pattern.finditer(text):
            hits.append(match.group(0)[:24] + "…")
            if len(hits) >= limit:
                return hits
    for match in _TOKENISH_RE.finditer(text):
        blob = match.group(0)
        # GUID 走这条会被误判（36 字符、含连字符与数字）——但它**不是凭据**，
        # 脱敏对它的处理是"假名化"（见 `GUID_RE`）而不是"替换成 ***"。
        # 扫描器跟着同一个口径，否则每一份带 tenantId 的数据都会被判红。
        if is_tokenish(blob) and not GUID_RE.fullmatch(blob):
            hits.append(blob[:24] + "…")
            if len(hits) >= limit:
                return hits
    return hits
