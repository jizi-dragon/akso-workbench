# ⚠️ 提交历史已被重写（2026-09-14）：请按本文迁移你的本地副本

## 发生了什么

本仓库**所有提交的历史**（全部分支与标签）经过一次 `git filter-repo` 重写，
目的是清除历史里的**客户/环境标识与个人身份信息**（真实部署域名、客户租户名、内网地址、真机账号名）。
**提交的 SHA 全部改变**（例如 `28a94b5` → 新的哈希），因此你本地的旧副本与远端不再有共同祖先。

同时工作区代码也做了 lint 修复（`ruff` 由 19 条违规 → 全绿），详见根 `CHANGELOG.md` 的 `[0.3.5]` 段。

## 你只需要做一次迁移（二选一）

### 方案 A：你的本地副本没有未推送的提交（推荐，最简单）

```powershell
# 先确认没有未推送的提交（输出应为空或只有你确定能丢弃的内容）
git log --oneline origin/main..HEAD

# 然后丢弃本地历史、重新对齐远端
git fetch origin
git reset --hard origin/main
git clean -fd          # 清掉重写后不再被跟踪的残留（谨慎：会删未跟踪文件，先确认没有你要留的）
```

> ⚠️ 执行 `reset --hard` 前，如果工作区有你要保留的改动，先 `git stash` 或拷出去。

### 方案 B：你有未推送的提交需要保留

不要 rebase 旧提交（历史已无共同祖先，会引入整段重复历史）。正确做法是**把你的改动摘成补丁再落到新历史上**：

```powershell
# 1) 把你的未推送提交导出为补丁（逐个提交，或用区间）
git format-patch origin/main..HEAD -o ..\my-unpushed-patches

# 2) 对齐重写后的远端
git fetch origin
git reset --hard origin/main

# 3) 应用补丁（可能需要在导入/排序上手工解冲突：ruff 已把 import 排序改过）
git am ..\my-unpushed-patches\*.patch
```

## 如果你有本地分支或标签

重写只影响**服务端已推送的引用**。你本地的功能分支需要以新 `main` 为基线重建：

```powershell
git fetch origin
git switch -c mybranch-v2 origin/main
git cherry-pick <你那些提交的原始 SHA>   # 逐个摘，不要 merge 旧历史
```

本地标签若指向被重写的提交，请重新打：

```powershell
git tag -d v0.3.3 v0.3.4     # 删掉旧标签
git fetch origin --tags       # 拉取重写后的标签
```

## 为什么必须这样（而不能再简单 merge）

`git filter-repo` 改变了每个提交的对象内容 → SHA 全变 → 旧历史与新历史**没有共同祖先**。
此时 `git pull` 会尝试把两条独立历史合并，结果是**每个提交出现两次**、冲突满屏。
因此只能「对齐远端 + 重放你的改动」，不能 merge/rebase 旧历史。

## 本次重写的替换规则（供你核对代码是否受影响）

| 原值（类别） | 新值 | 说明 |
|---|---|---|
| 真实平台/部署域名（`*.aksoegmp.com` 系） | `*.example.com` | 客户域名 → 文档占位域 |
| 客户租户名（一个含连字符的英文租户代号） | `example-config` | → 占位 |
| 环境代号（`demo-*` 系） | `demo-val` | → 占位 |
| 中文租户名 | `示例租户` | → 占位 |
| 内网地址 | `192.0.2.10` | → RFC 5737 文档专用网段 |
| 真机管理员账号名 | `dev-admin` | → 占位 |
| 真机普通账号名（词边界匹配） | `dev-user` | → 占位 |
| Java 包标识里的个人标识 | `com.example` | → 占位 |
| 商业 VPN 产品名（三种写法） | `VPN` | → 通用词 |
| `jizi-dragon`（**仅文档叙述处**） | `example-org` | GitHub 所有者的叙述性引用 → 占位 |

> ⚠️ **本表左侧刻意只写类别、不写原字面量**：这些值在重写时已被从工作区与全历史一并抹掉，
> 仓库里已不存在可对照的原文；把改造后的值再写一次（`A → A`）只会让表读不出信息。
> 定位受影响区域的方法：全文搜索**右侧的新值**（如 `example.com`、`demo-val`、`192.0.2.10`），
> 命中处就是当初做过替换的地方。

**刻意保留（功能锚点，改了会坏）**：

- `desktop/package.json` 的 `build.publish.owner = jizi-dragon`（electron-updater 的更新源）
- `desktop/proxy.js` 的 `PROBE_URL`（直连探测打的 GitHub releases.atom）
- `tools/publish_release.py` 的 `REPO` 常量与 `tools/verify_update_proxy.js` 的资产 URL 基准

这四处必须是真实值，否则自动更新与发布链路失效。该标识本就公开写在仓库 URL 里。

## 没有找到任何密钥泄漏（本轮已核查）

已对整个历史（72 个提交）扫描过：GitHub PAT（`ghp_`/`gho_`/`github_pat_`）、
AWS `AKIA`、Slack `xox*`、JWT（`eyJ…`）、`BEGIN … PRIVATE KEY`、
`password/token/secret/api_key = "…"` 赋值形态 —— **全部 0 命中**。
`sk-` 的命中全部是 CSS 类名（`sk-modal` / `sk-input` / `sk-title` /`sk-card`），
44 位 base64 命中全部是 `package-lock.json` 的 SRI 完整性哈希（`sha512-…`），均非密钥。

> 但仍请自查一次真实凭据是否曾在本机其它地方外传：Fernet 密钥与 API Key 只存放于
> `%APPDATA%\Workbench\workbench.db`（**未入库**）。若你曾把 `workbench.db`、
> 备份 JSON 或 `shell-state.json` 提交到任何地方，请单独处理。
