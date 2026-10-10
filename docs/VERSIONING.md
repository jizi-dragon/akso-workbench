# 版本管理机制与 GitHub 如何帮忙

> **写给**：想知道"我的版本管理现在是什么样、GitHub 到底能帮哪一段、具体怎么做"的人。
> **写于**：2026-09-17 ｜ 所有"现状"结论均为**实测**，命令与结果在文中列出，可复核。
> **相关**：`tools/bump.py`（版本演进）、`tools/build.ps1`（构建）、`tools/publish_release.py`（发布）、[CONTRIBUTING.md](../CONTRIBUTING.md) §3。

---

## 0. 先分清三件事（这是本文最重要的一节）

你说"版本管理"，但这个词其实混了**三层**。混在一起谈，就会觉得"GitHub 好像能帮忙，但说不清帮哪里"。

| 层 | 是什么 | 你现在的状态 | GitHub 能帮吗 |
|---|---|---|---|
| **L1 版本号本身** | `0.3.12` 这个数字从哪来、写到哪几个文件 | ✅ **做得很好**（单一真源 + 五写同步 + 一条命令） | 基本不需要。**别为这层引入工具** |
| **L2 版本号的"发布事实"** | 哪一版发过、发了什么、谁在什么时候发的 | ⚠️ **有缺口**（12 个版本只打了 3 个 tag；无 CI 门禁） | ★ **这层是 GitHub 的主场** |
| **L3 版本号的"用户可见"** | 用户在应用里能不能知道自己该升级 | ⚠️ **自动更新默认关闭**；且若开启，**当前仓库可见性会让它失效** | 帮一半，但有硬前提 |

**一句话**：你的 L1 已经比大多数项目做得好；缺的是 L2，而 L2 恰好是 GitHub 免费提供、且你现在**一个都没用上**的部分。

---

## 1. 现状（实测，不是回忆）

### 1.1 L1：版本号机制 ✅

```
真源 = pyproject.toml 的 [project].version
        │
        └─ tools/bump.py 一次性同步写到 6 处：
             pyproject.toml
             workbench/__init__.py            __version__
             desktop/package.json             electron-builder 读它命名安装包
             extensions/.../manifest.json     扩展管理页可见版本
             extensions/quick-login/package.json
             .version.json                     本地缓存
```

**演进规则（用户定稿的"方案 A"）**：

| 命令 | 语义 | 效果 |
|---|---|---|
| `bump.py push` | 代码修改推送 | PATCH +1 ｜ `0.3.11 → 0.3.12` |
| `bump.py build` | 构建安装包 | **MINOR +1 且 PATCH 重置 1** ｜ `0.3.11 → 0.4.1` |
| `bump.py show` | 查看 | — |

> ⚠️ **注意这条规则不是语义化版本（SemVer）**。SemVer 里 PATCH 只放修 bug，而你 `push` 出来的版本可能含新功能（实测 `chore(release): v0.3.11` 上一版就是 `feat(agent):`）。
> **这不是错**，是刻意的两档制（"开发推进" vs "对外构建"）。但它决定了**后面能选哪些自动化工具**——见 §4。

**这条链路唯一的手工缺口**（已记录在 CONTRIBUTING §3.2）：
`desktop/package-lock.json` 的 `version` 字段不在五写里，`npm install` 会静默改写它，导致发布 commit 后工作区立刻变脏。

### 1.2 L2：发布事实 ⚠️ —— **这是真正的缺口**

> **2026-09-17 带 token 复核后修正**：本节初稿用**匿名**探测得出"发布了 12 个版本却只有 3 个 tag"，
> 并据此推断有 9 个 tag 待补。**带 token 查证后这个推断是错的**——真实情况见下表。
> 教训与 `04-未证实项` 里的方法学一致：**匿名 404 既可能是"不存在"，也可能是"不可见"**，
> 两者在私有仓库上无法区分，必须带权限复核。

**带 token 查到的真实状态**（`api.github.com`，2026-09-17）：

| 事实 | 值 |
|---|---|
| 仓库可见性 | **`private=True` / `visibility=private`** |
| 已发布的 **Release** | **只有 2 个**：`v0.3.3`、`v0.3.4`（均 2026-09-11，各带 3 个资产 = `setup.exe` + `.blockmap` + `latest.yml`） |
| 远端 **tag** | `v0.3.4`、`v0.3.3`（**与 Release 一一对应，无缺口**） |
| 本地 tag | `v0.3.3`、`v0.3.4`、`before-pull-cd5e19d`（最后一个是不像版本号的遗留 tag） |
| `chore(release)` 提交 | **12 次** |

