"""上下文管理：token 估算 + 阈值裁剪（按轮次）。"""

from __future__ import annotations

import json
import re

_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-zA-Z0-9_]+")

#: 一张图进模型大概折合多少 token（**估个量级，不追求精确**）。
#:
#: 为什么要一个常数：多模态请求里图片**不占文本 token**，但**确实占上下文与账单**
#: （实测：一张 8×8 的 PNG 也让 `prompt_tokens` 从 20 量级涨到 223）。
#: 不折算的话，`build_context` 会以为"这条消息几乎不占地方"，于是图片一直不被折叠、
#: 越堆越多——而它们是**唯一**会随轮次悄悄推高成本的东西。
#: 600 是保守取法：真实值随分辨率走（远小于一张大图、远大于一个短句）。
IMAGE_TOKEN_ESTIMATE = 600


def content_to_text(content: object) -> str:
    """把 content（字符串 **或** 多模态 parts）压成可估/可摘要的文本。

    唯一真源：`estimate_*` 与摘要折叠都从这里走，别在别处再写一遍 isinstance 分支
    （那正是"多模态一上线，某条老路径就静默算错"的成因）。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                chunks.append(str(part.get("text") or ""))
            elif part.get("type") == "image_url":
                chunks.append("[图片]")
            else:
                chunks.append("[附件]")
        return "\n".join(chunks)
    return "" if content is None else str(content)


def estimate_tokens(text: str) -> int:
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


def tool_calls_text(message: dict) -> str:
    """把一条消息里**不属于 `content` 的那部分**压成可估的文本（计划 §7.1）。

    ## 为什么必须有这个函数

    `estimate_messages_tokens` 此前**只算 `content`** —— 于是 assistant 消息里的
    `tool_calls`（工具名 + 参数 JSON）与 tool 消息里的 `tool_call_id` **一个 token 都不算**。
    计划 §7.1 把这个缺陷的后果写得最清楚：**估偏低会导致该折叠没折叠**。

    ★ 而它们不是零头：一次 `read_local_file` 的参数是
    `{"path": "...", "maxBytes": 4000}`，一个回合里几条这样的 JSON 加起来
    比那些 `content: ""` 的 assistant 消息**本身大得多**。

    ⚠️ 这里只管 `tool_calls` / `tool_call_id`，**不管工具 schema** ——
    后者是**请求级**的（不在 messages 里），走 `estimate_tools_tokens`。
    两者分开是因为调用方的需要不同（见 `estimate_request_tokens` 的文档串）。
    """
    parts: list[str] = []
    calls = message.get("tool_calls")
    if calls:
        # 序列化而不是"数一数字段"：模型看到的就是这段 JSON，
        # 而它的大小取决于参数内容（那正是会变的那个量）。
        parts.append(json.dumps(calls, ensure_ascii=False))
    call_id = message.get("tool_call_id")
    if call_id:
        parts.append(str(call_id))
    name = message.get("name")
    if name:
        parts.append(str(name))
    return "\n".join(parts)


def estimate_content_tokens(content: object) -> int:
    """content（字符串或 parts）折合多少 token：文本按字算 + 每张图一个常数。"""
    if isinstance(content, list):
        images = sum(1 for part in content
                     if isinstance(part, dict) and part.get("type") == "image_url")
        return estimate_tokens(content_to_text(content)) + images * IMAGE_TOKEN_ESTIMATE
    return estimate_tokens(content_to_text(content))


def estimate_messages_tokens(messages: list[dict]) -> int:
    """一批消息折合多少 token：`content` ＋ **`tool_calls` / `tool_call_id`** ＋ 每条 4。

    ⚠️ **它不含工具 schema**（那是请求级的，不在 messages 里）——
    要那个量就用 `estimate_request_tokens(messages, tools)`。
    分开是刻意的：`_over_hard_limit` **刻意**只用不含 schema 的这一个
    （宁可漏判也不要误判，见那里的文档串），而折叠判定要用全量。
    """
    return sum(
        estimate_content_tokens(m.get("content"))
        + estimate_tokens(tool_calls_text(m))
        + 4
        for m in messages
    )


def estimate_tools_tokens(tools: list[dict] | None) -> int:
    """工具 schema（`tools` 请求参数）折合多少 token。

    ## ★ 这是**最大**的那一块，而它此前完全没被统计

    实测（2026-09-24，18 个工具）：schema 序列化约 **2,104 token**，
    而同一段对话的"消息正文"只有 **190** —— schema 是它的 **11 倍**。
    ⇒ 一个真实请求里 **91%** 的 token 此前不在估算范围内。

    ★ 这一段是**每一轮都发**的常量：`harness/loop.py` 的文档串早就写着
    "全量注入约 2k token/轮"，但**估算里没有它** —— 于是窗口设小一点，
    折叠永远不会触发（估出来的数远低于真实值），直到平台把请求拒回来。
    """
    if not tools:
        return 0
    return estimate_tokens(json.dumps(tools, ensure_ascii=False))


def calibration_factor(pairs, *, min_samples: int = 5,
                       low: float = 0.5, high: float = 2.0) -> tuple[float, str]:
    """用「估算 vs 真实」的配对值算一个**校准系数**（真实 / 估算）。

    ## 为什么是**中位数**而不是平均值

    ★ 一次异常样本（比如某次请求被服务端截断、或某条 span 写坏了）会把平均值拽走，
    而中位数对少量离群点稳定。校准系数是**要拿去乘一个阈值**的，
    被一个离群点拽走就等于**那一轮悄悄换了折叠策略**。

    ## 三道闸门（都是"宁可不校准"）

    | 闸门 | 为什么 |
    |---|---|
    | 样本数 < `min_samples` ⇒ **1.0** | 两三个样本算出来的系数比不校准更坏，而它**看起来更科学** |
    | 夹到 `[low, high]` | 防止一串坏样本把窗口阈值放大 10 倍（那会让请求**真的**超窗） |
    | 任一配对非正 ⇒ 丢掉那一条 | `prompt_tokens` 为 0 时比值无意义（回放夹具里就是 0） |

    ## 返回

    `(factor, reason)` —— **reason 是承重的**：调用方要把它写进日志/span，
    否则"这一轮的折叠阈值被悄悄改过"没有任何痕迹（铁律 28 的同一条推理）。
    """
    usable = [(float(e), float(a)) for e, a in pairs if float(e) > 0 and float(a) > 0]
    if len(usable) < int(min_samples):
        return 1.0, f"样本不足（{len(usable)} < {min_samples}），不校准"
    ratios = sorted(a / e for e, a in usable)
    mid = len(ratios) // 2
    median = ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2
    if median < low:
        return low, f"中位数 {median:.3f} 低于下限 {low}，夹住"
    if median > high:
        return high, f"中位数 {median:.3f} 高于上限 {high}，夹住"
    return median, f"中位数 {median:.3f}（{len(usable)} 个样本）"


def estimate_request_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """**一次真实请求**的估算 = 消息 ＋ 工具 schema（计划 §7.1 的口径）。

    ★ 折叠判定必须用它，不能用 `estimate_messages_tokens` ——
    前者是"这个请求有多大"，后者是"这段历史有多大"，而**超窗的是前者**。
    """
    return estimate_messages_tokens(messages) + estimate_tools_tokens(tools)


def split_turns(messages: list[dict]) -> list[list[dict]]:
    turns: list[list[dict]] = []
    current: list[dict] = []
    for message in messages:
        if message.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def build_context(messages: list[dict], window_tokens: int, threshold: float, keep_recent_turns: int):
    """返回 (kept_turns, truncated_messages)。未超阈值时 truncated 为空。"""
    limit = int(window_tokens * threshold)
    if estimate_messages_tokens(messages) <= limit:
        return messages, []
    turns = split_turns(messages)
    if len(turns) <= keep_recent_turns:
        return messages, []
    kept = [m for turn in turns[-keep_recent_turns:] for m in turn]
    truncated = [m for turn in turns[:-keep_recent_turns] for m in turn]
    return kept, truncated
