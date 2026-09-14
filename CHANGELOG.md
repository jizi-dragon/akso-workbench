# Changelog

本项目遵循语义化版本（SemVer），格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。
从第一天开始记录（对齐行业月更节奏惯例）。

## [未发布]

### Added

> ⚠️ **本节第一条（端口协商/顺延）已于同日被撤回国**——见下方 `### Changed` 的
> [ADR-0012](docs/adr/0012-fixed-ports.md) 条目。保留原文是为了留痕；
> **现行行为是端口固定、不顺延**，读本条时勿把"顺延"当成现状。

- **端口协商与公布（[ADR-0011](docs/adr/0011-port-and-session-negotiation.md)，用户提出的"端口好几个，是不是该控制平衡一下"）**。
  改造前 18765/18766/18767 被**壳、Python 服务端、浏览器扩展三方各自硬编码**，且没有任何一方
  负责裁决"谁在用这个口"。**壳现在是唯一仲裁者**：优先取基准端口，被占则顺延（每端口最多 +8，
  不做全局随机——固定端口对书签/扩展更友好），然后把实际值
  ①经 `WORKBENCH_PORT`/`_CDP_PORT`/`_CONTROL_PORT`/`_SESSION_MODE` **下发**给服务端、
  ②**公布**到 `<数据目录>/runtime/desktop-ports.json`（原子写、退出时清理）、
  ③由壳控制服务新增的 `GET /whoami` **答给扩展**（`desktop/ports.js`）。
  - **服务端新增 `GET /api/health`**：`{app:"AksoWorkbench", version, pid, ports, portsSource, shell}`，
    无副作用（与会写 `everConnected` 闩锁的 `/extension/health` 明确分工）。
  - **扩展不再把桌面地址当编译期常量**：新增 `background/core/desktop-endpoint.ts`
    （缓存 → 问 `/whoami` → 回退固定 18765），`parallel-session` 的"桌面同步口"白名单也随之
    从"一个数字"变成"已知端口集合"。扩展已有 `host_permissions: ["<all_urls>"]`，发现无需新增权限。
  - **顺带修掉 ADR-0002 的一半没生效**：`WORKBENCH_SESSION_MODE` 此前**从没被壳设置过**，
    托管会话实际一直跑原生 Chromium 而不是壳的 Chromium；现在随端口一起下发。
  - 端口值单点声明进 `config.py`（`PORT`/`CDP_PORT`/`CONTROL_PORT`），
    `routes_update` / `routes_extension` / `browser_pool` 一律读它，不再各自持常量。
  - 测试：`tests/test_ports_contract.py`（13 项，含"源码里除 config 外不得再出现这两个端口字面量"
    的扫描门禁）+ `tools/verify_port_broker.mjs`（23 项，真占用端口来验顺延与旧实例判定）。

### Fixed

- **"改了代码但 UI 没变"（用户实测，一次极难排查的事故）**。桌面壳跑的是 9/9 的旧打包副本、
  占着 18765；**新的壳因为 `waitReady()` 只看"这个端口上有人回了 200"就判定自己的服务已就绪**，
  于是主窗打开的是**旧实例**的页面——菜单里那两个明明已删除的命令又出现了。
  - `waitReady()` 改为打 `GET /api/health` 并**核对 `app === "AksoWorkbench"`**：
    从"有人应答"升级为"是我们的服务在应答"。
  - 启动时先读上一次的 `desktop-ports.json`：若其中的 `pid` 仍活着（且不是自己），
    弹**可执行**的对话框（PID / 版本 / 启动时间 / 它占了哪些端口 + "仍然启动 / 退出"），
    而不是静默等 40 秒后白屏。
  - 就绪失败的错误页不再只说"超时"：把"探到了什么"（`wrong-app` / `no-response` / `timeout` +
    对方身份）一并显示，并对"端口上是别的程序"给出针对性文案。
  - 端口顺延窗口全部被占时**明确报错退出**，并提示可用 `WORKBENCH_*_PORT` 指定端口。
- **端口协商的互斥顺序错误（本轮自测抓出）**：`pickPort` 原写"先看 `taken` 就跳过、否则再探"，
  于是一个端口被占会导致**三级连带顺延**（server 让给 18766 → cdp 因 18766 被预留而跳到 18767 →
  control 再跳到 18768）。改成"先探可绑、再看预留"后，只有真正首选端口不可用时才移动。
- **`desktop/ports.js` 未加入 electron-builder 的 `files` 白名单**（被 `verify_packaging.py` P6 当场拦下）——
  否则打包态会以 `require('ports.js')` 失败的形式崩，而开发态完全正常。
- **端口公布文件的清理时机**：原先挂在 `before-quit`，而启动失败分支走 `app.exit(0)`
  并**不触发** `before-quit` → 文件残留成陈旧数据（实测复现）。改挂 `will-quit`（对所有退出路径都触发），
  且删之前核对文件里的 `pid`，避免"新实例已写、旧实例才退出"时误删新实例的文件。

### Removed

- **平台洞察下线两个命令：`inventory`（全量盘点）与 `understand`（三层理解报告）**。
  用户定稿：这两个功能的**应用场景模糊、实际使用效果不理想**，留着只会让人在命令下拉里猜
  「我到底该用哪个」。现存命令收敛为两个：`login`（连通性验证 + token 预热）、
  `spider`（五步织网 + networkx 图分析）。
  - 删除代码：`insight/assemble.py`、`annotate.py`、`flowgraph.py`、`relations.py`、`render.py`、
    `understand.py`、`crawler.py`；`workspace.coverage_for_understand()`；
    `routes_insight.InsightRequest` 的 `known_objects` / `llm` 字段与 `_llm_summary_fn()`。
    `insight/` 因此从 **12 文件 1,766 行降到 6 文件 1,145 行**。
  - **保留的是通用产物契约而非「理解报告」那套**：`workspace.py`（工作区根 + 三态覆盖度 +
    manifest）、`kernel/`、`replay.py`、`drawio.py`、`report.py`、`lifecycle.py` —— spider 在直接用。
  - 页面：命令下拉只剩两项，去掉「已知对象白名单」「LLM 职责摘要」两行输入，
    对象编码输入框仅在 `spider` 下显示。
  - 测试删减：`tests/test_lifecycle_coverage.py`（13 项）与 `tests/fixtures/egmp/understand-capa-plan.json`
    一并删除（它们是 `understand` 的专属门禁）。全量测试 330 → **281 项**。
  - **历史数据不受影响**：`insight_runs` 里 `command` 为 `inventory`/`understand` 的旧行照常列出，
    产物读取仍按 `workspace_dir` → `artifact_dir` 回落（迁移 1–14 一行未动）。

### Fixed

- **平台洞察：对象读不到时产出一张「假网」还报 `complete`（正确性，本轮新发现）**。
  `client.get_object(code)` 拿到**空实体**（对象不存在 / 无权限 / token 失效）时并不抛异常，
  而 spider 原来只做 `meta = ... or {}` 就继续：流程一路走完，产出「**1 个对象节点 + 0 状态**」的
  空网，`stats` 照记，manifest 的 `coverage.status` 还是 `complete` —— **任务绿的、图是假的**，
  与 `INSIGHT-SPEC` §4.3「产物自证覆盖度」直接冲突。
  - 现改为：**目标对象**读不到实体即抛 `SpiderReadErrors`（带 code，任务记 `failed`，不写 manifest）；
    **相关对象**读不到只记进 `failures`（部分对象本就在织网范围外，不该炸整次运行）；
    状态表读不到则出网但如实记录。
  - 回归：`tests/test_workspace.py::test_run_spider_missing_object_raises_instead_of_empty_graph`。
  - 同一处还修掉一个**被静默吞掉的失败**：`get_object_statuses` 原先不在任何 `try` 里，
    而 `paged_post` 的翻页失败会返回空列表——现在失败会进 `failures` 而不是悄悄变成「没有状态」。
- **平台洞察：产物没有落到用户配的工作区（承诺与实现不符）**。`workspace.py` 的工作区契约
  （`<root>/<对象code>/<时间戳>/`）此前**只有 `understand` 在用**，而 `understand` 下线后
  `run_spider` 仍是「直接写 `runtime/insight/<job_id>/`」，同时 `routes_insight` 把
  `outcome.get("workspaces")` 写进 `insight_runs.workspace_dir` —— 而 spider **从不返回该键**，
  于是该列永远是空的，产物面板只能靠 `artifact_dir` 回落。现在改为**先定落点再爬**：
  路由先 `workspace.run_dir()` 拿到工作区目录，把它当 `output_dir` 交给 spider，并回填
  `workspaces`（同时在工作区留一份 `proc.log` 副本，好让产物面板在同一处列出日志与产物）；
  工作区不可写则退回任务目录，此时 `workspaces` 为空数组而**不谎报**路径。
  （没有做事后重试——那会把平台调用重放一遍。）
- **`run_insight` Agent 工具仍在跑已删除的 `understand` 命令**（会直接 400「未知命令」）。
  现改跑 `spider`，并且**改为调用 `routes_insight.run` 公共入口**而不是 `_start_job`/`_run_job`
  内部函数——原先绕过去就漏掉了 `workspace_dir` / `coverage_status` 的回填。
- **CHANGELOG 自身**：`[未发布]` 段出现两个 `Fixed` 标题（合并）；上一条 Security 条目里
  历史重写的替换规则因「替换结果又被再次替换」而全部显示成 `A → A` 的空转表，现已补回**左侧原文**，
  替换规则这才读得懂。

### Changed

- **模块 id 由上游项目名 `akso-cc` 改为 `insight`（用户定稿：「模块 id 要改名」）**。
  上一轮只改了界面文案，接口里仍是 `GET /api/modules/akso-cc`——名不副实的那半个还在。
  - `adapters/akso-cc.json` 的 `id` → `insight`；**文件名保持不变**，并在 `idNote` 里写明理由：
    文件名是「这份存档对应哪个来源项目」的引用真源（`repoPathEnv=AKSO_CC_REPO`、`repoPath`
    都指向它），改名会让该变量的语义失真。**id 与文件名不一致是刻意的**，由
    `tests/test_modules.py::test_adapter_id_matches_its_filename_reference` 守住。
  - 模块标题 → `平台洞察（登录 / 蜘蛛五步织网）`，原上游标题移入 `legacyTitle` 留档。
  - 前端改调 `/api/modules/insight`；新增 `test_old_module_id_is_gone` 钉住**旧 id 返回 404**——
    改名必须一次改干净，**不许留"第二个还能用的名字"**。
  - **迁移 15 `rename_insight_module_id`**（数据行改写，**不动历史迁移 1–14**）：
    `insight_runs.module` 统一为 `insight`。⚠️ 该列**历史上先后出现过三个值**——
    `akso-cc`（迁移 8 的列默认值）、`egmp-native`（原生化后 `routes_insight` 实际写入的）、
    `insight`（现行）。只改其中一个就会留下"同一个模块三个名字"，模块维度的统计/筛选**静默漏行**。
    真实库实测：`akso-cc` 1 行 + `egmp-native` 11 行 → **12 行全部归一为 `insight`**，
    且重复连接不再变化（可重入）。`routes_insight` 的 INSERT 同步改为写字面量 `'insight'`。
    > 列默认值仍是旧的 `'akso-cc'`：改默认值只能靠"新迁移 + 重建表"，而代码**始终显式写入**
    > 该列，默认值不会被用到。已在 [SCHEMA.md](docs/SCHEMA.md) §3.7 用表格记全三个值的来历。
  - 回归：`tests/test_app.py` / `test_modules.py` 同步（3 项失败当场指出旧 id 的调用方——
    这正是门禁的价值）；全量 299 → **301 项**。

- **「UI 永远最新」的兜底入口：应用菜单（用户提出要有这个习惯）**。
  页面一旦加载进渲染进程内存，服务端怎么改都不会反映到**已打开的那个窗口**——这是本轮
  "改了看不见"反复出现的机制性原因。正确动作是**重新加载**（服务端已加 `no-store`，重载必拿最新版），
  但 `Ctrl+R` 对非开发者不是可发现的操作。故：
  - 新增应用菜单 `文件 / 视图 / 窗口`，其中 **`重新加载界面（F5 / Ctrl+R）— 改完代码后按这个`**
    用菜单文案直说用途，不让人猜；F5 与 Ctrl+R 都收（F5 是 Windows/浏览器用户的肌肉记忆）。
  - 另给 `强制重新加载（忽略缓存）`、缩放、全屏、`开发者工具（Ctrl+Shift+I）`——
    排障时能自己开 DevTools 看 Network/Console，不必再靠"服务端实吐字节数"间接判断。
  - 与全局热键 `Alt+Q`（账号轮盘）不冲突：那个走 `globalShortcut`，菜单只在窗口聚焦时生效。

