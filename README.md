# Workbench

> 实施工程师的**单机工作台**：把对象分析、统一账号库与多账号快捷登录合到一个桌面应用里。
> 四个来源项目（akso-cc / mogul_simulator / akso-auto / quick-login）的能力已**全部内化**进本仓库——
> 运行时零依赖原项目、零依赖 Node、零联网（首次装载浏览器扩展除外）。
> 首个适配平台是阿克索 eGMP；**平台能力走插件与适配层，换平台不必改产品名与数据目录**（见下）。
>
> 仓库地址：`jizi-dragon/akso-mogul`（**仓库名本次未改**，见 [ADR-0013](docs/adr/0013-brand-neutralization-workbench.md) D-4）。

## 它解决什么问题

| 你的日常动作 | 以前 | 现在 |
|---|---|---|
| 摸清一个平台上有什么对象、字段、状态、流转 | 记命令、跑 Node 脚本、翻产物目录 | 工作台里填对象编码 → **蜘蛛五步织网**：关系网络 + `spider-network.drawio` + 生命周期流转图 |
| 同时以 3 个账号登录同一平台（管理员 + 普通用户） | 开多份浏览器 profile，反复退出登录 | **一个 Chrome 窗口内多账号并行**，桌面端 `Alt+Q` 轮盘切换身份，账号间存储/鉴权/Cookie/缓存/IDB 全链路隔离 |
| 记住十几个测试账号的口令 | 明文表格 / 记事本 | **统一账号库**：环境 + 账号 + 盒子分组，口令 Fernet 加密存本机，可备份迁移 |
| 搞清「这个操作在平台上到底发了什么请求」 | 手抄 Network 面板 | **监听录制**（内置 Chromium）→ 三级降噪 → 分段解读 + 复现计划 |
| 让 Agent 替你跑这些活 | 每件事都得自己点 | **对话 Agent + 18 个工具**：工具由 `ToolHub` 单一定义，**风险操作有确认门**（单次令牌，无令牌不执行），模型还能用 `request_user_confirmation` **先问再做**，每次调用**进审计表** |

> **本仓最该被看见的不是功能清单，而是它的工程纪律**：能力以「工具插件」形式接入，
> `ToolHub` 是**唯一扩展面**（不是 router、不是模块清单）；工具定义**只有一处**，
> 对话的 function calling、HTTP 调用面、审计表三处都从它读。契约与三个真踩过的坑见
> [docs/PLUGIN-SDK.md](docs/PLUGIN-SDK.md)。

## 四个来源项目的沉淀

| 来源 | 沉淀下来的能力 | 现在是什么 |
|---|---|---|
| **akso-cc** | 对象分析（原「平台洞察」）：登录验证 / 蜘蛛五步织网（networkx 图分析）<br>~~全量盘点~~ ~~对象三层理解~~（2026-09-14 下线，场景模糊、效果不佳） | Python 原生（`services/egmp/insight/`） |
| **mogul_simulator** | 对话工作台、会话历史、设置 | Python 原生（fork 基线） |
| **akso-auto** | Monitor 录制解读（三级降噪 / 分段解读 / 复现计划）<br>~~配置创建（蓝图校验 / 幂等创建 / 拓扑编排 / 断点续跑）~~（**2026-09-22 整体移除**，见下） | Python 原生（`services/egmp/monitor/`）<br>**写路径已不在本仓库** |
| **quick-login** | 统一账号库 + 自动登录节奏门控 + 六平面隔离 | 混合：桌面数据面（Python）+ 浏览器扩展执行面 |

> 四个原项目**冻结不动**：本仓库对它们只做只读引用（`adapters/*.json`）与知识迁移，绝不修改原文件。
> 详见 [ADR-0005](docs/adr/0005-native-internalization-of-four-projects.md)。
>
> **改名记录（2026-09-22，用户下达）**：界面上的「平台洞察」更名为「**对象分析**」。
> **只改显示名**——模块 id（`insight`）、工具名（`insight_*`）、接口路径（`/api/insight`）、
> 落盘目录（`…/Workbench/洞察`）全部不变，所以更早的 [规范](docs/INSIGHT-SPEC.md)、
> [ADR](docs/adr/) 与坑清单里出现旧名是**正常的**。

> ## ⚠️ 2026-09-22：「配置工厂」整体移除，平台知识留下
>
> 原 akso-auto 那条**写路径**（蓝图两层校验 / 幂等创建 / 8 步管道 / 拓扑编排 / 断点续跑）
> 连同它的插件、路由、前端页、验证工具与测试**已全部从本仓库删除**：
> 产品里不再有任何"会改平台数据"的能力，因此也**不再有 `Risk.DANGEROUS` 级工具**
> （曾唯一的一个是 `factory_create`）——确认门的 dangerous 分支、审批弹窗的高危样式
> 目前只剩测试在走，这是一个**已知的、刻意的**状态。
>
> **平台知识没有跟着丢**，它被迁进了两份不依赖任何产品代码的文档：
> [docs/PLATFORM-OPERATIONS-LEARNED.md](docs/PLATFORM-OPERATIONS-LEARNED.md)（平台事实台账：
> 端点、真机线格式、样本量 `n`、平台约束）与
> [docs/FACTORY-PLATFORM-COGNITION.md](docs/FACTORY-PLATFORM-COGNITION.md)（手册语义与配置分层认知），
> 原始真机取证报告在 [factory-tuning/reports/](factory-tuning/reports/)。
> 采集与分析工具链（`factory-tuning/tools`、`analyze`、`data`）随之下线——**报告留着，工具删了**。

