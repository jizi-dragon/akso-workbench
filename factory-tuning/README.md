# factory-tuning —— 配置工厂调优用的采集与分析（**不属于产品**）

## 这个目录是干什么的

配置工厂的蓝图 schema **本来就是从监听里得来的**：看人在平台上正常配一遍对象/字段/布局/
生命周期，平台实际发了哪些请求、请求体长什么样，然后照着建模。

现在工厂已经能跑了，但"建出来的东西跟人在平台上手配的到底差在哪"这件事**没有数据支撑**。
这个目录就是为了补上它：**把真实的配置操作录下来 → 分析 → 反推蓝图 schema 的缺口**。

## 为什么单独一个文件夹（而不是做进产品）

用户 2026-09-20 定稿：

> 「监听功能本不属于本项目的功能，只是为了优化而做，请单独起文件夹。」

所以这里的每一样东西**都不进产品**：

| | 产品 | 本目录 |
|---|---|---|
| 工具 | `workbench/plugins/*.py` 的 `ToolSpec` 才会被模型看到 | **不注册任何工具** |
| 界面 | `workbench/static/` 才是产品界面 | 无界面，只有命令行脚本 |
| 打包 | `server.spec` 的 `hiddenimports` | **不进打包**（`build.ps1` 不碰它） |
| 依赖 | 产品代码不许 import 本目录 | 本目录**可以**只读地 import `workbench`（见下） |

一句话：**删掉这个目录，产品一行都不会变。**

## 数据从哪来（用的是现成能力，没有新增采集代码）

平台的请求录制**产品里已经有了**：`workbench/services/egmp/monitor/monitor.py`
（三级降噪：DROP / 裁剪 / 陌生 API 全量保留；每条请求还带最近 5 次页面点击与输入）。

本目录只做两件事：**把它调起来**、**把它收进来分析**。

> **2026-09-22 的一次例外，用户明确下达后才动**：用户当面指出「分不清我在哪个页面窗口操作的」，
> 而根因在录制侧——只挂一个 page、新窗口一律不录（详见下面的**第七条**）。用户选定
> 「要改：以后无论哪个窗口都能录，且事后能看出每条记录来自哪个窗口」，于是**这一次**
> 动了 `services/egmp/monitor/monitor.py`（跟随同源新窗口 + 每条记录带 `pageLabel`/`pageUrl`）
> 与 `services/browser_pool.py` 的一行调用点。**除此之外本目录不进产品**；这个例外仍然遵守
> 「不改配置工厂与产品功能面」的边界。

## 一条命令跑通

```powershell
# 0) 先让工作台跑着（cd desktop; npm start  或  python -m workbench.main）

# 1) 开监听（会在应用内置 Chromium 里打开该账号的平台页，并挂上录制）
.\.venv\Scripts\python.exe factory-tuning\tools\capture.py start --account <账号id>
#    → 打印 log 路径；然后你在那个浏览器里**正常配一遍**（建对象、加字段、拖布局、加状态…）

# 2) 停止并落盘
.\.venv\Scripts\python.exe factory-tuning\tools\capture.py stop --account <账号id>

# 3) 收进本目录（**这一步做脱敏**，见下）
.\.venv\Scripts\python.exe factory-tuning\tools\ingest.py --latest

# 4) 出报告
.\.venv\Scripts\python.exe factory-tuning\analyze\report.py --latest
#    → factory-tuning/reports/<日期>-<会话>.md

# 5) 提交前兜底扫一遍
.\.venv\Scripts\python.exe factory-tuning\tools\scan_secrets.py
```