- **端口改为固定：撤回"壳当仲裁者、被占则顺延"（[ADR-0012](docs/adr/0012-fixed-ports.md) 取代
  [ADR-0011](docs/adr/0011-port-and-session-negotiation.md) §D-1）**。用户定稿原话：
  「我建议还是固定端口号吧，别拿所谓的桌面壳引导去调整，就固定端口号」。
  - **为什么推翻**：ADR-0011 要治的病（旧实例占端口 → 新壳认错服务）是真的，但**解法选错了**——
    它把"端口漂移"当成常规能力，于是引入协商窗口、实际端口公布、扩展按窗口探测、书签失效
    一整套活动部件；而本机端口冲突的唯一现实来源就是**工作台自己的旧实例**，
    这种情况**应该说清是谁占的**，而不是换号偷偷跑起来（用户不知道自己在 18768 上）。
  - **现在的行为**：三个端口是固定常量（18765/18766/18767，壳侧真源 `desktop/ports.js::PORTS`，
    Python 侧默认值必须一致）；启动顺序 = 清陈旧公布文件 → 判"是不是我们另一个实例" →
    探三个端口是否可绑 → **任一被占即 MessageBox + `app.exit(1)`**，文案区分
    "我们自己的实例"（给 PID/版本，去托盘退出它）与"无关程序"（给排查命令），
    并**不再提"可用 WORKBENCH_* 换端口重试"**（那等于教用户制造漂移）。
  - **结构上变成单实例**：端口固定后"活着的实例必然占着 18765"，原来那条
    「仍然启动 / 退出」的询问分支成了**永远走不到的死代码**，已删除。顺带消灭了
    ADR-0011 §6.2 的两个待办与 [PITFALLS](docs/PITFALLS.md) #23 的"两个实例共用同一份数据目录、
    凭据互相解密"那一族问题；`app.requestSingleInstanceLock()` 因此**明确不做**（端口占用已挡住）。
  - **扩展侧简化**：`desktop-endpoint.ts` 取消"按窗口探测控制端口"（那是为跟顺延而生的），
    只剩**一次** `GET /whoami` 身份确认 + 回退固定 18765。这一步保留的理由不是"跟上换端口"，
    而是确认"**18765 上是不是我们的服务端**"——凭据经过这条通道，而密钥就在本机库里。
  - **仍然保留的判据**（与顺延无关，别一起删）：`/api/health` 的 `app` 身份核对、
    "旧实例"必须核对**映像名**（`tasklist`）而非只看 pid 存活、启动时 `clearStale()`。
    这三条是本轮踩坑踩出来的真防线，见本节下方两条 Fixed。
  - **测试**：`tools/verify_port_broker.mjs` 整体改写为固定端口语义（27 项，含"不再导出协商 API"
    与"被占时点名固定端口号"）；`tests/test_ports_contract.py` 增 3 项——
    跨语言一致性（壳 `PORTS` == Python 默认值，这条壳侧脚本验不了）、固定值本身即契约、
    以及"不许把 `negotiate`/`MAX_DRIFT` 加回来"。
    > 跨语言一致性那条试过用 Node 正则解析 `config.py`，越改越脆（`PORT` 是
    > `env or env or "默认值"` 的跨行写法）——**这类检查本该放能真正 `import` 的那一侧**，故移入 pytest。

### Fixed

