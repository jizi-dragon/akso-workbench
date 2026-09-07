"""ddkb — 钉钉知识库只读通道（官方 dws CLI 的 Python 薄封装）。

对应 ddkb.mjs 的 Python 移植（消除 Node 依赖）：
- 凭据：dws OAuth 设备流（扫码授权），登录态由 dws 本地加密存储并自动刷新
  （Access Token 2h / Refresh Token 30 天轮转）；过期时运行 `dws auth login` 重新扫码
- 只读：search / read / wiki 树枚举；权限边界 = 登录用户本人在钉钉的可见范围

所有调用统一经过节奏控制（相邻调用最小间隔）与失败退避重试，
避免触发 dws/钉钉侧限流。阻塞调用应由上层用 asyncio.to_thread 包装。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

MIN_CALL_INTERVAL = 0.5   # 相邻 dws 调用最小间隔（秒）
RETRY_ATTEMPTS = 3        # 单次调用最大尝试次数（含首次）
RETRY_BACKOFF = (2.0, 4.0)  # 重试退避（秒）
CALL_TIMEOUT = 120        # 单次调用超时（秒）

_lock = threading.Lock()
_last_call = 0.0


class DdkbError(Exception):
    """dws 调用失败。"""


class DdkbAuthError(DdkbError):
    """未登录或登录态失效——需要重新扫码授权。"""


def _dws_path() -> str:
    env = os.environ.get("DDKB_DWS")
    if env and Path(env).exists():
        return env
    plugin_root = Path.home() / ".trae-cn" / "plugins" / "trae-remote-official" / "dingtalk"
    if plugin_root.exists():
        candidates = sorted(plugin_root.glob("*/bin/dws.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    raise DdkbError(
        "未找到 dws.exe。请安装 Trae 钉钉官方插件（trae-remote-official:dingtalk），"
        "或设置环境变量 DDKB_DWS 指向 dws 可执行文件。"
    )


def _pace() -> None:
    global _last_call
    with _lock:
        wait = MIN_CALL_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _run(args: list[str]) -> dict:
    """执行一条 dws 命令并解析 JSON 输出；带节奏控制与退避重试。"""
    dws = _dws_path()
    last_error = ""
    for attempt in range(RETRY_ATTEMPTS):
        _pace()
        try:
            proc = subprocess.run(
                [dws, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=CALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"dws 调用超时（{CALL_TIMEOUT}s）"
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                continue
            raise DdkbError(last_error) from exc
        except OSError as exc:
            raise DdkbError(f"无法启动 dws：{exc}") from exc

        out = (proc.stdout or "").strip()
        if out:
            try:
                data = json.loads(out)
            except ValueError:
                data = None
            if isinstance(data, dict):
                if data.get("success"):
                    return data
                error = data.get("error") or {}
                if error.get("category") == "auth" or error.get("reason") == "not_authenticated":
                    raise DdkbAuthError(
                        "钉钉登录态失效，请运行 `dws auth login`（或 node ddkb.mjs login）重新扫码授权。"
                    )
                last_error = error.get("message") or out[:200]
            else:
                last_error = out[:200]
        else:
            last_error = (proc.stderr or "").strip()[:200] or f"dws 退出码 {proc.returncode}"

        if attempt < RETRY_ATTEMPTS - 1:
            time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])

    raise DdkbError(f"dws 调用失败：{last_error}")


# ———— 鉴权 ————

def auth_status() -> dict:
    return _run(["auth", "status"])


# ———— 知识库空间 ————

def wiki_spaces(limit: int = 50) -> list[dict]:
    """列出我可见的知识库空间：[{workspaceId, name, updateTime, spaceUrl, description}]。"""
    data = _run(["wiki", "space", "list", "--limit", str(limit), "--format", "json"])
    return data.get("wikiSpaces") or []


# ———— 节点树 ————

def wiki_node_list(
    workspace: str, folder: str | None = None, cursor: str | None = None, limit: int = 50
) -> dict:
    """列出某空间（或某父节点下）的节点：{nodes, hasMore, nextPageToken}。

    节点字段：nodeId / name / nodeType(file|folder) / contentType / extension /
    hasChildren / createTime / updateTime / docUrl / workspaceId。
    """
    args = ["wiki", "node", "list", "--workspace", workspace, "--limit", str(limit), "--format", "json"]
    if folder:
        args += ["--folder", folder]
    if cursor:
        args += ["--cursor", cursor]
    data = _run(args)
    return {
        "nodes": data.get("nodes") or [],
        "hasMore": bool(data.get("hasMore")),
        "nextPageToken": data.get("nextPageToken"),
    }


def wiki_tree(workspace: str, max_nodes: int = 20000) -> list[dict]:
    """深度优先枚举空间内全部可读文档（ALIDOC）。

    钉钉 wiki 的子页面既可挂在 folder 下，也可挂在文档节点下
    （file 节点 hasChildren=true 表示存在子页面）——两种都要递归。
    返回字段与 wiki_node_list 相同；带安全上限防失控。
    """
    files: list[dict] = []
    visited: set[str] = set()
    queue: list[str | None] = [None]

    while queue:
        folder = queue.pop(0)
        cursor: str | None = None
        while True:
            page = wiki_node_list(workspace, folder=folder, cursor=cursor)
            for node in page["nodes"]:
                if len(files) >= max_nodes:
                    return files
                if node.get("hasChildren"):
                    node_id = node.get("nodeId")
                    if node_id and node_id not in visited:
                        visited.add(node_id)
                        queue.append(node_id)
                # 只收在线文档；表格（axls）/AI 表格（able）等 doc read 不支持
                if node.get("extension") == "adoc":
                    files.append(node)
            if not page["hasMore"] or not page["nextPageToken"]:
                break
            cursor = page["nextPageToken"]

    return files


# ———— 文档内容 ————

def doc_read(node_or_url: str) -> dict:
    """读取文档正文 → Markdown：{title, markdown, docUrl}。"""
    data = _run(["doc", "read", "--node", node_or_url, "--format", "json"])
    return {
        "title": data.get("title") or "钉钉文档",
        "markdown": data.get("markdown") or "",
        "docUrl": data.get("docUrl") or "",
    }


def search(query: str, limit: int = 10) -> list[dict]:
    """按关键词搜索我可见的文档（摘要级结果，供定位 nodeId/URL）。"""
    args = ["doc", "search", "--page-size", str(limit), "--format", "json"]
    if query.strip():
        args += ["--query", query.strip()]
    data = _run(args)
    return data.get("documents") or []