> ### ⚠️ 开监听之后，**必须先自己证明"录得到"，再请人操作**（2026-09-21 的教训）
>
> 上一轮跳过了这一步，结果是**用户白演示了一整套**（表单布局 + 生命周期状态），
> 而 `total: 0`——**那套数据没有副本、不可恢复**。
>
> 两条踩出来的口径：
>
> 1. **`start` 在监听已开时不重置，而是返回实时 `stats`**（`browser_pool.py:587-589`）
>    ⇒ 可以在**不 stop** 的情况下轮询 `total`，这是唯一的非破坏性探针。
> 2. **不能用"新开窗口的加载流量"验证**：挂载发生在 `_do_open`（内部已 `goto` 完平台）
>    **之后**（`browser_pool.py:583` vs `:593-594`）⇒ 那一段流量根本不在录制窗口里。
>    要造流量，得在挂载**之后**下发，例如
>    `POST /api/browser/navigate/<账号id>`，body `{"url": "<平台地址>"}`。
>
> ### ★ 裁量规则（**用户 2026-09-22 定稿**）
>
> 「**产品手册只是给你借鉴，最终决策 / 差异请让我下达，以我的决定为主。**」
>
> ⇒ 本目录的报告里，凡出现「手册 ↔ 平台真机 ↔ 工厂实现」三者不一致的地方：
> **只摆差异与证据，不替用户下结论**；措辞一律是「**差异，待用户下达**」，
> 而不是「应按手册改」。修法选项可以列，取舍权在用户。

> **固定流程**：开监听 → 自造一次流量 → 读 `total > 0` → **才开始请人操作**。
> （2026-09-22 补：这一步只证明「**监听挂上了、页面是活的**」——
> **不能**拿它判"此刻平台上没有流量"，因为**读 stats 这个动作自己会推动事件分发**，见下面第四条。）
>
> ### ⛔ 第七条（2026-09-22 用户当面指出）：**动手之前先证明"我听的就是你操作的那个窗口"**
>
> 用户原话：「**现在都分不清我在哪个页面窗口操作的，该问题不解决，后续会产生大问题。**」
>
> 他是对的，而且这**不是细心问题，是接口层就没有这个信息**：
> `/api/browser/sessions` 只回 `page.title()`，而平台**所有页面标题都是 `Akso eGMP (配置)`**；
> URL / target id / 窗口 id **一个字段都没有**。另一侧：监听原先只挂 `entry.page` **一个** page
> （`browser_pool.py::_do_open_cdp` 靠 `blank.html?w=acc-<账号>` 标记页定位它），
> 全仓 `context.on("page")` **零命中**、壳侧 `desktop/main.js` 没设 `setWindowOpenHandler`
> ⇒ 平台 `window.open` 真开出的新窗口**一条都录不到**，而 `filter-stats.json` 里是**干净的 0/0/0**
> （`total` 连 GET 都计数 ⇒ 0 的含义是"被监听的那个页面那段时间没有任何网络活动"）。
>
> ⇒ **每次开录之后、请人操作之前，先跑一次身份探针**（全部只读，不抢鼠标）：
>
> ```powershell
> .\.venv\Scripts\python.exe factory-tuning\tools\where.py          # 快照 + 判定
> .\.venv\Scripts\python.exe factory-tuning\tools\where.py --save    # 存基线
> .\.venv\Scripts\python.exe factory-tuning\tools\where.py --diff    # 与基线对比：有没有新窗口
> ```
>
> **判定怎么读**：
> - **外部页面恰好 1 个** → ✅ 绑定可判定，它直接打出 `id / title / url`；
> - **0 个** → ✗ 池子的窗口不在这个浏览器里（或已被关掉）——此时你在哪操作都录不到；
> - **≥2 个** → ⚠️ **无法判定你在哪一个**（接口层两个窗口同构）⇒ 它列出候选，
>   **必须由你指认或关掉多余的，不许我猜**。
>
> 然后做一次**握手**（唯一能证明"你的动作到了我这里"的东西）：
> 你在**打算用来演示的那个窗口**里做一次必定产生请求的动作 → `capture.py read` →
> `filter-stats.json` 的 **`total` 必须 > 0**。仍是 0 就**停手**、别继续演示
> （否则后面全是白干），先回来对账。
>
> 2026-09-22 补的能力：录制现在会**跟随同源新窗口**，并且**每条记录都带窗口身份**
> （`pageLabel` / `pageUrl`，日志顶层还有 `_pages`）⇒ 事后能回答"那次保存是在哪个窗口做的"。
>
> 判据：`python factory-tuning\tests\selfcheck.py` 的 **M1–M4**（只读承诺／快照不许含令牌形状／
> 判定语义在场）与 **N1–N4**（窗口身份一路透传；老会话缺键必须读作 `None`——
> **"没记录"不许当"主页面"**）。

