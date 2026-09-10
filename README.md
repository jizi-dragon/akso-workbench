# Akso Workbench（akso-mogul）

> 四项目融合重建：把 **akso-cc**（平台配置洞察）、**mogul_simulator**（知识工作台）、**akso-auto**（配置自动化引擎）、**quick-login**（多账号自动登录）的优点合并进一个新项目，技术栈升级为 **Python 3.12+ / FastAPI / SQLite / Playwright**。
>
> 四个原项目**冻结不动**：新项目对它们只做「只读引用」（`adapters/*.json` 声明仓库路径）与「知识迁移」，绝不修改原文件。

## 定位

| 来源项目 | 沉淀进 Workbench 的能力 | 形态 |
|---|---|---|
| mogul_simulator | 知识工作台（对话/知识库/检索/钉钉同步）——fork 基线 | Python 直接重命名 fork → `workbench/` |
| akso-auto | 配置工厂（蓝图校验/创建/编排/Monitor） | **阶段 3 已原生化**（egmp.writers + orchestrate + monitor，纯 Python） |
| akso-cc | 平台洞察（登录/盘点/理解/蜘蛛爬取/报告） | **阶段 3 已原生化**（egmp.insight，含 networkx 图分析） |
| quick-login | 统一账号库 + 自动登录节奏门控 | **混合架构**：Python/Playwright 托管自动化 + Chrome 扩展执行面（`extensions/quick-login/`，用户浏览器内会话切换） |

> **运行时零依赖原项目**：四项目能力已全部内化到 `workbench/services/egmp/`（Python 3.12+/httpx/pydantic/playwright/networkx）；
> 原仓库仅需在排查口径差异时作只读参考（`adapters/*.json` 为对照存档）。终端用户运行无需 Node
> （桌面壳为 Electron 自带运行时；开发态桌面壳与扩展构建需要 npm）。

## 快速开始（双人协作 · uv 为准）

```powershell
# 0) 安装 uv（一次性）：pip install uv 或 winget install astral-sh.uv

# 1) 按 uv.lock 精确拉齐依赖（已配国内镜像；不要用裸 pip install）
uv sync --extra dev

# 2) 托管浏览器内核（账号库/自动登录功能需要）
uv run playwright install chromium

# 3) 环境自检（原仓库只读引用 / 依赖体检；兼容 PowerShell 5.1/7）
powershell -ExecutionPolicy Bypass -File tools\setup.ps1

# 4) 启动服务（自动开浏览器）
uv run python -m workbench.main

# 5) Electron 桌面壳（推荐；含主窗/轮盘 Alt+Q/托盘/会话窗控制）
cd desktop
npm install
npm start

# 日常：测试 / lint
uv run pytest
uvx ruff check .
```

环境变量与依赖变更纪律见 `CONTRIBUTING.md`。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WORKBENCH_DATA` / `MOGUL_DATA` | `%APPDATA%/AksoWorkbench` | 数据目录 |
| `WORKBENCH_DB` / `MOGUL_DB` | `<数据目录>/workbench.db` | SQLite 路径（兼容接管 mogul.db） |
| `WORKBENCH_HOST` / `WORKBENCH_PORT` | `127.0.0.1:18765` | 服务监听 |
| `AKSO_AUTO_REPO` / `AKSO_CC_REPO` | 见 `adapters/*.json` | 原项目仓库路径覆盖 |
| `NODE_COMMAND` | `node` | Node 可执行文件 |

> 桌面壳内部端口（一般无需配置）：18766 = 内置 Chromium CDP（自动化挂接）；18767 = 会话窗控制服务。

## 目录蓝图

- `docs/架构分析.md` —— **先读这个**：总体架构（Electron 壳 + FastAPI 服务 + 扩展执行面）、模块地图、关键数据流、环境坑与维护红线
- `docs/SESSION-DIGEST.md` —— 会话速查（版本规则、运维速记、验收基线）
- `extensions/quick-login/docs/PROJECT-STATUS.md` —— 扩展执行面六平面隔离详解
- `CONTRIBUTING.md` —— 开发协作规范（环境、依赖变更、代码规约、分工边界）
- 执行计划原文（历史存档）：`D:\ai_assistant\akso-workbench-PLAN.md`

## 里程碑

- **M1** 骨架 + 子进程聚合：知识工作台可用；一键跑 akso-cc `understand`；上传蓝图跑 akso-auto `create`。
- **M2** 统一账号库 + 托管浏览器 + 自动登录引擎（quick-login 节奏门控 100% 迁移）。
- **M3** 深度 Python 化（egmp 客户端内核 / 写路径 / 读路径 / Monitor）+ Agent 层。
- **M4**（当前）桌面壳 Electron 化 + quick-login 混合架构（扩展执行面 + 桌面数据面）+ 浏览器分配政策（快捷登录=用户 Chrome，监听/自动化=内置 Chromium）。