- **静态页与首页禁止缓存（根因级修复：重建了产物，界面却一直显示旧版本）**。
  这是"用户两次报『已下线的功能还在下拉里』"的**真正根因**——服务端实吐的已是新版
  （实测 2563 bytes / 2 选项），但 Electron 渲染进程带 `If-Modified-Since` 复验、
  `StaticFiles` 回 **304 未修改** → **渲染进程继续用自己那份旧副本**。
  两侧各自都"正确"，所以从代码完全反推不出来，只能靠"服务端实吐字节数 vs 界面"对比才发现。
  - 现对 `/` 与 `/static/*` 统一加 `Cache-Control: no-store, must-revalidate`
    （+ `Pragma: no-cache` / `Expires: 0`）；`/api/*` **不动**（避免影响 SSE 与将来可能的缓存语义）。
  - 本机回环读几十 KB 的 IO 成本可以忽略——**不值得为省这点开销换来"改了看不见"**。
  - 回归：`tests/test_ports_contract.py` 增 2 项（静态面带 `no-store`；`/api/*` 不受影响）。
  - **用户实测确认（2026-09-14，决定性证据）**：启动后首次打开仍是 4 项，
    **`Ctrl+R` 重新加载后立刻变 2 项**，之后两次启动都直接是 2 项。
    `Ctrl+R` 正是"绕过缓存重新加载"——一变就新，证明**服务端与产物一直是对的**，
    卡住的只是渲染进程里那份旧缓存；后两次正常则说明 `no-store` 已生效。
    > **方法论教训（本轮最贵的一课）**：判"界面为什么不更新"必须去读**渲染层的真实 DOM**，
    > 而不是停在"服务端实吐的字节数对不对"。两者可以**同时都"正确"**，而用户看的是前者——
    > 本轮为此绕了四轮。取证手段：用 `desktop\node_modules\electron\dist\electron.exe`
    > 配 `--remote-debugging-port` 起探针（⚠ **别用 `npx electron`**，它会另下一份不完整的
    > Electron 并报 "Electron failed to install correctly"），再用 CDP `Runtime.evaluate` 读 DOM。
  - 附带排查记录：磁盘上三份 `insight.html`（源码 / `dist/AksoServer` / 安装包内）
    **哈希一致且都为修复版**，`dist\AksoWorkbench\` 旧产物与扩展的 Chromium `Cache\` 目录
    均已排除——证据共同指向"服务端对、客户端缓存旧"，与结论自洽。
    另经全盘普查确认：本机**没有安装任何应用副本**、**没有孤儿 Python 服务**、
    含 `AksoWorkbench` 的 `package.json` **全盘只有仓库自己这一份**；桌面 `akso.lnk`
    指向 `D:\akso`（用户自己的文档目录，与工作台无关）。
- **界面上的上游项目名（`akso-cc`）已清除**（用户提出：「右上角 akso-cc 标识以及背后的功能
  现在功能逻辑是单独成工具的，并不依赖上游文件」）。能力早已原生化，界面不该再自称"封装某项目"。
  - 状态徽标 `akso-cc 就绪（ok）` → **`依赖就绪（ok）`**（失败态为 `依赖体检 ok：<项> 未就绪`）——
    它本来就是**本工作台自己的依赖自检**（`services/modules.py`），不是"上游项目健康度"。
  - 页头副标题 `封装 akso-cc：登录 / 蜘蛛（原仓库只读）` → **`本工作台原生实现：登录 / 蜘蛛五步织网（不依赖任何外部仓库）`**。
  - 执行日志前缀 `$ akso-cc spider …` → **`$ insight spider …`**。
  - ⚠️ 模块 **id 仍是 `akso-cc`**（`adapters/*.json` 的存档键，只读不改；改它会牵动
    `docs/SCHEMA.md` 契约与多份文档引用）——**界面上不出现，但接口里还在**，这是刻意的。
- **`inventory` / `understand` 的残留彻底清掉（用户提出这两个功能"太直接和庞大，一般实施用不到"）**。
  命令本身早在 `2cc09bb` 已下线，本轮清的是**会让人以为它还在的残留**：
  - **死代码**：`insight/report.py::to_markdown()`（把 `inventory.json` 渲染成「Gaia 系统配置清单」）
    **零调用方**，且模块名会让人以为还存在一条全量盘点读路径。已删；该文件只剩 `_table`
    （`spider.py` 在用的同包渲染工具），并改名/改述为"Markdown 表格渲染工具"。
  - **存档与现值不符**：`adapters/akso-cc.json` 的 `commands` 里仍列着 `inventory` / `understand`，
    而它会经 `GET /api/modules` 暴露给设置页的模块体检视图。已移除这两条
    （`commands` 现为 `login` / `spider`），并在 `deprecation` 里写明来由。
  - **刻意保留**（不是遗漏）：`insight/lifecycle.py` 的 `LifecycleApi` 被 `spider.py` 使用；
    `drawio.workflow_to_drawio` 被 `tests/test_drawio.py` 与 `tools/verify_insight_drawio.py`
    共 20+ 项断言锁定，且是"单页 mxfile"这一输出形态的唯一实现。删它们会伤到在用的能力。
- **「另一个实例在运行」对话框被 pid 复用触发，应用再也起不来（严重，实测踩到）**。
  壳启动时读 `runtime/desktop-ports.json` 判「是否已有实例在跑」，判据是
  **「文件里的 pid 还活着」**——而 Windows 的 pid 会被回收复用，于是**任何**撞上同一 pid 的
  无关进程都会让它判定为真：对话框弹出 → 用户按提示选「退出」→ `app.exit(0)` →
  **应用一行都没起来**。实测现场：对话框点名 `PID 3572`（而那台机器上根本没有工作台在跑）。
  - **判据升级为三要素：活着 + 映像名是我们**。新增 `ownImageNames()` / `looksLikeOurs()`：
    Windows 用 `tasklist /FI "PID eq N" /NH /FO CSV` 取映像名（一次调用同时回答"活着吗"和"是谁"），
    必须命中 `AksoWorkbench.exe`（打包态）/ `electron.exe`（开发态）/ 本进程自己的映像名。
  - `isAlive()` 在 Windows 上改走 `tasklist`：`process.kill(pid, 0)` 对同用户进程**恒不抛错**，
    对已消失的 pid 也不一定抛，本来就判不准。
  - **连带的现场污染也被治掉**：应用被强杀（`taskkill /F`、崩溃）时 `will-quit` 不触发 →
    陈旧公布文件残留 → 下次启动误报。新增 `clearStale()`（**启动时先清**，且只清"写它的进程
    确实不是我们"的那种），非我们实例的文件一律不碰。
  - **回归**：`tools/verify_port_broker.mjs` 由 23 项 → **32 项**。⚠️ 其中**原有那条
    「pid 仍活着时能识别出另一个实例」用的正是 `System`(pid 4)** —— 它把假阳性当成正确行为
    固化了下来，是这次事故的"绿灯帮凶"，已重写为「活着的**无关**进程不得被误认」。
  - **该脚本另补前置检查**：它靠"真占住基准端口"验顺延，所以**必须先确认基准端口空闲**。
    实测在"边跑着工作台边验收"时会先误判顺延、再抛未捕获的 `EADDRINUSE` 直接崩（报出来的是
    栈而不是结论）。现在会先给出人话提示并指明怎么退出工作台后重跑。
- **占用提示报错端口号（顺着 ADR-0011 的病根，在文案里复现了一次）**。
  `describeOccupant()` 硬编码 `端口 ${BASE[key]}`（恒为 18765），而实例**实际**可能在
  18766+（端口顺延是 ADR-0011 的核心机制）。用户照对话框去查 18765 只会查到空气，
  正是 ADR-0011 要根治的「端口信息不可信」。现改为报 `prev.ports` 里的**实际端口**，
  顺延时附「（基准 18765 被占后顺延至此）」说明来由；公布文件缺端口信息时回落到基准号。
  `bootstrapPorts()` 里另一处 `另占一组端口（server=${ports.server}）` 同样改为不谎报具体值。
- **平台洞察页头副标题没跟着命令下线走（用户实测发现：界面上看着像四个功能都还在）**。
  `insight.html` 的副标题仍写着「登录 / 盘点 / 理解 / 蜘蛛」——**下拉框里早只剩 `login` / `spider` 两项**，
  但页头仍在给用户看已下线的「盘点 / 理解」，于是「到底该用哪个」的困惑又以另一种形式回来了
  （这正是当初下线这两个命令要消灭的东西）。改为「登录 / 蜘蛛」；
  `services/egmp/__init__.py` 模块地图里的「读路径（盘点/理解/蜘蛛/networkx）」同样改掉。
  - **误报排查记录**：用户报「两个被阉割的功能还在选项里」时，先核过三轮才定性——
    ① 源码/产物 `grep` 无 `inventory`/`understand` 选项；② 打包态 `GET /static/pages/insight.html`
    实吐 `<option>` 只有 login/spider；③ 与用户截图逐字比对，发现截图里的
    「对象编码（understand/spider 必填）」「inventory 兜底白名单（可选）」两行**只存在于
    `2cc09bb^` 那一版**。结论：截图来自**构建之前就在跑的旧实例**，不是本次构建的产物。
  - **教训（值得记）**：`dist/` 下会残留**上一代 PyInstaller 产物**（`dist/AksoWorkbench/`，
    2026-09-09，含 4 选项的旧 `insight.html`）。它与本次产物 `dist/AksoServer/` **同级并存**，
    光看目录名很像「当前构建」。判断「你看到的是哪一版」不能靠目录名或印象，
    要看 ① 进程的 `ExecutablePath`、② 端口占用者、③ 服务端实吐的字节数。

- **`Alt+Q` 在桌面端未运行时被浏览器扩展抢走（用户实测 bug）**：只有扩展挂在浏览器里、
  工作台没开时，按 `Alt+Q` 会在**浏览器里弹出浏览器版轮盘**（内容与桌面轮盘重复，
  且此刻桌面数据面不可达——弹出的轮盘点谁都意义不大）。
  - **根因**：`manifest.json` 给 `quick-wheel` 声明了 `suggested_key: {default: "Alt+Q"}`，
    注释写着「桌面壳运行时 Alt+Q 优先」——**这个假设是错的**：桌面没运行时没人抢，
    Chrome 就把 `Alt+Q` 交给扩展。扩展是**执行面**，它的轮盘只应由桌面经
    `/extension/commands` 下发 `wheel.toggle` 触发；桌面没运行 = 没有触发源。
  - **修法**：删除 manifest 的 `commands` 段（`Alt+Q` 归还桌面壳全局热键独占）；
    同步删掉 SW 里对应的 `chrome.commands.onCommand` 监听器；
    弹窗页脚文案由「`Alt+Q` 呼出账号轮盘」改为「轮盘由桌面端 `Alt+Q` 呼出」。
  - **⚠️ 连带修一个由本次改动暴露的 SW 顶层崩溃（严重）**：Chrome 在扩展**未声明任何命令**时
    **根本不提供 `chrome.commands` 这个 API**（值为 `undefined`），而 SW 顶层仍无条件执行
    `chrome.commands.onCommand.addListener(...)` → 抛
    `TypeError: Cannot read properties of undefined (reading 'onCommand')`。
    **顶层抛出 = 其后所有代码永不执行**，包括 `startDesktopSync()` ——
    表现是「账号不再下发、扩展徽标离线、状态上报全停」这种**看起来完全无关的静默故障**。
    同时修掉 `background/core/site-auth.ts:148` 里同一处无条件访问（加了 `chrome.commands?.` 判空）。
  - **测试缺口已补**：此次事故里扩展启动冒烟 B1~B8 **全绿**（SW 进程活着、宿主页零错误），
    只有拿 `chrome.alarms.getAll()` 才看得出同步桥压根没启动。故新增
    **B9「桌面同步桥已启动（`akso:sync` 闹钟存在）」**（顶层跑到最后一行的可靠证据）
    与 **B10「无 commands 声明时 SW 仍跑到底」**；已用「摘掉闹钟创建」模拟顶层崩溃反向验证：
    10/10 → 8/10，B9/B10 精准变红。
  - 另修 `tools/verify_extension_boot.py` 的既有 flake：宿主页 `goto` 失败后的
    `about:blank` 兜底未加保护，服务未起时整个脚本直接崩、连结果都不打印（改为二次 try）。
  - 回归工具输出脱敏：`tools/verify_extension_sync.mjs` 原先直接打印快照里的
    **真实账号名**（运行期账号库内容会泄进终端/CI 日志），改为掩码输出。

### Security

- **清除仓库中的客户/环境标识与个人身份信息（含提交历史重写）**：真实部署域名、客户租户名、
  内网地址、真机账号名全部替换为文档占位值。⚠️ **本次重写了全部提交历史（所有分支与标签），
  提交 SHA 全变** —— 协作者必须按 [`docs/HISTORY-REWRITE-NOTICE.md`](docs/HISTORY-REWRITE-NOTICE.md)
  迁移本地副本，不要 merge 旧历史。
  - 替换规则（工作区与全历史一并执行）：真实平台/部署域名 → `*.example.com`；客户租户名 →
    `example-config`；环境代号 → `demo-val`；中文租户名 → `示例租户`；内网地址 →
    `192.0.2.10`（RFC 5737 文档网段）；真机管理员账号名 → `dev-admin`；真机普通账号名 →
    `dev-user`；Java 包标识里的个人标识 → `com.example`；商业 VPN 产品名 → `VPN`；
    文档叙述中的 `jizi-dragon` → `example-org`。
    原文清单见 [`docs/HISTORY-REWRITE-NOTICE.md`](docs/HISTORY-REWRITE-NOTICE.md)。
  - **刻意保留的功能锚点**（改了会坏自动更新/发布）：`desktop/package.json` 的 `publish.owner`、
    `desktop/proxy.js` 的 `PROBE_URL`、`tools/publish_release.py` 的 `REPO`、
    `tools/verify_update_proxy.js` 的资产 URL 基准。该标识本就公开写在仓库 URL 里。
  - **核查结论：历史中无密钥泄漏**。对 72 个提交全量扫描 GitHub PAT / AWS `AKIA` / Slack `xox*` /
    JWT / `PRIVATE KEY` / `password|token|secret|api_key = "…"` 赋值形态，**全部 0 命中**；
    `sk-` 命中均为 CSS 类名，44 位 base64 命中均为 `package-lock.json` 的 SRI 哈希。
    Fernet 密钥与 API Key 只存在于未入库的 `%APPDATA%\AksoWorkbench\workbench.db`。

- **`ruff` 由 19 条违规清零（`uvx ruff check .` 全绿）**，恢复 lint 门禁：
  - `workbench/api/routes_insight.py:175` **F821 `Any` 未导入**（`_workspace_dirs(row: Any)`）——
    因模块有 `from __future__ import annotations`，注解被惰性化才没在运行时炸；补 `from typing import Any`。
  - `tests/test_env_isolation.py:127` **B017 裸 `Exception` 断言**改为断言 `httpx.HTTPError`：
    原写法把「实现里换了个完全无关的异常」也判成通过，等于什么都没钉住。
  - 删除 3 处真正未使用的导入（`tools/diag_electron_typing.py` 的 `time`、
    `tools/verify_accounts_ui.py` 的 `json`、`tests/test_env_isolation.py` 的 `Path`、
    `tests/test_replay.py` 的 `run_understand`）；修正 6 处 import 排序。
  - `tools/publish_release.py:360` B007 未使用的循环变量 `i` → `_attempt`。
  - **新增 `[tool.ruff.lint.per-file-ignores]`：`"tools/*.py" = ["E402"]`** ——
    `tools/` 下的验证脚本是「会被执行的一次性脚本」而非可导入模块：必须先 `stdout.reconfigure`
    设编码、拉起服务、等端口就绪，**然后**才 import playwright（放顶部会让驱动逻辑在导入期就跑）。
    这是刻意的引导式写法，仅豁免 E402；未定义名（F）与排序（I）等规则仍生效。

- **平台洞察：关系网的自环判定恒假（拿编码比显示名）**。`build_relation_graph` 用字段里的
  `referenceObjectCode`（**编码**）去比 `object_name`（**显示名**），比较永远不成立 →
  `enableTree=false` 时树形自引用**根本没被过滤**，`selfLoop` 标记也永远是 false。
  现改为比编码（新增 `object_code` 参数，`understand` 传入）。
- **平台洞察：任务状态会把「部分成功」记成成功**。`run_understand` 原先按「没抛异常」计数成功，
  平台返回空响应也会走到成功分支 → 10 个对象只读到 1 个仍记 `succeeded`。
  现改为按**每对象覆盖度**计数：只有 `coverage.status != degraded` 才算成功，
  并映射为任务状态 `complete→succeeded / partial→partial / degraded→failed`。
- **测试台架：`FakeEgmpClient.get` 的前缀路由顺序写反**。`BasicObject/` 的通用分支排在
  `BasicObject/field/` 与 `BasicObject/lifecycleStatus/` **之前**，后者被误判成对象元数据
  （真实平台靠路由区分，不靠前缀）。此前未暴露是因为假客户端自己实现了便捷方法绕过 `get()`；
  录制回放（走 `get()`）把它翻了出来。
- **平台洞察：跨环境/跨账号共用 token 与元数据缓存串台（正确性）**。
  token 缓存原先**只按 `env_id` 分键**、缺省回落 `token-cli.json` —— A 环境的 token 会被交给
  B 环境的 base_url 使用；同一环境的两个账号（真实数据里示例租户就有 `dev-user` 与 `T0901`）共享同一张票，
  第二个账号实际以第一个的身份运行。元数据缓存 `QueryCache` 又是进程级全局单例、键不含环境，
  四个真实部署在同一进程内查询同名对象（如 `init_status__c`）时会互相污染。现改为：
  - token 缓存按 **`(base_url, username)`** 分键，载入时校验归属（`baseUrl`/`username` 双重比对），
    按 JWT `exp` 提前判失效，改**原子写**（临时文件 + rename），`env_id` 退为文件内元数据；
  - 缓存键加环境作用域 `{scope}|{kind}:{code}`，`scope` = 归一化 base_url；
    按对象失效改为**跨全部环境**清（否则另一个环境会留旧值）；
  - 顺带修：`get_or_set` 原先用 `None` 同时表示 miss 与「缓存了 None」，
    导致平台合法返回空响应时每次重新发请求，现用哨兵区分。
  - 验收：`tests/test_env_isolation.py` **19 项**。
- **平台洞察：生命周期四列表两列同时出错（进入动作与用户动作混归类）**。`executeType=2`
  （进入状态时自动执行）的动作若经动作栏解析出「修改状态」，会被塞进 `userActionEdges`
  → 报告的「进入动作」列**缺项**、「用户动作」列**多项**。现按 `executeType` 归类，
  并给每条边加 `mechanism`（`button` / `enterAction` / `lifecycleStatusIds`）。
- **平台洞察：同名按钮跨状态错配**。`_consume_match` 的 docstring 写「同状态内」，代码却遍历
  **全部状态**的动作栏行——实测 `sa` 的「提交」被配到 `sb` 的动作栏行。现只在「本状态自身的动作」
  与「无法归属任何状态的动作」里按名称匹配，**明确属于别的状态的 id 一律排除**
  （后者保留，因为 GetActionBar 返回的 id 有时确实不解析，这正是该兜底存在的理由）。
- **平台洞察：有「发起工作流」的动作却不产出 drawio（键空间错配）**。`GetActionBar` 的 `targetId` 是
  **workflowBasicId**，而 `workflows_by_row_id` 与 `workflow_graphs` 都按**工作流行 id** 建键——
  原先直接拿 basicId 去查，既取不到工作流名也取不到流程图。现建一次反查索引修正。
- **平台洞察：工作流触发状态未使用**。工作流行上的 `lifecycleStatusIds`（上游实测 195/707 条携带）
  在移植版中完全没用，只靠按钮发现工作流、覆盖面窄于上游。现新增 `workflowTriggerEdges`
  与按钮通道**取并集**，只认已知状态 id（不产出悬空触发），报告新增「工作流触发状态」章节。
- **平台洞察：报告未标注进入条件为推断值**。四列表头改为 **`进入条件（描述推断）`** 并加固定注脚——
  平台没有进入条件的读接口（上游六轮 48+ 路径探测均不存在），不能让使用者误以为是结构化事实。
- **平台洞察：drawio 产物对任何输入都必须良构（三处缺陷）**。此前 `.drawio` 在特定输入下
  **完全无法打开**，且标签显示错误：
  - **双重转义**：`_label` 先转义、`_node_xml` 又转义一次 → 换行变成字面 `&lt;br&gt;`、`&` 变成 `&amp;`。
    现改为**单层转义**，并以实证口径修正换行表示：draw.io 自身用 **`&#xa;` 字符引用**而非 `<br>`
    （字面 `<` 在 XML 属性值里非法）；多行标签另补 `whiteSpace=wrap`。
  - **非法 XML 字符未清除**：C0 控制字符在 XML 1.0 中即使写成字符引用也非法，
    名字里出现一个（如 U+000B）即让整个文件报 `not well-formed`。现已统一清除。
  - **节点 id 与根单元冲突**：平台步骤 id 若为 `"0"`/`"1"`，会与 `<mxCell id="0"/>`/`id="1"` 重复。
    现 `cell_id()` 为**纯函数**（`n_<清洗名>_<短哈希>`），跨模块天然一致且永不撞根单元。
- **平台洞察：spider 网络图产出悬空边引用（运算符优先级 bug）**。`build_network_model` 原写
  `_find(...) or f"obj:{target}" if target else None`，实际解析为
  `(_find(...) or f"obj:{target}") if target else None` —— 为「工作流名」这类目标造出
  `obj:工作流「X」` 这种**永不存在的节点 id**（已用真实 spider 数据实测确认）。
  现改为三级解析：状态节点 → 已爬对象节点 → 兜底挂到「来源状态所属对象」；两端都定不了位时
  置 `unresolved: True`（产物里可见），drawio 侧不再画悬空边。
- **平台洞察：对象 code 路径穿越（可写到产物目录之外）**。对象 code 由用户输入且此前只做 `strip()`
  就拼进文件名，实测 `../../PWNED` 的产物 resolve 后落在 job 目录之外、`C:/Temp/pwn` 会写到 `C:\Temp\`。
  现新增 `workbench/services/paths.py`：`safe_path_component()` 剥离路径分隔符/Windows 非法字符/
  零宽与双向控制符/首尾点空格并处理保留设备名（**正常编码与中文恒等通过，不改名**），
  `resolve_within()` 落盘前断言仍在目标根内。同时 `routes_insight.py` 的产物读取守卫由
  **字符串前缀比较**改为 `Path.is_relative_to()`，并拒绝 `artifact_dir` 为空的行
  （原先会退化为「CWD 下任意文件」）。
- **全站权限切换后遗留的误导性诊断文案（0.3.5）**：`manifest` 早在 0.2.21 就改成声明式
  `host_permissions: ["<all_urls>"]`（装载即得、永不弹窗），但扩展内若干提示仍在暗示
  "浏览器未授权、需去站点页面点一次授权"，排障时会把人引向不存在的动作。本轮只改文案与
  注释、不动逻辑：
  - `parallel-session.ts` 401 自愈：`仍未获站点授权（需在该站点页面完成一次授权）` →
    `在手动停用名单内，规则不生效（如非本意请在扩展弹窗恢复）`；`授权有效` → `网络平面可用`；
  - `syncAccountRules` 的安装尾注：`未获站点授权，规则待授权后自动生效` →
    `在手动停用名单内，规则不生效`；
  - 模块头、`selfHealRules` / `statusOf` / `reapplyAllRules` 注释、webRequest 监听注释同步
    改为"网络平面健康 = 未被手动停用"的口径（webRequest 注释另更正：改声明式权限后事件
    覆盖全部站点，归属仍只认绑定页签）。
  - 复跑：`npm run typecheck` 零错、`verify_extension_boot.py` 7/7、
    `verify_extension_isolation.py` 8/8、`verify_host_logic.mjs` 22/22。

### Added

- **新增 `workbench/services/egmp/kernel/` 平台知识层（读写路径共用）**：把此前「各写一遍」的
  平台约定收成单点。
  - `kernel/endpoints.py`：**唯一**端点表（49 条），含命名空间归属（版本化 `/api/openapi/v1.0/*`
    vs 私有无版本 `/api/platform|config/*`）、`render()` 模板展开（路径参数做 URL 编码）、
    `namespace_of()` / `is_versioned()`；补齐此前表外缺失的 6 条，以及上游给出但从未实现的
    3 条发现链探针（`BasicObject/QueryList|GetPageList|Page`）。
  - `kernel/semantics.py`：dataType / behaviorType / stepType / dispatchMode / 后缀约定等实证词典
    ＋判定函数；新增 `object_suffix_of()` 与 `*_name()` 助手——**未登记的枚举值回
    `dataType=<n>` 而不是静默消失**。
  - `auth.py` 与 `writers/endpoints.py` 保留为**转发垫片**，既有调用点一行未动；
    旧名（`MENUGROUP_QUERY` / `OPENAPI_*`）以 `LEGACY_ALIASES` 显式标注保留。
  - **insight 与 egmp 顶层的硬编码端点字面量归零（36 处）**；后缀约定不再在
    `crawler.py` / `annotate.py` 各写一遍。`tests/test_kernel.py`（35 项）用**源码扫描**钉死：
    出现硬编码端点、模板未展开、常量重复定义都会立刻红。
- **新增 `workbench/services/egmp/replay.py` + 离线回放夹具**：把真实平台的响应录成夹具，
  之后无需联网即可跑通洞察全链路。含 `Recorder`（包住真实客户端录制，**失败也录**）、
  `ReplayClient`（按夹具回放，返回值深拷贝）、夹具格式与版本校验。
  两条覆盖度保证：回放时不得有请求缺失（`ReplayMiss` 会列出可用键），
  也**不得有夹具条目从未被用到**（能抓住夹具腐化）。
  配套 `tests/fixtures/egmp/spider-training-mgr.json`（**真实形态**夹具：结构取自 `Recorder`
  对假平台的实跑录制 11 条交互，ID 与业务命名换成确定性占位值，**不含真实平台数据**）
  与 `tools/record_insight_fixture.py`（`--command spider` 对着真实环境录一次即可长期复用；只走只读链路）。
  > 原名 `understand-capa-plan.json` 的夹具随 `understand` 下线删除（2026-09-14）。
- **新增 `workbench/services/egmp/insight/workspace.py` + 迁移 14**：洞察产物落到**用户可配的工作区**
  `<root>/<对象code>/<时间戳>/`，每对象一格并各带一份 `manifest.json`（含 `schemaVersion`、环境、
  覆盖度三态、产物 sha256、失败清单）。新增设置键 `insight_workspace_root` 与接口
  `GET/PUT /api/insight/workspace`；`insight_runs` 增列 `workspace_dir` / `coverage_status`
  （**只追加迁移 14，1–13 不动**）；产物读取接口优先从工作区取、历史行回落 `artifact_dir`。
- **新增 `tests/test_replay.py` 与 `tests/test_workspace.py`**：
  离线回放与工作区/manifest 门禁（`inventory`/`understand` 下线后重挂到 spider 夹具：
  回放 17 项、工作区 34 项）。
- **新增 `tests/test_lifecycle_coverage.py`（13 项）**：L3 生命周期图的覆盖面与语义门禁——
  同名按钮跨状态不得错配、进入动作必须归入「进入动作」、`lifecycleStatusIds` 与按钮通道取并集、
  未知状态 id 不得产出悬空触发、进入条件仍取 `description`。
  > **2026-09-14 已随 `understand` 下线删除**：这些断言全部挂在 understand 的 L3 产物上。
- **新增 `tests/test_env_isolation.py`（19 项）**：环境与身份隔离门禁——token 跨环境/跨账号不泄漏、
  归属校验、JWT `exp` 优先、原子写、缓存作用域、假值可缓存。
- **新增 `tests/test_drawio.py`（57 项）**：drawio 良构门禁——对抗性标签（`&`/`<`/`"`/换行/C0 控制字符/
  中文/emoji/空名）、id 为 `"0"`/`"1"` 的节点、重复步骤 id、并行同源边、成环分层、坐标重叠。
  断言分两层：**先良构**（`ET.fromstring` 必须成功），**再语义**（解析回来的属性值等于期望文本）。
- **新增 `tests/test_paths.py`**：路径安全门禁——正常编码恒等性、逐条对抗性输入
  （`../`、绝对路径、保留设备名、控制字符、双向覆盖字符、尾点空格、超长）。
  真实落盘的端到端断言 2026-09-14 移到 `tests/test_workspace.py`（`understand` 下线时一并搬）。
- **新增 `tools/verify_insight_drawio.py`（11 项真机验收）**：把对抗性产物交给 **draw.io 桌面版 CLI**
  读一遍再回写，回写结果仍需良构——这是「标准 drawio」的最终判据。
- **新增 `workbench/services/paths.py`**：`safe_path_component()` / `resolve_within()`，
  作为外部输入进入路径成分的统一收口点（详见 Fixed 段的路径穿越条目）。
- **新建 [`docs/INSIGHT-SPEC.md`](docs/INSIGHT-SPEC.md)**：平台洞察（insight）的**功能定位与产品能力规格**——
  定位（只读配置理解引擎，与配置工厂共用同一套 API 知识体系）、5 项核心能力与非目标、
  平台知识层的目标结构与端点表唯一化、产物与存储模型（可配工作区根目录 + `<对象code>/<时间戳>/` + `manifest.json`）、
  6 条已知边界（含「进入条件为描述级」这一产品能力天花板）、验收标准与 4 项质量门禁、数据与重构纪律。
  四项关键决策已拍板：进入条件接受描述级并显式标注 / 工作区可配 + 自动时间戳目录 /
  抽取显式平台知识层供读写两路径共用 / 修原生 drawio 生成器并加 well-formed 校验。
- **新建 [`docs/PITFALLS.md`](docs/PITFALLS.md)**：22 条真机环境坑，原 `AGENT.md` §5 的唯一留存处。
  相对原文新增**复核列**（✅ 已对码核实 / ⚠️ 仍成立但有新变化 / — 本轮未复核）与「只增不改、编号追加」的
  维护规则；其中 #9 网络代理补记 2026-09-13 实测（`build.ps1:71-75` 仍硬编码直连 push，与「直连会被重置」
  的结论相反；`AKSO_PROXY` 到不了 egmp 的 Python 路径），#15 补记 `_next_seq()` 吞掉 DB 写异常会让
  指令序号从 1 重来的残留风险。
- **新建 [`docs/方向调研-平台洞察独立发展.md`](docs/方向调研-平台洞察独立发展.md)**：平台洞察（insight）
  独立发展调研——现状诊断（真实库零运行记录、平台耦合面未验证、产物会「看起来完整」）、准备清单
  （P0 可验证性 / P1 契约化 / P2 能力深化）与九类边缘规则验收清单。**调研文档，不含代码改动。**
- **文档体系整体重构（v0.3.4 基准，本轮不动代码）**：把「一份自述版本的架构文档」拆成按
  **变更频率**分层的常驻文档，并新建四份此前缺失的文档。
  - **新建** `AGENT.md`：接手手册——项目快照、代码地图、**六条维护红线**、
    **已知问题清单**（代码缺陷 13 / 安全与产品级 5 / 测试缺口 4 / 构建脚本债 14 / 文档债 3）、
    **22 条环境坑**（含实测出处）、验收基线（每轮必跑 / 改扩展必跑 / 改蓝图必跑 / 改更新必跑）、
    真机验收记录与待人工复验项。
  - **新建** [`docs/API.md`](docs/API.md)：60+ 端点按 13 个路由域手写权威契约（方法/路径/请求/响应/错误码），
    并设**四类特殊通道专章**：SSE 对话事件序列、SSE 洞察进度流、`/extension` 长轮询与 `seq` 游标、
    产物读取的路径守卫；附壳控制服务 `:18767` 全部端点。
  - **新建** [`docs/SCHEMA.md`](docs/SCHEMA.md)：迁移账 1–13 逐条清单、9 张现行表的列级定义、
    非数据库产物路径与表的对应关系、**扩展协议契约**（快照 / 备份文件 / 指令 / 状态上报），
    以及五步「改表清单」。
  - **新建** [`docs/CONFIG.md`](docs/CONFIG.md)：**五层配置全覆盖**（环境变量 / `settings` 表键 /
    代码内行为参数 / 数据目录文件 / 前端硬编码常量），每项标注默认值、实测值与**「改了会怎样」**；
    附「我想改 X，该动哪里」速查表。
  - **新建** [`docs/USER-MANUAL.md`](docs/USER-MANUAL.md)：安装与扩展装载、五个模块的操作步骤、
    两条登录路径的差异说明（用户 Chrome vs 内置 Chromium）、数据位置与安全须知、11 组 FAQ。
  - **新建** [`docs/EXTENSION-PLANE.md`](docs/EXTENSION-PLANE.md)：扩展侧权威文档——六平面隔离原理、
    与上游的差异对照、桌面↔扩展三面协议（数据面/指令面/状态面）及**四条必读护栏**、
    私有改造史、验证基线、安全边界（含平台侧不可根治的越权方向）。
  - **新建** 10 篇 ADR [`docs/adr/`](docs/adr/)：SQLite 选型（承接原 `docs/数据层决策.md`）、
    浏览器分配政策、Electron 壳、扩展=执行面、四项目原生化、browser_pool 单线程、
    凭据不落明文、更新通道与代理、NSIS 分发、Monitor 不回放危险操作。
    每篇含**背景 / 决策 / 取舍（含被放弃的方案）/ 后果 / 复核触发器**。

### Changed

- **全仓 `AGENT.md` 引用重指**：原 **59 处 / 21 个文件**（7 篇 ADR、`docs/API.md` 7 处、`docs/CONFIG.md`、
  `docs/SCHEMA.md`、`docs/USER-MANUAL.md`、`docs/EXTENSION-PLANE.md`、`extensions/quick-login/README.md`、
  `docs/archive/`）。重指后全仓指向 `AGENT.md` 的 markdown 链接为 **0**；`CHANGELOG.md` 内的两处历史记录
  **保留原文不改写**，仅把链接语法降级为代码体，避免留下死链。
- **订正两处随 `AGENT.md` 一起失效的事实**（源头均为其 B7 / §7.1）：扩展
  `extensions/quick-login/package-lock.json` 的 version **实测已是 `0.3.5`**（原文与 `CONTRIBUTING.md`
  均称停在 `0.2.20`；只有 `desktop/package-lock.json` 确实仍是 `0.2.1`）；
  `CONTRIBUTING.md` 的 `uvx ruff check .` 一行补注「本机 `uv`/`ruff` 均不存在，该门禁当前无法执行」。
- **[`docs/CODEBASE_OVERVIEW.md`](docs/CODEBASE_OVERVIEW.md) 复核重写**：原锚定 v0.2.2（落后 8 个版本，
  其「11 个路由域 / 迁移 1-11 / Inno Setup / 50 测试函数 / 双写版本号」均已失效），本轮按代码实测重写并
  锚定 `d0efa84`（v0.3.5）。行数口径改为**物理行数**；补齐验收基线实测（`pytest` **63 passed / 1 failed**、
  `verify_packaging.py` **8/8**、`ruff` 无法执行）与完整风险台账；并加注 `AGENT.md` 承接说明。
- **README.md 收敛为「产品定位 + 用户向快速开始 + 文档地图」**：删去开发/构建内容
  （全部移入 `CONTRIBUTING.md`），补上「它解决什么问题」对照表与四个来源项目的能力沉淀表。
- **CONTRIBUTING.md 接管全部开发内容**：环境搭建（强调 `--extra build` 不能省、uv 不在 PATH 时的替代命令）、
  依赖变更流程、**构建与发布全链路**（6 步链路、版本规则与五写、发布三道自查、构建前必跑体检、
  PS5.1 脚本纪律、**顺序敏感的两处**）、代码规约、开发流程与分工边界、
  **已知状态诚实记录**（ruff 9 条违规未绿、`verify_packaging.py` 未被 `build.ps1` 调用、
  两份 lockfile 版本陈旧等）、验收纪律。
- **扩展侧文档合并**：`extensions/quick-login/` 下上游 7 份文档（README / PROJECT-STATUS /
  CODEBASE_OVERVIEW / DIAG-GUIDE / USER-MANUAL / BROWSER-ONLY-MULTILOGIN-RESEARCH / DESIGN）
  与上游 CHANGELOG 移入该扩展的 `docs/archive/`（加 `UPSTREAM-` 前缀与归档抬头），
  权威内容合并进根侧 `docs/EXTENSION-PLANE.md`；原位置留指针 stub（README）；
  **上游 CHANGELOG 与本项目私有改造 CHANGELOG 拆成两份**（前者 490 行上游发布史存档，
  后者按本项目版本号组织并标注「同步上游时需重新摘除」的 ⚠️ 条目）。

### Removed

- **`AGENT.md` 删除：内容按变更频率拆分承接（文档体系收敛）**。它把代码地图、六条红线、已知问题台账、
  环境坑、验收基线、文档分工全部混在一份「接手手册」里，随代码演进多处失真。2026-09-13 复核确认的
  具体错误：§8 的 `uv`/`uvx` 路径在本机**并不存在**（venv 已不再内置这两个 shim，`pyvenv.cfg` 记
  `uv = 0.12.10`）、§1 的项目快照与实际库不符（实测 **环境 4 / 账号 5 / 洞察运行 0**，原文记
  「环境 3 / 账号 4 / 洞察运行 4」）、B7 的扩展 `package-lock.json` 版本已过期、全篇行数为**漏算空行**
  的偏低口径（`Get-Content | Measure-Object -Line` 会跳过空行；`workbench/**.py` 实为 10,172 行而非 8,167）。
  删除后按变更频率重新落位：
  - **§5 环境坑 22 条 → 新建 [`docs/PITFALLS.md`](docs/PITFALLS.md)**（只增不改，机构记忆）；
  - **§2 代码地图 / §3 六条红线 / §4 已知问题台账 → [`docs/CODEBASE_OVERVIEW.md`](docs/CODEBASE_OVERVIEW.md)**
    ——台账编号 **C/S/T/B/D 原样保留**，以免打断 `docs/API.md`、`docs/SCHEMA.md` 与 7 篇 ADR 中按编号的既有引用；
  - §7 验收基线 → 同文件「验收基线实测」与 [`CONTRIBUTING.md`](CONTRIBUTING.md) §7；
  - §6 数据与运行时目录 → [`docs/SCHEMA.md`](docs/SCHEMA.md) / [`docs/CONFIG.md`](docs/CONFIG.md)（原本已在那里）。
- `docs/架构分析.md`、`docs/SESSION-DIGEST.md`、`docs/数据层决策.md` 移入 `docs/archive/`
  （分别改名 `ARCHITECTURE-2026-09-10.md` / `SESSION-DIGEST-2026-09-10.md` / `DATA-LAYER-DECISION.md`，
  加归档抬头并写明内容去向）。原因：前两份自述版本为 v0.2.2 且与现状有实质偏差
  （迁移账、测试项数、依赖口径、桌面分发方式都已变）；第三份已完整并入 ADR-0001。

### 说明

- 本轮**以文档为主，仅附带清掉三处已失效的引用**（均为零行为变更）：
  ① 6 个服务端模块的文档字符串原先指向**已删除**的 `docs/模块契约.md` / `docs/迁移台账.md`，
  已改指 `docs/API.md` / `docs/CONFIG.md` / `docs/adr/`；
  ② `api/routes_agent.py` 的 `run_insight` 工具描述仍写「子进程封装」，已改「egmp.insight 原生实现」；
  ③ `adapters/*.json` 的 `$schema` 由死链 `docs/模块契约.md#adapters` 改为 `docs/SCHEMA.md`（仅此字段）。
  另把 `tools/setup.ps1` 的 Node 检查从**必检项降为 INFO 信息项**并改写文案（原文案「洞察/工厂功能不可用」
  与 [原生化决策](docs/adr/0005-native-internalization-of-four-projects.md) 矛盾）。
  修完已复跑：`pytest` **64 passed**、`verify_packaging.py` **8/8 PASS**、两个 `.ps1` 语法检查通过
  （`setup.ps1` 编辑后重补了 UTF-8 BOM）。
- 盘点中发现的其余代码缺陷与安全风险一律以
  已删除的 `AGENT.md` §4 的「已知问题清单」形式落盘（代码缺陷 10 条、安全与产品级 5 条、
  测试缺口 4 条、构建脚本债 11 条），修复另开一轮。

## [0.3.4]

### Added

- **出网通道自动选择（0.3.4，实测倒逼；用户定稿："能直连就别配代理"）**：`v0.3.3` 发布后真机
  验证发现一个致命断层——更新**能发现新版、却下载不下来**。逐跳实测：查版本走
  `github.com/<o>/<r>/releases.atom`（1.9s）、资产走 `github.com/…/releases/download/…`（302）
  → CDN `release-assets.githubusercontent.com`（很快）；而本机**直连 `github.com` 是间歇性的**
  （同一 URL 1.1s 成功与 20s 超时都出现过），Chromium 默认又不真用系统代理（注册表
  `ProxyEnable=1` 摆着，请求仍直连超时）→ 于是表现为"检查得到、下载不动"。
  修法：新增 `desktop/proxy.js`，**默认优先生成直连**，只有直连探测失败才回落到代理：
  **环境变量 `AKSO_PROXY`/`HTTPS_PROXY`（强制代理）→ 数据目录 `proxy.txt`（一行=强制代理；
  空文件=强制直连，终极兜底开关）→ 自动（直连探测 Atom feed，通就直连、不通再用 Windows
  系统代理）**；探测带 6s 超时（直连失败在本机表现为"卡住不返回"，无超时会挂死启动）。
  三个必守细节：**必须显式放行回环**（`proxyBypassRules = <local>;127.0.0.1;localhost;[::1]`，
  否则壳与 sidecar/CDP(18765/18766/18767) 的本地通信会被塞进代理，表现为"网络正常但功能全废"）；
  通道在**任何联网动作之前**定下（`app.whenReady()` 里、updater 之前）；选中的通道与探测结果
  写进 `shell-state.json` 的 `proxy` 字段并透出 `GET /api/update`，排障一眼可见。
  实测：直连可用时选直连（探测 1131ms、取资产 1201ms），**完全不经过代理**。
- **`tools/verify_update_proxy.js`**：用**真实 Electron 网络栈**（`electron.net.fetch`，即
  electron-updater 内部同一套）对真实 Release 资产 URL 做基线 + 应用自动策略后的对比。
  判定以"策略生效后必须拿到 `latest.yml`"为准；直连基线成功不算失败（那正是 auto 想要的结果）。
- **`tools/publish_release.py`**：发布到 GitHub Releases 的可核对工具（建 draft → 传资产 → 转正 →
  匿名复验 `latest.yml`）。发布前三道自检：`latest.yml` 版本号 == 发布版本、其 sha512 == 安装包
  实际 sha512、指向本次安装包——发布错的清单会让**所有客户端**更新失败。资产顺序是"先小后大"
  （350MB 最容易失败，先把重试预算花在大头上）。支持 `--no-proxy`：关掉 Clash 后 **git 自己的
  `http.proxy` 仍写着 127.0.0.1:7890**，脚本会对着没人监听的端口连（`WinError 10061`）——
  "我关了代理"这句话必须在脚本里有对应开关。

### Changed

- 托盘「检查更新…」改为先**同步**把 `phase=checking` 落盘、再弹结论弹窗：手动检查在无新版/开发态
  会走弹窗分支，用户不点按钮时状态会永远停在 checking，排障与自动化验证都看不到结论。

## [0.3.3]

### Added

- **桌面端自动更新（0.3.3，用户定稿：通道 = GitHub Releases；策略 = 启动检查 + 静默后台下载 + 退出时安装 + 手动检查）**：
  - **壳内更新引擎**：新增 `desktop/updater.js`（状态机 + 事件接线，纯数据 `state()` 便于纯 node 验证）。启动后 20s 首次静默检查、此后每 6h 一次；`autoDownload=true` 后台静默下载（**不弹窗、不抢焦点**）；下载完成后只改托盘提示与账号中心角标，真正安装交给「你退出应用时」——`autoInstallOnAppQuit=true`，并在 `before-quit` 里**先 `killServer()` 再拉安装器**（顺序关键：安装器要覆盖 `AksoServer.exe` 与扩展目录，sidecar 还活着会锁文件）。托盘新增「检查更新…」（结论必落弹窗）与「关于 vX.Y.Z」；账号中心右上角版本角标可点击即手动检查。
  - **跨进程状态通道**：壳每 15s 把状态写 `%APPDATA%\AksoWorkbench\shell-state.json`（原子写；`%APPDATA%` 规则与 `workbench/config.py` 的 `DATA_DIR` 对齐），Python 侧 `workbench/api/routes_update.py` 提供 `GET /api/update`（超 60s 无心跳判 `live=false` → UI 显示「桌面壳未运行」）与 `POST /api/update/check|install`（代理到壳控制服务 18767）。开发态（未打包）返回 `phase=unsupported`，弹窗提示改用 `git pull`，**绝不误报**。
  - **为什么读文件而不是每次 HTTP 探壳**：账号中心每 3s 刷新，读本地 JSON 更便宜且壳不在时能优雅降级。
  - **为什么不做「静默自动重启安装」**：会打断正在进行的自动登录/页面操作——静默下载 + 退出时安装是「零打扰」与「必达」的平衡点。
- **扩展升级后自动检测「需重新加载」（0.3.3）**：扩展随安装包更新（`resources/extension` 被覆盖），但 Chrome 只在「重新加载」后才用新代码 → 此前升级后可能长期跑旧扩展而无任何迹象。现在：扩展在 `POST /extension/state` 上报自身版本（`extVersion`，**账号映射为空也照发**——旧实现 `if (!items.length) return` 会让这条事实永远传不出去），桌面端比对版本并把 `extVersion` / `desktopVersion` / `extStale` 加进 `GET /extension/health`；账号中心据此显示「浏览器扩展版本过旧」提示条 →「重新加载引导」= `POST /extension/setup-helper {mode:"reload"}` → 壳打开 `chrome://extensions` 并给出「点 ↻ 重新加载」步骤说明（与首次安装共用同一套壳能力，仅文案不同）。**刻意不做自动 `chrome.runtime.reload()`**：重载会掐断进行中的自动登录，把不可控时序留给用户点一次。
- **`tools/verify_updater_logic.mjs`（47 条断言，纯 node 秒级）**：用 `Module._load` 钩子把 `electron` / `electron-updater` 换成桩，验证开发态不崩、打包态装配（`autoDownload`/`autoInstallOnAppQuit`/`allowPrerelease`）、事件链（checking → available → progress → downloaded）、就绪后再检查走短路、无更新、失败路径（自动静默 / 手动弹窗）、`installOnExit` 参数与幂等、下载完成提示不打断。⚠ 脚本注释记下一个真实踩坑：**桩必须在 `init()` 期间仍然生效**——真实 `electron-updater` 包在模块体里就调用 `app.getVersion()`，只在 load 期间挂钩会让 init 内部那次 require 落到真实包并抛错，而错误会被 `init` 的 try/catch 静默吞掉（表现为 `supported` 恒为 false）。
- **扩展版本号纳入自动同步（0.3.3 修正）**：`tools/bump.py` 的「扩展双写」路径此前指向**不存在**的 `packages/extension/package.json`（唯一存在的是工作区根 `extensions/quick-login/package.json`），因有 `exists()` 守卫而**静默少写一次**——规则本意是"扩展版本随项目演进"，实际只写了 manifest。路径已修正，扩展版本面重新变为 manifest + 工作区 package.json 两处齐步走。

### Changed

- `tools/build.ps1` 的发布说明与 `docs/EXTENSION-INSTALL.md` 第四节新增「发布更新到 GitHub Releases」：含 **GH_TOKEN 的完整获取路径**（头像 → Settings → Developer settings → Personal access tokens → fine-grained（Contents: Read and write，选 `example-org/akso-mogul`）或 classic（勾 `repo`）→ Generate → 只显示一次的复制时机）、两种发布方式（`--publish always` 注入 `$env:GH_TOKEN` / 网页手动传 `setup.exe + latest.yml + blockmap`）、tag 与版本号必须一致的顺序要求、token 有效性与吊销的核对方法。**更新器匿名读取 Release ⇒ 仓库必须公开**（本仓库即公开）。
- 账号中心右上角版本角标从静态 `<span>` 变为可点击按钮：平时只显示版本号，有新版/下载中/已就绪时分别上色（`has-update` / `is-ready`），`title` 给出具体状态；浏览器直开本页（无壳）时依旧是纯展示，不影响任何既有功能。

### Fixed

- **安装引导提示条每次都弹（0.3.2，用户实测反馈）**：「未检测到浏览器扩展」横幅的判据此前放在**浏览器内存**里（`extEverConnected`）→ 刷新页面/重启应用即复位，于是每次打开账号中心都会重新弹出。改为**按安装实例落库的一次性闩锁**：`/extension/state` 收到执行面上报（或 `/extension/health` 判为已连接）的**第一次**即把 `ext_connected_once=1` 写入 settings 表，`/extension/health` 随之返回 `everConnected`；前端只在 `!connected && !everConnected` 时显示横幅。**语义（用户定稿）：以"能否与浏览器连接成功一次"为依据——连上过一次即永久静默**（之后即便关掉 Chrome 也不提示），单纯的 `connected=false` 不再触发提示。找回入口仍在托盘「安装浏览器扩展…」。
  - 实测（隔离实例 + 跨进程重启）：全新装机 `everConnected=false`（会提示）→ 首次上报后 `true`（立即静默）→ **重启服务进程后仍为 `true`**（核心回归点）→ 闩锁确实落在 settings 表。
- **`tools/build.ps1` 新增 `-Bump build|push|none`**：此前版本演进只有 MINOR+1 一条路，补丁修复也会被抬成 MINOR 版本（本次 0.3.2 即为 `-Bump push` 的补丁发布）。

- **打包链路的两个静默断点（0.3.1 首次真机构建暴露）**：
  - **`workbench/server_entry.py` 丢失** → `server.spec` 仍指向它，PyInstaller 直接失败（`script … not found`），而开发态走 venv + uvicorn 完全不受影响，故长期无人察觉。已重建：**绝对导入**（PyInstaller 把入口当顶层脚本执行，相对导入必 ImportError）、接受壳的 `--server` 调用约定、**不打开浏览器**（打包态 UI 由 Electron 主窗承载）、冻结态把启动信息落 `DATA_DIR/server.log`（壳以 `stdio:'ignore'` 启动，现场零输出）。实测：`AksoServer.exe` 正常起服，日志记 `AksoServer v0.3.1 pid=… (frozen=True)`。
  - **`desktop/icon.ico` 自首次提交起就是坏文件**：整份二进制被"当文本另存为 Unicode"了一次（UTF-16LE BOM + 每字节占 2 字节），且该过程**有损、无法还原**（git 历史里也只有坏版本）。后果两处且都静默：`electron-builder` 报 `image … shas unknown format` 导致**构建失败**；Electron 托盘图标一直是空白（被 main.js 的 try/catch 吞掉）。已新增 `tools/make_app_icon.py` 从品牌 PNG 重新生成 7 尺寸 ICO（16/24/32/48/64/128/256）。⚠ 两个坑写进工具注释：Pillow 的 ICO 写出**不会放大**（请求尺寸大于源图会被静默跳过），而 electron-builder 要求至少 256 → 必须先把源图 LANCZOS 放大到 256 再生成。
- 新增 `tools/verify_packaging.py`：**打包前置体检（秒级 7 项）**——spec 入口存在 / 入口只用绝对导入 / 壳 spawn 的 `resources/server/AksoServer.exe` 与 extraResources 映射一致 / 安装包要携带的扩展产物齐备且 manifest 版本 == 项目版本 / `tools/build.ps1` 具备 UTF-8 BOM。上述两个断点都能被它提前拦住，不必烧掉数分钟构建才失败。

### Added

- **安装包携带浏览器扩展 + 应用内一键安装引导（0.3.1）**：`desktop/package.json` 的 `extraResources` 增带 `extensions/quick-login/dist` → 安装后位于 `<安装目录>\resources\extension`，**无需联网下载扩展**。
  - 账号中心新增「未检测到浏览器扩展」提示条（数据源 `GET /extension/health`：TTL 60s 内有无执行面上报；**仅在从未连上过**时出现，关掉 Chrome 不会反复提示），按钮「一键安装引导」→ `POST /extension/setup-helper` → 桌面壳控制服务 `POST /extension-setup`：自动打开 `chrome://extensions` + 打开随包扩展目录 + 弹出分步说明；托盘菜单同样新增「安装浏览器扩展…」。
  - **为什么做不到静默一键装（逐条实证）**：Chrome 在 Windows 上拦截非商店 `.crx` 安装；`ExtensionInstallForcelist` 在 HKCU 下普遍不生效（[SO](https://stackoverflow.com/feeds/question/36208439)）、自托管 `update_url` 亦常见失败（[SO](https://stackoverflow.com/feeds/question/49473933)）；本仓库**无签名私钥**（只有 manifest 公钥 `key`），无法用既有 ID 重打 CRX。取舍与依据见新增 `docs/EXTENSION-INSTALL.md`（含"要真一键只能先上架商店"的后续路径）。代价：用户首次点 4 下，之后永久可用。
  - `tools/build.ps1` 增补：bump 之后**自动重建扩展**并校验包内 `manifest.json` 版本号 == 本次发布版本号（此前扩展不进安装包，故无此风险）；release commit 一并纳入扩展 manifest/package 与 `.version.json`。

### Changed

- **「桌面点击 → 浏览器打开」延迟优化（0.2.24）：约 1.1s → 约 30ms**。实测定位到延迟主项是**扩展每 2s 轮询一次指令队列**（量化延迟 0~2s，均值 ~1s），再叠加点击路径上一次 `tasklist` 子进程探测（~124ms）且与指令入队**串行**：
  - **长轮询取代轮询节拍**：`GET /extension/commands` 新增 `wait` 参数——无指令时服务端挂起请求（`threading.Condition`；`dispatch_command` 入队即 `notify_all`），指令延迟从「等下一拍」压到一次本机回环。默认 `wait=0` 保持原非阻塞语义（既有调用方与 `tools/verify_extension_sync.mjs` 不受影响）。A/B 实测：**平均 964ms → 20ms（≈50×）**，最坏 1671ms → 34ms。
  - **扩展侧改为长轮询流**（`sync.ts`）：新增 `commandStream()` 独立循环（`wait=15`），与 2s 的数据面 `tick()` 解耦。护栏四项：**消费互斥**（`consuming`——否则同一条 `par.open` 会开两个页签）、**流幂等闸**（`streaming`——alarm 每次触发都会调用它，无闸会累积并发循环）、**兜底轮询**（流 >25s 未取回时由 tick 补一次非阻塞拉取）、**离线退避**（`getJson` 返回 null 时退避 1.5s，避免热循环）。SW 被回收时退化为原 alarm 行为，不会更差。
  - **探测缓存 + 并行化**：`/extension/launch-chrome` 的 `tasklist` 探测加缓存（正结果 10s / 负结果 2s；实测探测成本 124ms → 命中 9ms，14×），拉起后立即标记「运行中」防连点多开；`wheel-picker.html` 与 `accounts.js` 的点击路径改为 launch-chrome 与指令入队**并行**（两者仍都 await 完才关轮盘——关窗会中断在途 fetch，冷启动时若 launch 没发出去会永久滞留指令）。
  - 新增回归工具 `tools/verify_command_latency.py`：独立实例上 A/B 旧节拍与长轮询，钉住该延迟契约（误删 `notify_all` 或 `wait` 会立刻红）。
  - **尚未优化（需产品决策）**：**Chrome 冷启动**（tasklist 124ms + Chrome 启动 1~3s 是硬成本）。可选两条：① `launch-chrome` 直接带登录 URL 打开，省掉「启动 → SW 起来 → 取指令 → 开页签」的往返（需配一条「绑定已开页签」的指令）；② 桌面应用启动或账号中心打开时预热 Chrome。

### Fixed

- **两个静默失效缺陷（0.2.23，红/绿验证）**：
  - **虚拟 Cookie 袋被无关操作清空**：MAIN 壳的 `Storage.prototype.clear` 补丁未区分存储实例，而 Cookie 袋住在 **localStorage** 命名空间——页面调 `sessionStorage.clear()` 会把袋（含 token）一并清空 = 登录态被无关操作销毁。修法：仅当 `this === window.localStorage` 才重置袋子（探测用 `try/catch`，避免沙箱 iframe 里裸读 `win.localStorage` 抛 SecurityError 穿出补丁）；`Storage.prototype` 其余补丁（getItem/setItem/removeItem/key/length）语义不变。`onNamespaceWrite` 的两存储 token 同步**刻意保留**（站点把 token 只写 sessionStorage 时仍需镜像进袋）。
  - **多 iframe 页面自动填表静默失效**：`auto-login.fillPasswordInIframes` 在第一个可访问 iframe 上就 `return Boolean(field)`——若该 iframe 不是登录表单，函数直接返回 false → 上层判「密码未填」→ **永不点提交**。修法：扫描全部 iframe（与 `readPasswordValue` 的全量语义一致），返回语义 = 「是否有 iframe 含密码框」。
  - 验证：新增 `tools/verify_extension_isolation.py`，**先红跑复现**（恰好两条 ★ 回归断言失败）**再绿跑 8/8**。

### Added

- **扩展端三个自动化回归工具（0.2.23）**——此前扩展端**没有任何自动化回归**，改动只能靠 typecheck（保类型不保运行期）：
  - `tools/verify_extension_boot.py`：隔离 profile 启动冒烟（SW 能否启动 / 命令清单 / storage 键 / 桌面数据面同步）。⚠ 必须 headful——MV3 扩展在旧 headless 下不加载（实测 `service_workers` 为空）。
  - `tools/verify_host_logic.mjs`：用 esbuild 单独打包 `host.ts` 后跑 22 条断言，锁住端口口径三平面分工（含 `127.0.0.1:18765` vs `:18996` 不串号、默认端口规范化、IP 父域不含端口）。
  - `tools/verify_extension_isolation.py`：自建 fixture 页 + 装载 dist 内容脚本产物，验 Cookie 袋隔离与多 iframe 自动填表（两条 ★ 即上述缺陷的回归点）。

### Changed

- **quick-login 架构清理（0.2.23，用户定稿：不影响功能）**：以「可达性分析 + 协议面审计」为依据清理死代码、合并重复实现，扩展收敛为**执行面**（收 `par.list` / `par.open` / `wheel.toggle` + 六平面隔离）。
  - **死文件 2,060 行**：`ui/parallel/*`（并行管理页 0.2.13 已退役：1,290 + 130 + 629 行）与 `ui/send.ts`。此前 `copyUiStatics` 仍把该页 html/css 复制进 dist，现 `dist/ui` 只剩 popup / wheel / theme.css。
  - **旧会话模型退役（≈260 行）**：删 `session-manager.ts`、`account-registry.ts`、`navigation.ts`（其唯一活口「页签关闭清凭证」迁入新建 `core/pending-login.ts`）、`Session` 类型、`SESSION_KEYS.sessionTabBindings`、`session.*` 协议与 SW 分支、`onErrorOccurred` 里的会话兜底。依据：`session.*` 在扩展内外**均无发送者**（桌面端只发 `par.open`/`wheel.toggle`），其绑定表恒空故兜底恒为 no-op。
  - **并行页专用协议（SW 死分发 ≈275 行，占该函数 55%）**：删 `par.create/update/delete/moveBox/renameBox/deleteBox/probeScheme`、`data.export/data.import`、`DataBackup`。它们不只是「没人调」——`sync.ts` 每 2s 用桌面快照对账并删除快照外账号，故本地增删改会在 2s 内被撤销，属**语义冲突**而非休眠能力。⚠ `parallelStore` 的增删改**保留**（`sync.ts` 直接调用，是活的数据面）。
  - **按 0.2.21 定稿保留**：`site-auth.ts` 与 `site.grants.*`（授权核心）、`par.grantChanged`（规则重装钩子）、`ql.diag`（台架/现场诊断）、`wheel.toggle`（桌面通道）。如实说明：清掉并行页协议后 `site-auth` 已无运行时入口，成为「休眠源码」（仅 `Scheme` 类型被引用）。
  - **协议键收敛为单一真源**：`__ql_ns_` / `__ql_cookies__` / `__auth_token__` / `__auth_user__` / `__device_fp__` / `QL_PAGE_TO_BRIDGE` / `QL_BRIDGE_TO_PAGE` 此前在 `shield-main.ts` 与 `parallel-session.ts` 各自硬编码（17 处），现全部 import `shared/constants.ts`（新增 `SHIELD_USER_KEY` / `SHIELD_DEVICE_FP_KEY`）；并在该模块注明「会被 MAIN world import，顶层禁止任何扩展 API 求值」。
  - **合并重复实现**：`applyTitle`（navigation 与 parallel-session 逐字重复）→ `background/tabs/tab-title.ts`；待登录凭证「键格式 + 读写」两份 → `background/core/pending-login.ts`；host/端口五函数（分居 tab-rules 与 parallel-session）→ 新建 `background/core/host.ts`；`sync.ts` 的盒子键字面量 → `LOCAL_KEYS`。
  - **顺带修掉潜伏 bug**：`parentDomainOf` 带端口入参时两个 `return` 仍返回**带端口的原 host**（契约要求 DNR 域不含端口；此前调用方都预剥端口故未暴露）。
  - 死代码：`parallelSession.isBoundTab` / `hostOfTab` / `deleteAccount` / `refreshTitle`、恒真分支 `details.tabId <= 0`、恒真表达式 `tabId !== null && reusedFlag`。
  - **验证**：typecheck 0 错误；build OK；隔离 profile 启动冒烟 7/7；`host.ts` 行为断言 23/23（端口口径三平面分工逐条锁定）；协议键复查——全仓库仅 `constants.ts` 保留定义，两个 bundle 内仍各出现 1 次（跨进程约定未变）。
  - **未做（需单独决策/回归）**：审查另发现 2 个**真功能缺陷**（`shield-main` 的 `Storage.clear` 补丁会让 `sessionStorage.clear()` 清空虚拟 Cookie 袋 → token 丢失；`auto-login.fillPasswordInIframes` 首个可访问 iframe 无密码框即 early-return → 多 iframe 页面自动填表静默失效），以及 bridge 上行入口缺 `.catch`、`title-hook` 观察器过宽等加固项——均属**行为变更**，不纳入本次「不影响功能」的清理，另行排期。

### Added

- **数据层决策与维护工具（0.2.22）**：实测 `workbench.db` 曾达 **82.02 MB**，其中 **74.03 MB 是 `doc_chunks.embedding`**——知识库功能下线后遗留的向量数据，运行时代码**零处读取**（全仓库仅 `db.py` 的迁移定义与 `storage.py` 的一句注释提及这两张表）；空闲页仅 0.25 MB，说明不是碎片而是「仍然存活但已无人使用」的行。真实业务数据合计不到 20 KB（4 账号 / 3 环境 / 5 条任务台账 / 11 项设置），`job_logs` 建了索引却零处写入（日志走磁盘 `log_path`，不入库）。
  - **决策定稿：继续使用 SQLite，不引入 MySQL，不引入 Redis。** 依据与量化触发器见新增 `docs/数据层决策.md`。要点：① Redis 不是数据库——RDB 间隔快照 / AOF `everysec` 均有丢失窗口，而本项目核心资产是 Fernet 加密凭据（`password_enc` + `account_fernet_key`），**丢失不可重建**，且 Redis 无关系约束（现依赖 `ON DELETE CASCADE`），数据还须全驻内存；② MySQL 的决定性理由是**交付形态而非性能**——单机安装包（Electron + 本机 sidecar + Inno Setup）多装一个服务端守护进程（服务账号/端口/root 密码/升级/防火墙）会直接损害「一键安装」卖点，而 `uvicorn` 默认 `workers=1`、`db.py` 单连接 + 全局 RLock 的单写入负载下，MySQL 相对 SQLite WAL 没有任何收益；③ 反例佐证：`routes_extension.py` 的扩展指令队列**刻意**用内存 `_commands` + ack + 单调 `ext_cmd_seq` 游标实现，未拉 Redis——⚠️ 该队列与 `_STATE` 均为进程内状态，是**多 worker/多机部署时最先静默失效的地方**，届时修法是把指令队列落成数据库表，而不是上 Redis。
  - 新增 `tools/db_maintenance.py`：默认只读诊断（表规模 / 大列体积 / 遗留 settings 键 / 独占锁探测），`--all` = `VACUUM INTO` 一致性备份 → 置空遗留向量（**保留文档与分块正文**）→ `VACUUM` 回收。刻意**不做成自动迁移**：迁移历史不可变，且「自动删除用户数据」的迁移风险过高。真机执行结果：**82.02 MB → 2.90 MB（回收 79.12 MB）**，`PRAGMA integrity_check = ok`、`foreign_key_check` 无违规，129 份文档 / 3,420 个分块正文全保留、4 账号 3 环境与盒子/分配池/导出备份全部正常，桌面端重启后服务 200、模块 4/4。
- **迁移 13 `add_created_at_indexes`**：补齐列表查询索引 `agent_audit(created_at)`、`blueprint_jobs(created_at)`、`insight_runs(created_at)`——三处列表接口均为 `ORDER BY created_at DESC LIMIT ?`，而既有索引只建在 `tool` / `status` 上；另加 `account(box)` 对齐 `rename_box` 的 `UPDATE ... WHERE box = ?`。已在真实数据副本上先行验证：升至版本 13、四个索引到位、账号/分配池/盒子/导出备份查询全部正常，随后 50 项 pytest 全绿、ruff 通过。

### Changed

- **`db.py` 增设「方言边界」一节（0.2.22）**：为「万一将来改判为服务端多人部署」预留切换缝，SQLite 特有构造全部收拢并注明——`PRAGMA` → `_PRAGMAS`；逗号串包含匹配 `(',' || col || ',') LIKE ?` → 新增 `db.csv_like()`（`pool_members` 已改用，组合值语义由既有测试覆盖）；`?` 占位符全库统一（psycopg/PyMySQL 为 `%s`，改 wrapper 即可）；`INTEGER PRIMARY KEY AUTOINCREMENT` 仅存于迁移 8 的 `job_logs.id`（历史不可变，保留），**新表不再使用**（其余表均为 UUID 文本主键，本身与方言无关）。换库 = 改这一节 + 数据搬迁，而非全库搜索替换。
- **扩展弹窗改版（0.2.22，用户定稿三点）**：① **「站点授权」区块整块从 UI 下线**——manifest 已声明 `host_permissions: ["<all_urls>"]`，装载即获全站权限，逐站点授权列表既无操作价值、又误导用户以为仍需手动授权；**只删展示层**，授权/停用名单核心逻辑（`site-auth.ts`、`par.grantChanged` 消息、`ql:blockedHosts`、`isEnforceable`）一律保留，随之删除的仅是 `renderGrantList()`（含其生成的「授权」按钮）与 `.grant*` 样式。② **「导出诊断」移入品牌头右上角**（柔和描边胶囊，保留文字标签而非纯图标，可读屏识别，`title` 说明用途）。③ **版本号从右上角移入页脚右下角**。弹窗高度 303px → 174px。
  - 页脚排版为实测结论：10.5px 字号 + 8px 间距时「提示 + 版本」总宽 305px > 300px 内容宽 → 提示尾字（「盘」「换」）被挤到第二行；改 10px + 6px 后总宽 290px 单行放下，**文案一字未改**。另将品牌头水平内边距 2px → 0，使彩环左缘与「导出诊断」右缘和统计卡两端对齐。
- **撤销上游 Page Monitor（0.2.22，用户定稿）**：该功能监听页面（MAIN 壳嗅探平台名称型 API → 桥上行 `pageNames` → 按 URL 分类解析主体名）并把结果**作用于页签**（合成标题 `账号别名 · 主体名·类型`，未解析时先占位 `类型 · guid前8位`），副产物是「最近配置页 MRU」与其 Alt+W 浮层轮盘。整块下线：
  - **删除**：`background/core/page-monitor.ts`、`content/pages-overlay.ts`、设计文档 `docs/FEASIBILITY-RECENT-PAGES.md`；`build.mjs` 的 `content/pages-overlay` 入口；manifest 的 `quick-pages` 命令（Alt+W）；桥上行 `pageNames` 载荷类型及其在 `parallel-session` 的消费分支；`shield-main` 的名称嗅探（`NAME_API_RE` / `extractNamePairs` / `reportPageNames` 及 fetch/XHR 两处钩子）；`messages.ts` 的 `pages.recent` / `pages.jump` 与 `RecentPageEntry`；`constants.ts` 的 `recentPages` 键与 `RECENT_PAGES_MAX`；`service-worker` 的对应消息分支、`quick-pages` 命令分支与 `togglePagesOverlay`。
  - **保留**：页签标题仍由既有 title 管线（`tabs/tab-title.ts` + `content/title-hook.ts`）权威写入**页签名**——撤销的是「用页面信息改写标题」，不是「标题显示页签名」；账号轮盘（Alt+Q）与浏览器池的监听录制（`services/browser_pool.monitor_start/stop` + `runtime/monitor/`，另一套东西）均未受影响。
  - 隔离 profile 启动冒烟 7/7：SW 可启动且未崩、`commands.getAll()` 仅剩 `quick-wheel`、storage 无 `ql:recentPages`、session 无 `ql:pageNames`、桌面数据面 `akso:acctMap` 仍同步成功。
  - ⚠ 上游同步注意：该功能来自上游 v3.11/v3.13，下次同步会把它带回来（manifest 命令 / 桥上行 op / 内容脚本入口 / SW 注册 / 构建入口），需按本清单再次摘除。
- **桌面轮盘扇区名改为「页签名优先」（0.2.22，用户定稿）**：`wheel-picker.html` 此前用 `a.username`，导致设了页签名的账号在轮盘上仍显示账号名；改为 `(a.tab_name || '').trim() || a.username`，与扩展端 `ui/wheel/wheel-core.ts` 的口径完全一致。另修一处连带缺陷：轮盘防重绘指纹只含 `id`，改页签名不触发重建（标签会一直停在旧名），现将显示名并入指纹。真机数据验证 5/5：`T0901`（页签名 TTTTT）显示 TTTTT，未设页签名的 `dev-admin`/`dev-user` 回落账号名。

### Fixed

- **弹窗/徽标/管理页版本号不随项目升版（0.2.22）**：`shared/constants.ts` 曾硬编码上游版本常量（`EXT_VERSION = '3.13.2'`），而 `tools/bump.py` 的五写只同步 manifest.json / package.json 等、**从不写这个 TS 常量** → 上游同步后版本号永久停旧（实测：项目已 0.2.21，弹窗仍显示 v3.13.2、扩展图标徽标显示 v3.13）。改为 `extVersion()` 读取 `chrome.runtime.getManifest().version`——**唯一真源 = manifest.json**，随后续 `bump.py` 五写自动跟随；弹窗、徽标、管理页、诊断包四处版本号一并同步。用函数而非模块级常量，避免内容脚本 import 本模块时的求值风险（`try/catch` 兜底 `'0.0.0'`）。
  - ⚠ 上游文档 `extensions/quick-login/README.md`、`extensions/quick-login/CHANGELOG.md`、`extensions/quick-login/docs/CODEBASE_OVERVIEW.md` 仍写「`EXT_VERSION` 三处必须一致」，已过时（上游文件，待下次同步时一并校正）。
- **带端口站点误判：0.2.21 那次修正只做了一半（0.2.22）**：0.2.21 给 `hostRelated` 加了「两端都带端口时必须端口一致」的守卫，但调用方仍用 `URL.hostname` 取 host——它**永远不含端口**，守卫 `up && bp` 里的 `up` 恒为空 → 「同主机不同端口是不同站点」从未真正生效。统一改走新增的 `urlHostOf()`（`new URL(url).host`；URL API 已把 http:80/https:443 规范化掉，不会造成假不匹配）：
  - `authHeaderForUrl`（下载归属 ②③ 层）：同主机其它端口的账号会互相串号（内网 `host:8080` / `host:18996`、桌面自身 `127.0.0.1:18765` 即典型）；
  - `adopt-candidate`（继承页签收编）：落到同主机别的端口会被当成"本站"而收编（身份头经 DNR 端口无关地注入）；
  - `onNavigation`：「是否本站」继续按无端口比（与 DNR `requestDomains` 同口径），但停用名单与健康缓存改用**带端口** host 查——名单条目来自 origin 推导（`192.0.2.10:8080`）、查询却用无端口 `192.0.2.10` → 永远查不到，**带端口站点的停用此前静默失效**。
  - **口径分工（勿混）**：身份平面（归属/停用名单/收编）端口参与比较；规则覆盖平面（DNR `requestDomains`）与 Cookie 作用域（RFC 6265）端口天然不参与，那两处按无端口比较是**正确**的，本次未改。
- **停用名单命中改双形匹配（0.2.22）**：新增 `blockedHit()`——带端口精确命中，或条目不带端口时覆盖该 host 全部端口（历史数据/仅填 host 的配置兼容）；带端口条目不跨端口误伤，与站点身份口径一致。`isEnforceable` 与 `syncAccountRules` 同步改用它。
- **`parentDomainOf` 入参先剥端口（0.2.22）**：带端口内网 host（`192.0.2.10:8080`）会让"全数字段=IP 字面量"判定失配，进而拼出 `0.105:8080` 这类无意义父域。当前调用方均已预剥端口，此为幂等加固（防后续踩坑）。

### Changed

- **扩展改为声明式全站权限，按站点授权整套下线（0.2.21，用户定稿）**：`manifest.host_permissions` 从"仅 127.0.0.1:18765 + `optional_host_permissions: ["*://*/*"]`（逐站点申请）"改为 **`["<all_urls>"]`**。
  - **为什么**：逐站点授权意味着每加一个新站点都要在浏览器弹窗点一次「允许」，未授权期间 DNR 改头规则**能装上但不生效**（实测：`permissions.contains` 为 false 的源上 `modifyHeaders` 静默失效）→ 多账号隔离与下载补身份全部哑火，且没有显式报错。这是用户实测"下载 401 却看不出原因"的根源。改声明式后**装载即获得全站权限、永不弹窗**（实测：`permissions.getAll().origins = ["<all_urls>"]`，且任意站点上 DNR `modifyHeaders` 立即生效）。代价是一次性授予全站访问权限、权限升级时可能需要在 `chrome://extensions` 重新加载/启用一次扩展。
  - **随之移除的组件**：内容脚本 `content/site-grant.ts`（页面授权提示条）、授权页 `ui/grant/`、消息 `site.grant.state` / `site.grant.request`、`site.grant.open` 桌面指令、桌面站点行授权徽标与「授权」按钮、账号卡「未授权」徽标、`site.grant.*` 相关样式与提示位；`grantStateForHost` / `requestHostGrant` / `probeHostAccess` 一并删除。
  - `isEnforceable` 语义随之简化：全站权限下"浏览器授权"不再是变量，唯一能关停某站点网络平面的只剩**用户手动停用名单**（`ql:blockedHosts`）；20s TTL 缓存保留（名单变更即时生效）。