> ### ⛔ 第六条（2026-09-22 实测事故）：**读数据一律用 `capture.py read`，不要用 `stop`**
>
> `stop` 与 `start` 是**两步**，而"**读完忘了重开**"**不会报错**——用户接着在平台上操作，
> 那一段**一条都录不到**，事后只能从他的描述里发现"那次没录到"。
> 本次就这么丢了「**新建表单布局**」那一整步（而那正是工厂缺的那个动词，F66），还让用户白做一次。
>
> ⇒ `capture.py read --account <id>` **= 停 → 落盘 → 收编（脱敏）→ 立刻重新开始监听**，
> 一条命令做完，**"忘记重开"在物理上不可能发生**。
> 它的**不变式**：命令返回后监听**一定是开着的**；万一调用时它本来就没在跑，
> 也只**报出来**、不中止，照样重开（已实测：`capture.py list` 的「监听中」必须是「是」）。
>
> 判据：`.\.venv\Scripts\python.exe factory-tuning\tools\capture.py --help` —— 必须列出 `read`。
>
> ### ⛔ 第五条（2026-09-22 段 3 实测，我自己犯的）：**`total: 0` 是"截至此刻没有"，不是"这次没有"**
>
> 段 3 那次：我在 11:01:06 / 11:01:30 / 11:02:44 / **11:03:42** 连读四次实时 `stats`，**四次都是 0**；
> 而请求发出于 **11:03:51** —— **只差 9 秒**。我据此下了"监听挂在别的页面上"的结论并**告诉了用户**，
> **那是错的**（`stop` 落盘后是 `{'total': 52, 'trimmed': 3, 'full': 4}`，一条没丢）。
>
> ⇒ **两条纪律**：
> 1. **权威读数只有 `stop` 落盘后的 `filter-stats.json`** 与 `monitor-log.json`——
>    实时 `stats` 只配用来判"链路通不通"，**不配用来判"有没有发生"**；
> 2. **不要用一次 0 去推翻操作者的说法**。他说他点了保存，而 0 只说明**你读早了**。
>    要确认，就跑一次 `stop`（它一定会把队列里的事件泵出来再落盘），而不是再读一次 `stats`。
> 实测样本（2026-09-22）：`{'total': 240, 'dropped': 233, 'trimmed': 2, 'full': 5}`。
>
> ### 🚫 操作口径（**用户 2026-09-22 定稿**）：不得擅自截图或驱动那个浏览器页面
>
> > 「你要截图的话，需要提前告知我，**不能抢我的鼠标**。」
>
> - **截图 / 驱动页面（调试端口 `127.0.0.1:18766` 上的 CDP 操作、`page.evaluate`、导航）**：
>   **必须先告知用户并得到同意**，且**不得抢占鼠标与焦点**。
>   背景：本目录曾为判断"保存有没有发出请求"而未事先告知就连接页面截图，影响了用户的操作体验。
> - **不受此限**：只读的 HTTP 接口（`capture.py` 用的 `/api/monitor/*`、`/api/browser/sessions`、
>   `/api/health`）——它们不碰页面。
> - 判据（自查）：任何新脚本若要 `connect_over_cdp` / `page.evaluate` / `Page.captureScreenshot`，
>   先问一句"**我告知用户了吗**"。

