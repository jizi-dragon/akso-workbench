# 环境坑清单（PITFALLS）

> **性质**：真机事故攒下来的机构记忆。原文明确写着「这些是**只在真机复现过**的教训，**很多无法从代码反推**」——
> 因此本文件按 **只增不改** 维护（同 ADR），新增坑请**追加编号**，不要重排或删除既有条目。
>
> **来源**：本文件是 `AGENT.md` §5 的**唯一留存处**。`AGENT.md` 已于 2026-09-13 因整体滞后被删除
> （删除原因与遗留引用处理见 [CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md)）；其 §2 代码地图 / §3 红线 /
> §7 验收基线已分别由 [CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md) 与 [CONTRIBUTING.md](../CONTRIBUTING.md) §7 承接。
> 原文可用 `git show <删除前提交>:AGENT.md` 取回。
>
> **复核状态**：`✅` = 2026-09-13 本轮对码复核，仍成立；`⚠️` = 仍成立但有新增变量，见备注；`—` = 本轮未复核，按原文保留。

| # | 复核 | 坑 | 处置 |
|---|---|---|---|
| 1 | ✅ | Playwright sync 单线程单实例 | 所有浏览器操作必须走 `BrowserPool` 专职线程队列；**测试勿另起 chromium 夹具**（见 [ADR-0006](adr/0006-browser-pool-single-thread.md)） |
| 2 | ✅ | **事件回调内禁止连接调用**（`response.text()` / `page.evaluate` 重入 = worker 死锁） | 事件只入队，worker 作业间隙统一 `drain()`（0.2.2 严重修复） |
| 3 | ✅ | **CDP 挂接三律** | `remote-debugging-port` 必须 app ready **前**设置；**先 `:18767` 开户再 connect**（Electron 33 怪癖）；Playwright 实例由 `_CdpState` 单一持有 |
| 4 | — | ES 模块严格模式下**重复函数声明 = 整模块加载失败** | 表现为页面停在静态「加载中」（`accounts.js` 的 `isOnline` 事故） |
| 5 | ✅ | PS5.1 脚本 | `.ps1` 必须 UTF-8 **BOM**；不支持 `??`；heredoc `<<` 不可用；中文经命令行参数传 curl 会乱码（写文件 + `--data-binary @file`）。BOM 一项由 `tools/verify_packaging.py` P5 持续守着 |
| 6 | ✅ | pydantic `BaseModel` **默认忽略多余字段** | 请求体必须显式建模（`AccountPatch` 漏 `box` 导致静默失效）。**注意**：这一点在反向也成立——`AccountPatch` 用 `exclude_none=True`，传 `null` = 不修改 |
| 7 | — | `build.ps1` **禁用** `$ErrorActionPreference='Stop'` | PS5.1 会把 git/uv 的 stderr 进度当终止错误 |
| 8 | — | 关窗回收 | 退出走 `taskkill /T /F` 防孤儿 chromium；同线程禁止第二个 `sync_playwright` |
| 9 | ⚠️ | 网络：GitHub / PyPI 间歇抖动 | 重试循环；uv 已配清华镜像。**push 的代理口径必须按实际情况现场判定**：旧经验是「全局代理常失效 → 用 `git -c http.proxy= push`」，但 2026-09-11 实测本机**直连 GitHub 会被重置**（`Recv failure: Connection was reset`），必须**显式走系统代理**：`git -c http.proxy=http://127.0.0.1:7890 push`。两种都失败时先探测 `Get-NetTCPConnection -LocalPort 7890 -State Listen` 与 `Invoke-WebRequest -Proxy` 哪个通道真的通。<br>**2026-09-13 新增**：`build.ps1:71-75` 仍**硬编码直连**（`git -c http.proxy= push`），与上面的实测结论相反；且 `AKSO_PROXY` 环境变量**只对 shell 生效、到不了 egmp 的 Python 路径**（`httpx` 走 `trust_env`），会出现「shell 里代理通、insight 卡死」 |
| 10 | — | 自愈计数 | 成功时**不可清零**（会掩盖已自愈事实）；上限 2 防死循环 |
| 11 | — | 桌面壳只开一个实例 | 第二实例端口冲突，**只出窗口不连服务** |
| 12 | — | 扩展更新 `dist` 后 | **必须在扩展卡片点「重新加载」**（Chrome 缓存 SW 脚本；顽固时删档案 `Default/Service Worker/`） |
| 13 | ✅ | 版本号 | 只走 `tools/bump.py` 五写，勿手改单一文件（安装包命名会错位）。**注意**：五写**不含**两个 `package-lock.json`，`desktop/package-lock.json` 因此长期停在 0.2.1 |
| 14 | ✅ | **字段名双语义** | DB 行 snake_case（`env_base_url`）vs `export_backup` 输出 camelCase（`envBaseUrl`）——`routes_extension` 曾读错键 → 快照 host 恒空 → 扩展**静默丢弃全部账号**（0.2.4 主断点） |
| 15 | ✅ | **扩展游标持久 vs 服务端内存 seq** | 指令序号必须跨重启单调递增（已落 `settings.ext_cmd_seq`），否则重启一次指令通道整体哑火且无报错。**残留**：`_next_seq()` 的 DB 写异常被吞（`routes_extension.py:66-67`），失败时 seq 会从 1 重来，本条会复现 |
| 16 | — | **SW 自消息死链** | SW 内 `chrome.runtime.sendMessage` **不投递给自身** `onMessage`——跨模块复用行为请直调函数（`toggleAccountWheel` 已抽至 `account-wheel.ts`） |
| 17 | ✅ | **Fernet 键位规范** | sign-key = `key[:16]`（HMAC）、enc-key = `key[16:32]`（AES-CBC）——写反导致扩展端全员解密失败（0.2.6 断点之一）。已对 `sync.ts:50-66` 复核仍一致 |
| 18 | ✅ | **WebCrypto AES-CBC 自动去 PKCS7 填充** | 解密结果**不可**再按尾字节手工剥离（否则把口令尾字符当填充长度剥掉，0.2.6 断点之二）。`sync.ts:70-72` 保留了这条注释 |
| 19 | — | Playwright 测 MV3 扩展 | `ctx.service_workers` 需先开一个页面才暴露目标；**headless 下 MV3 扩展不加载**（必须 `headless=False` + `--load-extension`） |
| 20 | — | **孤儿 uvicorn 占 18765** | 测试脚本异常退出会遗留服务进程 → 新服务绑定失败、HTTP 验证全打到**旧代码**上，表现为「修复无效」。先 `Get-NetTCPConnection -LocalPort 18765` 查占用 |
| 21 | ✅ | **扩展的桌面地址是编译期常量** | `sync.ts` 硬编码 `http://127.0.0.1:18765` → 验证扩展行为时**必须**确保该端口上是本项目服务，且端口空闲（`verify_extension_version.py` 强制要求） |
| 22 | ✅ | **Electron 不支持 `window.prompt`** | UI 输入一律用原生 `<dialog>`（焦点最稳）——账号中心的「移入盒子」「重命名」即因此改造 |

---

## 维护规则

1. **只增不改**：新增坑追加编号；既有条目的**事实**若已失效，改「复核」列为 `⚠️` 并在「处置」列补一句现状说明，**不要删行**——坑的价值在于「曾经踩过」。
2. 每条尽量写清：**症状**（怎么表现）→ **根因** → **处置**（下次怎么绕过）。原文多数已具备，新增请照此格式。
3. 只在真机复现过的才进来。从代码能直接读出来的约束属于 `docs/CODEBASE_OVERVIEW.md` 的「约定」或 ADR，不占坑位。
4. 复核建议节奏：每次大版本发布前把 `—` 的条目过一遍，能确认的改成 `✅`。
