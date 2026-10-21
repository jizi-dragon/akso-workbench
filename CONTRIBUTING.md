# 开发协作规范（CONTRIBUTING）

> 目标：两人协作时不互相踩脚，且构建/发布链路不出静默错误。
> 原则先于工具——**先契约后实现，先复现后修复**。
>
> 使用者请看 [README.md](README.md) 与 [docs/USER-MANUAL.md](docs/USER-MANUAL.md)；
> 接手维护请先读 `docs/CODEBASE_OVERVIEW.md`（2026-10-19 精简重写：代码地图 / 模块职责 / 风险索引）
> 与 [docs/PITFALLS.md](docs/PITFALLS.md)（环境坑 / 排障速查）。
>
> **用 AI 助手改这个仓库时**：把 **[AGENTS.md](AGENTS.md)** 交给它（多数工具会自动读取根目录的该文件）。
> 那份文件是**同一套规则的"规则形状"版本**——本文档写给能容忍模糊的人，
> 而 AI 会把"没写下来的约定"当成"不存在这个约定"。两份内容不重复：这里讲**为什么与怎么做**，
> 那里只讲**做与不做**（外加每条的判据与出处）。

## 1. 环境搭建（新同事 5 分钟）

```powershell
# 前置：Python 3.12+、Git、Rust 1.90+、Node 20+（Tauri CLI 与 Web UI E2E 需要）
uv sync --extra dev --extra build   # 严格按 uv.lock 拉齐依赖（含国内镜像，无需自己配源）
# 仅当要跑 Web UI E2E 验收脚本（tools/verify_*_ui.py）时才需要浏览器内核：
# uv run playwright install chromium
powershell -ExecutionPolicy Bypass -File tools\setup.ps1   # 环境自检（兼容 PS 5.1/7）
uv run pytest                       # 快层全绿即环境就绪（约 35 秒；慢层见 §3.6）
```

日常一切 Python 命令走 `uv run <cmd>`（自动使用锁定环境），**禁止**直接 `pip install` 后不落
`pyproject.toml`。

