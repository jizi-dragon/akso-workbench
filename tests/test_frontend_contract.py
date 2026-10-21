"""前端 API 客户端契约（短平快，不需要浏览器）。

## 为什么这条判据必须存在

`workbench/static/js/api.js` 是 Python→JS 的**无类型接口**。2026-10-19 之前
`jsonFetch` 不序列化对象 body，`stopChat` 实际发出 `"[object Object]"`：
服务端 422，前端 catch 后把按钮退回"停止" —— 用户看到的是"点了没反应"，
而当时 973 条 pytest 与全部 `verify_*.py` 都是绿的。

⇒ 判据必须落在**发出去的请求字节**上，而不是"源码里有没有 JSON.stringify"。
本文件用 Node 直接 import 真实的 `api.js`，用假 `fetch` 截获请求再断言。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "js" / "api_contract.mjs"


def _run() -> list[dict]:
    node = shutil.which("node")
    if node is None:  # pragma: no cover —— CI 装了 Node；本机没有时跳过
        pytest.skip("node 不在 PATH（前端契约测试需要 Node ≥ 18 的 Response 全局）")
    proc = subprocess.run(
        [node, str(SCRIPT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"node 跑 api_contract.mjs 失败：\n{proc.stderr}"
    return json.loads(proc.stdout)["calls"]


def test_stop_chat_sends_json_body_not_object_coercion() -> None:
    """★ 停止键的根因判据：对象 body 必须序列化成 JSON。

    反证：把 `jsonFetch` 里那段 `JSON.stringify` 删掉 ⇒ 本断言红
    （body 会变成 `"[object Object]"`，服务端真实返回 422）。
    """
    calls = _run()
    stop = next(c for c in calls if c["url"] == "/api/chat/stop")
    assert stop["method"] == "POST"
    assert stop["body"] != "[object Object]", "对象 body 被 fetch 的 String() 强转了 —— 停止键会 422"
    assert json.loads(stop["body"]) == {"conversationId": "conv-1"}


def test_already_serialized_bodies_are_not_double_encoded() -> None:
    """调用点自己 `JSON.stringify` 过的 body 不得被入口二次编码（会变成字符串套字符串）。"""
    calls = _run()
    for call in calls:
        if call["url"] in {"/api/settings", "/api/settings/prompt"}:
            parsed = json.loads(call["body"])
            assert isinstance(parsed, dict), f"{call['url']} 的 body 被二次编码了：{call['body']!r}"


def test_json_content_type_is_set_for_every_call() -> None:
    calls = _run()
    assert calls, "一条请求都没截到 —— 脚本没真的调用 api.js"
    for call in calls:
        assert call["headers"].get("Content-Type") == "application/json"


def test_csv_parser_handles_quotes_crlf_and_empty() -> None:
    """产物面板新增 CSV 预览：引号内逗号 / 双引号转义 / CRLF 不能错位。"""
    node = shutil.which("node")
    if node is None:  # pragma: no cover
        pytest.skip("node 不在 PATH")
    script = ROOT / "tests" / "js" / "artifact_parser.mjs"
    proc = subprocess.run(
        [node, str(script)], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    parsed = json.loads(proc.stdout)
    assert parsed["simple"] == [["a", "b", "c"], ["1", "2", "3"]]
    assert parsed["quotedComma"] == [["name", "note"], ["a,b", '他说 "好"']]
    assert parsed["crlf"] == [["h1", "h2"], ["v1", "v2"]]
    assert parsed["empty"] == []
