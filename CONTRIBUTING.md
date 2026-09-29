# 开发协作规范（CONTRIBUTING）

> 目标：两人协作时不互相踩脚，且构建/发布链路不出静默错误。
> 原则先于工具——**先契约后实现，先复现后修复**。
>
> 使用者请看 [README.md](README.md) 与 [docs/USER-MANUAL.md](docs/USER-MANUAL.md)；
> 接手维护请先读 `docs/CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**）（代码地图 / 红线 / 风险台账）
> 与 [docs/PITFALLS.md](docs/PITFALLS.md)（环境坑 / 排障速查）。
>
> **用 AI 助手改这个仓库时**：把 **[AGENTS.md](AGENTS.md)** 交给它（多数工具会自动读取根目录的该文件）。
> 那份文件是**同一套规则的"规则形状"版本**——本文档写给能容忍模糊的人，
> 而 AI 会把"没写下来的约定"当成"不存在这个约定"。两份内容不重复：这里讲**为什么与怎么做**，
> 那里只讲**做与不做**（外加每条的判据与出处）。

## 1. 环境搭建（新同事 5 分钟）

```powershell
# 前置：Python 3.12+、Git、Node 20+（构建桌面壳/扩展时要）；uv 没有就先装（pip install uv 或 winget install astral-sh.uv）
uv sync --extra dev --extra build   # 严格按 uv.lock 拉齐依赖（含国内镜像，无需自己配源）
uv run playwright install chromium  # 托管浏览器内核（账号库/自动登录/监听需要）
powershell -ExecutionPolicy Bypass -File tools\setup.ps1   # 环境自检（兼容 PS 5.1/7）
uv run pytest                       # 382 项全绿即环境就绪
cd desktop; npm install; cd ..       # 桌面壳（Electron）依赖
cd extensions\quick-login; npm install; npm run build; cd ..\..   # 扩展构建产物 dist/
```

日常一切 Python 命令走 `uv run <cmd>`（自动使用锁定环境），**禁止**直接 `pip install` 后不落
`pyproject.toml`。

> **`--extra build` 不能省**：`uv sync` 不带它会把 PyInstaller 移出环境，
> 之后 `tools/build.ps1` 的第 5 步（PyInstaller）直接失败。`build.ps1` 内部已带这两个 extra。
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
> **跑出来的结论可以不同**——CI 的 runner 没有 chromium，`tests/test_browser_parallel.py`
> 的 4 项 P0 浏览器验收在那里是 **skip**，本机是 **pass**。CHANGELOG 里"CI 从建立那天起
> 就没绿过"那起事故，就是被这个差集掩盖了**九次运行**。
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
> 主力 `.venv`（3.14.6，加 `-o addopts=""` 含 slow 层）→ **501 passed, 0 skipped**。
> 差的正是那 8 项浏览器验收（slow 层由 CI 单独一步显式跑）。
>
> ⚠️ **那 8 项跳过的根因是 Playwright 版本，不是解释器版本**——`.venv312` 里 `uv pip install -e .`
> 会解出**更新的 playwright**（实测它找 `chromium-1243`），而本机 `%LOCALAPPDATA%\ms-playwright`
> 里只有主力环境那版（`chromium-1234`）→ 体检如实报"chromium 未安装"、8 项 skip。
> 这也**恰好复刻了 CI runner**（那里一份浏览器都没有，同样 8 项 skip）。
> **要在这套环境里也真跑浏览器那 8 项**，补一句即可（下载约 150MB）：
>
> ```powershell
> .\.venv312\Scripts\python.exe -m playwright install chromium
> ```
>
> 结果与主力环境一致（`0 skipped`）就说明**没有环境相关的差异**；若两边都是绿但 skip 数不同，
> 请把 skip 行当结论的一部分读——**这才是 `-rs` 的意义**。
>
> `.venv312/` 已在 `.gitignore` 中忽略。**3.14 是合法的开发环境**（`requires-python` 允许），
> 本仓不禁止它；被禁止的是"两边版本不同步却以为结论一样"。

>
> **ruff 现已全绿**（`uvx ruff check .` → All checks passed；`tools/*.py` 的 E402 是刻意豁免的引导式导入，
> 见 `pyproject.toml` 的 `per-file-ignores`）。**验收基线是 493 项 pytest**（默认跳过 slow 层）+ **8 项 slow**
> = **501 项**（`-o addopts=""` 一次跑全；2026-09-17 实测）——不是下面模板里的旧数字。
> ⚠️ **`uv tool run ruff` 要联网取 ruff**：PyPI 直连 TLS 失败时（本机实测过
> `tls handshake eof`）改用清华镜像——`uv tool run --index https://pypi.tuna.tsinghua.edu.cn/simple ruff check .`
> （`pyproject.toml` 里那条镜像只对**项目依赖**生效，`uv tool run` 取的是工具，不吃它）。
> ⚠️ **改了代码就必须重跑**：别把"上次的绿"当成"这次的绿"（[PITFALLS](docs/PITFALLS.md) #41 就是这么栽的：
> 测试绿了、ruff 没重跑，而那一行恰好是 ruff 能抓的 `F821`）。

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
产物：`desktop\dist\Workbench-<ver>-setup.exe` + `latest.yml`。

### 3.2 版本规则（方案 A，用户定稿）

- 真源 = `pyproject.toml` 的 `version`；**只走 `tools/bump.py`**，勿手改单一文件。
- `bump.py push` = PATCH+1；`bump.py build` = MINOR+1 且 PATCH 重置 1（`0.0.2 → 0.1.1 → 0.2.1`）。
- **五写同步**：`pyproject.toml` + `workbench/__init__.py` + `desktop/package.json`
  + 扩展 `manifest.json` + 扩展模块 `package.json`（+ `.version.json` 缓存）。
- 桌面端与扩展**同号发布**：`extStale` 判定（「扩展是否需要重新加载」）建立在此约定上。
- ⚠️ **`desktop/package-lock.json` 的 version 字段是陈旧的**（实测 `0.2.1`，而 `package.json` 已是
  `0.3.5`），`bump.py` 不写它。**已知问题**——`npm install` 会静默改写它，让发布 commit 后工作区立刻变脏。
  （扩展工作区 `package-lock.json` 曾被记为陈旧，**2026-09-13 实测已是 `0.3.5`，该说法不再成立**。）

### 3.3 发布到 GitHub Releases

`build.ps1` 用 `--publish never` 只出产物。发布走 `tools/publish_release.py`（推荐，带三道自查：
`latest.yml` 版本号 == 发布版本、其 `sha512` == 安装包实际 sha512、指向本次安装包），
或网页手工上传 `setup.exe + latest.yml`（+ blockmap）。完整步骤与 `GH_TOKEN` 获取路径见
`docs/EXTENSION-INSTALL.md`（**该文件已于 2026-09-29 删除**） 第四节。

> ⚠️ 更新器**匿名**读 Release ⇒ 仓库必须公开。
> ⚠️ 发布错清单会让**所有客户端**更新失败——所以那三道自查不要跳过。
> ⚠️ **2026-09-17 实测该前提当前不成立**：匿名探测 `api.github.com/repos/jizi-dragon/akso-mogul` 返回 **404**
> （同 owner 的 `quick-login` / `akso-cc` / `mogul_simulator` 均 200 = 公开）。
> 即：**开启自动更新（`WORKBENCH_AUTO_UPDATE=1`）并指向 GitHub 时，更新链路会静默失效**。
> 三个可选处置见 [docs/VERSIONING.md](docs/VERSIONING.md) §3.1。

### 3.3.1 tag 与 CI（2026-09-17 起）

- **tag 由 `build.ps1` 自动打**：release commit 推送成功后执行 `git tag -a v<版本>` 并推送。
  四条守卫（`-Bump none` / commit 未产生 / commit 未推上去 / tag 已存在）都不会打 tag。
  > **为什么必须打**：实测本仓**发布过 12 个版本却只有 3 个 tag**，导致 `git checkout v0.3.11` 直接报错——
  > 回滚与"用户跑的是哪一版"都没有锚点。tag 是"版本号 ↔ commit"的唯一连接。
- **CI 门禁**：`.github/workflows/ci.yml` 在 push/PR 上跑
  `扩展构建 → uv sync → pytest → verify_packaging.py → ruff`（`windows-latest`）。
  **它是 `verify_packaging.py` 的第一个自动调用方**（此前该工具无人调用，见 §6）。
  > ⚠️ 改 CI 时别加 `paths-ignore`：被跳过的 workflow 在分支保护下会以 pending 卡住合并。
  > ⚠️ **改 `tools/build.ps1` 后必须补 UTF-8 BOM**（编辑会静默丢它，P5 立刻掉到 8/9）——
  > 处置命令见 [PITFALLS.md](docs/PITFALLS.md) 环境坑 #37。

### 3.4 构建前的秒级体检（必跑）

```powershell
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 9 项静态核对，当前 9/9 PASS
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 9 项静态核对，当前 9/9 PASS
.\.venv\Scripts\python.exe tools\gen_tools_doc.py      # 工具清单/数字门禁，当前 PASS
```

它拦住的是「跑完 PyInstaller（数分钟）才暴露」的断点：spec 入口脚本缺失、入口用了相对导入、
壳 spawn 路径与 `extraResources` 不一致、扩展产物缺失/版本不符、`build.ps1` 丢 UTF-8 BOM、
壳新增 `.js` 忘了进 electron-builder 的 `files` 白名单、**工具插件漏登记 `hiddenimports`（P7）**。

> ⚠️ **`verify_packaging.py` 目前没有被 `build.ps1` 自动调用**（已知问题，见 §6）——请手动跑。
> ✅ **2026-09-17 起由 CI 自动调用**（`.github/workflows/ci.yml`）：漏跑的代价从"潜伏到发版"
> 降为"push 后 CI 报红"；**本机裸跑 `build.ps1` 时仍需手动跑一次**（CI 要等 push 之后才跑）。
> ⚠️ **P4b 红了先看"扩展是不是没重建"**：它比对的是**构建产物** `extensions/quick-login/dist/manifest.json`
> 与项目版本；`git pull` 之后、`npm run build` 之前必然红。2026-09-16 实测踩过；
> 2026-09-17 在写 CI 时又确认一次——**CI 必须在 pytest 之前 `npm run build`**，
> 否则全新 checkout 里 `dist/` 根本不存在 → P4b/P4c 必然红。
> 这条恰好证明 §3.5 那条"扩展重建必须在 bump 之后"不是理论洁癖：**手工漏一次，门禁就是唯一防线。**
> ⚠️ **改了扩展源码（TS）必须重建 + 重载**：`cd extensions\quick-login; npm run build`，
> 并在 `chrome://extensions` 点一次「重新加载」（`tools/verify_extension_sync.mjs` 需要服务在 18765 上跑）。

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

1. **动代码前**先读 `docs/CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**）（红线与约定）与
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
| `desktop/*`、`extensions/quick-login/*` | 壳与扩展；改扩展必跑 §7 的三个回归工具 |
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
  （参考 [ADR-0014](docs/adr/0014-sandbox-for-untrusted-code.md) §6 与
  [ADR-0004](docs/adr/0004-extension-as-execution-plane.md) 末尾的写法）。
  **标注后补不是免责**，是让读者分清"决策当时的认知"与"后来的认知"。
- **本仓已有的对照成果**：外部项目的系统梳理在
  `docs/OPENSOURCE-LANDSCAPE.md`（**该文件已于 2026-09-29 删除**）（含 star 实测数据与许可红线）。
  **写新 ADR 前先翻一遍它**——很可能那个问题已经调研过了。

## 6. 已知状态（诚实记录，勿当绿灯）

| 项 | 现状 | 影响 |
|---|---|---|
| `tools/verify_packaging.py` | **9/9 PASS**（2026-09-16 新增 P7 插件登记对账）；**2026-09-17 起由 CI 自动调用**（`.github/workflows/ci.yml`），但 `build.ps1` 仍**不**调用它 | 构建链路上的断点已由 CI 兜住；**本机裸跑 `build.ps1` 时仍需手动跑一次**（CI 要等 push 之后才跑） |
| 文档里的工具/插件数字 | **已上机检**：`tools/gen_tools_doc.py` + `tests/test_tools_doc.py` 核对 `docs/TOOLS.md` 与运行期中枢，并扫活跃文档里的"N 个工具/插件" | 2026-09-15 曾四处文档同时写旧数字且由用户先发现（[docs/TOOLS.md](docs/TOOLS.md) §7） |
| `desktop/package-lock.json` | version `0.2.1`（`package.json` 已 `0.3.12`）——`bump.py` 五写不含它 | `npm install` 会改写它，发布后工作区变脏；安装包命名不受影响（electron-builder 读 `package.json`） |
| ~~`extensions/quick-login/package-lock.json`~~ | ~~version `0.2.20`~~ → **2026-09-13 实测已是 `0.3.5`，本条已失效** | 同上 |
| ~~`tools/setup.ps1` §5~~ | ~~仍把 **Node** 当必检项并说「洞察/工厂功能不可用」~~ → **2026-09-14 已改为 `INFO` 信息项**（不计入失败），文案改为「仅影响 desktop/ 与 extensions/ 的构建（运行时不需要）」 | ✅ 口径已对齐 [ADR-0005](docs/adr/0005-native-internalization-of-four-projects.md) |
| `tests/test_proc.py`（7 项） | 仍在测 Node 子进程封装 | 它测的是**遗留封装本身**，不代表运行时链路 |
| ⚠️ **仓库可见性** | **2026-09-17 匿名探测：`api.github.com/repos/jizi-dragon/akso-mogul` → 404**（同一 owner 的 `quick-login` / `akso-cc` / `mogul_simulator` 均 200 = 公开）。而 §3.3 与 `publish_release.py` 都要求**仓库公开** | **一旦开启自动更新（`WORKBENCH_AUTO_UPDATE=1`）并指向 GitHub，更新链路会失效**（更新器匿名读 Release）。详见 [docs/VERSIONING.md](docs/VERSIONING.md) §3 |
| `adapters/*.json` 的 `$schema` | 原指向已删除的 `docs/模块契约.md#adapters` | ✅ 本轮已改为 `docs/SCHEMA.md`（仅此字段） |

> 完整清单（含代码级缺陷与安全风险）见 `docs/CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**） 的风险台账。

## 7. 验收纪律

```powershell
.\.venv\Scripts\python.exe -m pytest          # 382 项（2026-09-17 合并后实测全绿；含 test_plugin_api/test_cache/test_agent_loop/test_tools_doc）
.\.venv\Scripts\python.exe tools\gen_tools_doc.py   # 工具清单与数字门禁（PASS：5 插件 / 20 工具）
node tools\verify_port_broker.mjs             # 固定端口 + 占用判定（离线，不需要 Electron；**须先退出工作台**——它靠真占固定端口验判定）
.\.venv\Scripts\ruff.exe check .               # All checks passed（★ 先探一次，见 §1；PITFALLS #50）
.\.venv\Scripts\python.exe tools\verify_packaging.py   # 9/9
```

> **`git pull` 之后先重建扩展再跑体检**：`extensions/quick-login/dist/` 是构建产物，
> 拉取只更新源码 → `verify_packaging.py` 的 P4b（扩展 manifest 版本 == 项目版本）必红，
> 那反映的是本地构建残留，不是上游坏了。`cd extensions\quick-login; npm run build` 即消。

改动扩展后**额外必跑**（扩展端无单测框架，这三个就是它的回归测试）：

```powershell
.\.venv\Scripts\python.exe tools\verify_extension_boot.py        # 启动冒烟 7/7
.\.venv\Scripts\python.exe tools\verify_extension_isolation.py   # Cookie 隔离 + 多 iframe 填表 8/8
node tools\verify_host_logic.mjs                                 # host/端口口径 22/22
```

改动 `/extension/*` 蓝图后必跑 `node tools\verify_extension_sync.mjs`（**该蓝图无 pytest 覆盖**）。
改动**端口 / 壳启动链路 / 扩展的桌面地址**后必跑 `node tools\verify_port_broker.mjs`（离线 27 项，不需要 Electron；**先退出工作台**，否则固定端口被占，脚本的前置检查会拦下并提示）。**端口是固定常量、不顺延**（[ADR-0012](docs/adr/0012-fixed-ports.md)）——改端口号属于破坏性变更，必须同步壳侧 `desktop/ports.js::PORTS`、Python 侧 `workbench/config.py` 与 `tests/test_ports_contract.py`。
改动**洞察页产物预览（`static/js/artifact-preview.js` / markdown 渲染 / drawio 画布）**后必跑
`.\.venv\Scripts\python.exe tools\verify_insight_preview.py`（14 项，真浏览器断言渲染结果：表格列口径、引用块、drawio 节点与连线计数、多页切页、零 JS 错误）。它自起服务、用临时数据目录与专用端口（18791），**不需要先退出工作台**。
完整验收基线（含真机项与 E2E）见本文 §7 与 `docs/CODEBASE_OVERVIEW.md`（**该文件已于 2026-09-29 删除**） 的「验收基线实测」。

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
| **① Web 模式** | `.\.venv\Scripts\python.exe -m workbench.main` | **不要** | 全部页面与业务功能（对话/洞察/账号/沙箱/Skill）；自动开浏览器 | Electron 壳的一切（托盘/热键/单实例/更新器）；`WORKBENCH_SESSION_MODE=cdp`（壳才下发，Web 模式跑 `local` 的 Playwright chromium）；扩展的桌面地址发现（回退固定 18765，能用） |
| **② 桌面壳开发态** | `cd desktop; npm start` | **不要** | **完整桌面应用**：真窗口 + 托盘 + 热键 + sidecar 生命周期 + CDP 会话模式 + 扩展协作面 | 打包特有行为（见 8.3） |
| **③ 打包安装版** | `powershell -File tools\build.ps1` → 跑 NSIS 安装包 | **要**（数分钟） | 全部，含 PyInstaller 冻结态、chromium 打包、electron-builder `files` 白名单、NSIS 安装/卸载 | 调试便利性（改一行要重打） |

**结论：日常测试打开应用页面，①②都不需要编译。**
`desktop/node_modules` 与 `.venv` 就绪即可（本机已就绪）。

### 8.2 什么时候**必须**编译

只有三件事需要"构建步骤"，且**都不是"编译应用"**：

| 你改了什么 | 要跑的 | 说明 |
|---|---|---|
| `extensions/quick-login/**`（TS） | `cd extensions\quick-login; npm run build` | 产 `extensions/quick-login/dist/`。**这不是编译桌面应用**，只是把扩展打成 Chrome 能加载的产物 |
| 要让 `verify_packaging.py` 的 P4b/P4c 转绿 | 同上 | 它们比对 `dist/manifest.json` 的版本 == 项目版本；`git pull` 之后、build 之前**必然红**（那是本地残留，不是上游坏了） |
| 要验打包特有行为 | `tools\build.ps1` | 见 8.3 |

> **开发态跑应用时，扩展 `dist/` 不是必需的**——壳只在「安装引导」里用它展示路径
> （`main.js::extensionDir()` 开发态取 `extensions/quick-login/dist`）。
> 要测**快捷登录执行面**才需要：先在 Chrome 装一次 `dist/`，改完重建后到
> `chrome://extensions` 点「重新加载」（Chrome 会缓存 SW 脚本，不重载等于没改）。

### 8.3 只有**打包态**才验得到的（改完必须打一次包）

这些是"开发态永远绿、装出来才炸"的类别，改动相关代码后要真打一次包：

| 项 | 为什么开发态验不到 |
|---|---|
| **PyInstaller 漏收插件**（PITFALLS #32） | 动态导入 `importlib.import_module` 静态分析看不见——开发态导入得到，装完**静默少功能** |
| **`sidecar-stdio-*.log`**（壳抓的进程原始输出） | 由 Electron 主进程写，开发态/测试脚本都不起壳 |
| **冻结态的日志落盘** | `config.FROZEN` 只影响默认格式（JSONL）；`DATA_DIR` 可写性在安装目录环境下才见真章 |
| **托盘图标 / `desktop/package.json` 的 `files` 白名单**（B-g） | P6 只 glob `*.js`，`icon.ico` 这类资源无人守 |
| **chromium 是否真打进包** | `server.spec` 的守卫只在打包时触发（P8 / P8b） |
| **NSIS 安装/升级/卸载**、`latest.yml` | 只有产物才有 |
| **壳的异常生命周期**（强杀 / 崩溃 → sidecar 是否成孤儿） | 需要真实进程树；但**已有自动化**：`pytest tests/test_lifecycle.py -o addopts="" -m slow` 真起父子进程、对父 `taskkill /F`（不带 `/T`）、断言 sidecar 自退 |

> **窗口/进程行为速查**（用户定稿 2026-09-20，PITFALLS #55）：
>
> | 动作 | 应用 | sidecar |
> |---|---|---|
> | 最小化 | 继续运行（托盘「显示主窗」可捞回，已补 `restore()`） | 继续运行 |
> | **关闭窗口（X / 菜单）** | **退出** | **一并被杀**（`taskkill /T`） |
> | 托盘/应用菜单「退出」 | 退出 | 一并被杀 |
> | 强杀 / 崩溃 | 进程消失 | **自行退出**（`workbench/lifecycle.py` 等父句柄，2026-09-20 起） |

### 8.4 日常配方（建议照抄）

```powershell
# ① 一次性准备（本机已完成）
cd desktop; npm ci; cd ..
cd extensions\quick-login; npm install; npm run build; cd ..\..

# ② 每次开工前：确认基线是绿的（约 40 秒）
.\.venv\Scripts\python.exe -m pytest -o addopts=""      # 596 passed
.\.venv\Scripts\ruff.exe check .                        # ★ 先探一次，见 §1

# ②b 改了生命周期/壳的启动链路时，补跑慢层（真起进程，约 10–30 秒）
.\.venv\Scripts\python.exe -m pytest -o addopts="" -m slow

# ③ 打开完整应用来测（零编译）
cd desktop; npm start
#    只想快速看页面、不想开 Electron 时：
#    .\.venv\Scripts\python.exe -m workbench.main

# ④ 测试期间排障：日志是完整可查的
#    %APPDATA%\Workbench\logs\workbench-<日期>.jsonl
curl.exe -s "http://127.0.0.1:18765/api/logs/tail?level=WARNING&n=50"
#    界面响应头里的 X-Trace-Id → 还原整条链路
curl.exe -s "http://127.0.0.1:18765/api/logs/trace/<traceId>"

# ⑤ 改完代码：重跑 ②；动了日志/确认门/敏感表再跑这三条
.\.venv\Scripts\python.exe tools\verify_logging.py           # 36/36（自起服务，不必退出工作台）
.\.venv\Scripts\python.exe tools\verify_confirmation.py      # 40/40（同上，端口 18795；2026-09-22 实测 40/40）
.\.venv\Scripts\python.exe tools\verify_regression_tests.py  # 14/14 反证

# ⑥ 发版前：打一次包，走 §8.3 清单
powershell -ExecutionPolicy Bypass -File tools\build.ps1
```

> ⚠️ **端口是固定常量、不顺延**（[ADR-0012](docs/adr/0012-fixed-ports.md)）：
> `npm start` 与已打开的打包版**不能同时跑**（都抢 18765），会直接报错退出并说清占用者是谁。
> 起不来时先 `Get-NetTCPConnection -LocalPort 18765 -State Listen` 看是谁占着
> （PITFALLS #20 / #25：那种情况下的典型症状是"改了代码界面没变"——**先怀疑跑的是旧进程**）。