> **另一条（2026-09-22 更正）**：`flush()` 不只在 `stop()` 里被调用——**页面关闭也会触发它**
> （`monitor.py:133` 的 `self.page.on("close", self.stop)`）。实测：关掉会话窗后，
> 那个会话目录里照样出现 `monitor-log.json` / `filter-stats.json` / `checkpoints.jsonl`。
> ⇒ 真正的丢失窗口**只有"整个应用被强杀或崩溃"**，比原先写的窄得多。
> 仍然按「**做一项、停一次**」分段（分段的好处是可分析、可复核，不只是防丢）。
>
> ⚠️ **还有一条更该记住的**：实时 `stats` 是**那一刻的快照，不是最终账**。
> 实测同一次会话：我读到的实时 `total` 是 **240**，关窗落盘后 `filter-stats.json` 里是
> **517**（中间那几分钟平台上有人在动）。⇒ **一切以落盘后的 `filter-stats.json` 为准**。
>
> ### 🕳 第三条（2026-09-22 预检）：**录不到 ≠ 平台没发请求**
>
> `monitor.py::classify()` 对 **PATCH / PUT / DELETE 只有路径逐字命中
> `writers/endpoints.py` 的 57 条常量才录**，否则**静默丢弃**；`GET` 则一律丢。
> ⇒ 想用录制去发现"平台用的是哪个方法"时，**录制只认得它已经知道路径的那些方法**
> （`monitor.py` 文档串里的「陌生 API 完整保留」**只对 POST 成立**）。
>
> 判据（一行）：
>
> ```powershell
> .\.venv\Scripts\python.exe -c "from workbench.services.egmp.monitor.monitor import classify; print(classify('PATCH','/api/config/lifecycle/Status/Update'), classify('PATCH','/api/config/lifecycle/Status/ChangeStatus'))"
> # → RECORD_TRIMMED DROP
> ```
>
> **解读口径**（三种结局，事前定死，别事后猜）：
> ① 录到写请求 → 答案到手；
> ② **界面上真的变了、但没录到** → 平台用的是「非 POST + 未建模路径」——
> **这本身就是答案**（排除了 POST + 已建模路径）；
> ③ 界面也没变 → 那一步没生效，**重做**。
>
> ⚠️ **不要用 `dropped` 增量判断**（它把 GET 一起算了）。详见
> [docs/FACTORY-PLATFORM-COGNITION.md](../docs/FACTORY-PLATFORM-COGNITION.md) §8。