## 走向"可复用工作台"：能力都在插件与工具层

工作台的**核心资产是插件/tools 层**——Agent 的所有能力都经 `plugin_api.ToolHub` 注册，
业务能力（洞察 / 账号 / 检索）各自是一个插件。当前 **4 个插件 / 18 个工具**
（16 个 `read` + 2 个 `write`）。

| 想知道什么 | 去哪看 |
|---|---|
| **现在到底有哪些工具、谁在管、怎么加一个** | **[docs/TOOLS.md](docs/TOOLS.md)**（工具注册表 + 变更纪律 + 机检） |
| 怎么写一个工具插件、风险分级与确认门语义 | [docs/PLUGIN-SDK.md](docs/PLUGIN-SDK.md) |
| **不受信代码（上传的 Skill）在哪跑、拦得住什么** | **[ADR-0014](docs/adr/0014-sandbox-for-untrusted-code.md)** + 工作台「沙箱」页（策略/状态/执行审计） |
| **怎么上传 / 安装 / 卸载 Skill** | 工作台「Skill 管理」页 + `docs/TOOLS.md`（工具治理纪律） |
| 为什么平台相关代码集中在 `services/egmp/` | [ADR-0005](docs/adr/0005-native-internalization-of-four-projects.md)、[ADR-0013](docs/adr/0013-brand-neutralization-workbench.md) |

> 工具清单**不靠人记**：`docs/TOOLS.md` 的清单由 `tools/gen_tools_doc.py` 从运行中的工具中枢生成，
> `python tools/gen_tools_doc.py --check` 会对不上就报红——防止再出现"我不知道多了几个工具"。

## 安装（终端用户）

1. 下载并运行 `Workbench-<版本>-setup.exe`（NSIS 安装包，约 350 MB，含 Python 服务端与浏览器内核）。
   安装路径可自选，无需管理员权限、无需预装 Python / Node。
2. 首次运行会自动完成数据初始化（`%APPDATA%\Workbench`）。
3. **装载浏览器扩展**（一次性，为了「多账号快捷登录」）：
   托盘右键「安装浏览器扩展…」或账号中心右上角的提示条 → 按弹窗四步操作
   （打开开发者模式 → 加载已解压的扩展程序 → 选中已自动打开的目录）。
   详细图文步骤见 [docs/EXTENSION-INSTALL.md](docs/EXTENSION-INSTALL.md)。
4. 之后：账号由桌面端自动下发到扩展，桌面端升级时会提示你点一次扩展卡片上的「重新加载」。

> 升级：**应用默认不自动更新**（2026-09-15 定稿：不再自己替换自己，避免"跑的到底是哪个副本"难以判断）。
> 需要自动更新时设环境变量 `WORKBENCH_AUTO_UPDATE=1` 再重启；否则直接跑新版本安装包覆盖安装。
> 手动检查入口：托盘「检查更新…」（关闭状态下只回一句说明）。详见 [docs/CONFIG.md](docs/CONFIG.md) §2.2。

## 快速开始（首日上手）

1. **配一个平台环境**：账号中心 → 新增环境，填平台地址（如 `https://demo-val.example.com`）。
2. **加账号**：同一账号 + 用户名口令。口令加密存本机，界面永不回显。
3. **试用两个能力**：
   - **对象分析**：填对象编码（如 `capa_plan__c`）→ 跑「蜘蛛织网」→ 看关系网络、
     `spider-network.drawio` 与 networkx 分析结论；
   - **快捷登录**：按 `Alt+Q` 呼出轮盘选账号 → 用户 Chrome 里以该身份打开（或卡片上的「打开」用内置浏览器 + 免密直达）。
4. **可选**：设置页填 DeepSeek API Key，解锁 AI 对话（工具调用 + 附件理解）。

## 文档地图

> ⚠️ **协作者注意**：2026-09-14 做过一次**提交历史重写**（清除客户/环境标识与个人身份信息），
> 提交 SHA 已全部改变。若你的本地副本早于该时间点，请先读
> [`docs/HISTORY-REWRITE-NOTICE.md`](docs/HISTORY-REWRITE-NOTICE.md) 再拉取——**不要 merge 旧历史**。