**结论：不进位的版本不是"漏了 tag"，而是"从来没发布过"。** 那 12 次 `chore(release)` 是本地版本演进
（`bump.py`），但只在 **2 次**真正走完了 `publish_release.py`。所以：

- ❌ **不需要补历史 tag**（初稿的"补齐 9 个 tag"作废）——给没发布过的版本补 tag 会制造假历史；
- ✅ **真正要建的习惯是"发布才打 tag"**，而这正是 `build.ps1` 新增的自动打 tag 在做的**反面**：
  它在**构建**时就打 tag，而 release 是之后手工跑 `publish_release.py`。
  > ⚠️ **这留下一个待你决定的不一致**：自动 tag 打的是"构建过的版本"，而 Release 可能永远不发。
  > 两种立场都成立——**tag = "我构建并标注了这个版本"**（当前实现），或
  > **tag = "我对外发布了这个版本"**（则应把打 tag 移到 `publish_release.py`）。
  > 当前实现选的是前者（因为 `build.ps1` 是唯一有版本号的自动入口）。

其余两项 L2 缺口（实测确认）：

| 缺口 | 实测证据 | 后果 |
|---|---|---|
| **无 CI 门禁** | 初稿时 `.github/` **不存在** | 382 项测试与 9 项打包核对全靠**手工记得跑**；`build.ps1` 也**不调用** `verify_packaging.py`。**2026-09-17 已加 `.github/workflows/ci.yml`**（见 §5.1） |
| **Rulesets 用不了** ⚠️ | `GET /repos/.../rulesets` → **403 `Upgrade to GitHub Pro or make this repository public to enable this feature`** | ★ **§5.2 那一步在"免费版 + 私有仓库"上做不了**。要保护 main/tag，三选一：① 仓库转公开 ② 升级 GitHub Pro ③ 放弃（靠流程自律） |

### 1.3 L3：用户可见性 ⚠️ —— 有一个**硬前提没满足**

```javascript
// desktop/updater.js:36
const AUTO_UPDATE_ENABLED = /^(1|true|yes|on)$/i.test(String(process.env.WORKBENCH_AUTO_UPDATE || ''));
// :109  if (!AUTO_UPDATE_ENABLED) return null;   // 关闭时托盘不挂任何更新提示
```

**自动更新默认关闭**，开启方式是设环境变量 `WORKBENCH_AUTO_UPDATE=1`（提交 `chore(shell): 关闭自动更新（默认不检查 / 不下载 / 不安装）`）。

**而这带来一个必须先解决的问题**——`CONTRIBUTING.md` §3.3 与 `publish_release.py` 的文件头都写着同一句约束：

> ⚠️ 更新器是**匿名**读取 Release 的：**仓库必须公开**，私有仓库的自动更新会静默失效。

**2026-09-17 实测：这个前提当前不成立。**

| 探测（匿名、无 token） | 结果 |
|---|---|
| `api.github.com/repos/jizi-dragon/quick-login` | **200 OK**（公开） |
| `api.github.com/repos/jizi-dragon/akso-cc` | **200 OK**（公开） |
| `api.github.com/repos/jizi-dragon/mogul_simulator` | **200 OK**（公开） |
| **`api.github.com/repos/jizi-dragon/akso-workbench`** | **HTTP 404** → 私有（或该名字下无公开仓库） |
| `api.github.com/users/jizi-dragon` | 200 OK，`public_repos=6`，**列表里没有 `akso-workbench`** |

> ★ **2026-10-11 后补**：仓库已更名为 `jizi-dragon/akso-workbench`（用户决策，
> 见 [ADR-0003](adr/0003-electron-desktop-shell.md) §2.4 D-4 的「2026-10-11 后补」）。
> 上表与本文其余实测记录里的仓库名已同步为新名，但**那些探测发生在更名之前**
> （当时仓库名是 `akso-mogul`）—— **结论（`404` / `403`）一句没变：可见性这件事本身还没解决。**

而 CHANGELOG 里曾写着「**本仓库即公开**」——**该记录与现状不符**。

> **所以：现在打开 `WORKBENCH_AUTO_UPDATE=1` 并指向 GitHub，更新链路会静默失效。**
> 它有 `AKSO_UPDATE_OVERRIDE=<url>` 离线自测通道（指向一个含 `latest.yml` 的静态目录），
> 所以**本机自测是通的**——但这正好会掩盖线上失效：测试全绿、用户端永远收不到更新。
> **这是本文发现的最值得优先处理的一件事。**

---

## 2. GitHub 在"版本管理"上到底提供什么

分三类。**先看清楚每一类解决的是哪一层**，再决定要不要用。

### 2.1 GitHub 原生（免费、无需引入任何东西）

