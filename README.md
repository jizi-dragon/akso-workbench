# Akso Workbench（akso-mogul）

> 阿克索实施工程师的**单机工作台**：把平台洞察、配置工厂、统一账号库与多账号快捷登录合到一个桌面应用里。
> 四个来源项目（akso-cc / mogul_simulator / akso-auto / quick-login）的能力已**全部内化**进本仓库——
> 运行时零依赖原项目、零依赖 Node、零联网（首次装载浏览器扩展除外）。

## 它解决什么问题

| 你的日常动作 | 以前 | 现在 |
|---|---|---|
| 摸清一个平台上有什么对象、字段、状态、流转 | 记命令、跑 Node 脚本、翻产物目录 | 工作台里填对象编码 → 出**三层理解报告**（内容 / 关系网 / 生命周期流转图） |
| 批量建对象、字段、选项集、生命周期、工作流、布局、菜单 | 手工点，或写一堆一次性脚本 | 写（或让 AI 生成）**蓝图 JSON** → 校验 → 一键创建，**幂等可重跑**、带断点续跑 |
| 同时以 3 个账号登录同一平台（管理员 + 普通用户） | 开多份浏览器 profile，反复退出登录 | **一个 Chrome 窗口内多账号并行**，桌面端 `Alt+Q` 轮盘切换身份，账号间存储/鉴权/Cookie/缓存/IDB 全链路隔离 |
| 记住十几个测试账号的口令 | 明文表格 / 记事本 | **统一账号库**：环境 + 账号 + 盒子分组，口令 Fernet 加密存本机，可备份迁移 |
| 搞清「这个操作在平台上到底发了什么请求」 | 手抄 Network 面板 | **监听录制**（内置 Chromium）→ 三级降噪 → 分段解读 + 复现计划 |

## 四个来源项目的沉淀

| 来源 | 沉淀下来的能力 | 现在是什么 |
|---|---|---|
| **akso-cc** | 平台洞察：登录验证 / 蜘蛛五步织网（networkx 图分析）<br>~~全量盘点~~ ~~对象三层理解~~（2026-09-14 下线，场景模糊、效果不佳） | Python 原生（`services/egmp/insight/`） |
| **mogul_simulator** | 对话工作台、会话历史、设置 | Python 原生（fork 基线） |
| **akso-auto** | 配置工厂：蓝图两层校验 / 幂等创建 / 拓扑编排 / Monitor 录制解读 | Python 原生（`services/egmp/writers` + `orchestrate` + `monitor`） |
| **quick-login** | 统一账号库 + 自动登录节奏门控 + 六平面隔离 | 混合：桌面数据面（Python）+ 浏览器扩展执行面 |

> 四个原项目**冻结不动**：本仓库对它们只做只读引用（`adapters/*.json`）与知识迁移，绝不修改原文件。
> 详见 [ADR-0005](docs/adr/0005-native-internalization-of-four-projects.md)。

## 安装（终端用户）

1. 下载并运行 `AksoWorkbench-<版本>-setup.exe`（NSIS 安装包，约 350 MB，含 Python 服务端与浏览器内核）。
   安装路径可自选，无需管理员权限、无需预装 Python / Node。
2. 首次运行会自动完成数据初始化（`%APPDATA%\AksoWorkbench`）。
3. **装载浏览器扩展**（一次性，为了「多账号快捷登录」）：
   托盘右键「安装浏览器扩展…」或账号中心右上角的提示条 → 按弹窗四步操作
   （打开开发者模式 → 加载已解压的扩展程序 → 选中已自动打开的目录）。
   详细图文步骤见 [docs/EXTENSION-INSTALL.md](docs/EXTENSION-INSTALL.md)。
4. 之后：账号由桌面端自动下发到扩展，桌面端升级时会提示你点一次扩展卡片上的「重新加载」。

> 升级：应用会自动检查新版本并在后台静默下载，**退出时自动安装**（不打断你正在做的事）。
> 也可以托盘「检查更新…」手动触发。

