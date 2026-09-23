"""Monitor 录制引擎（akso-auto monitor/monitor.js 的 Python 化，载体换 Playwright-Python）。

三级降噪（上游口径）：
- DROP：非写方法 + 查询类 POST 白名单命中且非已知 API；
- RECORD_TRIMMED：已知 API（本包 writers 端点集合），响应体 >10KB 裁剪；
- RECORD_FULL：陌生 API 完整保留（known=False）。
每条记录附 precedingActions（最近 5 条 DOM click/change，页面注入 1s 同步）。

## 多窗口（2026-09-22 追加）：录到的每一条都必须能说出"来自哪个窗口"

背景是用户当面指出的一句话：「**现在都分不清我在哪个页面窗口操作的**」。查下来这不是细心
问题，是**结构性盲区**：

- 产品接口 `/api/browser/sessions` 只回 `page.title()`，而平台**所有页面标题都是
  `Akso eGMP (配置)`**；URL / target id / 窗口 id 一个字段都没有；
- 录制引擎原先只挂 `entry.page` **一个** page（`browser_pool.py::_do_open_cdp` 靠
  `blank.html?w=acc-<账号>` 标记页定位它）；
- 全仓 `context.on("page")` 零命中、壳侧 `desktop/main.js` 未设 `setWindowOpenHandler`
  ⇒ 平台 `window.open` 真开出的新窗口**一条都录不到**，而 `filter-stats.json` 里是干净的 0。

⇒ 现在：**主页面 + 同源新窗口一起录**，每条记录带 `pageLabel` / `pageUrl`，
`monitor-log.json` 顶层再落一份 `_pages`（label → url/title），事后能反查"那次保存是在哪个窗口"。

两条约束下的实现取舍（**都不是风格偏好，是硬约束**）：

1. `docs/CORE-ARCHITECTURE.md` 规则 24：**事件回调内禁止连接调用**（`page.evaluate` /
   `page.url` 重入 = worker 死锁）。所以监听器是**按页面生成的闭包**，`label` 随闭包进来
   ——回调里**一个连接调用都不需要**就能标出身份；新页面只**入队**，归属判定留给 `drain()`。
2. 动作缓冲是**每个 document 一份**（导航即丢）。所以出栈按页面分别做，并且在发现
   `window.__aksoActionBuffer` 不见了时**就地重新注入**——否则"平台内跳一次页，
   之后所有动作都挂不上"，而那正是判定"这条记录对应哪次点击"的唯一依据。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# 端点常量取自 **kernel**（唯一事实源）。2026-09-22 前这里指向 `..writers.endpoints`，
# 那只是一个转发垫片；配置工厂整体移除后，垫片随之消失，`_KNOWN_PATHS` 的取值集**不变**
# （`kernel/semantics.py` 里没有任何 `/api/` 开头的常量）。
from ..kernel import endpoints as W

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

#: 裁剪后**保留哪些顶层键**。
#:
#: ⚠️ 第一版是 `("code", "message", "id", "name", "success")` —— 那是**照着一个猜的信封**
#: 写的。真机信封是 `{code, errorCode, message, data, requestID, traceId}`：**内容全在
#: `data` 里，而白名单没有 `data`**。后果（2026-09-21 真机量化）：一次 20 条写请求的采集里
#: **11 条响应被裁成只剩 `{code, message}`**，被裁的恰是"保存后回读对象有哪些字段"这类
#: **平台侧核对依据**。讽刺的是它只作用于**已知端点**（本包建模过的），陌生端点反而全量保留
#: —— 于是"越是对某个端点建模，越看不到它的完整响应"。见 docs/PITFALLS.md #63。
_KEEP_KEYS = ("code", "errorCode", "message", "data", "id", "name", "success")

#: `data` 自身也可能很大（字段列表 93 键 × N 条），按**序列化长度**有界保留。
_DATA_KEEP_CHARS = 2000


def classify(method: str, path: str) -> str:
    if method.upper() in {"GET"}:
        return "DROP"
    if method.upper() == "POST" and not any(re.search(p, path) for p in QUERY_POST_PATTERNS):
        return "RECORD_FULL" if path not in _KNOWN_PATHS else "RECORD_TRIMMED"
    if path in _KNOWN_PATHS:
        return "RECORD_TRIMMED"
    return "DROP"


def trim_body(body: Any) -> Any:
    """大响应体的有界裁剪。**不许把 `data` 整个丢掉**，也不许静默丢弃。

    - 保留 `_KEEP_KEYS` 里的顶层键（含 `data`）；
    - `data` 超长时**截断为字符串**并追加 `…(data trimmed)`；
    - 一律加 `_trimmed: True` —— 本仓硬要求"截断必须显式告知，不许静默丢弃"
      （AGENTS.md 规则 4）。消费方据此知道这条不是完整响应。
    """
    if isinstance(body, str) and len(body) > TRIM_SIZE:
        try:
            data = json.loads(body)
        except ValueError:
            return body[:500] + "…(trimmed)"
        if not isinstance(data, dict):
            # 顶层不是对象（少数接口直接回数组）：整个序列化后截断，形状信息还在
            return {"_trimmed": True, "data": body[:_DATA_KEEP_CHARS] + "…(trimmed)"}
        out = {k: data.get(k) for k in _KEEP_KEYS if k in data}
        if "data" in out:
            serialized = json.dumps(out["data"], ensure_ascii=False)
            if len(serialized) > _DATA_KEEP_CHARS:
                out["data"] = serialized[:_DATA_KEEP_CHARS] + "…(data trimmed)"
        out["_trimmed"] = True
        return out
    return body


def origin_of(url: str) -> str:
    """`scheme://host:port`；空串 = 判不了（**调用方必须据此放弃判定，不许猜**）。"""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


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

#: 出栈 + **顺带报告注入是否还在**。导航会把 `window.__aksoActionBuffer` 冲掉，
#: 而"冲掉了"这件事只有把 `present` 一起要回来才知道（旧版只 splice，拿回空数组，
#: 与"用户确实没点"无法区分）⇒ 于是跳页之后所有动作都静默消失。
_ACTION_DRAIN_JS = """
(() => {
  const present = Boolean(window.__aksoActionBuffer);
  const items = present ? window.__aksoActionBuffer.splice(0) : [];
  return { present: present, items: items };
})();
"""


def _new_stats() -> dict[str, int]:
    return {"total": 0, "dropped": 0, "trimmed": 0, "full": 0}


class MonitorSession:
    """一次录制会话：page（+ 同源新窗口）监听 → monitor-log.json。

    sync-Playwright 约束（血泪）：事件回调（request/response）内禁止再调连接方法
    （response.text() / page.evaluate 都是重入——会死锁 worker 线程）。因此：
    - 回调只做内存入队（response 对象延迟补捞）；页面身份由**闭包携带的 label** 给出，
      回调内不做任何连接调用；
    - 真正的响应体抓取、动作缓冲出栈、新窗口归属判定由 drain() 完成，调用方须在
      worker 线程的"作业间隙"（非事件分发中）周期调用。
    """

    def __init__(self, page: Any, output_dir: Path, on_log: Callable[[str], None] | None = None,
                 context: Any = None) -> None:
        self.page = page
        self.context = context          # 跟随新窗口要枚举它；None = 只录主页面（旧行为）
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.output_dir / "monitor-log.json"
        self.checkpoints_path = self.output_dir / "checkpoints.jsonl"
        self.entries: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = {**_new_stats(), "byPage": {}}
        self._lock = threading.Lock()
        self._on_log = on_log or (lambda line: None)
        self._pending_bodies: list[tuple[dict[str, Any], Any]] = []
        self._dirty_pages: set[str] = set()          # 待出栈动作缓冲的页面 label
        self._tracks: dict[int, dict[str, Any]] = {}  # id(page) → {"label", "page", "primary"}
        self._meta: dict[str, dict[str, Any]] = {}    # label → {"url", "title"}
        self._pending_pages: list[Any] = []           # context.on("page") 只入队，drain 里判定
        self._primary_label: str | None = None
        self._active = True

    # ------------------------------------------------------------ 挂载与跟随

    def start(self) -> None:
        """挂监听 → **再**注入（顺序不能反，见下）。

        旧版是 `page.evaluate(注入)` 在前、`page.on(...)` 在后。差别在于：sync-Playwright
        的事件**只在 worker 进入一次 Playwright 调用时才分发**，所以 evaluate 这一刻会把
        "上一次作业之后堆起来的事件"一次性泵出来——挂在后面的监听**接不到它们**
        （旧的 recorder 已 `_active=False` 丢弃，新的还没挂上）。反过来的顺序，
        泵出来的事件正好落进刚挂好的监听里。
        """
        self._attach(self.page, primary=True)
        if self.context is not None:
            try:
                self.context.on("page", self._on_new_page)   # 纯入队（规则 24）
            except Exception as exc:  # noqa: BLE001 —— 跟随失败不该挡住录制
                self._on_log(f"⚠️ 新窗口跟随未挂上（{exc}）——本次只录主页面")
            self._adopt_existing()
        self.page.evaluate(ACTION_BUFFER_JS)
        self._refresh_meta()
        self.checkpoint("start")
        self._on_log(f"Monitor 录制开始 → {self.log_path}"
                     f"（页面 {len(self._tracks)} 个：{', '.join(self._meta)}）")

    def _attach(self, page: Any, primary: bool = False) -> str:
        label = "p1" if primary else f"p{len(self._tracks) + 1}"
        self._tracks[id(page)] = {"label": label, "page": page, "primary": primary}
        self._meta[label] = {"url": None, "title": None}
        self.stats["byPage"][label] = _new_stats()
        if primary:
            self._primary_label = label
        page.on("request", self._req_handler(page, label))
        page.on("response", self._res_handler(page, label))
        # **只有主页面关闭才结束录制**；子窗口关闭只摘掉它自己（旧版一律 self.stop
        # ⇒ 关掉一个弹出的设计器窗口会把整场录制停掉，而且 stop 会落盘 + 折检查点）。
        page.on("close", self._close_handler(page, label, primary))
        return label

    def _req_handler(self, page: Any, label: str) -> Callable[[Any], None]:
        def handler(request: Any) -> None:
            self._on_request(request, label)
        return handler

    def _res_handler(self, page: Any, label: str) -> Callable[[Any], None]:
        def handler(response: Any) -> None:
            self._on_response(response, label)
        return handler

    def _close_handler(self, page: Any, label: str, primary: bool) -> Callable[..., None]:
        def handler(*_args: Any) -> None:
            self._on_page_closed(page, label, primary)
        return handler

    def _on_page_closed(self, page: Any, label: str, primary: bool) -> None:
        if primary:
            self.stop()
            return
        self._tracks.pop(id(page), None)
        self._dirty_pages.discard(label)
        self._on_log(f"窗口关闭：{label}（{self._meta.get(label, {}).get('url')}）——录制继续")

    def _on_new_page(self, page: Any) -> None:
        """`context.on("page")` 回调：**只入队**（连接调用一律留到 drain）。"""
        self._pending_pages.append(page)

    def _adopt_existing(self) -> None:
        """已在场的同源页面（例如开始监听前就开着的窗口）也纳入录制。"""
        try:
            pages = list(self.context.pages)
        except Exception:  # noqa: BLE001
            return
        self._pending_pages.extend(p for p in pages if id(p) not in self._tracks)

    def _same_origin_as_primary(self, page: Any) -> bool:
        base = origin_of(self._meta.get(self._primary_label or "", {}).get("url") or "")
        if not base:
            return False
        try:
            return origin_of(page.url) == base
        except Exception:  # noqa: BLE001
            return False

    def _adopt_pending(self) -> None:
        """在 drain（作业间隙）里给新页面定性：**只收同源**。

        为什么必须卡同源：CDP 模式下 `entry.context` 是**整个 Electron 浏览器**的上下文，
        里面还有工作台 UI 自己的窗口（`http://127.0.0.1:18765/`，每 3 秒拉一次
        `/api/browser/sessions`）。不卡同源就会把**产品自己的界面流量**录进来，
        既污染采集，又让"这条记录来自哪个窗口"重新变得不可信。
        """
        pending, self._pending_pages = self._pending_pages, []
        for page in pending:
            if id(page) in self._tracks:
                continue
            try:
                if page.is_closed():
                    continue
            except Exception:  # noqa: BLE001
                continue
            if not self._same_origin_as_primary(page):
                self._on_log(f"跳过非平台同源页面：{self._safe_url(page)}")
                continue
            label = self._attach(page)
            try:
                page.evaluate(ACTION_BUFFER_JS)
            except Exception:  # noqa: BLE001 —— 页面正在跳转
                pass
            self._refresh_meta([label])
            self._on_log(f"新窗口纳入录制：{label} → {self._meta.get(label, {}).get('url')}")

    @staticmethod
    def _safe_url(page: Any) -> str:
        try:
            return str(page.url)
        except Exception:  # noqa: BLE001
            return "（URL 读不到）"

    def _refresh_meta(self, labels: list[str] | None = None) -> None:
        """刷新页面 URL/标题（**只在作业间隙调用**）。"""
        for rec in list(self._tracks.values()):
            label = rec["label"]
            if labels is not None and label not in labels:
                continue
            page = rec["page"]
            try:
                self._meta[label]["url"] = str(page.url)
                self._meta[label]["title"] = str(page.title())
            except Exception:  # noqa: BLE001 —— 跳转中/已关闭
                pass

    # ------------------------------------------------------------ 事件入队

    def _on_request(self, request: Any, label: str) -> None:
        if not self._active:
            return
        url = request.url
        path = url.split("://", 1)[-1]
        path = "/" + path.split("/", 1)[1] if "/" in path else "/"
        method = request.method.upper()
        verdict = classify(method, path)
        with self._lock:
            self.stats["total"] += 1
            self.stats["byPage"].setdefault(label, _new_stats())["total"] += 1
            if verdict == "DROP":
                self.stats["dropped"] += 1
                self.stats["byPage"][label]["dropped"] += 1
                return
            key = "trimmed" if verdict == "RECORD_TRIMMED" else "full"
            self.stats[key] += 1
            self.stats["byPage"][label][key] += 1
            self._dirty_pages.add(label)
            post_data = None
            try:
                post_data = request.post_data
            except Exception:  # noqa: BLE001
                pass
            self.entries.append({
                "method": method, "path": path, "url": url,
                "timestamp": int(time.time() * 1000),
                "direction": "request",
                "postData": post_data,
                "known": verdict == "RECORD_TRIMMED",
                # 页面身份：`label` 来自闭包（回调内零连接调用）；`pageUrl` 是**最近一次
                # `drain()` 刷新到的**那个 URL —— 也就是"这条请求发出时页面上大概在哪"，
                # 精度到一个 drain 间隔。要精确的当前 URL 看 `_pages`，别拿这个当逐条精确值。
                "pageLabel": label,
                "pageUrl": self._meta.get(label, {}).get("url"),
                "precedingActions": None,  # drain() 时统一补捞（避免事件内 evaluate）
            })

    def _on_response(self, response: Any, label: str) -> None:
        if not self._active:
            return
        url = response.url
        with self._lock:
            # 先在同页面的条目里找（同一 URL 可能同时出现在平台页和工作台页上）
            entry = next((e for e in reversed(self.entries)
                          if e.get("pageLabel") == label and e["url"] == url
                          and e.get("body") is None), None)
            if entry is None:
                return
            entry["status"] = response.status
            # 响应体不在此处抓取（重入禁令）——登记后由 drain() 补捞
            self._pending_bodies.append((entry, response))

    # ------------------------------------------------------------ 作业间隙

    def drain(self) -> None:
        """作业间隙调用（worker 线程、非事件分发中）：跟随新窗口 + 补捞动作与响应体。"""
        if not self._active:
            return
        self._refresh_meta()
        if self.context is not None:
            self._adopt_pending()
        # 1) 动作缓冲出栈 → 挂到**同一个页面**最近的无动作条目
        for label in sorted(self._dirty_pages):
            self._dirty_pages.discard(label)
            rec = next((r for r in self._tracks.values() if r["label"] == label), None)
            if rec is None:
                continue
            try:
                pulled = rec["page"].evaluate(_ACTION_DRAIN_JS)
                buffer = list((pulled or {}).get("items") or [])[-5:]
                if not (pulled or {}).get("present"):
                    # 导航把注入冲掉了：就地补回，否则此后该页面的动作全是空的
                    rec["page"].evaluate(ACTION_BUFFER_JS)
            except Exception:  # noqa: BLE001 —— 页面可能正在跳转
                buffer = []
            with self._lock:
                for entry in reversed(self.entries):
                    if entry.get("pageLabel") != label:
                        continue
                    if entry.get("precedingActions") is None:
                        entry["precedingActions"] = buffer
                    else:
                        break
        # 2) 响应体补捞（可能已被浏览器丢弃——尽力而为）
        with self._lock:
            pending, self._pending_bodies = self._pending_bodies, []
        for entry, response in pending:
            try:
                body: Any = response.text()
            except Exception:  # noqa: BLE001
                body = None
            with self._lock:
                entry["body"] = trim_body(body) if entry["known"] else body

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

    def pages_summary(self) -> dict[str, Any]:
        """本次录制**覆盖了哪些页面**（label → url/title/是否主页面）。

        这个字典就是"事后能回答『你那次保存是在哪个窗口』"的依据：它随日志一起落盘，
        而不是只活在内存里。
        """
        return {
            label: {
                "url": meta.get("url"), "title": meta.get("title"),
                "primary": bool(next((r["primary"] for r in self._tracks.values()
                                      if r["label"] == label), False)),
                "closed": not any(r["label"] == label for r in self._tracks.values()),
            }
            for label, meta in self._meta.items()
        }

    def flush(self) -> None:
        with self._lock:
            self.log_path.write_text(json.dumps(
                {"entries": self.entries, "_actions": [], "_pages": self.pages_summary(),
                 "filterStats": self.stats},
                ensure_ascii=False, indent=1,
            ), encoding="utf-8")

    def stop(self, *_args: Any) -> None:
        """结束录制并落盘。

        ⚠️ **监听器刻意不摘**：`stop` 既可能被作业调用，也可能是某个 page 的 `close`
        事件回调（在事件分发中）。在分发中调 `page.remove_listener` 属于"回调内碰连接
        对象"那一类动作（规则 24 的边界），而 sync 包装下 `remove_listener` 的处理器
        身份也不可靠（`_wrap_handler` 每次都会新建包装体）。因此沿用**`_active` 早退**：
        监听器留着，但 `self._active is False` 之后一律立即返回，不会再写任何条目。
        """
        self._active = False
        self.checkpoint("user-query")
        self.flush()
        (self.output_dir / "filter-stats.json").write_text(
            json.dumps(self.stats, ensure_ascii=False, indent=2), encoding="utf-8")
        self._on_log(f"Monitor 录制结束：{self.stats}")
