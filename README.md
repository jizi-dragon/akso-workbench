# Workbench

> 实施工程师的**单机工作台**：把对象分析、云端账号库与对话 Agent 合到一个桌面应用里。
> 四个来源项目（akso-cc / mogul_simulator / akso-auto / quick-login）的能力已**全部内化**进本仓库——
> 运行时零依赖原项目、零依赖 Node、零联网。
> 首个适配平台是阿克索 eGMP；**平台能力走插件与适配层，换平台不必改产品名与数据目录**（见下）。
>
> 仓库地址：`jizi-dragon/akso-workbench`（**2026-10-11 已按用户决策更名**，原为
> `jizi-dragon/akso-mogul`； D-4 的「暂不改」与 §7.1 的触发器据此已触发，
> 的「2026-10-19 后补」）。
>
> ⚠️ **2026-10-09：浏览器扩展已整体移出本仓库**（用户决策：桌面端与扩展不耦合，
> 本仓只需账号库管理页来承接云端数据环境）。原先随安装包分发的 MV3 扩展、
> `/extension/*` 通信面、桌面轮盘窗与「安装浏览器扩展…」引导**都已删除**。
> "在用户自己的 Chrome 里多账号并行"这项能力改由独立仓库
> [quick-login](https://github.com/jizi-dragon/quick-login) 承担。

## 它解决什么问题

| 你的日常动作 | 以前 | 现在 |
|---|---|---|
| 摸清一个平台上有什么对象、字段、状态、流转 | 记命令、跑 Node 脚本、翻产物目录 | 工作台里填对象编码 → **蜘蛛五步织网**：关系网络 + `spider-network.drawio` + 生命周期流转图 |
| 记住十几个测试账号的口令 | 明文表格 / 记事本 | **账号中心**：数据源是云端账号库（设备流登录、只读展示）；旧本地库可一次性「本地推到云端」，日常增删改在 Akso Cloud 控制台 |
| 让 Agent 替你跑这些活 | 每件事都得自己点 | **对话 Agent + 20 个内置工具**：工具由 `ToolHub` 单一定义，**风险操作有确认门**（单次令牌，无令牌不执行），模型还能用 `request_user_confirmation` **先问再做**，每次调用**进审计表** |

> **本仓最该被看见的不是功能清单，而是它的工程纪律**：能力以「工具插件」形式接入，
> `ToolHub` 是**唯一扩展面**（不是 router、不是模块清单）；工具定义**只有一处**，
> 对话的 function calling、HTTP 调用面、审计表三处都从它读。工具治理见
> docs/TOOLS.md（已于 2026-10-25 删除），MCP 见 docs/MCP.md（已于 2026-10-25 删除）。

## 四个来源项目的沉淀

| 来源 | 沉淀下来的能力 | 现在是什么 |
|---|---|---|
| **akso-cc** | 对象分析（原「平台洞察」）：登录验证 / 蜘蛛五步织网（networkx 图分析）<br>~~全量盘点~~ ~~对象三层理解~~（2026-09-14 下线，场景模糊、效果不佳） | Python 原生（`services/egmp/insight/`） |
| **mogul_simulator** | 对话工作台、会话历史、设置 | Python 原生（fork 基线） |
| **akso-auto** | ~~Monitor 录制解读（三级降噪 / 分段解读 / 复现计划）~~（**2026-10-19 随内置浏览器下线**）<br>~~配置创建~~（**2026-09-22 整体移除**） | 已不在本仓库 |
| **quick-login** | 统一账号库（盒子分组 / 备份导入导出）<br>~~自动登录节奏门控~~（**2026-10-19 随内置浏览器下线**） | 云端账号库（只读展示 + 本地一次性推送）<br>**浏览器扩展执行面已于 2026-10-09 移出本仓** |

> 四个原项目**冻结不动**：本仓库对它们只做只读引用（`adapters/*.json`）与知识迁移，绝不修改原文件。
> 详见 [docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md) §2 / §3。
>
> **改名记录（2026-09-22，用户下达）**：界面上的「平台洞察」更名为「**对象分析**」。
> **只改显示名**——模块 id（`insight`）、工具名（`insight_*`）、接口路径（`/api/insight`）、
> 落盘目录（`…/Workbench/洞察`）全部不变，所以更早的 `docs/INSIGHT-SPEC.md`（**该文件已于 2026-09-29 删除**）、
> 坑清单里出现旧名是**正常的**。

> ## ⚠️ 2026-09-22：「配置工厂」整体移除，平台知识留下
>
> 原 akso-auto 那条**写路径**（蓝图两层校验 / 幂等创建 / 8 步管道 / 拓扑编排 / 断点续跑）
> 连同它的插件、路由、前端页、验证工具与测试**已全部从本仓库删除**：
> 产品里不再有任何"会改平台数据"的能力，因此也**不再有 `Risk.DANGEROUS` 级工具**
> （曾唯一的一个是 `factory_create`）——确认门的 dangerous 分支、审批弹窗的高危样式
> 目前只剩测试在走，这是一个**已知的、刻意的**状态。
>
> **平台知识没有跟着丢**，它被迁进了两份不依赖任何产品代码的文档：
> `docs/PLATFORM-OPERATIONS-LEARNED.md`（**该文件已于 2026-09-29 删除**）（平台事实台账：
> 端点、真机线格式、样本量 `n`、平台约束）与
> `docs/FACTORY-PLATFORM-COGNITION.md`（**该文件已于 2026-09-29 删除**）（手册语义与配置分层认知），
> 原始真机取证报告（`factory-tuning/reports/`）**已于 2026-09-29 删除**——结论先并进上面两份台账，再删的原文。
> 采集与分析工具链（`factory-tuning/tools`、`analyze`、`data`）随之下线——**工具先删，报告后删**。

## 走向"可复用工作台"：能力都在插件与工具层

工作台的**核心资产是插件/tools 层**——Agent 的所有能力都经 `plugin_api.ToolHub` 注册，
业务能力（洞察 / 账号 / 检索）各自是一个插件。当前 **5 个插件 / 20 个内置工具**
（16 个 `read` + 2 个 `write`）；**MCP server 的工具在运行期动态进入同一个中枢**
（installed 层，见 docs/MCP.md（已于 2026-10-25 删除））。

| 想知道什么 | 去哪看 |
|---|---|
| **不受信代码（上传的 Skill）在哪跑、拦得住什么** | [docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md) §3.4.1（安全姿态与诚实边界）+ 工作台「沙箱」页（工作区/权限、策略、状态、执行审计） |

> 工具清单**不靠人记**：`docs/TOOLS.md（已于 2026-10-25 删除）` 的清单由 `tools/gen_tools_doc.py` 从运行中的工具中枢生成，
> `python tools/gen_tools_doc.py --check` 会对不上就报红——防止再出现"我不知道多了几个工具"。

## 安装（终端用户）

1. 下载并运行 `Workbench_<版本>_x64-setup.exe`（Tauri 2 NSIS 安装包，per-user，含 Python sidecar）。
   无需管理员权限、无需预装 Python / Node；Windows 10/11 需要 WebView2 Runtime（Win11 自带，Win10 一般已随 Edge 安装）。
2. 首次运行会自动完成数据初始化（`%APPDATA%\Workbench`）。
3. 打开「账号中心」：先登录**云端账号库**（设备流授权），查看云端账号/站点/盒子；
   未登录时账号中心整页遮罩。旧本地库可用「本地推到云端」一次性迁移。

> **关于浏览器扩展**：本仓库**不再内嵌、也不再随安装包分发浏览器扩展**（2026-10-09，用户决策：
> 桌面端与扩展不耦合，本仓只需账号库管理页来承接云端数据环境）。
> 需要"在用户自己的 Chrome 里多账号并行"时，请单独装载
> [quick-login](https://github.com/jizi-dragon/quick-login) 扩展。

> **Tauri（2026-10-19 起）**：Electron 壳已退役，`src-tauri/` 的 Tauri 2 + Python sidecar
> 是唯一桌面分发路径。已在本机完成 release 构建、NSIS 安装与启停冒烟；
> 构建与更新骨架见 docs/TAURI-MIGRATION.md（已于 2026-10-25 删除）。

> 升级：**当前版本还没有接入自动更新的 UI**（Tauri updater 的签名/清单/构建链路已就绪）。
> 需要升级时直接运行新版本安装包覆盖安装；Tauri 的“检查→下载→退出安装”在后续版本接入。

## 快速开始（首日上手）

1. **配一个平台环境**：账号中心 → 新增环境，填平台地址（如 `https://demo-val.example.com`）。
2. **加账号**：同一账号 + 用户名口令。口令加密存本机，界面永不回显。
3. **试用两个能力**：
   - **对象分析**：填对象编码（如 `capa_plan__c`）→ 跑「蜘蛛织网」→ 看关系网络、
     `spider-network.drawio` 与 networkx 分析结论；
   - **账号库**：管理环境、账号、盒子与配置池；账号连通性可用 Agent 的 `accounts_login`
     （httpx 直连，不打开浏览器）验证。
4. **可选**：设置页填 DeepSeek API Key，解锁 AI 对话（工具调用 + 附件理解）。

## 文档地图

> ⚠️ **协作者注意**：2026-09-14 做过一次**提交历史重写**（清除客户/环境标识与个人身份信息），
> 提交 SHA 已全部改变。若你的本地副本早于该时间点，请先读
> `docs/HISTORY-REWRITE-NOTICE.md`（**该文件已于 2026-09-29 删除**） 再拉取——**不要 merge 旧历史**。

| 文档 | 给谁看 | 内容 |
|---|---|---|
| **[docs/USER-MANUAL.md](docs/USER-MANUAL.md)** | **使用者** | 功能详解、操作步骤、常见问题、数据与安全须知 |
| [docs/API.md](docs/API.md) | 要调接口/写脚本的人 | 端点权威契约 + SSE/长轮询/产物下载四类特殊通道 |
| [docs/CONFIG.md](docs/CONFIG.md) | 现场排障、要调行为的人 | 五层配置全覆盖（环境变量 / settings 键 / 代码参数 / 数据目录文件 / 前端常量） |
| [docs/SCHEMA.md](docs/SCHEMA.md) | 要改数据层的人 | DB 迁移 1–26、表结构、跨进程数据契约、改表清单 |
| **[docs/CORE-ARCHITECTURE.md](docs/CORE-ARCHITECTURE.md)** | 要动 Agent 核心的人 | 八层结构、不变量 I1–I18、与行业做法的差异 |
| [docs/PITFALLS.md](docs/PITFALLS.md) | 现场排障、要改底层的人 | **135 条真机环境坑**（只在真机复现过、无法从代码反推，只增不改） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发者 | 环境搭建、版本与发布（§3）、代码规约、验收纪律 |
| [CHANGELOG.md](CHANGELOG.md) | 所有人 | 版本变更明细（含每次事故的根因） |

## 环境要求

| 场景 | 要求 |
|---|---|
| 使用安装包 | Windows 10/11 x64、WebView2 Runtime（Win11 自带；Win10 一般随 Edge 安装） |
| 从源码开发 | Python 3.12+、[uv](https://docs.astral.sh/uv/)、Rust 1.90+、Node 20+（Tauri CLI / E2E） |
| 生产环境 | 一个可访问的 eGMP 平台 + 一个测试账号 |

## 许可

MIT，见 [LICENSE](LICENSE)。
