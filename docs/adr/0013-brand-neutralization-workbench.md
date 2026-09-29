# ADR-0013：品牌中立化——AksoWorkbench → Workbench

- **状态**：已接受（2026-09-16）
- **决策人**：用户（原话：「更名，剥离 akso 的信息，按照 workbench 进行后续发展（逐渐将项目转变成可复用的工作台，核心差距就在于插件/tools 功能）」）
- **影响面**：58 个文件 / 200 处字符串 + 6 处**协议与持久化标识**（会静默坏数据，见 §3）
- **不做**：不改仓库名（用户决定「暂不改」）、不改上游项目名与平台适配层（见 D-4）

---

## 1. 为什么要中立化

`akso` 是**首个适配平台的厂商名**，不是这个工作台的名字。它此前长在了四类地方：

| 类别 | 例子 | 为什么是问题 |
|---|---|---|
| 产品标识 | `AksoWorkbench` / `akso-workbench` / `com.akso.workbench` / `AksoServer.exe` | 换个平台实施就要改一遍代码，改名成本随代码量线性上涨 |
| 部署契约 | `%APPDATA%\AksoWorkbench`、`~/.akso-workbench` | 用户数据目录绑死厂商名，换平台 = 换数据目录 = 看起来"账号全没了" |
| 环境变量 | `AKSO_PROXY` / `AKSO_UPDATE_OVERRIDE` / `AKSO_NO_PROXY` | 部署脚本、快捷方式、排障手册全被厂商名污染 |
| 协议/持久化标识 | 备份 `format`、快照 `format`、扩展存储键 | **最危险的一类**：改错就是静默丢账号（见 §3） |

判断标准（本次实际使用的分界线）：**"这是我们的名字"还是"这是别人的名字"**。
`akso-cc` / `akso-auto` 是**来源项目**的名字（ADR-0005 的引用真源），`services/egmp` 是**平台适配层**，
它们描述的是事实，不是我们的品牌 —— 保留（D-4）。

## 2. 决策

### D-1 自有品牌标识一次性改净

| 位置 | 旧 | 新 |
|---|---|---|
| Python 包 | `akso-workbench` | `workbench` |
| 桌面包 / 产品名 | `akso-workbench-desktop` / `AksoWorkbench` | `workbench-desktop` / `Workbench` |
| appId | `com.akso.workbench` | `com.workbench.app` |
| 安装包产物 | `AksoWorkbench-<版本>-setup.exe` | `Workbench-<版本>-setup.exe` |
| 服务端 sidecar 产物 | `dist/AksoServer/`、`AksoServer.exe` | `dist/WorkbenchServer/`、`WorkbenchServer.exe` |
| 数据目录 | `%APPDATA%\AksoWorkbench`、`~/.akso-workbench` | `%APPDATA%\Workbench`、`~/.workbench` |
| 环境变量 | `AKSO_PROXY` / `AKSO_NO_PROXY` / `AKSO_HTTPS_PROXY` / `AKSO_UPDATE_OVERRIDE` | `WORKBENCH_*` 同名 |
| 洞察默认工作区 | `<我的文档>/AksoWorkbench/洞察` | `<我的文档>/Workbench/洞察` |

### D-2 数据目录：改名 + **一次性自动接管**（不许出现"账号全没了"）

`workbench/config.py::legacy_db_candidates()` 新增**最高优先级**候选
`%APPDATA%\AksoWorkbench\workbench.db`，沿用既有 bootstrap 机制：

- **只在目标库不存在时**接管（`bootstrap_database` 的既有语义）——**绝不覆盖**现有数据；
- 连同 `-wal` / `-shm` 一起复制（WAL 里有未落盘的事务）；
- **复制而非移动**，旧目录原样保留（可回滚、可对比）；
- 已实测：真机 `%APPDATA%\AksoWorkbench` 的库被接管后，`/extension/snapshot` 读出 **5 个账号**全部通过过滤。

> ⚠️ **迁移后请只使用新版本**：复制语义意味着旧版本若继续写旧库，两份数据会分叉；
> 下次启动新版本时**不会**再次接管（目标库已存在）。

### D-3 兼容读取（旧名继续认，写出端只发新名）

改名不能把用户手上的东西变成废纸。以下**读**路径保留旧名兼容：

| 对象 | 兼容做法 | 不兼容的后果 |
|---|---|---|
| 环境变量 | `WORKBENCH_PROXY` 缺失时回落 `AKSO_PROXY`（`WORKBENCH_UPDATE_OVERRIDE` / `WORKBENCH_NO_PROXY` / `WORKBENCH_HTTPS_PROXY` 同理） | 用户手写的启动脚本、快捷方式静默失效 |
| 备份文件 | `import_backup` 接受 `akso-workbench-backup` 与 `workbench-backup` 两种 `format` | 已有备份变成"不是有效的备份文件" |
| 桌面→扩展快照 | `sync.ts` 同时接受 `akso-workbench-snapshot` 与 `workbench-snapshot` | 扩展要手动「重新加载」才升级，**旧扩展会静默丢弃全部账号**（PITFALLS #14 的形态） |
| 进程占用判定 | `ports.js::ownImageNames` 同时认 `workbench.exe` 与 `aksoworkbench.exe` | 同机还跑着旧版实例时，把它误判成"外人"，报错信息指向错误的占用者 |
| 洞察工作区 | `<我的文档>/AksoWorkbench/洞察` 已存在且新目录不存在时**继续沿用旧目录** | 用户已落盘的产物"消失"（表现为"我的产物去哪了"） |