- **规则安装不再被授权判定门控（0.2.21）**：`syncAccountRules` 与 `parallelSession.restore` 无条件安装绑定规则（DNR host access 自身就是生效门控，未授权源上规则静默不生效、无害）。此前"授权判定误判为 false"会让规则**永久不装**且 UI 无任何提示——用户实测下载 401 的直接根因。唯一保留的硬门控是手动停用名单。
- **401/403 自愈（0.2.21）**：绑定页签被服务端拒绝时（节流 10s/页签）强制刷新网络平面健康缓存并重装该页签规则——"规则没装上/装的是旧 token"这类静默失效无需重启扩展即可自愈。

### Fixed

- **下载失败"无法单账号归属"（0.2.21，用户实测仍未修复）**：`authHeaderForUrl` 旧实现要求"全库中该 host 只有 1 个账号"才敢重试，而实测站点是**一站两账号**（示例租户：dev-user / T0901）→ 永远返回 null → 下载 401 后从不重试（诊断包原话：`下载失败（无法单账号归属，不自动重试）`）。改为分层归属：①同 URL 被 webRequest 观察到的绑定页签账号（最精确）→ ②该 host 上**唯一有活跃绑定页签**的账号（正在浏览该站点的账号）→ ③全库唯一账号（原兜底）；都不成立才不注入。实测 `chrome.downloads.download({headers})` 不需要 host 权限即可带出 Authorization（Chromium 早已移除该校验），故该重试路径不受授权状态影响。
- **带端口站点被误判为"漫游到非授权域"而解绑（0.2.21）**：`onNavigation` 用 `URL.hostname`（永不含端口）与 `binding.host` 直接比较，站点自身 host 带端口（如 `192.0.2.10:8080`）时永远不相等 → 一打开就被解绑（规则不装、身份不注入）。比较前先去端口。
- **同主机不同端口被当成同一站点（0.2.21）**：`hostRelated` 仅比较域名，导致桌面自身页面 `127.0.0.1:18765` 会被当作内网站点 `127.0.0.1:18996`（下载归属、Cookie 归属、站点判定的误判来源）。现两端都带端口时要求端口一致。
- **诊断日志被桌面同步轮询淹没（0.2.21）**：桌面同步端点（`127.0.0.1:18765`）的响应此前被归属给"唯一绑定账号"，每 2s 刷两条 `captureResponseCookies：无页签响应…`，把 120 条环形诊断缓冲冲掉、真问题被挤出。此类 URL 不再计入。

