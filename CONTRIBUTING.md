# 开发协作规范（CONTRIBUTING）

> 目标：两人协作时不互相踩脚，且构建/发布链路不出静默错误。
> 原则先于工具——**先契约后实现，先复现后修复**。
>
> 使用者请看 [README.md](README.md) 与 [docs/USER-MANUAL.md](docs/USER-MANUAL.md)；
> 接手维护请先读 [AGENT.md](AGENT.md)（代码地图 / 红线 / 排障速查 / 已知问题）。

## 1. 环境搭建（新同事 5 分钟）

```powershell
# 前置：Python 3.12+、Git、Node 20+（构建桌面壳/扩展时要）；uv 没有就先装（pip install uv 或 winget install astral-sh.uv）
uv sync --extra dev --extra build   # 严格按 uv.lock 拉齐依赖（含国内镜像，无需自己配源）
uv run playwright install chromium  # 托管浏览器内核（账号库/自动登录/监听需要）
powershell -ExecutionPolicy Bypass -File tools\setup.ps1   # 环境自检（兼容 PS 5.1/7）
uv run pytest                       # 64 项全绿即环境就绪
cd desktop; npm install; cd ..       # 桌面壳（Electron）依赖
cd extensions\quick-login; npm install; npm run build; cd ..\..   # 扩展构建产物 dist/
```

日常一切 Python 命令走 `uv run <cmd>`（自动使用锁定环境），**禁止**直接 `pip install` 后不落
`pyproject.toml`。

> **`--extra build` 不能省**：`uv sync` 不带它会把 PyInstaller 移出环境，
> 之后 `tools/build.ps1` 的第 5 步（PyInstaller）直接失败。`build.ps1` 内部已带这两个 extra。
>
> **`uv` 不在 PATH 时**（本机常见，`.venv\Scripts\uv.exe` 才是可用的那个）：
> `.\\.venv\\Scripts\\uv.exe sync --extra dev --extra build`。同理 `ruff` 走
> `.\\.venv\\Scripts\\uvx.exe ruff check .`。
>
> **ruff 当前并非全绿**（9 条违规，见 §6 已知状态）——不是环境问题，是待修的债。

## 2. 依赖变更流程

1. 改 `pyproject.toml` 的 `dependencies`（或 `optional-dependencies`）；
2. `uv lock && uv sync --extra dev --extra build`；
3. lockfile 变更**单独成提交**，方便回溯「哪个依赖引入了什么问题」。

> 容器化的取舍：本项目深绑 Windows 本机生态（`%APPDATA%` 数据目录、Electron 壳、
> 内置 Chromium、扩展装载），容器化收益低、摩擦大。**协作以 `uv.lock` 为准**；
> 若未来上 CI/Linux 部署再补 Dockerfile（`adapters` 的 `repoPath` 走环境变量注入，机制已备好）。

## 3. 构建与发布

### 3.1 一键构建

```powershell
powershell -ExecutionPolicy Bypass -File tools\build.ps1 [-Bump build|push|none] [-SkipPush]
```

链路（6 步）：① 版本演进 → ② **重建扩展并校验版本一致** → ③ **`uv sync`** → ④ release commit + push
→ ⑤ PyInstaller sidecar → ⑥ electron-builder NSIS。
产物：`desktop\dist\AksoWorkbench-<ver>-setup.exe` + `latest.yml`。

### 3.2 版本规则（方案 A，用户定稿）

- 真源 = `pyproject.toml` 的 `version`；**只走 `tools/bump.py`**，勿手改单一文件。
- `bump.py push` = PATCH+1；`bump.py build` = MINOR+1 且 PATCH 重置 1（`0.0.2 → 0.1.1 → 0.2.1`）。
- **五写同步**：`pyproject.toml` + `workbench/__init__.py` + `desktop/package.json`
  + 扩展 `packages/extension/manifest.json` + 扩展工作区 `package.json`（+ `.version.json` 缓存）。
