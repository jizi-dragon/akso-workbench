# ADR-0005：四个原项目能力全部原生化，运行时零依赖原仓库与 Node

- **状态**：已采纳（阶段 3 完成；`adapters/*.json` 降级为只读参考存档）
- **相关**：[ADR-0003](0003-electron-desktop-shell.md)、[SCHEMA.md](../SCHEMA.md)、
  [CONFIG.md](../CONFIG.md) §2、[AGENT.md](../../AGENT.md) §6

## 背景

本项目的起点是**四个既有项目的优点融合**（用户定稿的方向）：

| 原项目 | 能力 | 原始形态 |
|---|---|---|
| `akso-cc` | 平台配置洞察（登录 / 盘点 / 理解 / 蜘蛛爬取 / 报告） | Node CLI（`dist/main.js` + 子命令） |
| `mogul_simulator` | 知识工作台（对话 / 设置 / 检索） | Python（本项目 fork 基线） |
| `akso-auto` | 配置自动化（蓝图校验 / 创建 / 编排 / Monitor） | Node CLI（`scripts/main.js` + 子命令） |
| `quick-login` | 多账号统一库 + 自动登录节奏门控 | Chrome MV3 扩展（TS） |

融合期（阶段 1–2）的形态是**子进程聚合**：`workbench/services/proc.py` 封装 Node 子进程，
Workbench 侧传 `--url/--user/--pass` 内联凭据调两个原仓库的 CLI。这带来三个硬问题：

1. **终端用户安装包必须带 Node**（或用 PyInstaller 夹带一个 Node 运行时）。
2. **凭据要经命令行参数传递**（出现在进程列表里），与「凭据不落明文」的纪律冲突。
3. **产物与错误口径全在对方手里**：一次 CLI 参数变更就会静默改变行为，而对方仓库不在本仓库的
   测试范围内（`adapters/*.json` 只是路径声明，没有任何拉齐机制）。

同时四个原项目被要求**冻结不动**：新项目对它们只做只读引用与知识迁移，绝不修改原文件。

## 决策

**把四个项目的能力全部内化到本仓库（Python + 扩展），运行时不再调用任何原仓库、不再依赖 Node。**

落地形态：

| 原能力 | 内化位置 | 载体 |
|---|---|---|
| akso-cc 读路径 | `workbench/services/egmp/insight/`（11 模块） | Python + httpx |
| akso-auto 写路径 | `workbench/services/egmp/writers/`（9 模块） | Python + pydantic |
| akso-auto 编排/复杂度/蓝图生成 | `egmp/orchestrate.py` / `complexity.py` / `generate.py` | Python（DeepSeek 融合） |
| akso-auto Monitor | `workbench/services/egmp/monitor/` | Playwright（载体从 Puppeteer 换成 Playwright-Python） |
| quick-login 账号库 + 节奏门控 | `services/accounts.py` + `services/autologin.py` + 扩展 | Python + MAIN-world JS |
| 四项目共用的 HTTP 内核 | `egmp/client.py`（信封 `code==0` / 分页 / 55min token 缓存） | httpx |

`adapters/akso-cc.json` / `adapters/akso-auto.json` 保留但**降级为只读参考存档**：
`mode: "native-python-internalized"`、`frozen: true`、`deprecation` 字段写明原生化事实。
模块体检（`/api/modules`）对它们只做「Python 依赖 + 原仓库是否可达（信息项，缺失不影响功能）」。

**内化时的纪律：实证资产不得丢失。** 原仓库里用大量探测得出的常量与口径被逐一搬进代码并注明来源，
例如：eGMP 登录端点与响应信封形状、分页参数（`pageIndex` 从 1、`pageSize` 默认 1000、`data.hasNext` 续页、
`datas`/`items` 兼容）、Token TTL 55 分钟、进入动作结构化条件**无公开读接口**
（`UNRESOLVED_ACTION_READ_PROBES`，6 轮 48+ 路径探测全部 404/405/500）、写路径端点常量
（`egmp/writers/endpoints.py`，自原仓库只读提取）、自动登录五重门的全部时长参数。

## 取舍

### 换来什么

- **终端用户零 Node 依赖**：安装包 = Electron 运行时 + PyInstaller sidecar + chromium + 扩展。
- **凭据不再出现在命令行**：凭证从统一账号库内联注入（Fernet 解密仅在内存），见
  [ADR-0007](0007-credentials-never-plaintext.md)。
- **口径可控可测**：`tests/test_native.py`（12 项）用 `FakeEgmpClient` 在内存平台离线验收全链路，
  不依赖任何外部仓库；改行为必须同步改测试。
- **单一语言栈**：服务端全部 Python，异常/日志/超时口径统一。

### 代价（已接受）

| 代价 | 表现 |
|---|---|
| 一次性重写工作量巨大 | `services/egmp/` 约 3.5k 行，含 11 个 insight 模块与 9 个 writers 模块 |
| 与原项目存在**行为漂移风险** | 原仓库继续演进时本项目不会自动跟上。缓解：`adapters/*.json` 作为对照存档 + 本 ADR 记录口径来源 |
| 遗留对照代码未清理 | `services/proc.py`（Node 子进程封装）与 `services/modules.py` 的部分描述仍是遗留口径（如 `proc.py` 的文档字符串指向已删除的 `docs/模块契约.md`）——见 [AGENT.md](../../AGENT.md) §4 |
| `tests/test_proc.py`（7 项）仍在测 | 它测的是遗留封装本身（Node 探测/超时/流式日志），**不代表运行时链路**——不要误认为「项目仍依赖 Node」 |

### 明确排除的方案

- **把原仓库作为 git submodule 引入**：仍需 Node 运行时，且违反「原仓库只读」纪律（submodule 会诱使改原文件）。
- **打包一个便携 Node 进安装包**：包体再增，且要维护 Node 版本与两个仓库的依赖树一致性。
- **保留子进程但只传文件路径不传凭据**：仍需 Node，且引入临时凭据文件的落盘面。

## 后果

- **运行时零依赖**成为维护红线：任何「顺手调一下 node 脚本」的改动都不接受。
  新增能力一律落在 `services/egmp/` 或扩展里。
- **原仓库只读**是硬纪律：`adapters/*.json` 指向的目录**任何情况下不修改**
  （`services/accounts.py` 的「原项目 env 导入」也只读扫描）。
- `docs/模块契约.md`、`docs/迁移台账.md` 等融合期文档已删除；代码里仍有指向它们的引用，
  属于待清理的文档债（见 [AGENT.md](../../AGENT.md) §4）。