### Changed

- **账号中心 UI 复刻一期（0.2.8）**：按上游 quick-login 管理页视觉与功能基线重写（`accounts.html`/`accounts.js` + 移植 `ql-theme.css`/`ql-parallel.css`）。落地：顶栏品牌头 + 实时统计（账号/在线/盒子）+ 版本 chip；盒子 chips 悬停操作（✎ 重命名 / ⏸▶ 禁用启用 / ✕ 两步删除——连同账号删除或并入默认盒）；**批量管理模式**（勾选 + 已选计数 + 批量移盒/删除）；**移入盒子弹窗**（单选带计数 + 新盒名自动创建，替代裸 prompt）；**四态实时徽标**（在线×N/待登录/离线/未授权·已暂停——新增扩展执行面状态回传通道 `/extension/state`，扩展每 ~6s 上报绑定页签数/token/授权暂停）；**指纹防闪烁渲染**（数据未变不重建 DOM）；轮盘 0 键=第 10 账号（页内覆盖层 + Electron 轮盘窗）；禁用盒轮盘跳过（settings 表 `disabled_boxes`）；平台环境管理收进对话框、批量添加收进折叠面板——消除提示冗杂。DOM 验证 11/11（`tools/verify_accounts_ui.py`）。

### Fixed

- **Fernet 双 bug（0.2.7，E2E 最后一公里）**：①扩展端 fernetDecrypt 键位写反——Fernet 规范 sign-key=key[:16]/enc-key=key[16:32]，原实现互换导致 HMAC 全失败、账号被静默跳过；②WebCrypto AES-CBC 已自动去 PKCS7 填充，原代码再按尾字节手工剥离，把口令尾字符当填充长度剥掉。修复后**隔离 Chrome 全链路真机验收通过（E2E_PASS）**：快照同步 2/2 → par.open 开登录页 → 自动填表提交 → 进入平台工作台。新增 `tools/acceptance_extension_e2e.py` 可复用验收脚本。