> **`--extra build` 不能省**：`uv sync` 不带它会把 PyInstaller 移出环境，
> 之后 `tools/build_tauri.ps1` 的 sidecar 步骤直接失败。该脚本内部已带这两个 extra。
>
> **⚠️ `uv` / `ruff` 的口径在本仓已翻转过三次，别再背命令——先探一次**（[PITFALLS #50](docs/PITFALLS.md)）：
>
> ```powershell
> Get-ChildItem .venv\Scripts -Filter "uv*.exe","ruff*.exe"
> ```
>
> **2026-09-20 实测**：`ruff.exe` **存在**（0.16.0）→ **lint 命令是 `.\.venv\Scripts\ruff.exe check .`**；
> `uv.exe` **不存在**（只有 `uvicorn.exe`，`python -m uv` 报 `No module named uv`，PATH 上也没有）。
> 若哪天 `uv.exe` 回来，回退是 `.\.venv\Scripts\uv.exe tool run ruff check .`（首次会下载 ruff，需网络）。
> **两条都记着。**
>
> 历史（**保留以留痕，均已过期**）：原 `AGENT.md` 说"两个都没有" → 2026-09-15 记"uv 存在"
> → 2026-09-16"更正说 uv 不存在、ruff 存在" → 2026-09-17 再更正回来。
> **每一版在当时都是实测的**——问题不在诚实，在于**把一个会漂的环境事实固化成了散文**。
> 根治方向是把它变成探测（`tools/` 下一条 `lint.ps1`，或让某个 `verify_*` 探一次并打印实际命令），
> 这样"用哪条命令"由代码回答而不是由人记。

### 1.1 本机 Python / uv 的真实位置（**先看这条，再理 VS Code 的报错**）

本机的 uv 与 Python **都是非常规位置**，这不是「装漏了」也不是「忘了加 PATH」，而是两种既有选择的结果：

| 组件 | 实际位置 | 为什么在这里 |
|---|---|---|
| **uv** | `.venv\Scripts\uv.exe`（**不在 PATH**） | uv 是以 **pip 包**形式装进项目 venv 的（`.venv\Lib\site-packages\uv-<ver>.dist-info`），而不是用官方安装器装到用户目录 |
| **Python** | `.venv\Scripts\python.exe` → 由 **conda 的 3.14.6** 创建 | `.venv\pyvenv.cfg` 的 `home = D:\software\conda`。venv 本体在项目内，但 `python` 命令本身不在系统 PATH |

**因此 VS Code 会弹「未找到 Python，是否用 uv 安装一个」——那是误判**（本机已有可用解释器），
不要点安装，它会白下一份 Python。仓库已提交 `.vscode/settings.json` 显式指定解释器，
比「新装一个 Python」更省事也更安全。

若你的编辑器仍然找不到解释器，手动选一次即可（**不是**配置错误，只是首次发现）：
`Ctrl+Shift+P` → `Python: Select Interpreter` → `Enter interpreter path` →
`<仓库>\.venv\Scripts\python.exe`。

> 想彻底摆脱「uv 不在 PATH」，可选（非必需）：
> `winget install astral-sh.uv` 或 `pip install --user uv`，然后重开终端。
> 项目文档里的 `uv run` / `uvx` 命令在这之后才可直接用；**在此之前一律走 `.venv\Scripts\` 下的副本**。

> **本机 venv 是 Python 3.14.6，项目要求 `>=3.12`** —— 满足要求，但要注意 3.14 上
> `from __future__ import annotations` 的注解已不再于运行时求值，所以「注解里写了未导入的名字」
> 这类错误不会在运行时暴露（`routes_insight.py` 的 `Any` 就是这么潜藏的）。ruff 的 F821 是唯一防线。
>
> ⚠️ **但这不只是"注解求值"一件事（P0-5，2026-09-17 补）**：本机 3.14 与 CI 的 3.12
> **跑出来的结论可以不同**。当时的具体差集是 CI runner 没有 chromium、浏览器验收在那里
> skip 而本机 pass；**2026-10-19 内置浏览器下线、相关测试删除后，这个差集已消失**，
> 但"解释器口径必须一致"这条纪律仍有效。
> 所以本仓现在有一个明确的解释器口径：**`.python-version`（= `3.12`）是唯一真源**，
> `tests/test_python_version_contract.py` 会断言它与 `.github/workflows/ci.yml` 的
> `python-version` **逐字一致**（两处分居文本与 YAML，没有别的机制保证同步）。
>
> **想在本地按 CI 的口径复现**（强烈建议在"CI 红了但本地绿"时先做这一步）：
>
> ```powershell
> # 独立目录，**不动**你的主力 .venv（3.14 仍可继续日常开发）
> .\.venv\Scripts\uv.exe venv .venv312 --python 3.12
> .\.venv\Scripts\uv.exe pip install --python .venv312\Scripts\python.exe -e ".[dev]"
> .\.venv312\Scripts\python.exe -m pytest -o addopts="" -q -rs    # -rs 打印跳过原因
> ```
>
> **2026-09-17 实测**这套口径：`.venv312`（3.12.14）→ **493 passed, 8 skipped**；
> 历史记录（2026-09 的 conda 3.14 环境）→ **501 passed, 0 skipped**；现役 `.venv` 是 3.12.14，基线见下方。
> 差的正是那 8 项浏览器验收（slow 层由 CI 单独一步显式跑）。
>
> ⚠️ **2026-10-19 内置浏览器下线**：`test_browser_parallel.py` / `test_autologin.py` /
> `test_browser_state.py` 已删除；上述"8 项浏览器 skip"不再存在。Playwright 只留在 dev
> 依赖里供 Web UI E2E 脚本使用，不再影响 pytest 的解释器口径结论。
>
> `.venv312/` 已在 `.gitignore` 中忽略。**3.14 是合法的开发环境**（`requires-python` 允许），
> 本仓不禁止它；被禁止的是"两边版本不同步却以为结论一样"。

>
> **ruff 现已全绿**（`uvx ruff check .` → All checks passed；`tools/*.py` 的 E402 是刻意豁免的引导式导入，
> 见 `pyproject.toml` 的 `per-file-ignores`）。**验收基线是 1004 passed / 1 skipped**（`-o addopts=""` 一次跑全，**含 slow 层**；数字随判据增长，以当日实测为准）
> （2026-10-07 实测）——不是下面模板里的旧数字。
> ⚠️ **`uv tool run ruff` 要联网取 ruff**：PyPI 直连 TLS 失败时（本机实测过
> `tls handshake eof`）改用清华镜像——`uv tool run --index https://pypi.tuna.tsinghua.edu.cn/simple ruff check .`
> （`pyproject.toml` 里那条镜像只对**项目依赖**生效，`uv tool run` 取的是工具，不吃它）。
> ⚠️ **改了代码就必须重跑**：别把"上次的绿"当成"这次的绿"（[PITFALLS](docs/PITFALLS.md) #41 就是这么栽的：
> 测试绿了、ruff 没重跑，而那一行恰好是 ruff 能抓的 `F821`）。

## 2. 依赖变更流程

1. 改 `pyproject.toml` 的 `dependencies`（或 `optional-dependencies`）；
2. `uv lock && uv sync --extra dev --extra build`；
3. lockfile 变更**单独成提交**，方便回溯「哪个依赖引入了什么问题」。

> 容器化的取舍：本项目深绑 Windows 本机生态（`%APPDATA%` 数据目录、桌面壳），
> 但 2026-10-19 起已不再内置 Chromium；容器化收益低、摩擦大。**协作以 `uv.lock` 为准**；
> 若未来上 CI/Linux 部署再补 Dockerfile（`adapters` 的 `repoPath` 走环境变量注入，机制已备好）。

## 3. 构建与发布

### 3.1 一键构建

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_tauri.ps1 `
  [-Bump build|push|none] `
  [-UpdateBaseUrl https://updates.example.com/workbench/<版本>] `
  [-Notes "更新说明"] `
  [-SkipSidecar]
```

链路：① 版本检查/演进 → ② PyInstaller sidecar → ③ Tauri build（NSIS per-user + updater 签名）
→ ④ 可选生成 `latest.json`。
产物：

```text
src-tauri\target\release\bundle\nsis\Workbench_<ver>_x64-setup.exe
src-tauri\target\release\bundle\nsis\Workbench_<ver>_x64-setup.exe.sig
src-tauri\target\release\bundle\nsis\latest.json   # 传了 -UpdateBaseUrl 才有
```

### 3.2 版本规则（用户 2026-10-21 定稿）

- **唯一真源 = 仓库根目录的 `VERSION`**（单行 `major.minor.patch`）；**只走 `tools/bump.py`**，勿手改单一副本。
- **正式版本**（用户明确说"发布正式版本"）：`bump.py build` → **MINOR+1、PATCH 归零**
  （`0.4.1 → 0.5.0`），然后才构建安装包、tag、发布。
- **优化版本**（用户没有说正式版本）：`bump.py push` → **PATCH+1**
  （`0.4.1 → 0.4.2`）；只提交源码/构建，**不构建安装包、不发布**。
- **派生副本**（`bump.py check` 全部核对）：
  `pyproject.toml` + `workbench/__init__.py` + `uv.lock`（本项目条目）+
  `.version.json`（缓存）+ `src-tauri/tauri.conf.json` + `src-tauri/Cargo.toml`。
  > 2026-10-09：浏览器扩展的 `manifest.json` 与 `package.json` 副本已删除。
  > **2026-10-19**：Electron 退役，`desktop/package*.json` 不再是副本。
  > **2026-10-19（后续）**：真源从 `pyproject.toml` 改为 `VERSION`；所有副本由
  > `bump.py` 写入，CI 在 `uv sync` **之前**跑 `uv lock --check` 与 `bump.py check`。
- **锁文件顺序**：`uv lock`（或 `uv sync`）必须在 release commit **之前**；
  CI 在 `uv sync` **之前**跑 `uv lock --check`（放在后面会被 sync 静默修好，永远通过）。

### 3.3 发布到内部 HTTPS 静态源

Tauri updater 不再读 GitHub Releases。发布步骤：

1. 构建时带 `-UpdateBaseUrl https://<内部源>/workbench/<版本>` 生成 `latest.json`；
2. 先上传安装包与 `.sig`（路径不可变），最后原子替换 `latest.json`；
3. `latest.json` 的 `signature` 是 `.sig` **文件内容**，`url` 是安装包完整 HTTPS 地址；
4. 私钥只在 CI Secret / 本机 `%USERPROFILE%\.tauri\workbench.key`，**绝不入库**。

> ⚠️ 发布错清单会让所有客户端更新失败；先用 `tools/tauri_update_manifest.py` 做机械校验。
> ⚠️ Tauri 更新器签名与 Windows Authenticode 是两套，后者未配置时 SmartScreen 仍可能提示。

### 3.3.1 tag 与 CI（2026-09-17 起）

- **tag 在 release commit 推送后手工打**：`git tag -a v<版本> && git push origin v<版本>`。
  构建脚本不再自动提交/推送/tag；版本决策仍由 `bump.py` 负责。
  > **为什么必须打**：实测本仓**发布过 12 个版本却只有 3 个 tag**，导致 `git checkout v0.3.11` 直接报错——
  > 回滚与"用户跑的是哪一版"都没有锚点。tag 是"版本号 ↔ commit"的唯一连接。
- **CI 门禁**：`.github/workflows/ci.yml` 在 push/PR 上跑
  `uv sync → pytest → verify_packaging.py → ruff`（`windows-latest`）。
  > ⚠️ 改 CI 时别加 `paths-ignore`：被跳过的 workflow 在分支保护下会以 pending 卡住合并。
  > ⚠️ **改 `tools/build_tauri.ps1` 后必须补 UTF-8 BOM**（编辑会静默丢它，P5 立刻红）——
  > 处置命令见 [PITFALLS.md](docs/PITFALLS.md) 环境坑 #37。

### 3.4 构建前的秒级体检（必跑）

```powershell
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 7 项静态核对，当前 7/7 PASS
.\.venv\Scripts\python.exe tools\gen_tools_doc.py      # 工具清单/数字门禁，当前 PASS
```

它拦住的是「跑完 PyInstaller（数分钟）才暴露」的断点：spec 入口脚本缺失、入口用了相对导入、
Tauri 资源映射与 `dist/WorkbenchServer` 不一致、`build_tauri.ps1` 丢 UTF-8 BOM、
Tauri UI/图标缺失、**工具插件漏登记 `hiddenimports`（P7）**。

> ✅ **2026-09-17 起由 CI 自动调用**（`.github/workflows/ci.yml`）；
> **本机裸跑 `build_tauri.ps1` 时仍需手动跑一次**（CI 要等 push 之后才跑）。

### 3.4.1 工具清单门禁（改了 `plugins/` 必跑）

```powershell
.\.venv\Scripts\python.exe tools\gen_tools_doc.py --write   # 重生成 docs/TOOLS.md 的清单块
.\.venv\Scripts\python.exe tools\gen_tools_doc.py           # 核对：文档清单/数字 == 运行期中枢
```

`tests/test_tools_doc.py` 把它也钉进了 pytest，所以**忘了跑也会在测试里红**。
治理规则见 [docs/TOOLS.md](docs/TOOLS.md)：工具是产品面，加/删/改都要过确认门与文档两道。

### 3.5 构建链路的脚本纪律（PS 5.1）

- `.ps1` 必须 **UTF-8 BOM**（编辑后极易丢失，中文会乱码）；
  核对：`[System.IO.File]::ReadAllBytes(...)` 前 3 字节 == `239,187,191`。
- **禁止 `$ErrorActionPreference='Stop'`**：PS5.1 会把 git/uv 写 stderr 的正常进度当终止错误。
- 不支持 `??` 运算符；heredoc `<<` 不可用。
- **顺序敏感的一处**：`uv sync` 必须在 release commit **之前**（它会写 `uv.lock` 里的项目版本，
  反了则 commit 里的 lock 立刻过期）。

### 3.6 版本管理的三层（2026-10-19 合并自 `docs/VERSIONING.md`）

> 原 `docs/VERSIONING.md` 的 534 行已合并到这里；文件保留为指针（历史链接仍要能点开）。
> 下面只写**今天仍然成立**的规则，不再抄旧文里的 tag/Release 实测数字（它们已过期）。

| 层 | 是什么 | 现在怎么做 |
|---|---|---|
| **L1 版本号** | `0.4.1` 从哪来、写到哪 | `VERSION` 是唯一真源；`bump.py` 同步派生副本；`bump.py check` 机检 |
| **L2 发布事实** | 哪一版发过、tag 与产物是否对应 | release commit 推送后**手工打 tag**；`build_tauri.ps1` 只出产物，发布是另一步上传内部 HTTPS 静态源 |
| **L3 用户可见** | 用户能不能知道该升级 | 当前手动覆盖安装；Tauri updater 的检查/安装 UI 尚未接入 |

**L2 的两条纪律**

1. **tag = "我构建并标注了这一版"**：release commit 推送后手工 `git tag -a v<版本>`。
   它**不等于**"已对外发布"——安装包与 `latest.json` 由发布流程单独上传。
   → **不要**给没构建过的版本补 tag（那是假历史）。
2. **CI 不发布**：`.github/workflows/ci.yml` 只读不写、无 secret，避免"CI 误发一版"。

**L3 的当前口径**

更新源是**公司内部 HTTPS 静态源**，不要求仓库公开；安装包与 `.sig` 先上传，`latest.json`
最后原子替换。自动更新 UI 未接入前，用户按 [USER-MANUAL.md](docs/USER-MANUAL.md) §8 手动覆盖安装。

> ★ **不要在没有公开仓库时打开自动更新**：失败方式是**静默**的（更新器 404，界面不报错），
> 用户只会觉得"怎么一直没更新"。

**CHANGELOG 是机器接口**

`CHANGELOG.md` 顶部必须有且只有一行 `## [Unreleased]`；发布前把它改成 `## [<版本>] - <日期>`，
并在顶部补一行新的 `## [Unreleased]`。当前没有自动抽取脚本，发布说明由 `build_tauri.ps1 -Notes`
传给 `latest.json`。

**发版前的最小检查**

```powershell
.\.venv\Scripts\python.exe tools\bump.py check      # VERSION 与派生副本同步
.\.venv\Scripts\uv.exe lock --check                 # 锁文件与 pyproject 同步
.\.venv\Scripts\python.exe tools\verify_packaging.py
.\.venv\Scripts\python.exe tools\gen_tools_doc.py
.\.venv\Scripts\python.exe -m pytest -o addopts=""  # 含 slow 层
```

## 4. 代码规约

- **Lint 门禁**：`uvx ruff check .` 应全绿（E4/E7/E9/F/I/B，配置在 `pyproject.toml`）。
  fork 存量代码同样受约束，无豁免区。
- **类型注解**：新代码必须带完整注解 + `from __future__ import annotations`。
- **校验**：API 入参用 pydantic `BaseModel`；⚠️ `BaseModel` **默认忽略多余字段**——
  请求体必须**显式建模**每一个字段，否则字段静默丢失（`AccountPatch` 漏 `box` 的事故）。
- **异常**：服务层抛领域错误（`ApiError` / `AccountError`），路由层翻译为
  `HTTPException` + `raise ... from exc` 保留因果链。
- **文档字符串**：每个模块头部说明职责 + **知识来源**（fork 自谁 / 迁移自谁 / 实证出处）——
  这是本仓库最重要的可读性投资，新代码沿用。
- **数据库**：只走迁移（`db.py MIGRATIONS` 追加新版本号，**不改历史迁移**）。改表清单见
  [docs/SCHEMA.md](docs/SCHEMA.md) §6。

## 5. 开发流程与分工边界

1. **动代码前**先读 `docs/CODEBASE_OVERVIEW.md`（2026-10-19 精简重写：代码地图 / 约定 / 风险索引）与
   [docs/PITFALLS.md](docs/PITFALLS.md)（环境坑）、[docs/SCHEMA.md](docs/SCHEMA.md)（数据契约）；
2. 新路由挂到 `workbench/api/__init__.py::_optional_routers`（可选路由）或同级注册；
3. 新增/修改端点 → 同步 [docs/API.md](docs/API.md)；新增配置项 → 同步 [docs/CONFIG.md](docs/CONFIG.md)；
   架构级决策 → 新增 [docs/adr/](docs/adr/) 一篇，**用 [docs/adr/0000-template.md](docs/adr/0000-template.md)**；
4. 每完成一块：更新 `CHANGELOG.md` 的 `Unreleased` 段；
5. 提交信息建议 `阶段/模块: 动作`（如 `2B: 自动登录引擎节奏参数对齐上游`）。

| 区域 | 说明 |
|---|---|
| `workbench/api/*` + `workbench/services/*` | 主战场；同一文件避免两人同时改 |
| `workbench/harness/*` | fork 存量（对话 Agent 循环），改动最小化 |
| `src-tauri/*` | 桌面壳（Tauri 2 / Rust）；改完跑 `cargo check --manifest-path src-tauri\Cargo.toml` + `pytest tests/test_tauri_config.py -o addopts=""` |
| `adapters/*.json` 指向的原四项目目录 | **冻结**：任何情况下不写 |
| `docs/adr/*` | 改已采纳的决策 = 新增一篇取代它，不允许悄悄改历史 ADR。新增**必须用 [0000-template.md](docs/adr/0000-template.md)**，其中的 **§6「外部参照」与 §7「触发器」不得省略**（理由见下） |

### 5.1 为什么 ADR 模板多了「外部参照」一栏（2026-09-20 起）

模板比历史 ADR（0001–0015）多一栏 **§6 外部参照**：**谁解决过同一问题、他们怎么做、我们为何不同**。

它要解决的是一类具体的损失：**你写下决策时，脑子里一定权衡过若干做法，
但只有结论落了纸——半年后没人能重建那个权衡。** 而这两件事的价值完全不同：

| 对照的结果 | 含义 | 该怎么写 |
|---|---|---|
| **一致** | 行业共识，直接采纳即可 | 一句"我们跟随"就够，**不必论证** |
| **分歧** | 真权衡，**只能靠自己判断** | 必须写清"我们为何不同"——**这一栏的价值几乎全在这里** |

填写要求（写进模板里了，这里再强调一次）：

- **署名来源**：能给仓库/文档/标准链接的就给。给不出来源的写 **"未核实"**——
  **不要凭印象写**，那会变成一条没人敢质疑的假共识。
- **不要求逐条穷尽**：3–5 个真实对照胜过 20 个凑数的。
- **历史 ADR 的该栏可以后补**，但**必须显式标注"本栏为后补"**
  （参考 [ADR-0014](docs/adr/0007-credentials-sandbox-and-trust.md) §6 与
  [ADR-0004](docs/adr/0002-browser-allocation-policy.md) 末尾的写法）。
  **标注后补不是免责**，是让读者分清"决策当时的认知"与"后来的认知"。
- **本仓已有的对照成果**：外部项目的系统梳理在
  `docs/OPENSOURCE-LANDSCAPE.md`（**该文件已于 2026-09-29 删除**）（含 star 实测数据与许可红线）。
  **写新 ADR 前先翻一遍它**——很可能那个问题已经调研过了。

## 6. 已知状态（诚实记录，勿当绿灯）

| 项 | 现状 | 影响 |
|---|---|---|
| `tools/verify_packaging.py` | **7/7 PASS**（P1/P2/P5/P7/P9/P10/P11；Electron 时代的 P3/P6 已删）；**CI 自动调用** | 构建链路上的断点已由 CI 兜住；**本机裸跑 `build_tauri.ps1` 时仍需手动跑一次**（CI 要等 push 之后才跑） |
| 文档里的工具/插件数字 | **已上机检**：`tools/gen_tools_doc.py` + `tests/test_tools_doc.py` 核对 `docs/TOOLS.md` 与运行期中枢，并扫活跃文档里的"N 个工具/插件" | 2026-09-15 曾四处文档同时写旧数字且由用户先发现（[docs/TOOLS.md](docs/TOOLS.md) §7） |
| ~~`desktop/package-lock.json` 版本陈旧~~ | ✅ **2026-10-19 Electron 退役**：该文件与整个 `desktop/` 已删除 | 版本副本只剩 `VERSION` / pyproject / `__init__` / `uv.lock` / `.version.json` / Tauri 两处 |
| `tools/setup.ps1` §5 | Node 是 **INFO 信息项**（不计入失败），文案已改为「仅影响 Tauri CLI 构建与 Web UI E2E」 | ✅ 与当前 Tauri 交付形态一致 |
| `tests/test_proc.py`（7 项） | 仍在测 Node 子进程封装 | 它测的是**遗留封装本身**，不代表运行时链路 |
| ⚠️ **Tauri 自动更新 UI** | 签名/清单/构建链路已就绪，但“检查→提示→下载→安装”尚未接入 | 用户当前只能手动覆盖安装；详见 [TAURI-MIGRATION.md](docs/TAURI-MIGRATION.md) §4 |
| `adapters/*.json` 的 `$schema` | 原指向已删除的 `docs/模块契约.md#adapters` | ✅ 本轮已改为 `docs/SCHEMA.md`（仅此字段） |

> 完整清单（含代码级缺陷与安全风险）见 `docs/CODEBASE_OVERVIEW.md` 的风险索引与\n> `docs/AGENT-BENCHMARK.md` §3。

## 7. 验收纪律

```powershell
.\.venv\Scripts\python.exe -m pytest          # 快层（约 35 秒；默认跳过 slow）
.\.venv\Scripts\python.exe tools\gen_tools_doc.py   # 工具清单与数字门禁（PASS：4 插件 / 15 工具）
.\.venv\Scripts\uv.exe tool run ruff check .  # All checks passed（★ 先探一次，见 §1；PITFALLS #50）
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 7/7
```

Tauri 壳的端口是**动态的**（Rust `free_port()` 绑 0）；不再有固定 18765/18767 契约，
也没有 `verify_port_broker.mjs`。改壳启动/端口链路时跑：

```powershell
cargo check --manifest-path src-tauri\Cargo.toml
.\.venv\Scripts\python.exe -m pytest tests\test_tauri_config.py -o addopts="" -q
```
改动**洞察页产物预览（`static/js/artifact-preview.js` / markdown 渲染 / drawio 画布）**后必跑
`.\.venv\Scripts\python.exe tools\verify_insight_preview.py`（14 项，真浏览器断言渲染结果：表格列口径、引用块、drawio 节点与连线计数、多页切页、零 JS 错误）。它自起服务、用临时数据目录与专用端口（18791），**不需要先退出工作台**。
完整验收基线（含真机项与 E2E）见本文 §7；代码地图与当前风险索引见 `docs/CODEBASE_OVERVIEW.md`。

- 阶段验收以 `tests/` 机器验收为准，不靠肉眼；
- 真机验收（真实 eGMP 平台）只在测试环境账号上做；
- 测试发现问题：**先记根因，再修**——不修「看起来好了」。

## 8. 日常测试：三种运行模式（**以及"要不要编译"**）

> 这一节存在的原因很具体：**"开发态怎么起"从来没被写清楚过**，于是没人注意到
> 「桌面壳的开发态是直接 spawn `uvicorn`、不经过任何入口脚本」——
> 结果是**日常开发测试时日志里没有启动横幅**（2026-09-20 实测确认，已把横幅移到
> `api.lifespan` 修掉）。**没写下来的东西不会有人去验。**

### 8.1 三种模式对照

| 模式 | 命令 | 要编译吗 | 覆盖什么 | **不覆盖**什么 |
|---|---|---|---|---|
| **① Web 模式** | `.\.venv\Scripts\python.exe -m workbench.main` | **不要** | 全部页面与业务功能（对话/洞察/账号/沙箱/Skill）；自动开系统浏览器 | Tauri 壳（窗口/单实例/外链/进程清理/更新器） |
| **② Tauri 开发态** | `npx --yes @tauri-apps/cli@2 dev` | **第一次要**（Rust 编译；之后增量） | **完整桌面应用**：真窗口 + 单实例 + sidecar 生命周期 + 外链 | 打包特有行为（见 8.3） |
| **③ 打包安装版** | `tools\build_tauri.ps1` → 跑 NSIS 安装包 | **要**（数分钟） | 全部，含 PyInstaller 冻结态、Tauri 资源映射、NSIS 安装/卸载 | 调试便利性（改一行要重打） |

**结论：日常业务开发用 ①；验桌面壳用 ②。**
`.venv` 与 `src-tauri\target` 就绪即可；Tauri 开发态会自己拉起 `.venv` 里的 sidecar。

### 8.2 什么时候**必须**编译

| 你改了什么 | 要跑的 | 说明 |
|---|---|---|
| 只改 Python / 静态 UI | 重启应用 | Python 无构建步骤；`static/**` 刷新窗口即可 |
| 改 Rust / `tauri.conf.json` / 打包 | `tools\build_tauri.ps1` | 见 8.3 |
| 只验 Rust 类型 | `cargo check --manifest-path src-tauri\Cargo.toml` | 不生成安装包 |

### 8.3 只有**打包态**才验得到的（改完必须打一次包）

| 项 | 为什么开发态验不到 |
|---|---|
| **PyInstaller 漏收插件**（PITFALLS #32） | 动态导入 `importlib.import_module` 静态分析看不见——开发态导入得到，装完**静默少功能** |
| **`tauri-sidecar.log`** | 由 Tauri 壳重定向写入；开发态/测试脚本都不起壳 |
| **冻结态的日志落盘** | `config.FROZEN` 只影响默认格式（JSONL）；`DATA_DIR` 可写性在安装目录环境下才见真章 |
| **Tauri 资源映射 / sidecar 路径** | 只有 NSIS 安装后才验证得到；P10 只做静态核对 |
| **NSIS 安装/升级/卸载**、`latest.json` / `.sig` | 只有产物才有 |
| **壳的异常生命周期**（强杀 / 崩溃 → sidecar 是否成孤儿） | 需要真实进程树；但**已有自动化**：`pytest tests/test_lifecycle.py -o addopts="" -m slow` 真起父子进程、对父 `taskkill /F`（不带 `/T`）、断言 sidecar 自退 |

> **窗口/进程行为速查**：
>
> | 动作 | 应用 | sidecar |
> |---|---|---|
> | 最小化 | 继续运行 | 继续运行 |
> | **关闭窗口（X）** | **退出** | **一并被杀**（`taskkill /T` + Python 父监视） |
> | 强杀 / 崩溃 | 进程消失 | **自行退出**（`workbench/lifecycle.py` 等父句柄） |

### 8.4 日常配方（建议照抄）

```powershell
# ① 一次性准备（本机已完成）
uv sync --extra dev --extra build
# Tauri CLI 由 npx 按需拉取；第一次 dev/build 会编译 Rust

# ② 每次开工前：确认基线是绿的（约 35 秒）
.\.venv\Scripts\python.exe -m pytest -o addopts=""      # 全量（含 slow，约 48 秒）
.\.venv\Scripts\uv.exe tool run ruff check .            # ★ 先探一次，见 §1

# ②b 改了生命周期/壳的启动链路时，补跑慢层（真起进程，约 14 秒）
.\.venv\Scripts\python.exe -m pytest -o addopts="" -m slow

# ③ 打开完整应用来测（Tauri；首次会编译 Rust）
npx --yes @tauri-apps/cli@2 dev
#    只想快速看页面、不想开桌面壳时：
#    .\.venv\Scripts\python.exe -m workbench.main

# ④ 测试期间排障：日志是完整可查的
#    %APPDATA%\Workbench\logs\workbench-<日期>.jsonl
#    Tauri 打包态还多一份 %APPDATA%\Workbench\logs\tauri-sidecar.log
#    Web 模式（固定 18765）：
curl.exe -s "http://127.0.0.1:18765/api/logs/tail?level=WARNING&n=50"
#    界面响应头里的 X-Trace-Id → 还原整条链路
curl.exe -s "http://127.0.0.1:18765/api/logs/trace/<traceId>"

# ⑤ 改完代码：重跑 ②；动了日志/确认门/敏感表再跑这几条
.\.venv\Scripts\python.exe tools\verify_logging.py           # 36/36（自起服务，不必退出工作台）
# 确认门：2026-10-19 起产品暂无 WRITE 工具，端到端 verify_confirmation.py 已退役；
# 机制由下面两条（合成 WRITE 探针）覆盖，不依赖真实浏览器工具。
.\.venv\Scripts\python.exe -m pytest tests\test_user_confirmation.py tests\test_agent_approval.py -o addopts="" -q
.\.venv\Scripts\python.exe tools\verify_regression_tests.py  # 14/14 反证

# ⑥ 发版前：打一次包，走 §8.3 清单
powershell -ExecutionPolicy Bypass -File tools\build_tauri.ps1 `
  -UpdateBaseUrl https://updates.example.com/workbench/<版本>
```

> ⚠️ **端口策略已分叉**：
> - Web 模式仍用 `127.0.0.1:18765`；
> - Tauri 壳用**动态端口**（Rust `free_port()` 绑 0），不抢 18765/18767。
> 两种模式不要同时指向同一个 `WORKBENCH_DATA`（SQLite 会互相干扰）；
> 起不来时先看 `Get-NetTCPConnection` 与 `%APPDATA%\Workbench\logs\tauri-sidecar.log`。
> 典型症状仍是"改了代码界面没变"——**先怀疑跑的是旧进程**（PITFALLS #20 / #25）。
