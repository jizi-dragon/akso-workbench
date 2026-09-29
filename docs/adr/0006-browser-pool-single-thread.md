# ADR-0006：`browser_pool` 单专职线程串行化（Playwright sync 约束）

- **状态**：已采纳（阶段 2B）
- **相关**：[ADR-0002](0002-browser-allocation-policy.md)、[API.md](../API.md) §12.5、
  [PITFALLS.md](../PITFALLS.md)（环境坑清单）

## 背景

托管浏览器（`services/browser_pool.py`，624 行）需要同时满足三件事：

1. **多账号并行**：一张卡片一个账号，多个账号同时在线、互不干扰（用户明确要求「任务栏归组、
   同一浏览器应用内多窗口」）；
2. **Web 服务并发**：FastAPI 把并发请求派到线程池的不同线程；
3. **Playwright sync API 的硬约束**：**sync API 绑定创建它的线程，且非线程安全**。
   跨线程使用会抛 greenlet 错误（历史上的「浏览器启动失败」正是这个根因）。

另有一个更隐蔽的约束：**同一个线程里不能起第二个 `sync_playwright()` 实例**——会死锁。

## 决策

**`BrowserPool` 持有唯一的专职工作线程，所有浏览器操作以 `Future` 提交、在该线程内串行执行。**
Playwright 与浏览器对象**只在该线程内存在**，线程外只拿到 `Future` 的结果（会话快照）。

```python
def _submit(self, job, timeout=None):
    future = Future()
    self._jobs.put((future, job))     # queue.Queue
    return future.result(timeout=timeout)   # 线程外阻塞等待

def _worker(self):
    # 打包态：先设 PLAYWRIGHT_BROWSERS_PATH 指向随包 chromium（必须在 sync_playwright 前）
    pw = None
    browsers: dict[bool, Any] = {}     # headful 标志 → browser（local 模式）
    sessions: dict[str, SessionEntry] = {}
    while True:
        future, job = self._jobs.get()
        try:
            result = job(pw, browsers, sessions)
            for entry in sessions.values():        # ← 关键：作业间隙统一 drain
                if entry.monitor is not None and getattr(entry.monitor, "_active", False):
                    entry.monitor.drain()
            future.set_result(result)
        except Exception as exc:
            future.set_exception(exc)
```

三条派生的硬性规则：

1. **事件回调内禁止任何连接调用**（`response.text()` / `page.evaluate` / `page.goto` 重入 = worker 死锁）。
   事件回调**只能入队**，在 worker 的作业间隙（上方的 `monitor.drain()`）统一处理。
   这是 0.2.2 修掉的严重缺陷，也是 `egmp/monitor/monitor.py` 三级降噪（`dropped`/`trimmed`/`full`）
   的存在原因——录制期间不能就地处理响应体。
2. **CDP 模式三律**：`remote-debugging-port` 必须在 Electron `app ready` **之前**用
   `app.commandLine.appendSwitch` 设置；**先 `:18767 /windows` 开户、再 `connect_over_cdp(:18766)`**
   （Electron 33 实测：窗口必须先于 connect 存在才可见）；
   Playwright 实例由 `_CdpState` **单一持有**（`browsers[True]` 是 `_CdpState`；同线程第二个
   `sync_playwright()` 会死锁）。
3. **CDP 连接按开户次数追加、旧连接保持存活**（`_CdpState.connections`），
   其会话句柄继续有效；`close_all()` 才统一清理。

## 取舍

### 换来什么

- 彻底消除跨线程 greenlet 错误——这是历史上「启动失败」的根因。
- 多账号并行靠**同一 browser 多个 context** 实现（每账号一个独立窗口），
  既满足「任务栏归组」，又拿到 context 级原生隔离（cookie/storage 天然分开）。
- 状态与副作用集中在一处：`sessions: dict[account_id, SessionEntry]` 是唯一真源，
  自愈计数、监听句柄、token 捕获都挂在这个 dataclass 上。

### 代价（已接受）