### Changed

- **扩展基线上游同步（0.2.6）**：`extensions/quick-login` 整体前移 **v3.11.0 → v3.13.2**（36 文件），重放全部私有改造（manifest 18765/alarms/Ctrl+Shift+Q、sync.ts+account-wheel.ts 保留、service-worker 挂载+抽取、parallel.html 隐藏数据区块、wheel-overlay interval 修复）。带入上游 8 个版本的能力：登录态生命周期跟随页签（3.12.0 免密复制语义/最后页签关闭终结登录态）、绑定时 Cookie 袋权威同步（3.12.1）、自动登录逐事件取证黑匣子+管理页导出诊断（3.12.2）、AuthCode 入时效集（3.12.3）、亲子继承候选期零种子（3.13.0）、AUTH 规则补 main_frame（3.13.1）与全资源类型（3.13.2）。typecheck 零错误验证 sync.ts 与新 API 兼容。

### Added

- **扩展连通性专项（0.2.5）**：桌面↔quick-login 扩展链路断点修复——快照 host 字段错位（envBaseUrl）、指令 seq 跨重启持久化（settings 表单调递增）、host 保留端口 + scheme 随快照下发、扩展 chrome.alarms 30s 保活复活、wheel.toggle 改直调（SW 自消息死链）、毒指令逐条隔离（不再卡死队列）、快照空载删除护栏、tabName 缺失护栏、凭据解密失败留痕、wheel-overlay 双 interval 泄漏修复；扩展 quick-wheel 热键让位（Alt+Q→Ctrl+Shift+Q，避免与 Electron 全局热键抢占）；账号中心页内轮盘选人补 launch-chrome；新增 `tools/verify_extension_sync.mjs` 离线模拟扩展验收脚本。
- **浏览器分配政策落地 + Monitor 监听接入托管会话（0.2.2）**：政策定稿——快捷登录/打开 = 用户 Chrome（扩展指令 par.open + launch-chrome）；监听/自动化 = 应用内置 Chromium（browser_pool，cdp = Electron 壳）。`routes_monitor`（/api/monitor/start|stop|status）；browser_pool 新增 monitor_start/stop（自动开户 → MonitorSession 挂 page → 产物落 runtime/monitor/）；卡片 UI（快捷登录主按钮 + 监听会话/开始/停止监听 + 监听中 chip）；routes_browser 补 `/navigate/{account_id}`。真机验收：248 请求捕获（dropped 238 / trimmed 2 / full 8），三级降噪正确，monitor-log 落盘。
- **桌面壳迁移 Electron（pywebview 退役）+ browser_pool CDP 复用模式**：`desktop/main.js`——Python sidecar 生命周期（打包态 AksoServer.exe --server / 开发态 venv uvicorn）、主窗、托盘、全局热键 Alt+Q、electron-updater 自动更新（latest.yml）；会话控制服务 :18767（按 windowId 开户，每账号 `persist:` 独立持久分区 / 聚焦 / 关闭）；browser_pool 经 `connect_over_cdp(:18766)` 复用内置 Chromium（`_CdpState` 单实例持有者）。
- **quick-login 混合架构（v0.1.2）**：扩展执行面 + 桌面数据面/触发面。`routes_extension` 同步蓝图（快照：账号/盒子/站点，凭据以「Fernet 密文 + 密钥」经 127.0.0.1 回环下发；指令队列 wheel.toggle/par.open，内存态 + ack）+ 扩展 `sync.ts`（2s 轮询、snapshotId 内容哈希幂等、WebCrypto Fernet 解密直连 parallelStore、桌面不可达离线回退）。扩展源码入库 `extensions/quick-login/`。
- **轮盘 v2**：径向扇形无边框半透明独立窗（Electron 承载）+ 扩展 sync v2 直连 store + Chrome 拉起。
- **桌面壳重构与实机调通**：`shell/shell.py` 改为「uvicorn 子进程 + webview 主线程」架构（服务与窗口生命周期解耦，关闭窗口即退出并回收服务）；pythonw 的 stdout/stderr=None 兜底；pywebview 以 `desktop` extra 入库（`uv sync --extra desktop`）；新增 `tools/start-desktop.vbs` 双击启动器。实机验证：原生窗口可见、服务就绪、首屏体检线程正常。
- **quick-login 全量内化收口**：托管浏览器会话持久化（storage_state——登录成功/正常关闭落盘，重开免密直达、跨进程重启有效；Cookie 袋/DNR 回放的原生等价物）；会话自愈（直达首页被踢回登录页 → 自动重跑节奏门控）；`POST /api/browser/forget/{id}` 登出语义 + `GET /api/browser/saved/{id}`；状态墙持久会话徽章；`tests/test_browser_state.py`（5 项）。真机验证：重开 restored=True 且引擎 phase=idle（完全未走登录页）。迁移台账「不迁清单」重新定性：各项均为"被 Playwright 原生机制等价替代"，原仓库不再是功能归宿。
- **阶段 3 全量完成：akso-cc / akso-auto 原生化（运行时零依赖原项目）**
  - 读路径 `egmp/insight/`：crawler（对象发现链）/assemble+report（盘点）/lifecycle+flowgraph+relations（L3/L2）/annotate/render/drawio/understand/spider（五步织网，step5 用 networkx 图分析）。
  - 写路径 `egmp/writers/`：blueprint（pydantic 两层校验+规范化+审阅三件套，替代 ajv）/idempotency/objects/fields/picklists/lifecycle/workflows（a-i 管道+两次提交连线）/layouts（全量替换语义）/menus/endpoints（自原仓库只读提取的实证常量表）。
  - 编排 `egmp/orchestrate.py`（runFullWorkflow/拓扑排序/checkpoint 落盘）+ `complexity.py`（规则移植）+ `generate.py`（DeepSeek 蓝图生成融合：规范化+校验回炉）。
  - Monitor `egmp/monitor/`：Playwright 录制三级降噪/查询层与参数推断/segment 解读+reproduce-plan（API_MAP 对位 Python writers；DANGEROUS 永不回放）。
  - `routes_insight` / `routes_factory` 切换原生调用；`routes_agent` 工具同步原生；`adapters/*.json` 降级为只读参考存档；Node 不再是运行时依赖。
  - 测试：`tests/test_native.py`（12 项，FakeEgmpClient 内存平台离线验收校验/编排幂等/盘点/理解/蜘蛛/Monitor）+ `tests/fakes.py`；真机验收：原生 login / understand（training_hjy__c，1.4MB 理解模型）/ spider 均在真实平台跑通。
