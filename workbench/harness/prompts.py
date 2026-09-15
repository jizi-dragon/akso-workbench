"""系统提示词：**最小形态 + 用户可改**。

## 2026-09-15 的简化（用户定稿）

改造前这里写着 700+ 字的"身份 + 职责 + 回答格式 + 合规红线"，其中大半是**重复劳动**：

- **工具清单**在下发请求时本来就带（每个工具自带 `description`，模型看得到）。
  提示词里再列一遍 = 同一件事写两份，且必然漂移——本轮加插件时就得同步改两处，
  下次谁忘了改，模型就会以为自己没有某个工具（或以为有不存在的工具）。
- **回答格式**（结论先行 / 依据溯源 / ⚠️ 风险提示）是**产品偏好**，不是能力约束。
  它该由用户决定，而不是钉死在代码里。
- **身份设定**（"你是阿克索的实施工程师智能体…ISPE GAMP5…纯 4 类可配置平台"）
  对行为没有约束力，却占着每个请求最前面的字节——而**前缀越靠前越值钱**：
  DeepSeek 的上下文缓存要求"完整匹配缓存前缀单元"，system 消息正是第一个字节。

现在的基线只回答三个问题：**你是谁、工具从哪找、信息从哪来**。

## 提示词存哪

- 基线 `DEFAULT_SYSTEM_PROMPT`：在代码里，随时可用（改它要走发版）。
- 用户版：设置页可编辑，存 `settings` 表的 `systemPrompt` 键，**空串 = 用基线**。
  改完立即生效（下一轮对话就是新提示词）。

> ⚠️ 改提示词会让缓存前缀失效一次（system 是第一个字节）。这是可接受的：改设置是低频动作，
> 而改完之后每一轮都重新稳定命中。反过来说，**不要在运行时按轮次拼提示词**——
> 那等于每轮都让缓存从头失效。
"""

from __future__ import annotations

from ..services import storage

SETTING_KEY = "systemPrompt"

DEFAULT_SYSTEM_PROMPT = """你是这个工作台的 AI 助手，帮用户完成他交办的事。

# 工具
你能调用的工具由本次请求下发（`tools` 里每个工具的 description 写明了它的用途与后果），
界面上的「工具」按钮能看到同一份清单。**该用工具的时候就用**：需要平台上的真实数据、
需要跑任务、需要读文件或网页时，去调用对应工具，不要凭记忆编造。

- 不确定参数（比如对象编码、账号）就先问用户，或用只读工具确认，**不要猜**。
- 写操作 / 危险操作类的工具需要用户确认后才会执行；被拦下时说明将要做什么，然后等用户决定。
- 工具报错时先看错误本身，多数是参数或环境问题；同一个工具连续失败两次就如实告诉用户，
  不要空转，也不要把失败描述成成功。

# 信息
- 可信度排序：工具返回的结果 > 用户提供的材料 > 你自己的知识。
- 用工具拿到的事实要说清来自哪次调用；没查到就说没查到，**不要补齐成看起来完整的样子**。
- 涉及法规条款号、产品函数名这类"说错了代价很高"的内容，宁可标注"待核实"，也不要编造。

# 风格
简体中文，直接、专业、克制。先给结论再展开。回答长度跟问题匹配，不要为了显得完整而注水。
"""


def default_prompt() -> str:
    return DEFAULT_SYSTEM_PROMPT


def user_prompt() -> str:
    """用户在设置页填的提示词（空 = 未自定义）。"""
    try:
        return (storage.get_setting(SETTING_KEY) or "").strip()
    except Exception:  # noqa: BLE001 —— 读设置失败不该让对话起不来
        return ""


def save_prompt(text: str | None) -> None:
    """保存用户提示词；传空串 = 恢复基线（不删行，与其余 settings 一致）。"""
    if text is None:
        return
    storage.set_setting(SETTING_KEY, text.strip())


def build_system_prompt() -> str:
    """实际下发的系统提示词：用户版优先，空则用基线。"""
    return user_prompt() or DEFAULT_SYSTEM_PROMPT


def prompt_state() -> dict:
    """给设置页的状态（含"是否自定义"，前端据此显示「恢复默认」）。"""
    custom = user_prompt()
    effective = custom or DEFAULT_SYSTEM_PROMPT
    return {
        "prompt": effective,
        "default": DEFAULT_SYSTEM_PROMPT,
        "custom": bool(custom),
        "chars": len(effective),
    }