- 桌面端与扩展**同号发布**：`extStale` 判定（「扩展是否需要重新加载」）建立在此约定上。
- ⚠️ **`desktop/package-lock.json` 与 `extensions/quick-login/package-lock.json` 的 version 字段是陈旧的**
  （0.2.1 / 0.2.20），`bump.py` 不写它们。**已知问题，见 §6**——`npm install` 会静默改写它们，
  让发布 commit 后工作区立刻变脏。

### 3.3 发布到 GitHub Releases

`build.ps1` 用 `--publish never` 只出产物。发布走 `tools/publish_release.py`（推荐，带三道自查：
`latest.yml` 版本号 == 发布版本、其 `sha512` == 安装包实际 sha512、指向本次安装包），
或网页手工上传 `setup.exe + latest.yml`（+ blockmap）。完整步骤与 `GH_TOKEN` 获取路径见
[docs/EXTENSION-INSTALL.md](docs/EXTENSION-INSTALL.md) 第四节。

> ⚠️ 更新器**匿名**读 Release ⇒ 仓库必须公开。
> ⚠️ 发布错清单会让**所有客户端**更新失败——所以那三道自查不要跳过。

### 3.4 构建前的秒级体检（必跑）

```powershell
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 8 项静态核对，当前 8/8 PASS
```

它拦住的是「跑完 PyInstaller（数分钟）才暴露」的断点：spec 入口脚本缺失、入口用了相对导入、
壳 spawn 路径与 `extraResources` 不一致、扩展产物缺失/版本不符、`build.ps1` 丢 UTF-8 BOM、
壳新增 `.js` 忘了进 electron-builder 的 `files` 白名单。

> ⚠️ **该工具目前没有被 `build.ps1` 自动调用**（已知问题，见 §6）——请手动跑。

### 3.5 构建链路的脚本纪律（PS 5.1）

- `.ps1` 必须 **UTF-8 BOM**（编辑后极易丢失，中文会乱码）；
  核对：`[System.IO.File]::ReadAllBytes(...)` 前 3 字节 == `239,187,191`。
- **禁止 `$ErrorActionPreference='Stop'`**：PS5.1 会把 git/uv 写 stderr 的正常进度当终止错误。
- 不支持 `??` 运算符；heredoc `<<` 不可用。
- **顺序敏感的两处**：`uv sync` 必须在 release commit **之前**（它会写 `uv.lock` 里的项目版本，
  反了则 commit 里的 lock 立刻过期）；扩展重建必须在 bump **之后**（否则打进安装包的扩展版本停在上一版）。

## 4. 代码规约

- **Lint 门禁**：`uvx ruff check .` 应全绿（E4/E7/E9/F/I/B，配置在 `pyproject.toml`）。
  fork 存量代码同样受约束，无豁免区。
- **类型注解**：新代码必须带完整注解 + `from __future__ import annotations`。
- **校验**：API 入参用 pydantic `BaseModel`；⚠️ `BaseModel` **默认忽略多余字段**——
  请求体必须**显式建模**每一个字段，否则字段静默丢失（`AccountPatch` 漏 `box` 的事故）。
- **异常**：服务层抛领域错误（`ApiError` / `AccountError` / `BrowserError`），路由层翻译为
  `HTTPException` + `raise ... from exc` 保留因果链。
- **文档字符串**：每个模块头部说明职责 + **知识来源**（fork 自谁 / 迁移自谁 / 实证出处）——
  这是本仓库最重要的可读性投资，新代码沿用。
- **数据库**：只走迁移（`db.py MIGRATIONS` 追加新版本号，**不改历史迁移**）。改表清单见
  [docs/SCHEMA.md](docs/SCHEMA.md) §6。

## 5. 开发流程与分工边界

1. **动代码前**先读 [AGENT.md](AGENT.md)（红线与环境坑）与 [docs/SCHEMA.md](docs/SCHEMA.md)（数据契约）；
2. 新路由挂到 `workbench/api/__init__.py::_optional_routers`（可选路由）或同级注册；
3. 新增/修改端点 → 同步 [docs/API.md](docs/API.md)；新增配置项 → 同步 [docs/CONFIG.md](docs/CONFIG.md)；
   架构级决策 → 新增 [docs/adr/](docs/adr/) 一篇；