- 架构分析文档 `docs/架构分析.md`：总体架构图、四条设计底线、模块分布地图（11 组 40+ 模块的职责/血统/依赖）、数据流图、DB 迁移版本账、三层测试策略与逐模块回归集、排障速查表、维护红线、新模块 checklist、演进触发器。
- 双人协作基建：`CONTRIBUTING.md`（开发协作规范）、`.editorconfig`、`tools/setup.ps1`（环境自检脚本）；uv 默认走清华镜像（`[[tool.uv.index]]`，随仓库分发）。
- 质量门禁：ruff 规则集（E4/E7/E9/F/I/B）入 `pyproject.toml`，全仓通过；修复 19 处（未用导入/未排序 import/zip strict/异常链 from exc 等）。
- 模块契约 §1 补强 pydantic 约定：请求体强制 BaseModel，新代码响应强制 response_model。

### Changed

- **构建分发链 v3（Electron）**：PyInstaller 服务端 sidecar（`dist/AksoServer` onedir，含 chromium 本体）→ electron-builder NSIS（`desktop/dist/AksoWorkbench-<ver>-setup.exe` + `latest.yml`，GitHub Releases 承载 electron-updater 增量更新）。Inno Setup（`tools/installer.iss`、`tools/ChineseSimplified.isl`）退役，2026-09-10 删除。
- **bump.py 三写机制**：版本写入 pyproject + `workbench/__init__.py` + `desktop/package.json`（electron-builder 以 package.json 版本命名安装包与 latest.yml；此前漏写导致版本错位，需手工对齐提交补救）。
- `pyproject.toml`：uvicorn 对齐为 `[standard]` 变体（与实测环境一致）；依赖变更一律走 `uv lock && uv sync`。

