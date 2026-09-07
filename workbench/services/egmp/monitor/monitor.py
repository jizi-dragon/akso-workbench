"""Monitor 录制引擎（akso-auto monitor/monitor.js 的 Python 化，载体换 Playwright-Python）。

三级降噪（上游口径）：
- DROP：非写方法 + 查询类 POST 白名单命中且非已知 API；
- RECORD_TRIMMED：已知 API（本包 writers 端点集合），响应体 >10KB 裁剪；
- RECORD_FULL：陌生 API 完整保留（known=False）。
每条记录附 precedingActions（最近 5 条 DOM click/change，页面注入 1s 同步）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..writers import endpoints as W

QUERY_POST_PATTERNS = [
    r"Login", r"RefreshToken", r"QueryList", r"GetPageList", r"Get.*List$", r"FieldPage",
    r"LayoutList", r"GetWorkflowSteps", r"GetWorkflowStepWithDetails", r"GetActionBar",
    r"UserAction/GetList", r"Status/Detail", r"Listlayout/Columns", r"Page$",
]

_KNOWN_PATHS: set[str] = set()
for _name in dir(W):
    if _name.isupper() and isinstance(getattr(W, _name), str) and getattr(W, _name).startswith("/api/"):
        _KNOWN_PATHS.add(getattr(W, _name))

TRIM_SIZE = 10 * 1024


def classify(method: str, path: str) -> str:
    if method.upper() in {"GET"}:
        return "DROP"
    if method.upper() == "POST" and not any(re.search(p, path) for p in QUERY_POST_PATTERNS):
        return "RECORD_FULL" if path not in _KNOWN_PATHS else "RECORD_TRIMMED"
    if path in _KNOWN_PATHS:
        return "RECORD_TRIMMED"
    return "DROP"


def trim_body(body: Any) -> Any:
    if isinstance(body, str) and len(body) > TRIM_SIZE:
        try:
            data = json.loads(body)
            return {k: data.get(k) for k in ("code", "message", "id", "name", "success") if k in data}
        except ValueError:
            return body[:500] + "…(trimmed)"
    return body


ACTION_BUFFER_JS = """
(() => {
  if (window.__aksoActionBuffer) return;
  window.__aksoActionBuffer = [];
  ['click', 'change'].forEach((type) => {
    window.addEventListener(type, (e) => {
      const t = e.target;
      window.__aksoActionBuffer.push({
        type: type,
        tag: t && t.tagName ? t.tagName : '',
        text: t && t.textContent ? String(t.textContent).slice(0, 60) : '',
        at: Date.now(),
      });
    }, true);
  });
})();
"""


class MonitorSession:
    """一次录制会话：page 监听 → monitor-log.json（运行中纯数组覆盖写）。"""

    def __init__(self, page: Any, output_dir: Path,
                 on_log: Callable[[str], None] | None = None) -> None:
        self.page = page
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.output_dir / "monitor-log.json"
        self.checkpoints_path = self.output_dir / "checkpoints.jsonl"
        self.entries: list[dict[str, Any]] = []
        self.stats = {"total": 0, "dropped": 0, "trimmed": 0, "full": 0}
        self._lock = threading.Lock()
        self._on_log = on_log or (lambda line: None)
        self._active = True

    def start(self) -> None:
        self.page.evaluate(ACTION_BUFFER_JS)
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("close", self.stop)
        self.checkpoint("start")
        self._on_log(f"Monitor 录制开始 → {self.log_path}")

    def _on_request(self, request: Any) -> None:
        if not self._active:
            return
        url = request.url
        path = url.split("://", 1)[-1]
        path = "/" + path.split("/", 1)[1] if "/" in path else "/"
        method = request.method.upper()
        self.stats["total"] += 1
        verdict = classify(method, path)
        if verdict == "DROP":
            self.stats["dropped"] += 1
            return
        self.stats["trimmed" if verdict == "RECORD_TRIMMED" else "full"] += 1
        post_data = None
        try:
            post_data = request.post_data
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            actions = self._drain_actions()
            self.entries.append({
                "method": method, "path": path, "url": url,
                "timestamp": int(time.time() * 1000),
                "direction": "request",
                "postData": post_data,
                "known": verdict == "RECORD_TRIMMED",
                "precedingActions": actions,
            })

    def _on_response(self, response: Any) -> None:
        if not self._active:
            return
        url = response.url
        with self._lock:
            entry = next((e for e in reversed(self.entries)
                          if e["url"] == url and e.get("body") is None), None)
        if entry is None:
            return
        body: Any = None
        try:
            body = response.text()
        except Exception:  # noqa: BLE001
            pass
        entry["status"] = response.status
        entry["body"] = trim_body(body) if entry["known"] else body

    def _drain_actions(self) -> list[dict[str, Any]]:
        try:
            buffer = self.page.evaluate("() => window.__aksoActionBuffer ? window.__aksoActionBuffer.splice(0) : []")
            return list(buffer)[-5:]
        except Exception:  # noqa: BLE001
            return []

    def checkpoint(self, kind: str, **event: Any) -> None:
        with self._lock:
            segment = len([e for e in self.entries if True]) and self._segment_count()
            with self.checkpoints_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"checkpoint": kind, "segment": segment,
                                     "timestamp": int(time.time() * 1000), **event},
                                    ensure_ascii=False) + "\n")

    def _segment_count(self) -> int:
        if not self.checkpoints_path.exists():
            return 1
        return sum(1 for _ in self.checkpoints_path.read_text(encoding="utf-8").splitlines())

    def flush(self) -> None:
        with self._lock:
            self.log_path.write_text(json.dumps(
                {"entries": self.entries, "_actions": [], "filterStats": self.stats},
                ensure_ascii=False, indent=1,
            ), encoding="utf-8")

    def stop(self, *_args: Any) -> None:
        self._active = False
        self.checkpoint("user-query")
        self.flush()
        (self.output_dir / "filter-stats.json").write_text(
            json.dumps(self.stats, ensure_ascii=False, indent=2), encoding="utf-8")
        self._on_log(f"Monitor 录制结束：{self.stats}")