4. 每完成一块：更新 `CHANGELOG.md` 的 `Unreleased` 段；
5. 提交信息建议 `阶段/模块: 动作`（如 `2B: 自动登录引擎节奏参数对齐上游`）。

| 区域 | 说明 |
|---|---|
| `workbench/api/*` + `workbench/services/*` | 主战场；同一文件避免两人同时改 |
| `workbench/harness/*` | fork 存量（对话 Agent 循环），改动最小化 |
| `desktop/*`、`extensions/quick-login/*` | 壳与扩展；改扩展必跑 §7 的三个回归工具 |
| `adapters/*.json` 指向的原四项目目录 | **冻结**：任何情况下不写 |
| `docs/adr/*` | 改已采纳的决策 = 新增一篇取代它，不允许悄悄改历史 ADR |

## 6. 已知状态（诚实记录，勿当绿灯）

| 项 | 现状 | 影响 |
|---|---|---|
| `uvx ruff check .` | **9 条违规**：`tools/diag_electron_typing.py`（F401/E402）、`tools/publish_release.py:360`（B007）、`tools/verify_accounts_ui.py`（F401/E402）、`tools/verify_box_ops.py:24`（E402）、`tools/verify_wheel_page.py`（I001/E402）、`workbench/api/routes_update.py:13`（I001） | lint 门禁不成立；其中 4 条可 `--fix` 自动修 |
| `tools/verify_packaging.py` | 8/8 PASS，但**未被 `build.ps1` 调用**、无 CI | 断点仍可能在构建数分钟后才暴露 |
| `desktop/package-lock.json` | version `0.2.1`（package.json 已 `0.3.4`） | `npm install` 会改写它，发布后工作区变脏 |
| `extensions/quick-login/package-lock.json` | version `0.2.20` | 同上 |
| `tools/setup.ps1` §5 | 仍把 **Node** 当必检项并说「洞察/工厂功能不可用」 | 口径已过时（能力已原生化，Node 只影响遗留 `proc.py` 与它们的测试） |
| `tests/test_proc.py`（7 项） | 仍在测 Node 子进程封装 | 它测的是**遗留封装本身**，不代表运行时链路 |
| `adapters/*.json` 的 `$schema` | 原指向已删除的 `docs/模块契约.md#adapters` | ✅ 本轮已改为 `docs/SCHEMA.md`（仅此字段） |

> 完整清单（含代码级缺陷与安全风险）见 [AGENT.md](AGENT.md) §4。

## 7. 验收纪律

```powershell
.\.venv\Scripts\python.exe -m pytest          # 64 项（含原生链路 12 项、更新 14 项、浏览器 9 项）
.\.venv\Scripts\uvx.exe ruff check .          # 见 §6，当前 9 条待修
.\.venv\Scripts\python.exe tools\verify_packaging.py
```

改动扩展后**额外必跑**（扩展端无单测框架，这三个就是它的回归测试）：

```powershell
.\.venv\Scripts\python.exe tools\verify_extension_boot.py        # 启动冒烟 7/7
.\.venv\Scripts\python.exe tools\verify_extension_isolation.py   # Cookie 隔离 + 多 iframe 填表 8/8
node tools\verify_host_logic.mjs                                 # host/端口口径 22/22
```

改动 `/extension/*` 蓝图后必跑 `node tools\verify_extension_sync.mjs`（**该蓝图无 pytest 覆盖**）。
完整验收基线（含真机项与 E2E）见 [AGENT.md](AGENT.md) §7。

- 阶段验收以 `tests/` 机器验收为准，不靠肉眼；
- 真机验收（真实 eGMP 平台）只在测试环境账号上做；
- 测试发现问题：**先记根因，再修**——不修「看起来好了」。