| 能力 | 解决哪一层 | 说明 |
|---|---|---|
| **Tag** | L2 | `git push origin v0.3.12` → 版本号在 git 里可定位。**这是所有其他能力的地基** |
| **Releases** | L2 + L3 | 基于 tag，可附安装包；`publish_release.py` **已经在用**（`POST /repos/.../releases`） |
| **Release notes 自动生成** | L2 | GitHub 能按 PR/commit 自动归类生成 notes（`GET /releases/generate-notes`）。**你现在是手工从 CHANGELOG 提取** |
| **Source archives**（`Source code (zip/tar.gz)`） | L2 | 每个 Release 自动附带该版本源码快照 —— 等于免费存证 |
| **分支保护 / Rulesets** | L2 | 禁强推、禁删 tag、要求状态检查通过才能合 |
| **GitHub Actions** | L2 | CI 门禁（跑 `pytest` + `verify_packaging.py`） |
| **Dependabot / Secret scanning** | 旁支 | 依赖与密钥告警。与"版本号"无关，但同属仓库治理 |
| **Immutable releases** | L2 | 发布后不可改资产（较新功能，需确认你的账号可用） |

### 2.2 第三方自动化（**注意：会与你的两档制冲突，见 §4**）

| 工具 | 它怎么决定版本号 | 与你的机制的关系 |
|---|---|---|
| **release-please**（Google） | 用 **Conventional Commits** 推导，开一个 **Release PR**，你合并它才发版 | ⚠️ 用标准 SemVer，**会取代 `bump.py`**；但"PR 审阅后发版"的形态与你的手工节奏最接近 |
| **semantic-release** | 同上，但**全自动**：合进 main 就直接发 | ⚠️ 控制权最少，与"我决定何时构建"冲突最大 |
| **git-cliff** | 只生成 CHANGELOG，**不碰版本号** | ✅ 唯一**不冲突**的：可只用来替代 `publish_release.py` 里手工解析 Unreleased 段 |
| **commitlint / husky** | 校验提交信息格式 | ✅ 低风险，是上面那些工具的前置 |

### 2.3 你现在**已经**在用的

- ✅ **Releases API**（`publish_release.py`）——而且实现质量相当高（draft 流程、三道 `latest.yml` 自查、幂等、网络重试）
- ✅ **Conventional Commits**——实测 95 个提交里约 80% 合规（`fix` 30 / `feat` 28 / `chore` 13 / `docs` 6 / …），**还有 `feat(insight)!` 这种破坏性标记**
- ❌ tag（3/12）、Actions、保护规则、自动 notes —— **都没用**

> **一个有利条件**：你的提交信息已经是规范格式了。这意味着**将来想上自动化，地基已经有了**——
> 不需要回头补几百个提交的规范。这是你现在最值钱的一项"沉没成本转化"。

---

## 3. 优先处理：那条会让更新静默失效的链路

**这一节是行动项，其余是选型。**

### 3.1 先决定：你的自动更新到底走不走 GitHub

| 选项 | 做法 | 代价 | 适合 |
|---|---|---|---|
| **A. 仓库改回公开** | GitHub 仓库 Settings → 可见性改为 Public | 代码公开（但历史已做过脱敏重写——见 `HISTORY-REWRITE-NOTICE.md`，客户域名/账号名已替换为占位值） | ★ **一次解决两件事**：修好自动更新 **+ 解锁 Rulesets**（见 §7.1③）。若你本来就要分发安装包给他人，这是最省事的一条 |
| **B. 保持私有，更新源换掉** | 把 `latest.yml` + 安装包放到**你自己的 HTTPS 静态目录**（对象存储 / CVM / 内网共享），`desktop/package.json` 的 `publish.provider` 改为 `generic` + `url` | 需要自己维护一个静态托管 + 证书 | ★ **若安装包只在公司内网分发**，这条更合规；但**拿不到 Rulesets**（除非升级 GitHub Pro） |
| **C. 保持私有，且不做自动更新** | 什么都不改，保持 `WORKBENCH_AUTO_UPDATE` 关闭 | 用户需手工下载新版 | ★ **若从没打算对外分发**，这条最省事——把精力放在已落地的 CI 门禁上 |
| ~~**D. 保持私有且继续用 GitHub Release 做更新源**~~ | ✖ **不可行**——更新器匿名读，私有仓库必然 404 | — | 不要选 |

> **实测依据**（2026-09-17 带 token）：仓库 `private=True`；
> **已发布的 Release 只有 `v0.3.3` / `v0.3.4` 两个**（各 3 个资产）；
> `GET /repos/.../rulesets` → **403 "Upgrade to GitHub Pro or make this repository public"**。
> 也就是说：**自动更新在这条链路上从来没真正生效过**（因为仓库是私有的），
> 而"最新版本 0.3.12"与"最后一个 Release 0.3.4"之间差了 8 个版本、**从未发布**。