## 快速开始（首日上手）

1. **配一个平台环境**：账号中心 → 新增环境，填平台地址（如 `https://demo-val.example.com`）。
2. **加账号**：同一账号 + 用户名口令。口令加密存本机，界面永不回显。
3. **试用三个能力**：
   - **平台洞察**：填对象编码（如 `capa_plan__c`）→ 跑「蜘蛛织网」→ 看关系网络、
     `spider-network.drawio` 与 networkx 分析结论；
   - **配置工厂**：上传或让 AI 生成蓝图 → 先校验 → **勾选环境确认** → 创建；
   - **快捷登录**：按 `Alt+Q` 呼出轮盘选账号 → 用户 Chrome 里以该身份打开（或卡片上的「打开」用内置浏览器 + 免密直达）。
4. **可选**：设置页填 DeepSeek API Key，解锁 AI 对话与蓝图生成。

## 文档地图

> ⚠️ **协作者注意**：2026-09-14 做过一次**提交历史重写**（清除客户/环境标识与个人身份信息），
> 提交 SHA 已全部改变。若你的本地副本早于该时间点，请先读
> [`docs/HISTORY-REWRITE-NOTICE.md`](docs/HISTORY-REWRITE-NOTICE.md) 再拉取——**不要 merge 旧历史**。

| 文档 | 给谁看 | 内容 |
|---|---|---|
| **[docs/USER-MANUAL.md](docs/USER-MANUAL.md)** | **使用者** | 功能详解、操作步骤、常见问题、数据与安全须知 |
| [docs/API.md](docs/API.md) | 要调接口/写脚本的人 | 60+ 端点权威契约 + SSE/长轮询/产物下载四类特殊通道 |
| [docs/CONFIG.md](docs/CONFIG.md) | 现场排障、要调行为的人 | 五层配置全覆盖（环境变量 / settings 键 / 代码参数 / 数据目录文件 / 前端常量） |
| [docs/SCHEMA.md](docs/SCHEMA.md) | 要改数据层的人 | DB 迁移 1–13、表结构、跨进程数据契约、改表清单 |
| [docs/adr/](docs/adr/) | 想知道「为什么这么设计」的人 | 10 篇架构决策记录（含被放弃的方案与重新评估的触发器） |
| [docs/EXTENSION-PLANE.md](docs/EXTENSION-PLANE.md) | 要改浏览器扩展的人 | 六平面隔离原理、桌面↔扩展协议、私有改造史、安全边界 |
| [docs/EXTENSION-INSTALL.md](docs/EXTENSION-INSTALL.md) | 首次装载扩展的人 | 逐步图文 + **为什么不能静默一键装**的实证 |
| [docs/INSIGHT-SPEC.md](docs/INSIGHT-SPEC.md) | 要发展/扩展平台洞察的人 | **洞察的功能定位与产品能力契约**：能力范围与非目标、平台知识层、产物与存储、已知边界、验收标准 |
| **[docs/PLUGIN-SDK.md](docs/PLUGIN-SDK.md)** | **要给 Agent 加工具的人** | **插件 SDK 契约**：`ToolHub` 唯一公开面、怎么写一个工具插件、风险分级与危险操作确认门、审计、启动自检、三个真踩过的坑 |
| [docs/research/deepseek-api-capabilities.md](docs/research/deepseek-api-capabilities.md) | 要调模型/算成本的人 | **DeepSeek API 能力核查**：现行模型名（V4.1-Flash）、**联网搜索到底有没有**（没有）、上下文缓存的命中规则与 50 倍价差 |
| [docs/CODEBASE_OVERVIEW.md](docs/CODEBASE_OVERVIEW.md) | 接手维护的人（含 AI 助手） | 代码地图、架构与数据流、红线与约定、风险台账、验收基线实测 |
| [docs/PITFALLS.md](docs/PITFALLS.md) | 现场排障、要改底层的人 | **22 条真机环境坑**（只在真机复现过、无法从代码反推，只增不改） |
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