| 文档 | 给谁看 | 内容 |
|---|---|---|
| **[docs/USER-MANUAL.md](docs/USER-MANUAL.md)** | **使用者** | 功能详解、操作步骤、常见问题、数据与安全须知 |
| [docs/API.md](docs/API.md) | 要调接口/写脚本的人 | 端点权威契约 + SSE/长轮询/产物下载四类特殊通道 |
| [docs/CONFIG.md](docs/CONFIG.md) | 现场排障、要调行为的人 | 五层配置全覆盖（环境变量 / settings 键 / 代码参数 / 数据目录文件 / 前端常量） |
| [docs/SCHEMA.md](docs/SCHEMA.md) | 要改数据层的人 | DB 迁移 1–20、表结构、跨进程数据契约、改表清单 |
| [docs/adr/](docs/adr/) | 想知道「为什么这么设计」的人 | **16 篇**架构决策记录 ＋ `0000-template.md`（含被放弃的方案与重新评估的触发器） |
| [docs/EXTENSION-PLANE.md](docs/EXTENSION-PLANE.md) | 要改浏览器扩展的人 | 六平面隔离原理、桌面↔扩展协议、私有改造史、安全边界 |
| [docs/EXTENSION-INSTALL.md](docs/EXTENSION-INSTALL.md) | 首次装载扩展的人 | 逐步图文 + **为什么不能静默一键装**的实证 |
| [docs/INSIGHT-SPEC.md](docs/INSIGHT-SPEC.md) | 要发展/扩展平台洞察的人 | **洞察的功能定位与产品能力契约**：能力范围与非目标、平台知识层、产物与存储、已知边界、验收标准 |
| **[docs/TOOLS.md](docs/TOOLS.md)** | **要给 Agent 加工具 / 想知道有哪些工具的人** | **工具注册表与治理**：全量清单（插件 × 风险 × 确认门）、加一个工具要动的文件、机检命令、历史漂移教训 |
| **[docs/PLUGIN-SDK.md](docs/PLUGIN-SDK.md)** | **要给 Agent 加工具的人** | **插件 SDK 契约**：`ToolHub` 唯一公开面、怎么写一个工具插件、风险分级与危险操作确认门、审计、启动自检、三个真踩过的坑 |
| [docs/FRAMEWORK-INVENTORY.md](docs/FRAMEWORK-INVENTORY.md) | 要复用本仓、或要把业务与框架切开的人 | **框架清单与判据**：换一个目标平台后哪些文件不需要改、业务耦合点在哪几处、特征词扫描门禁 |
| [docs/PLUGIN-UPSTREAM-RESEARCH.md](docs/PLUGIN-UPSTREAM-RESEARCH.md) | 要动宿主/插件边界的人 | 宿主与插件分离的先例、可行性判断、POC 验证与陷阱清单（`FRAMEWORK-INVENTORY` 的姊妹篇） |
| [docs/research/core-plugin-separation-research.md](docs/research/core-plugin-separation-research.md) | 想知道「插件 API 为什么长这样」的人 | 插件扩展面的可行性研究与**权利边界**论证（`plugin_api.py` 文件头的依据） |
| [docs/research/deepseek-api-capabilities.md](docs/research/deepseek-api-capabilities.md) | 要调模型/算成本的人 | **DeepSeek API 能力核查**：现行模型名（V4.1-Flash）、**联网搜索到底有没有**（没有）、上下文缓存的命中规则与 50 倍价差 |
| [docs/CODEBASE_OVERVIEW.md](docs/CODEBASE_OVERVIEW.md) | 接手维护的人（含 AI 助手） | 代码地图、架构与数据流、红线与约定、风险台账、验收基线实测 |
| **[docs/VERSIONING.md](docs/VERSIONING.md)** | **管版本 / 发版 / 想接 GitHub 自动化的人** | **版本管理机制**（三层拆解）、现状实测（tag 缺口、更新链路的公开仓库前提）、GitHub 能帮哪一段、三个方案选型、可直接照抄的 CI 与打 tag 做法 |
| [docs/PITFALLS.md](docs/PITFALLS.md) | 现场排障、要改底层的人 | **89 条真机环境坑**（只在真机复现过、无法从代码反推，只增不改；其中一批的主体已随配置工厂移除，文件头有对照表） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发者 | 环境搭建、依赖变更、构建发布、代码规约、验收纪律 |
| [CHANGELOG.md](CHANGELOG.md) | 所有人 | 版本变更明细（含每次事故的根因） |
| [docs/HISTORY-REWRITE-NOTICE.md](docs/HISTORY-REWRITE-NOTICE.md) | 协作者（受影响者） | 历史重写说明 + 本地副本迁移步骤（勿 merge 旧历史） |
| [docs/archive/](docs/archive/) | 追溯用 | 已失效的历史文档（勿作现状依据） |

## 环境要求

| 场景 | 要求 |
|---|---|
| 使用安装包 | Windows 10/11 x64；Chrome/Edge（要「多账号快捷登录」时才需要） |
| 从源码开发 | Python 3.12+、[uv](https://docs.astral.sh/uv/)、Node 20+（仅构建桌面壳与扩展时需要） |
| 生产环境 | 一个可访问的 eGMP 平台 + 一个测试账号 |

## 许可

MIT，见 [LICENSE](LICENSE)。