| 代价 | 表现与处置 |
|---|---|
| **全局串行**：一个慢操作阻塞所有账号的操作 | 这是有意换取正确性。缓解：多数作业无超时（见下）反而是隐患的另一面 |
| **无超时会挂死 HTTP 请求** | 只有 `monitor_start` 显式给了 `timeout=90`（因为可能要开户 + 等 CDP 定位）；`open_account` / `session` / `navigate` 等**无超时**——页面卡住时请求会一直挂着。见 `../CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**） 的风险台账 |
| 事件回调纪律容易被违反 | 新增录制/监听能力时必须走「入队 + drain」，且**没有测试能拦住违规**（表现为运行期死锁，不是断言失败） |
| 测试必须复用该池 | **测试不得另起 chromium 夹具**（`tests/conftest.py` 只隔离数据目录；`test_browser_parallel.py` 用本地 `http.server` 模拟平台 + 池本身）。生命周期收尾靠 `pool.close_all()` |

## 后果

- **`browser_pool` 是进程级单例**（`get_pool()` 持 `_pool_lock` 惰性创建），
  其工作线程随进程存活（daemon 线程）。
- **关窗 ≠ 停监听**：`_close_entry()` 落盘登录态、关 context、置 `stopped`，但**不清 `entry.monitor`**
  ——监听会话对象仍留在 `SessionEntry` 上直到 `sessions.clear()`。见 `../CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**） 的风险台账。
- **`_close_entry` 的 finish 判定存在缺陷**：它在判定前就把 `entry.status` 置为 `"stopped"`，
  导致 `detail` 里的 `entry.status == "online"` 恒为假，首次登录的会话关窗会误报「已关闭」
  （应为「已关闭（登录态已保存）」）。见 `../CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**） 的风险台账。
- **真机验收入口固定**：`GET /api/browser/check`（chromium 可执行文件体检）、
  `tests/test_browser_state.py`（storage_state 5 项）、`tests/test_browser_parallel.py`（双账号并行/免密/自愈 4 项）。

---

## 外部参照（2026-09-20 后补）

> ⚠️ **本栏为后补**（正文保持决策当时的样子，不改写）。

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| **Playwright 官方文档**（sync API 章节） | 明确：sync API **非线程安全**、绑定创建它的线程；同线程不得起第二个 `sync_playwright()` | **一致（跟随）** | 这不是本仓的发明，是**上游的硬约束**。"专职单线程 + Future 提交"是满足该约束的标准解法，**无需自证** |
| [browser-use](https://github.com/browser-use/browser-use) / [Skyvern](https://github.com/Skyvern-AI/skyvern) / [Stagehand](https://github.com/browserbase/stagehand) | 一律用 **async API**（`async_playwright`），跑在自己的 asyncio 事件循环里，**因此不需要专职线程**——并发由事件循环提供 | **★ 分歧** | 这是一个**真实的权衡**，不是"我们落后"：本仓 `services/egmp/client`、`proc`、`sandbox.run` **都是同步的**，且 FastAPI 的同步依赖也走线程池。选 async 意味着把整条业务链改成 async（fork 存量大、改动面广），**而收益只是省掉一个线程**。我们选了 sync + 单线程队列，用 `asyncio.to_thread` 桥接 |
| 同上三者的"浏览器池" | 通常**每任务一个新上下文**，用完即弃，任务间无共享状态 | **分歧** | 我们的会话**必须长期存活**（登录态要复用、监听要持续），所以状态集中在 `sessions: dict[account_id, SessionEntry]`。代价就是正文里那条"**全局串行**：一个慢操作阻塞所有账号" |

**由本次对照得出的结论**：

- **可以放心的部分**：单线程串行化本身是上游约束下的标准解法，不必再论证。
- **必须自己盯住的部分**（也是本 ADR 最有价值的原创内容）：
  ① 正文那条「**事件回调内禁止连接调用**」——async 派系的框架天然没有这个问题（回调在事件循环里、
  它们本来就要 await），**所以行业里没有现成教训可抄**，这是 sync + 队列这个组合**独有**的坑；
  ② 正文承认的「**新增录制能力时没有测试能拦住违规**」——同上，只能靠纪律与 code review。
- **一个值得记住的推论**：本项目选了与主流相反的那条路（sync vs async），
  **代价集中体现在 ADR 的"代价（已接受）"表里，而收益是改动面小**。
  这正是"外部参照"栏存在的意义——**不是为了证明我们错了，是为了让代价可见**。