> ### 🕳 第四条（2026-09-22 段 2 实测）：**`timestamp` 记的是"事件被分发"的时刻，不是"请求发出"的时刻**
>
> 段 2 那 5 条写请求的**观测**时间戳只差 **14 ms**，而动作缓冲里最后一次点击（「保 存」）比它早 **61.4 秒**。
> 同一形态：段 1 = **38.4 s**、段 0 = **44.5 s**；而 09-21 的六个会话只有 **0.1–3.1 s**。
> 两个候选机制**都还没被排除**：① sync-Playwright 的事件只在 worker **进入 Playwright 调用**时才被分发，
> 而 `browser_pool.py:166` 的 `self._jobs.get()` **没有超时**（事件可能在队列里等几十秒才跑到 handler，
> `monitor.py` 的 `int(time.time()*1000)` 记的是**那一刻**）；② **触发保存的操作不在这 5 槽里**
> ——缓冲只挂 `click/input/change`，键盘动作、iframe / shadow DOM 内的点击**都不进缓冲**。
>
> ⇒ **三条硬口径**（写结论时按这个来）：
>
> 1. **同批事件之间的相对间隔可用；跨"动作缓冲 ↔ 请求"的时间差不可用**——
>    不许拿它推人工节奏，更不许写成"用户犹豫了 61 秒"；
> 2. **`stats` 只统计"已被分发"的事件**，而"读 `total` 验证录制"这个动作**自己会推动分发**
>    （`monitor_start` / `session()` 都走 `_submit`）⇒ **读到 0 不能证明"什么都没发生"**。
>    上面记的「实时 240 vs 落盘 517」就是这个原因，**不是**"中间那几分钟平台上有人在动"；
> 3. 结论里只写"这批请求的**先后顺序**"，而顺序另有**内容语义**背书
>    （保存流水线：3× 下拉查询 → `GetStableComponentIds` → `SaveLayoutDetail`）。
>
> 详见 [PITFALLS #74](../docs/PITFALLS.md) 与[段 2 报告 §6](reports/2026-09-22-真机取证-段2-新建部分与整行.md)。

没录过数据也想先看链路通不通：给第 4 步加 `--fixtures`（用合成样例，**不是真实数据**）。

账号 id 从哪来：产品界面「账号中心」里那一列，或
`capture.py list`，或 `curl.exe -s http://127.0.0.1:18765/api/accounts`。

## 自检（改完本目录请跑一次）

```powershell
.\.venv\Scripts\python.exe factory-tuning\tests\selfcheck.py
```

它**不进仓库的 pytest**（`pyproject.toml` 的 `testpaths = ["tests"]`），所以要手动跑。
不是形式：`redact.py` 写成的头十分钟里就暴露了两个真 bug（脱敏不足 + 过度脱敏），
两个都是靠"跑一遍真数据形状"发现的，而不是靠读代码。

> ⚠️ **自检有副作用（2026-09-22 记）**：D 组会**真的**调 `report.py --fixtures`，
> 于是每跑一次自检，`reports/` 里就多一份**当日日期**的 `*-SAMPLE-*.md`。
> 那是**合成样例**，不是采集分析结果——**别把它读成"今天采到数据了"**，
> 也别当成需要提交的产物（提交前清掉即可）。形态与 `docs/PITFALLS.md` #71 同族：
> **跑一次检查就在别处留下痕迹，而检查本身是绿的。**

## 脱敏：**在落盘那一刻做，不是提交前补**

⚠️ **这是本目录唯一一条硬规矩。**

平台流量里带着 JWT / 会话令牌 / 口令。这个仓库是要推到 GitHub 的，
原始流量一旦进了 git 历史**很难真正删掉**——那既是安全事故，也直接违反本仓铁律
19「凭据不落明文」与 26「凭据永不入日志」。

所以：`ingest.py` 在**写文件之前**就脱敏，磁盘上**从头到尾没有一份原始流量**。
脱敏后的数据才是 `data/` 里被提交的东西。

| 处理 | 规则 |
|---|---|
| 请求/响应体里的凭据字段 | **键名子串命中**（`token`/`password`/`secret`/`authorization`/`cookie`/`apiKey`/…）→ 值换成 `"***"` |
| 只在整键时才可疑的词 | `signature` / `session` / `auth` / `key` / `encrypted` → **整键命中**才打码（否则 `enableSignatures` 这种真实业务字段会被误伤） |
| **用户手打的自由文本**（2026-09-22 增） | `keywords` / `searchText` / `searchKey` / `searchWord` / `queryText` / `filterValue` → **整键命中**才打码。它不是凭据而是**个人数据**：人搜的经常是**人名/单位名**，而 schema 分析要的是键名与结构、用不到这些值。实测触发点：一次浏览流量里 `ListlayoutRecord/GetRecordPositionAll` 的 `{"keywords":"<真人姓名>"}`——**那个会话因此没有 ingest**。刻意不收过于通用的 `inputValue` 与单数 `keyword`（更像业务字段名）；判据：selfcheck **A12–A15 / B8** |
| 值本身像令牌的 | JWT（`eyJ….….…`）、`Bearer xxx`、带 `=` 填充的 base64 长串、32 位以上连续 hex、`token_urlsafe` 形状 → 整段换成 `"***"` |
| URL 里的查询参数 | 同上按键名处理；**主机名整个丢掉**（只留路径，不记录你用哪个环境） |
| 账号 id / GUID / 本机路径 | 换成稳定假名（`acct-<hash8>` / `id-<hash8>` / `<path>`） |
| 对象编码 / 字段名 / 显示名 / 页面操作文本 | **保留**——那正是要分析的东西。<br>⚠️ **例外（2026-09-22 实测）**：**≥24 字符**的编码会撞上"像令牌"的兜底启发式 ⇒ **在脱敏侧被打码**（实测见 `code: "***"`）。全量 9 份会话里这类只出现 **1 次**。这是**刻意的不对称**（见 `redact.py::PLATFORM_CODE_RE` 的注释：脱敏侧不豁免，方向偏安全）。**要取回它就读运行时原始日志**（本地、不入库）。是否改成"脱敏侧也豁免"，见 [docs/FACTORY-PLATFORM-COGNITION.md](../docs/FACTORY-PLATFORM-COGNITION.md) §7 的 **E7** |

**过度脱敏与脱敏不足一样是缺陷**：把 `enableSignatures` 打掉不会"更安全"，
只会让采集到的结构缺一块，分析据此得出"平台不发这个键"的错误结论。
所以 `selfcheck.py` 里 `B` 那一组断言专门守"不该打的一个都不许少"。

兜底：`tools/scan_secrets.py` 可以在提交前扫一遍仓库，扫到令牌形状的串就非零退出。
它进不了 CI（本目录不进 CI），但**改完数据后请手动跑一次**。

另外注意：Monitor 自己会把 `Login` / `RefreshToken` 这类端点整条丢掉
（`monitor.py::QUERY_POST_PATTERNS`），所以口令最密集的那几个请求根本不会被录。

## 目录结构

```
factory-tuning/
├── tools/
│   ├── capture.py       起/停监听（调产品的 /api/monitor/*；不改产品代码）
│   ├── redact.py        脱敏规则（唯一事实源，ingest 与 scan 共用）
│   ├── ingest.py        把 runtime/monitor 的产物脱敏后收进 data/
│   ├── scan_secrets.py  提交前扫描兜底
│   ├── audit_keys.py    线格式审计：真机**请求键**（默认）/ **响应 data 形状**（--responses）
│   │                    └─ `collect_arrays()`：真机**嵌套数组元素**的键集（按端点分开存）
│   └── keygate.py       键名闸门：AST 抽工厂的**字面量请求体**（顶层 + `NAME.append({…})` 嵌套两层），
│                        与真机键集自动比对；嵌套那层按「同 writer 模块的端点」归属（PITFALLS #75）
│                        （**改 `writers/**` 的请求体后跑它**；当前因 D-A/D-C 故意 exit 1）
├── analyze/
│   ├── model.py         统一数据模型 + 加载器
│   ├── coverage.py      平台端点覆盖率：**哪些真实用到的端点我们没建模**
│   ├── shapes.py        真实请求体的键 vs 蓝图模型 → schema 缺口
│   └── report.py        生成 reports/<会话>.md
├── data/                脱敏后的采集数据（**提交**）
├── reports/             分析报告（**提交**）
└── tests/fixtures/      合成样例（**不是真实数据**，用来证明分析链路能跑）
```

## 刻意不做的事

- **不新增任何采集代码**：用户 2026-09-20 定稿"只用内置 Chromium（现成能力，不改任何代码）"，
  所以不写浏览器扩展、不碰用户自己的 Chrome。
- **不录对话内容**：本目录只分析平台侧的 API 与页面操作（用户同一次定稿）。
- **不注册工具、不加界面、不进打包**：它不是一个产品功能。
- **不做"从采集数据自动生成蓝图"**：先有数据与报告，再谈自动化；
  现在做等于在没看过数据的情况下猜 schema。

## 与产品文档的关系

采集与分析产出的**结论**（该补哪个字段、哪个端点没建模、哪个步骤顺序不对）
应当落进 [docs/FACTORY-VERIFICATION-PLAN.md](../docs/FACTORY-VERIFICATION-PLAN.md)
的台账与 [docs/PITFALLS.md](../docs/PITFALLS.md)，而不是只留在这个目录里——
本目录是**取证工具**，不是知识库。