### D-4 刻意不改（写下来免得下次有人"顺手统一"）

- **上游项目名**：`akso-cc` / `akso-auto` / `akso-mogul`（仓库名）、`adapters/akso-*.json` 文件名、
  `AKSO_CC_REPO` / `AKSO_AUTO_REPO`（由 `repoPathEnv` 派生，见 [CONFIG](../CONFIG.md) §2.3）——
  它们是"这份存档对应哪个原仓库"的引用真源，改了会让 [ADR-0005](0005-native-internalization-of-four-projects.md) 的语义失真。
- **平台适配层**：`workbench/services/egmp/` 目录名与 `eGMP` 措辞——它描述被适配的平台，不是我们的品牌。
- **扩展内部存储键**：`akso:acctMap` / `akso:snapshotId` / `akso:cmdCursor` / `akso:desktopEndpoint` / `akso:sync` 告警名。
  它们对用户不可见，但**改名会让指令游标与快照缓存复位**——游标不单调正是 [PITFALLS](../PITFALLS.md) #15
  "指令通道整体哑火且无报错"的成因。**收益为零、风险为正，故不动。**
- **历史留痕**：`docs/adr/*`（本 ADR 之前）、`docs/archive/*`、扩展的 `docs/archive/*`、`CHANGELOG.md` 的历史条目
  （历史产物名是事实）、`PITFALLS.md` 既有条目——本仓约定「只增不改」。
- **仓库名**：仍为 `jizi-dragon/akso-mogul`（用户 2026-09-16 决定「暂不改」）。
  **这是一处刻意保留的不一致**：`desktop/package.json` 的 `publish.repo`、`README`、发布脚本继续指向它。

### D-5 身份握手必须四处同步改（改名最容易踩的坑）

`/api/health` 与 `/whoami` 答的 `app` 字段是**壳的存活判据**与**扩展的身份判据**，改一处不改另一处
= 壳认为自己的服务没起来（[PITFALLS](../PITFALLS.md) #20 的完整形态）。本次同步改动的四处：

| 位置 | 作用 |
|---|---|
| `workbench/api/__init__.py` | 服务端 `/api/health` 的 `app` 字段（真源） |
| `desktop/ports.js` | 壳控制服务 `/whoami` 的 `app` 字段 |
| `desktop/main.js` | 壳就绪探测核对 `data.app === 'Workbench'` |
| `extensions/.../background/core/desktop-endpoint.ts` | 扩展核对桌面端身份后才把凭据发给它 |

外加 `tests/test_ports_contract.py`、`tools/verify_port_broker.mjs`、`tools/verify_update_flow.py` 三处断言/夹具。

## 3. 后果

**正面**

- 换平台实施不再需要改产品名与数据目录；"适配新平台"从此只走插件与适配层（正是用户说的"核心差距在插件/tools"）。
- 工具清单与插件契约有了独立文档与机检（见 [TOOLS.md](../TOOLS.md)），不再出现"我不知道有 19 个工具"。
- 数据接管机制顺带把"更名 = 用户丢账号"这一类事故封死。

**负面 / 风险**

| 风险 | 说明 | 处置 |
|---|---|---|
| **旧版不会被覆盖安装** | NSIS 的安装目录与卸载项由 `productName` 决定，改名后是**并存的两套程序** | 升级时先卸载旧版；`README` 已写明 |
| **发布产物名变化** | `Workbench-<版本>-setup.exe`；旧客户端的 `latest.yml` 检查会断 | 自动更新**默认已关闭**（[CONFIG](../CONFIG.md) §2.2），影响面小 |
| **迁移后旧版本继续写 = 数据分叉** | 复制语义的固有代价 | 文档写明「迁移后只用新版本」；旧目录不删以便回滚 |
| 仓库名未改 | 代码里 `publish.repo` 与新品牌不一致 | 已在 D-4 记录；将来改仓库名时同步 `publish.repo` + remote + 本 ADR 补一条 |

## 4. 重新评估触发器

- 用户决定改仓库名（`akso-mogul` → 任意名）→ 同步 `publish.repo`、remote URL、`../HISTORY-REWRITE-NOTICE.md`（**该文件已于 2026-09-29 删除**）。
- 第二个平台适配层落地 → 那时应把 `services/egmp/` 提升为"平台适配目录 + 各平台子包"，
  本条 ADR 的 D-4 需要重新审视（平台名会从"唯一"变成"之一"）。
- 兼容读取（D-3）建议**保留一个大版本**后清理；清理时删掉旧名回落与旧 `format` 分支，并补一条 CHANGELOG。