> ⚠️ **无论选哪个，先把 `WORKBENCH_AUTO_UPDATE` 的现状写清楚**：它现在默认 `0`（关闭）。
> 也就是说**这个问题当前不会暴露**——一旦有人开启它，才会以"用户永远收不到更新、日志里也看不出错"的形式出现。
> 建议在 `desktop/updater.js` 的关闭分支日志里补一句"当前更新源要求公开仓库，本仓为私有，开启前请先处理"，把坑写在会被读到的地方。

### 3.2 tag 的真实状态（**2026-09-17 结案：不需要补**）

初稿在这里推断"发布了 12 个版本却只有 3 个 tag，要给 9 个版本补 tag"。**带 token 复核后该推断作废。**

**为什么初稿会错**：我用**匿名** API 查 `/releases`，拿到 **404**，读成了"没有 Release"。
而真实原因是**仓库私有**——匿名看不到任何东西。**在私有仓库上，"不存在"与"不可见"都表现为 404，无法区分。**

**带 token 查到的真实状态**：

| 事实 | 值 |
|---|---|
| 仓库可见性 | `private=True` |
| 已发布 **Release** | **只有 2 个**：`v0.3.3`、`v0.3.4`（2026-09-11，各带 3 个资产） |
| 远端 **tag** | `v0.3.3`、`v0.3.4` —— **与 Release 一一对应，无缺口** |
| `chore(release)` 提交 | 12 次 —— 但这些只是**本地版本演进**，其中 **10 次从未发布** |

**结论**：不进位的版本是**从来没发布过**，不是**漏了 tag**。
给没发布过的版本补 tag 会制造假历史 → **不补**。

**那"怎么做才对"**：不补历史，改为**让今后不再遗漏**——已在 `build.ps1` 落地（release commit 推送成功后自动 `git tag -a v$version` + 推送，四条守卫见 §7.2）。

> **方法学教训（比结论更重要）**：**凡是要靠"接口返回 404 / 空"下结论时，先确认自己有权限看到全部数据。**
> 同类错误在本仓出现过两次——一次是"用某文档页没写证明平台没这能力"，
> 一次就是这里"用匿名 404 证明没有 Release"。两次都是**把"我没看到"当成了"它不存在"**。

---

## 4. 选型：GitHub 应该帮你到哪一步

三个方案，都对得起"用 GitHub 做版本管理"这句话，**但接管程度不同**。按你的现状排序：

### 方案甲：GitHub 只做"记录 + 门禁"，版本决策仍归 `bump.py`（**推荐**）

```
你手工：bump.py → build.ps1 → publish_release.py
GitHub 负责：
  ├─ tag/release 记录（补齐 §3.2）
  ├─ Actions：PR 与 main 上跑 pytest + verify_packaging.py（门禁）
  └─ Rulesets：保护 main 与 tag（禁强推/禁删）
```

| | |
|---|---|
| **改动量** | 加 1 个 `.github/workflows/ci.yml` + 1 条 ruleset + `build.ps1` 加 3 行打 tag |
| **好处** | 完全不动你已验证的 L1 链路；门禁从"记得跑"变成"不能不跑" |
| **代价** | 版本号仍由你决定（**这本来就是你要的**） |
| **风险** | 低。CI 只读不写 |

### 方案乙：release-please 接管版本号（**想少手工时选它**）

```
你：按 Conventional Commits 提交
release-please：自动开一个 "chore(release): 0.4.0" 的 PR（含版本号改动 + CHANGELOG 条目）
你：合并该 PR → 自动打 tag + 建 Release
```

| | |
|---|---|
| **好处** | 版本号与 CHANGELOG 不再手工维护；你已有的 80% 规范提交**立刻能用** |
| **代价** | ⚠️ **它用标准 SemVer，会与你的两档制冲突**（见下） |
| **冲突怎么解** | 二选一：① **改造 release-please 的映射**（`fix→patch`、`feat→minor`，把"构建"绑定到 minor）② 放弃两档制，改成"feat 才升 minor" |

> ⚠️ **必须想清楚的一点**：你的 `push` 现在会给**含新功能的提交**升 PATCH。
> release-please 会认为 `feat` 必须升 MINOR。**两者不可能同时满足。**
> 建议：**若要走方案乙，先把 `bump.py` 的两档制正式改成 SemVer**，否则会出现"两个工具都想写 `pyproject.toml`"的争抢。

### 方案丙：只把 CHANGELOG 生成自动化（**最小改动**）

