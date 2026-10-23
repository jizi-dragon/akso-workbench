"""Cache guard：provider-visible 前缀（system + tool schema）必须逐字节稳定。

## 为什么单独一条

DeepSeek 的缓存命中条件是"**完整匹配**一个缓存前缀单元"；system 是第一个字节，
tools schema 紧随其后。两者任何一处混进时间戳、会话 id、随机顺序，命中率就归零，
而**功能完全正常**——只是账单变成 50 倍。`tests/test_cache.py` 钉的是 messages；
这条钉的是请求里另外两块常量。
"""

from __future__ import annotations

import json
import time

from workbench.harness import prompts
from workbench.hub import get_hub


def _request_prefix() -> str:
    return json.dumps(
        {"system": prompts.build_system_prompt(), "tools": get_hub().list_api()},
        ensure_ascii=False,
        sort_keys=True,
    )


def test_system_prompt_and_tool_schema_are_byte_stable() -> None:
    first = _request_prefix()
    time.sleep(0.02)  # 时间戳/随机 id 只要混进来，这里就会不同
    second = _request_prefix()
    assert first == second, "system 或 tools schema 在某次调用之间变了 —— 缓存前缀会整段失效"
    assert len(first) > 100, "前缀为空，这条判据没有验到东西"


def test_tool_schema_has_no_volatile_fields() -> None:
    """逐项检查：工具 schema 只含模型需要的稳定字段。"""
    tools = get_hub().list_api()
    assert tools, "工具表为空"
    allowed = {"type", "function"}
    for tool in tools:
        assert set(tool) <= allowed, f"工具 schema 出现了未知顶层字段：{set(tool) - allowed}"
        fn = tool.get("function") or {}
        assert set(fn) <= {"name", "description", "parameters"}, fn.keys()
        # 参数 schema 里不许出现示例值/时间（示例值会随配置变，且占 token）
        text = json.dumps(fn.get("parameters") or {}, ensure_ascii=False)
        assert "generatedAt" not in text and "timestamp" not in text.lower()