### Fixed

- **sync-Playwright 事件回调内禁止连接调用**（response.text()/page.evaluate 重入 = worker 死锁）——事件只入队，worker 作业间隙统一 drain（响应体补捞 + 动作缓冲出栈）。
- **cdp 模式三修**：pw 单线程单实例持有者 / 先开户后连接（Electron33 挂接怪癖）/ 平台异步鉴权复核；logging_in 会话复用（禁二次开户）；close_all 连接池清理。
- 轮盘单例 toggle + js_api 关闭/拖动（从误 stash 中恢复的关键修复）；启动会话 openAccount ReferenceError；账号编辑对话框与批量添加链路。

- 立项：四项目融合重建（akso-cc / mogul_simulator / akso-auto / quick-login），原项目冻结不动。
- 阶段 0：uv 工程定义（`pyproject.toml`）、模块契约（`docs/模块契约.md`）、迁移台账（`docs/迁移台账.md`）、只读适配器声明（`adapters/akso-auto.json`、`adapters/akso-cc.json`）。
- 阶段 1A：fork mogul_simulator/mogul → `workbench/`（包名、数据目录、端口可并存；数据库兼容接管 mogul.db）。
- 阶段 1B：Node 子进程统一封装 `services/proc.py`；模块注册表 `services/modules.py` + `adapters`；模块健康体检路由 `api/routes_modules.py`。
- 阶段 1B：平台洞察路由 `api/routes_insight.py`（封装 akso-cc login/inventory/understand/spider，产物读取回传）。
- 阶段 1B：配置工厂路由 `api/routes_factory.py`（封装 akso-auto create/编排/monitor，蓝图暂存 + 断点状态展示）。
- 阶段 1B：前端页 `factory.html` / `insight.html` + 对应 js/css（复用 mogul 设计系统，四模块 Tab 导航）。
- 阶段 1B：pywebview 桌面壳 `shell/shell.py`（起 uvicorn + 开窗口 + 依赖体检首屏）。
- 阶段 2A：统一账号库（DB 迁移 7：platform_env / account 表，Fernet 凭据加密；卡片墙 UI；三项目 env 一键导入脚本）。
- 阶段 2B：托管浏览器 `services/browser_pool.py`（Playwright 账号↔context 池）；自动登录引擎 `services/autologin.py`（quick-login 节奏门控全量迁移：齐备门槛/提交前回读/MutationObserver 即时填充/用户点击接管/失败让位 + srcdoc iframe 探测 + token 捕获）；路由 `api/routes_browser.py`；前端页 `browser.html`；机器验收 `tests/test_autologin.py`（9 项断言）。
- 阶段 3：egmp 平台客户端 Python 化内核（client/auth/cache/checkpoint + writers/insight/monitor 骨架）与 Agent 工具层（`routes_agent.py`：自然语言→登录/读配置/写配置/查知识）。