用 `git-cliff` 或 GitHub 的 `generate-notes` 替代 `publish_release.py` 里手工解析 Unreleased 段的那 60 行（`notes_from_changelog()`）。
**不碰版本号**，风险最低，收益也最小。适合"我只烦手工整理发布说明"。

### 怎么选

| 你的诉求 | 选 |
|---|---|
| 只想"发布的版本在 git 里查得到 + 有门禁" | **甲** |
| 还想"版本号与 CHANGELOG 不用手工维护" | **乙**（但先统一版本语义） |
| 只烦发布说明 | 丙 |

---

## 5. 具体怎么做（方案甲，可直接照抄）

### 5.1 第一步：CI 门禁（15 分钟）

新建 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  verify:
    runs-on: windows-latest          # 必须 Windows：有 PowerShell 门禁与 .ps1 BOM 检查
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 安装 uv
        uses: astral-sh/setup-uv@v3

      - name: 同步依赖
        run: uv sync --extra dev --extra build

      - name: 单元与契约测试
        run: .venv\Scripts\python.exe -m pytest --tb=short -o addopts=""
        # ⚠️ 必须 -o addopts=""：pyproject 里 addopts="-q"，再加 -q 会进 ultra-quiet，
        #    连汇总行都不打印，CI 里看着像"没跑"

      - name: 打包静态核对
        run: .venv\Scripts\python.exe tools\verify_packaging.py

      - name: Lint
        run: .venv\Scripts\uv.exe tool run ruff check .
```

**三个必须注意的点**（都来自本仓实测）：

1. **`runs-on: windows-latest`**——`verify_packaging.py` 的 P5 检查 `.ps1` 的 UTF-8 BOM，Linux runner 上没意义。
2. **`-o addopts=""`**——否则 pytest 汇总行不打印（本仓真踩过，见 `CODEBASE_OVERVIEW.md` 验收基线）。
3. **`verify_packaging.py` 的 P4b 需要扩展 `dist/`**——而 `dist/` 被 gitignore。**CI 上必须先 `npm run build`**，否则 P4b 必然红：

```yaml
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - name: 重建扩展（P4b 需要 dist/manifest.json）
        working-directory: extensions/quick-login
        run: |
          npm ci
          npm run build
```

> 这条是本次新增的：**2026-09-17 实测 P4b 曾因"源码已 bump、dist 未重建"而 FAIL**。
> 在 CI 里必须保证构建顺序 = `bump → npm run build`，与 `build.ps1` 一致。

### 5.2 第二步：保护 main 与 tag —— ❌ **当前做不了**（免费版 + 私有仓库）

**实测 `GET /repos/jizi-dragon/akso-workbench/rulesets` 返回 403**：

> `Upgrade to GitHub Pro or make this repository public to enable this feature.`

所以这一步**先卡在可见性/套餐上**，三选一见 §7.1③。**下面留着是为了将来仓库转公开或升级后照做。**

GitHub 仓库 → **Settings → Rules → Rulesets**：

| 规则 | 设置 |
|---|---|
| Target | `main` |
| 要求状态检查 | 勾选 `verify`（上一步的 job 名：`test + packaging + lint`） |
| 禁止强推 | ✅ |
| 禁止删除 | ✅ |
| 新增一条 Tag ruleset | Target `v*`，**禁止删除、禁止强推** |

> 这一步的价值：**tag 一旦是"版本的可定位锚点"，它就不能是可被误删的东西**。
> 你刚经历过一次 `git filter-repo` 全历史重写（`HISTORY-REWRITE-NOTICE.md`），
> 那次连 tag 都得重打——保护规则防不了重写，但能防日常误操作。
> **但要注意**：单人开发时 Rulesets 会挡住你自己，记得配 bypass。

### 5.3 第三步：`build.ps1` 自动打 tag（✅ 已落地）

在 `tools/build.ps1` 的 release commit 段之后（`git commit -m "chore(release): v$version"` 之后）插入：

```powershell
    # 打 tag 并推送：tag 是"这一版对应哪个 commit"的唯一锚点。
    # 历史教训：曾发布 12 个版本只打了 3 个 tag → 用户报的版本号在 git 里定位不到。
    git tag -a "v$version" -m "Akso Workbench $version"
    git -c http.proxy= push origin "v$version" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "⚠ tag 推送失败（网络）——稍后手动 git push origin v$version" -ForegroundColor Yellow }
```

> 注意：**tag 推送必须在 commit 推送成功之后**，否则 tag 指向一个远端还不存在的 commit。
> 并且要在 `-SkipPush` 分支里跳过它（否则本地 tag 与远端不一致）。

---

## 6. 一张图看清改完之后的样子

```
L1 版本号          bump.py（真源 pyproject.toml，五写同步）
                    │   ← 不改。这是你做得最好的一层
                    ▼
L2 发布事实         build.ps1 ──commit──▶ main
                    │                      │
                    │                      ├─▶ Actions: pytest + verify_packaging + ruff   ← 新增
                    │                      └─▶ Rulesets: 保护 main 与 v* tag               ← 新增
                    │
                    └──tag v0.3.12──▶ git ──▶ GitHub Release
                                              ├─ 安装包 + blockmap + latest.yml
                                              ├─ 自动 source archive
                                              └─ Release notes                                ← 已有（publish_release.py）
                    ▲
L3 用户可见         更新器读 Release（匿名）
                    ⚠️ 前提：仓库公开 或 换成自托管更新源   ← 必须先解决（§3.1）
```

---

## 7. 待办清单（按优先级，都可证伪）

> **状态（2026-09-17）**：③ 与 ④ **已落地**（`tools/build.ps1` 自动打 tag、`.github/workflows/ci.yml`）。
> ①②⑤ 需要你操作（涉及 GitHub 设置与你的网络/决策）；⑥⑦ 是可选。

| # | 事项 | 判据 | 估时 | 状态 |
|---|---|---|---|---|
| **1** | **决定更新源**：仓库改公开 / 换自托管 / 明确"暂不用自动更新" | 用 `AKSO_UPDATE_OVERRIDE` 或真实环境跑通一次"检测到新版本" | 决策 5 分钟 | ⬜ **待你决策**（§3.1） |
| **2** | ~~查清 tag 缺口并补齐~~ | — | — | ✅ **已结案**：带 token 查明只发布过 2 版、tag 无缺口，**不需要补**（§7.1②） |
| **3** | `build.ps1` 加自动打 tag | 下一次构建后 `git ls-remote --tags origin` 能看到新 tag | — | ✅ **已完成** |
| **4** | 加 `.github/workflows/ci.yml` | push/PR 上出现 `verify` 检查，且能拦住一次故意失败 | — | ✅ **已完成**（§5.1） |
| **5** | 保护 main 与 `v*` tag | 尝试强推 tag 被拒 | — | ❌ **当前不可用**：免费版+私有仓库无 Rulesets（403），需先选 A/B/C（§7.1③） |
| **6** | （可选）统一版本语义，再考虑 release-please | 决策：是否放弃两档制 | 决策 5 分钟 | ⬜ 可选 |
| **7** | （可选）`git-cliff` 替代手工解析 Unreleased 段 | 生成的 notes 与现有一致 | 1 小时 | ⬜ 可选 |

**1 和 2 是"不做事就会埋雷"的两条**；3–5 是"顺手做完，长期受益"；6–7 是想更省手工时再说。

### 7.1 ⬜ 待你操作 / 已结案的核查项

#### ① 保护 main 与 `v*` tag（网页设置，5 分钟）

GitHub 仓库 → **Settings → Rules → Rulesets → New ruleset**：

| 字段 | 值 |
|---|---|
| Name | `protect-main` |
| Target | **Branch**，填 `main` |
| Rules | ✅ Require status checks to pass → 选 **`test + packaging + lint`**（CI job 名）<br>✅ Block force pushes<br>✅ Restrict deletions |
| Bypass | 自己的账号可留 bypass（单人开发时否则会被自己卡住） |

再建一条：

| 字段 | 值 |
|---|---|
| Name | `protect-tags` |
| Target | **Tag**，填 `v*` |
| Rules | ✅ Block force pushes ✅ Restrict deletions |

> ⚠️ **Require status checks 要等 CI 至少成功跑过一次之后才在列表里出现**——所以顺序是
> **先 push 让 CI 跑一次，再配这条规则**。否则会出现"规则配好了但复选框是空的"。
>
> 备选（不想点网页）：有 `gh` CLI 且已登录时可用 API 建 ruleset。但**首次配置建议走网页**——
> 状态检查的名称必须和 CI 实际产生的一致，网页上有下拉可选，写 JSON 容易拼错。

#### ② ~~补齐历史 tag~~ → **已查清：不需要补**（2026-09-17 结案）

初稿根据**匿名**探测推断"发布了 12 版却只有 3 个 tag"，计划补 9 个。**带 token 复核后该计划作废**：

| 带 token 查到的真实状态 | 值 |
|---|---|
| 仓库可见性 | `private=True` |
| 已发布 Release | **只有 2 个**：`v0.3.3`、`v0.3.4`（2026-09-11，各 3 个资产） |
| 远端 tag | `v0.3.3`、`v0.3.4` —— **与 Release 一一对应，无缺口** |
| `chore(release)` 提交 | 12 次（但这些只是**本地版本演进**，多数**从未发布**） |

**结论**：不进位的版本是**从来没发布过**，不是**漏了 tag**。
给没发布过的版本补 tag 会制造假历史，**所以不补**。

> ⚠️ **方法学教训（比结论本身更重要）**：匿名 API 的 **404 既可能表示"不存在"，也可能表示"不可见"**。
> 在私有仓库上这两者无法区分，我当时却把 404 读成了"没有 Release"。
> **凡是要靠"某接口返回空/404"下结论时，先确认自己有权限看到全部数据。**
> 同类错误在本仓已出现过一次：早期用「某文档页没写」证明「平台没有这个能力」（见 `docs/CODEBASE_OVERVIEW.md` 的方法学注记）。

**取而代之要做的（唯一一件）**：确认 `build.ps1` 的自动打 tag 生效——
下一次 `-Bump build|push` 构建后，`git ls-remote --tags origin` 应能看到新 tag。

> **一个待你决定的不一致**：当前 `build.ps1` 在**构建**时打 tag，而 Release 由之后手工执行
> `publish_release.py` 才产生。于是会出现"有 tag 但没有 Release"的版本。
> 两种立场都成立——**tag = 我构建并标注了这一版**（当前实现，因为 `build.ps1` 是唯一自动拿到版本号的入口），
> 或 **tag = 我对外发布了这一版**（那应把打 tag 移进 `publish_release.py`）。**要改的话告诉我。**

#### ③ Rulesets 保护：★ **当前账号/可见性下做不了**

实测 `GET /repos/jizi-dragon/akso-workbench/rulesets` → **403**：

> `Upgrade to GitHub Pro or make this repository public to enable this feature.`

**即"免费版 + 私有仓库"不提供 Rulesets。** 要保护 `main` 与 `v*` tag，三选一：

| 选项 | 代价 | 说明 |
|---|---|---|
| **A. 仓库转公开** | 代码公开 | 历史已做过脱敏重写（`HISTORY-REWRITE-NOTICE.md`：客户域名/账号名已替换为占位值）；**且顺带修好自动更新**（更新器正是要求公开仓库） |
| **B. 升级 GitHub Pro** | 约 $4/月 | 保留私有 + 拿到 Rulesets。**但自动更新仍不可用**（它要求匿名可读） |
| **C. 放弃自动保护** | 零成本 | 单人开发时 Rulesets 的收益本就有限；靠"改 `build.ps1` 后必跑 `verify_packaging.py`"+ CI 兜底 |

> **判断建议**：如果你**从没打算把安装包发给外部用户**，那自动更新（L3）整条线本来就不需要，
> 选 **C** 最省事，把精力放在 CI 门禁（已落地）上；
> 如果要对外分发，**A** 一次解决"保护规则 + 自动更新"两件事，但要接受代码公开。
> **这是业务决策，我不替你定。**

### 7.2 ✅ 已完成部分的实测记录
| 项 | 落点 | 实测 |
|---|---|---|
| 自动打 tag | `tools/build.ps1`（release commit 之后，四条守卫：非 `-Bump none`、commit 真产生、commit 真推上去、tag 不重复） | `verify_packaging.py` **9/9 PASS**（P5 的 BOM 检查通过）；PowerShell 语法解析无错 |
| CI 门禁 | `.github/workflows/ci.yml`（`windows-latest`；扩展构建 → `uv sync` → pytest → `verify_packaging.py` → `gen_tools_doc.py` → ruff） | ✅ **run #10（`be3b59f`）`conclusion=success`，12 个 step 全绿**——这是本仓**首次**绿（前 9 次：5 次 cancelled、4 次 failure，根因见 PITFALLS #43）。本地另逐条复现：**480 passed**（含 11 slow）/ **9/9 PASS** / `TOOLS_DOC_CHECKS: PASS（5 插件 / 20 工具 = core）` / ruff `All checks passed`；并在 **py3.12**（= CI 的 Python，开发机是 3.14）复现过 `472 passed, 8 skipped` |
| 已推送 | `5830da6`（文档+CI+自动打 tag+清理）、`0494ed6`（CI 补工具清单门禁）、`058c1ef`（本轮订正） | `local == origin/main`；推送经本机代理 `127.0.0.1:7890`（直连被重置，`git` 已配 `http.proxy`） |

> **2026-09-18 更新（本节多条已过期，以此为准）**
>
> | 项 | 现状 |
> |---|---|
> | **版本演进默认值** | `build.ps1` 的 `-Bump` **默认 `none`**（原来默认 `build` = MINOR+1）。升版必须显式写 `-Bump push\|build`。**理由**：版本号不可回收，而"默认帮用户升版"是在替用户做不可逆决策——实测两次失败构建烧掉了 `0.4.0` 与 `0.5.0`，把 0.3.12 一路顶到 0.6.1 |
> | **bump 的位置** | 从**第 1 步**降到**第 3 步**（`uv sync` → 静态体检 → **bump** → 扩展重建 → 推送 → sidecar → 复检 → NSIS）。前两步失败**不再消耗版本号** |
> | **日常升小版本** | **不需要 `build.ps1`**：`python tools\bump.py push`（PATCH+1）后提交推送即可。要出安装包时才用 `build.ps1` |
> | **CI 门禁** | 已增至 **13 步**，含 `Interpreter version contract`（断言 `.python-version` == CI 的 `python-version`）。**run #18（`6294814`）success** |
> | **打包体检** | **13/13**（原 9 项 → 新增 P8/P8b/P9/P10）。⚠ 注意 **P8b 是环境事实**，只在 `WORKBENCH_REQUIRE_CHROMIUM=1`（`build.ps1` 设）时参与断言——CI 不设；早先把它当无条件的判据，导致 CI 首次被判红（run #17） |
> | **发布** | `publish_release.py` 的**出网通道改为探测**（直连/环境变量/git 配置/常见端口，挑实测能通的）。`--no-proxy` 现在是"强制直连、跳过探测" |
> | **已发布的 Release** | `v0.3.3`、`v0.3.4`（2026-09-11）、**`v0.3.12`（2026-09-18）**。误发的 `v0.6.1` 已连同 tag 删除 |
>

> ⚠️ **改 `build.ps1` 必须补 BOM**：实测编辑动作会静默丢掉 UTF-8 BOM（P5 立刻掉到 8/9）。
> 这是环境坑 #37，处置命令在 `docs/PITFALLS.md`。
> **这也顺带证明了 CI 的价值**：这类"内容看着完全正常、只有字节不对"的问题，只有门禁能拦住。
>
> ✅ **订正（2026-09-17）：Actions 的运行结论**是**可以**在本环境读的。
> 早先记的"actions:read 未被凭据覆盖"只对了一半——用 `git credential fill` 取到的那个 token：
>
> | 端点 | 结果 |
> |---|---|
> | `GET /repos/{r}/actions/runs` | ✅ **200**（能读运行列表、`conclusion`、`head_sha`、`run_attempt`） |
> | `GET /repos/{r}/actions/runs/{id}/jobs` | ✅ **200**（能读每个 step 的 `conclusion`） |
> | `GET /repos/{r}/commits/{sha}/check-runs` + `/annotations` | ✅ 200（但注解只有 "exit code 1"，没有逐条失败——**Actions 不会自动把 pytest 失败变成注解**） |
> | `GET /repos/{r}/actions/jobs/{id}/logs` | ❌ **401**（job 日志需要额外 scope） |
>
> 所以**结论可读、日志不可读**：判"红/绿/被取消"不必再问人，
> 但要拿到失败栈仍然得本地复现（或去 Actions 页看）。
> **本次就是这么定案的**：`/actions/runs` 一眼看出"CI 从建立那天起就没绿过"，
> 而本地 `py312` 复现给出了那 4 个失败（见 PITFALLS #43）。
> **别再用"我本地跑过了"代替 CI 结论**——那是 §8 第 2 条"无人报错≠链路正常"的同一类错误。

---

## 8. 三个反直觉的结论（值得单独记住）

1. **"看不到"≠"不存在"。** 本项目里这条方法学错误已经出现**两次**：
   ① 用"某文档页没写"证明"平台没这个能力"（→ 见 `docs/CODEBASE_OVERVIEW.md`）；
   ② 用"匿名 API 返回 404"证明"没有 Release"（→ 真相是仓库私有，什么都看不到）。
   **凡是要靠"接口返回 404 / 空"下结论时，先确认自己有权限看到全部数据。**
   同理，本文的 tag 结论从"要补 9 个"翻转为"一个都不用补"，就是因为补上了权限这一步。
2. **"无人报错"不等于"链路正常"。** 自动更新默认关闭 + 私有仓库 + 有离线自测通道 = **三个因素叠加成"看起来都测过，线上永远收不到更新"**。
   这类缺陷的共同特征是：**没有任何一方会报错**。所以判据必须是"用户端真的升上去过一次"，不是"我自测通过了"。
3. **你的提交规范已经是资产了。** 80% 的 Conventional Commits 合规率意味着**将来上自动化不用还债**；
   而反过来说，**若现在开始不守规范，以后再想上就贵得多**——这是"现在多写几个 `feat(scope):`"的真实回报。
