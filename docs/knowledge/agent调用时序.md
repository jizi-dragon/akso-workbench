# agent 调用 akso-workbench 的时序

> 这张图回答三个问题：模型调一次工具，本机到底做了哪些事；哪一步会碰 akso-cloud / eGMP；
> 哪一步需要人点一下。排查时先看图，再按 `_uid` 找日志，最后才翻代码。

图用 Mermaid 写。GitHub、带 Mermaid 插件的 VS Code、以及支持 Mermaid 的 Markdown 预览都能直接渲染；
渲染不出来时，代码块本身也按「谁 → 谁、做了什么」可读；看不了 Mermaid 的，§11 有渲染好的 PNG。
改工具流程时**同一提交里改这篇**。

**目录**

1. [一次工具调用的外壳](#1-一次工具调用的外壳)
2. [公共前置：账号门与换票](#2-公共前置账号门与换票)
3. [`akso_workbench_accounts`：读快照](#3-akso_workbench_accounts读快照)
4. [`akso_workbench_config_plan`：只出计划](#4-akso_workbench_config_plan只出计划)
5. [`akso_workbench_config_apply`：审批 → 执行](#5-akso_workbench_config_apply审批--执行)
6. [`akso_workbench_object_analysis`：只读分析](#6-akso_workbench_object_analysis只读分析)
7. [出错时怎么对号](#7-出错时怎么对号)
8. [排查一次调用](#8-排查一次调用)
9. [与代码的对应](#9-与代码的对应)
10. [什么时候要改这篇](#10-什么时候要改这篇)
11. [渲染版（PNG）](#11-渲染版png)

## 参与者

| 参与者 | 是谁 | 关键事实 |
|---|---|---|
| 模型（agent） | DSH 里的模型 | 只看得到工具 description 与返回值，看不到 token |
| DSH 工具注册表 | `ctx.tools.register` | 调 `definition.execute(args)`，把结果交回模型 |
| host | 插件的 node 半边（`plugin/src/index.ts`） | 唯一持有 eGMP token 的地方；围栏只放行本机 |
| 本机状态/日志目录 | `<dshHome>/akso-workbench/` | `cloud.json` / `prefs.json` / `pool.json` / `egmp-session.json` / `config-approvals/` / `config-audit/` / `journal/` / `analysis-history/` |
| 工作区数据目录 | `<当前工作区>/akso-workbench-data/` | `analysis/` / `config-plans/` / `config-tasks/` / `captures/` / `reviews/` / `reports/`；没有工作区根时拒绝写入 |
| GUI | 浏览器里的插件页面 | 底部账号条、对话里的审批窗；只读 host 的 HTTP 接口 |
| akso-cloud | 云端账号库 | 存口令、代登录 eGMP、提供 `/api/ui/state` 快照 |
| eGMP | 客户平台 | 所有配置读写的最终落点 |

三条不变式（图里会反复出现它们的原因）：

1. **令牌不下发**：`egmp-session.json` 只在 host 里读写，HTTP 响应与工具返回值都不含 token；
   浏览器只拿「已登录 + 环境 + 账号名」。
2. **一份快照喂所有座位**：账号条、账号中心、`akso_workbench_accounts` 读的是 host 里**同一个**
   `snapshot` 变量；任何写操作都走 `cloud op → sync → 返回整份新快照`，不做本地先改。
3. **写要过三道门**：账号门（账号条 + 配置池）→ 审批门（人点「同意」）→ 引擎复验（计划指纹、
   目标环境、账号、审批记录）。三道门都在 host，**都不在工具参数里**。

## 1. 一次工具调用的外壳

五个工具都经 `withToolUid()` 注册（`plugin/src/tool-uids.ts`）。包装层只做三件事：给 description
追加 UID、开始/结束打日志、给返回值注入 `_uid`。业务结果（拒批、缺账号等）用返回值表达，
**不抛异常**，所以 `done` 里要同时看 `ok=`；真正抛异常才会出现 `failed`。

```mermaid
sequenceDiagram
    autonumber
    participant A as 模型
    participant D as DSH 工具注册表
    participant H as host
    participant F as 本机数据目录

    A->>D: 工具调用，参数是 JSON
    D->>H: definition.execute(args)
    Note over H: withToolUid 包装：<br/>info 日志「[AKW-TOOL-…-V1] invoke 工具名」<br/>开始计时
    H->>F: 业务实现（图 2–图 6）
    H-->>D: 返回值，或抛出异常
    Note over H: 成功：info「[UID] done 工具名 耗时ms ok=true 或 false」<br/>抛错：warn「[UID] failed 工具名 耗时ms: 错误」
    D-->>A: 结果（成功时带 _uid）
```

五个工具的 UID：

| 工具 | UID |
|---|---|
| `akso_workbench_accounts` | `AKW-TOOL-ACCOUNTS-V1` |
| `akso_workbench_config_plan` | `AKW-TOOL-CONFIG-PLAN-V1` |
| `akso_workbench_config_apply` | `AKW-TOOL-CONFIG-APPLY-V1` |
| `akso_workbench_config_verify` | `AKW-TOOL-CONFIG-VERIFY-V1` |
| `akso_workbench_object_analysis` | `AKW-TOOL-OBJECT-ANALYSIS-V1` |

## 2. 公共前置：账号门与换票

`config_plan`、`config_apply` 走**写路径** `openWriteSession()`：账号必须来自底部账号条
（`prefs.currentAccountId`）**且在配置池**（`pool.json`）。`object_analysis` 与能力查询走**读路径**
`ensureEgmpSession()`：只要求账号条不为空，不要求在池里。两者都不回落到磁盘上的旧会话。

账号条为空、账号不在池、或换不到票时，写路径会把 `needsAccountAt` 置为当前时间；界面看到
`state.needsAccount = true`（5 分钟内有效）就自动弹出选账号面板。

```mermaid
sequenceDiagram
    autonumber
    participant H as host
    participant F as 本机数据目录
    participant C as akso-cloud
    participant E as eGMP

    Note over H: 读路径 ensureEgmpSession<br/>写路径 openWriteSession
    H->>F: prefs.json 里的 currentAccountId
    alt 账号条为空
        H-->>H: 写路径记 needsAccountAt；返回「底部账号条还没有连接账号」
    else 有账号
        opt 写路径
            H->>F: pool.json 里的 accountIds
            alt 账号不在配置池
                H-->>H: 记 needsAccountAt；返回「当前账号不在配置池里，不能用来写入」
            end
        end
        H->>F: egmp-session.json
        alt 票还在、账号一致、距到期超过 5 分钟
            H-->>H: 直接复用这张票
        else 需要换票
            H->>F: cloud.json
            alt akso-cloud 未登录
                H-->>H: 返回「akso-cloud 未登录，换不到 eGMP 令牌」
            else
                opt 账号快照还没拉过
                    H->>C: GET /api/ui/state
                    C-->>H: accounts + sites + boxes + revision
                end
                H->>C: POST /api/accounts/ID/egmp-token
                C->>E: 云端解密口令，再调 POST /api/openapi/v1.0/Auth
                E-->>C: JWT
                C-->>H: baseUrl + account + token + expiresAt
                H->>F: 写 egmp-session.json（0600，只存票，不存口令）
            end
        end
    end
```

`REFRESH_MARGIN_MS = 5 * 60 * 1000`：票离到期不到 5 分钟就提前换，避免写到一半 401。

## 3. `akso_workbench_accounts`：读快照

只读，**不碰 eGMP**；`refresh:true` 时先向 akso-cloud 回拉一次。快照是 host 里的一个变量，
所以界面和工具看到的是同一份数据、同一个 `revision`。

```mermaid
sequenceDiagram
    autonumber
    participant A as 模型
    participant D as DSH 工具注册表
    participant H as host
    participant F as 本机数据目录
    participant C as akso-cloud

    A->>D: akso_workbench_accounts(section, query, limit, refresh)
    D->>H: execute
    opt refresh 为 true
        H->>F: cloud.json
        alt 已登录
            H->>C: GET /api/ui/state
            C-->>H: accounts + sites + boxes + revision（一次响应）
            H->>H: snapshot 整体替换；syncedAt 置为现在
        end
    end
    alt 未登录
        H-->>A: loggedIn=false + 登录提示 + _uid
    else 已登录
        H->>F: prefs.json / pool.json
        H->>H: 按 query 模糊过滤（账号 5 个字段、站点 3 个、盒子 2 个）<br/>按 limit 截断（默认 50，最大 200）
        H-->>A: revision / counts / defaultBox / connectedAccountId /<br/>poolAccountIds / accounts / sites / boxes / _uid
    end
    Note over H,F: 返回值没有口令、没有 token；connected 只可能对池里的账号为 true
```

## 4. `akso_workbench_config_plan`：只出计划

计划阶段**一个写请求都不发**。产出一份 `config-plans/pln_*.json`：目标环境 + 账号、逐资源
create/update/skip、风险与不可逆标记、依赖顺序、警告、30 分钟后过期。计划文件是后续 apply
唯一的输入；工具参数里没有「顺便改一下」的位置。

```mermaid
sequenceDiagram
    autonumber
    participant A as 模型
    participant D as DSH 工具注册表
    participant H as host
    participant F as 本机数据目录
    participant E as eGMP

    A->>D: akso_workbench_config_plan(blueprint)
    D->>H: execute
    H->>H: openWriteSession（图 2）
    alt 拿不到写入会话
        H-->>A: ok=false / status=needs-account / hint + _uid
        Note over H: 界面读 state.needsAccount<br/>自动弹出选账号面板
    else 有会话
        H->>H: JSON.parse(blueprint)，不合法直接返回 error<br/>validateBlueprint 校验结构与字段规则
        H->>E: 只读现状：query.object.get / query.fields.list /<br/>query.picklist.get / query.lifecycle.status.list
        E-->>H: 平台现状
        H->>H: 逐资源 diff（create / update / skip）<br/>标风险与不可逆、按依赖排序、收集警告
        H->>F: 写 config-plans/pln_xxx.json<br/>target = baseUrl + accountId，30 分钟后过期
        H-->>A: ok=true / planId / target / summary / steps / next + _uid
    end
    Note over H,E: 现状读失败（EgmpError）→ ok=false / error；平台没有被改过
```

`next` 会明确告诉模型：有几步不可逆、要让用户确认后用哪个 `planId` 调 apply。

**任务级计划**：把 `blueprint` 换成 `task:{name, stages:[{name, blueprint}]}` 时，工具逐段算出计划
（仍要读现状、仍不写文件），返回 `taskId` 与各段步骤，并把任务落盘到 `config-tasks/tsk_*.json`
（**60 分钟**过期）。任务里各段共用返回摘要，执行时再按当时的平台状态重算——所以第二段可以用上
第一段刚建出来的对象，不需要提前知道它的 id。

## 5. `akso_workbench_config_apply`：审批 → 执行

这个工具分两段：**审批**（有人参与）和**执行**（引擎逐步写、逐步回读）。审批关掉时
（调试模式的页面开关 / 环境变量）直接从执行开始，但引擎仍要求计划绑定了目标环境与账号。

### 5.1 审批：人点「同意」才往下走

审批门在 host 内存里（`plugin/src/config/approval.ts`）：同一时间只挂一条；等待上限 8 分钟；
工具被中断会结算成 `cancelled`。浏览器每 2 秒轮询 `pending`，弹窗里**没有 ×**——只能显式选同意、
拒绝或补充。

审批前 host 会先写一份审核文档（`plugin/src/config/approval-review.ts`）：
优先写到 `<当前工作区>/akso-workbench-data/reviews/<任务名>-YYYYMMDD-HHmmss.md`（`review.workspaceDir`
优先，其次 `exec` 提供的工作区根；两者都没有时返回 `review-unavailable`，不回落 profile 或仓库内目录），内容为「问题 / 执行计划 / 预期结果 checklist」，并附
计划指纹与逐条执行明细（方法/路径/参数），保证审核文件与实际工具输入一致。
任务已执行后的局部优化/更新用 `update` 类型，写到 `<任务名>-更新-YYYYMMDD-HHmmss.md`，篇幅更短，
只含任务概述 / 调整计划 / 预期 checklist / 本次操作清单，可带 `previousReviewPath` 与 `reason`。
报告里带 `review.path`、`review.kind` 与 `review.url`；弹窗展示任务概述、文档地址与「在右侧打开」，
通过 `ctx.sidebarRight.openResource` 在 DSH 右侧文件预览打开；弹窗常驻「调整内容」文本框，填写后只能
「提交调整」，空内容时只能「同意」。`config_apply` 可传
`review{kind,taskName,previousReviewPath,problem,approach,expected[],reason,workspaceDir}` 覆盖自动
生成的内容；审批前会校验文档存在、类型匹配且计划指纹匹配。**指纹不符或文档已结束时不再直接失败**：
host 会按当前计划补一份新文档（旧文档原样保留），返回值带 `reviewRegenerated: true`，然后继续审批；
只有文档根本写不出来（没有工作区根）才返回 `review-unavailable`。
两步审批：先 `prepareOnly:true` 只生成文档并返回 `status:'prepared'` + 路径/URL（不弹窗、不写平台），
模型把目标和文件链接发给用户；之后用 `reviewPath` 再调用，复用同一份文档进入审批，不会重新生成。
任务成功后 host 才把文档标记为「已结束（审核文件作废，仅作历史记录）」并附执行结果；拒批 / 超时 /
失败不作废。**已结束文档禁止再修改**，后续调整必须新建 `update` 文档。

**要不要审批只看「有没有写入」**（`plugin/src/config/approval-policy.ts` 的 `approvalScope`）：
能力目录里 `readonly: true` 或 `method: 'GET'` 的步骤不算写入。全是只读时**不弹窗、不生成审核文档**，
直接执行并返回 `status:'no-approval-needed'` + `approval{needed:false, reason:'read-only'}`；
调试模式关掉审批时 `reason:'disabled'`。这一处判据由工具与引擎共用，不允许各写一份。

```mermaid
sequenceDiagram
    autonumber
    participant A as 模型
    participant D as DSH 工具注册表
    participant H as host
    participant F as 本机数据目录
    participant G as GUI
    participant R as DSH 右侧栏
    participant U as 用户

    A->>D: akso_workbench_config_apply(planId, review?)
    D->>H: execute
    H->>H: openWriteSession（图 2）+ loadPlan
    alt 计划不存在 / 已过期 / 已执行
        H-->>A: error，或直接返回已完成的计划
    else 有写入且要求审批（默认）
        H->>H: buildApprovalReport：业务摘要 + 逐条方法/路径/参数/风险 + 计划指纹
        H->>F: 写 akso-workbench-data/reviews/<任务名>-<时间戳>.md
        H->>H: 校验文件存在且计划指纹匹配
        alt 指纹不符 / 文档已结束
            H->>F: 按当前计划补一份新文档（旧文档保留）
            H->>H: reviewRegenerated = true
        end
        H->>H: report.review = 三节文档 + checklist + 执行明细
        H->>H: approvals.request：挂起，8 分钟超时，同时只挂一条
        loop 每 2 秒
            G->>H: GET /api/akso-workbench/config/approval/pending
            H-->>G: pending（id + report + review + expiresAt）或 null
        end
        G->>U: 弹窗：环境 / 账号 / 业务摘要 / 三节摘要 + checklist / 逐条接口明细
        U->>G: 点「在右侧打开」
        G->>R: sidebarRight.openResource(dsh-resource://file/session/<sid>/<path>)
        R-->>U: 右侧打开审核文档
        U->>G: 同意 / 拒绝 / 补充（附意见）
        G->>H: POST /api/akso-workbench/config/approval/decide
        H->>H: 结算挂起的 Promise
        alt approve
            H->>F: config-approvals/pln_xxx.json（计划指纹，一次性）
            Note over H: 进入 5.2 的引擎复验
        else deny / supplement / timeout / cancelled / busy
            H->>F: 写一条审批审计（busy 不记）
            H-->>A: ok=false / status=denied / supplement / approval-timeout / cancelled / busy
        end
    else 无需审批（只有只读查询，或调试模式关掉了审批）
        Note over H: 不弹窗、不写审核文档<br/>approval.reason = read-only / disabled，直接进入 5.2
    end
```

### 5.2 执行：引擎复验 + 逐步回读 + 事后确认

引擎（`plugin/src/config/engine.ts` 的 `applyPlan`）不信任工具：计划指纹、目标 `baseUrl`、
`accountId`、审批记录未用过，全部再验一遍；通过后先把审批标记为已用，**中途失败也不能拿同一张
审批重试**。

**执行一次、验证一次**（2026-11-27 用户口径）：每一步写入后**立刻**回读，回读不符先就地修一次，
**仍不符就停在这一步**——后面的步骤一个包都不发，返回里点明「停在第 N 步」。判据只有一条：
这一步有没有可核对的东西。**有判据的写入一律是门**（不再区分严格/非严格）；只有能力目录里根本
没有对应查询接口的写入才降级，记 `NO_CRITERIA` 继续跑，台账里留痕。

小问题不进「失败」，进台账：回读晚一拍（等 300 ms 用新上下文重读一次就对上）、瞬时 5xx / 连接被
重置（只重试一次）、就地修好或修不好、写报错但回读证明已生效——各记一条 `IssueRecord`
（`$DSH_HOME/akso-workbench/config-audit/issues-<YYYY-MM>.jsonl` + journal 的 `issue.recorded`），
随返回值带 `issues[]`，不打断施工。

**小错就地修（`AUTO_REPAIR`）**：回读没过、或写入报错时，如果这一步是**幂等写入**（`field.save`、
`picklist.save`、`layout.save`、`listlayout.*`、`lifecycle.status.update`、`lifecycle.useraction.update`、
`workflow.update`、`workflow.status.change`、四类 `workflow.*Step.update`），引擎按**原审批范围**重放
一次（等 400 ms 后重放 + 重新回读），不回到审批窗、不扩大写入范围；重放通过记 `AUTO_REPAIR_OK`，
仍不过记 `AUTO_REPAIR_FAILED` 并按原规则处理。新建类（`*.create` / `*.add`）**绝不自动重放**——
多建一条记录的代价比多问一次人大得多。

```mermaid
sequenceDiagram
    autonumber
    participant A as 模型
    participant H as host
    participant F as 本机数据目录
    participant E as eGMP

    H->>H: applyPlan 复验：计划存在且未过期、指纹匹配、<br/>目标环境与账号匹配、审批未用过
    H->>F: 有写入且要求审批时，先把审批标记为已用
    loop plan.steps
        alt action=skip
            H->>H: 记 skipped（已存在且一致）
        else 只读查询
            H->>E: invokeCapability
            H->>H: 记 ok（只读查询，不写平台），不参与审批判定
        else 真正写入
            H->>E: invokeCapability（method + path + params）
            alt 瞬时错误（502/503/504、连接被重置）
                H->>H: isTransientFailure → 等 500 ms 重试一次，记 issue TRANSIENT_RETRY
                H->>E: 重试
            end
            alt 写入成功
                H->>E: verifyStep 回读
                alt 回读一致
                    H->>F: config-audit 记 verified
                else 没有回读判据（能力目录里没有对应查询）
                    H->>F: 记 unverified + issue NO_CRITERIA，继续下一步
                else 回读没对上（可能只是晚一拍）
                    H->>H: 等 300 ms，用新上下文再读一次
                    alt 这次对上
                        H->>F: 记 verified + issue VERIFY_EVENTUAL
                    else 仍然没确认
                        H->>H: 幂等写入？按原审批范围重放一次（记 issue AUTO_REPAIR）
                        alt 重放后回读通过
                            H->>F: 记 verified + issue AUTO_REPAIR_OK
                        else 仍然对不上
                            H->>F: 记 failed（有判据即门），后面的步骤不发包
                            H-->>A: ok=false / status=failed / results
                        end
                    end
                end
            else 写入报错
                H->>E: 失败也要回读一次（防「报错但已落库」）
                alt 回读确认已生效
                    H->>F: config-audit 记 verified + issue WRITE_ERROR_BUT_APPLIED
                else 回读也没确认
                    H->>H: 幂等写入？自动重放一次（记 issue AUTO_REPAIR）
                    alt 重放后回读通过
                        H->>F: 记 verified + issue AUTO_REPAIR_OK
                    else 仍然没确认
                        H->>F: 记 failed，保存计划（checkpoint），后面的步骤不发包
                        H-->>A: ok=false / status=failed / results
                    end
                end
            end
        end
    end
    H->>H: 执行完跑一次 verifyAppliedPlan：换全新查询上下文，<br/>把每一步重新读一遍（工作流另核 workflow.steps.list 节点链）
    H->>F: 写 plan.verification + plan.issues，计划状态 applied 或 failed
    H-->>A: ok / status / results / approval / issues / verification
```

三个容易误判的细节：

- 平台存在「HTTP 200 + `code=401`，但数据已经落库」的端点（实测 `picklist.save`）。所以**写报错
  也要回读一次**：回读确认已生效就记 `verified`，否则记 `failed` 并停，避免模型重试把数据写两遍。
- 结果里的 `unverified` 现在只剩一种来源：**能力目录里没有回读判据**（`NO_CRITERIA`）。有判据的
  写入回读不上就是 `failed` 并停——「执行一次、验证一次」要求配四步工作流节点就有四次回读，不能
  拿错误状态继续往下配。
- **菜单也有回读判据了**：`menu.list`（`?menuType=1`）按 code 找菜单，核对父菜单与绑定对象，
  不再返回「平台无按 code 回查菜单的接口，需人工确认」。

两条「别信接口返回」的实测教训：

- `lifecycle.status.update` 的 `sort` 是**假成功**：2026-11-26 三次改排序都返回成功，`Status/GetList`
  读回来还是旧值。现在只要提交了 `sort` 就必须核回来，核不上记成不一致（非严格，不阻断，但台账留痕）。
- 「严格验证」和「有没有判据」必须对齐：单测断言严格集 ⊆ 有回读判据的能力集，否则会出现
  「要求严格回读 → 却没判据 → 永远 failed」的死锁。

### 5.3 任务级：多段一条任务，只审一次

上面的图 4 / 图 5 画的是**单计划**路径。当一条任务里有多个阶段（建对象 → 建字段 → 改表单布局 →
改列表布局 → 建菜单 → 配工作流 → 挂载）时，走任务路径，审批只发生一次：

1. `config_plan({task})` 逐段算出「预览计划」，拼成一份任务摘要（步骤 id 带阶段前缀 `1.s1`，
   标题带阶段名），落 `config-tasks/tsk_*.json`；此时**不写任何计划文件、不碰平台**。
2. `config_apply({taskId})` 按任务指纹 `taskHash`（环境 + 账号 + 全部阶段含顺序）生成审核文档并挂审批；
   弹窗与审核文档看到的是整条任务的分段步骤。
3. 人点「同意」后，host 才逐段 `buildPlan` → `savePlan` → `applyPlan(requireApproval:false)`：
   **每段在轮到它时重新读现状、重新算差异**，所以字段/布局能引用前一段刚建出来的对象。
4. 任一段失败即停，已执行段与失败段写回 `tsk_*.json` 的 `results`（checkpoint）；任务状态 `failed`。
   任务审批是一次性的，失败后必须重新 `config_plan` + 重新审批，不能拿旧 `taskId` 续跑。

任务级与单计划的差别只在「审批覆盖几段」：单计划 30 分钟过期，任务 60 分钟过期；单计划指纹覆盖
一份 blueprint，任务指纹覆盖阶段顺序。执行引擎、逐步回读、审计与 checkpoint 完全复用。

### 5.4 输出绑定：把前序步骤的真实 id 传给下一步

平台生成的 workflow / step / control / task id 有前后依赖，计划在**生成时**还看不到这些值。参数需要
前序输出时，写单键对象 `$ref`，不要填临时假 id：

```json
{ "$ref": "steps.s1.after.data.id" }
{ "$ref": "stages.1.steps.s3.after.data.step.id" }
```

- `steps.<stepId>.after[.path]`：同一计划内、只允许引用更早步骤的返回。
- `stages.<n>.steps.<stepId>.after[.path]`：任务级跨阶段，`n` 从 1 开始，只允许引用已执行阶段。
- 路径支持属性与数组下标（如 `.data[0].id`）；缺失路径、未来步骤、未来阶段、非法 `$ref` 都在计划或
  执行前报错，不会静默传 `undefined`。
- 审批指纹保留原始 `$ref` 模板，执行时按真实返回解析；审核文档的「输出绑定」段会列出引用，避免
  审批时是一套值、执行时换成另一套值。
- 任务级多段依赖应装进**一条任务、一次审批**；任一段失败即停，已执行段的 `after` 会保留在 checkpoint。

### 5.5 事后确认：`akso_workbench_config_verify` 怎么读

执行完配置、或拿不准平台现在到底是不是目标状态时，用只读工具 `akso_workbench_config_verify`
（`AKW-TOOL-CONFIG-VERIFY-V1`，不弹审批、不写平台）重新确认一遍。它不信任 apply 时的那次回读：
换一个全新的查询上下文，把计划里每一步重新读一遍，产出 `VerificationReport`，并把报告写到
`<当前工作区>/akso-workbench-data/verifications/验证-<标题>-YYYYMMDD-HHmmss.md`。

| 传什么 | 它做什么 | 结论怎么看 |
|---|---|---|
| `planId` | 按计划文件逐步复验 | `ok` 全绿说明现在确实是目标状态；`failed` 段逐条列出哪一步对不上与原因 |
| `taskId` | 取 `results[].planId`，逐段复验 | 段级 `verification` 各自独立，`failed` 段即问题所在 |
| `workflowConfigId` | 只列节点链 | `hasStart`/`hasEnd` 为真、计划里的 step id 都在链上，才算流程结构完整 |

`plan.status === 'planned'` 说明**还没执行过**，这时确认的是「现在是否已经是目标状态」，不是「我写成功了吗」。
`unverified` 的段在报告里会单独列出，别把「没确认」当成「没写」。

## 6. `akso_workbench_object_analysis`：只读分析

读路径换票（图 2）之后，把「对象 → 生命周期 → 状态 → 动作 → 工作流」一次性读成模型；只读，
**不写平台**。局部接口失败只进 `warnings`，不猜、不编。每次调用都落一条分析历史（失败也落，带
错误原因与发起时的环境/账号），页面与工具共用同一份历史。

```mermaid
sequenceDiagram
    autonumber
    participant A as 模型
    participant D as DSH 工具注册表
    participant H as host
    participant F as 本机数据目录
    participant E as eGMP

    A->>D: akso_workbench_object_analysis(query)
    D->>H: execute
    H->>H: ensureEgmpSession（图 2；读路径不要求账号在配置池里）
    alt 没有会话
        H-->>A: ok=false / error + _uid
    else 有会话
        H->>E: GET BasicObject/AllObjectWithType（按 id 反查 code）
        H->>E: GET BasicObject/CODE + BasicObject/field/CODE
        H->>E: GET Status/GetList（全量，按 lifecycleId 过滤）
        H->>E: GET StatusEnterRule/GetList（进入条件）
        H->>E: GET UserAction/GetUserAction + GetListByStatusId<br/>GET EnterAction/GetListByStatusId + GetById
        H->>E: POST Workflow/GetWorkflowBasicPageViewList<br/>GET Workflow/GetWorkflowSteps + GetWorkflowStepWithDetails
        H->>H: 用 behaviorType 码表翻译动作；解析目标状态、关联对象、工作流名称<br/>读不到的保留 id 并进 warnings
        H->>F: 写 <dshHome>/akso-workbench/analysis-history/ID.json（只留最近 50 条）
        H-->>A: ok=true / historyId / object / lifecycle / statuses / fields / workflows / totals / warnings
    end
```

查询参数是对象 id（UUID）或 code（如 `homework2__c`）；返回里的 `source` 带上本次实际用的
`baseUrl` 与 `account`，报告/流程图必须把它写进去，避免和环境判断混在一起。

## 7. 运行日志：一次调用留了什么、怎么带走

DSH 的宿主日志（`ctx.logger`）只在开发机控制台里，**用户拿不到**，所以「别的同事用出问题」时
我们手里什么都没有。运行日志（journal）补的就是这一段：host 把工具调用、HTTP、业务动作与页面异常
写成 JSONL，页面上给一个「下载日志」按钮，用户把文件发回来即可复盘。它**不替代**审计与审批记录
（那两类仍是写入口径），只是给支持用的使用轨迹。

```mermaid
sequenceDiagram
    autonumber
    participant M as 模型 / 页面
    participant H as host（journal）
    participant D as 本机磁盘 journal/*.jsonl
    participant U as 用户（运行日志页）

    Note over M,H: ① 所有调用都留痕（写盘失败不影响响应）
    M->>H: 工具 execute（tool.invoke / tool.done / tool.fail）<br/>或任意 HTTP 请求（http 事件）
    H->>H: redactValue()：敏感 key → [redacted]<br/>JWT / Bearer → 占位符
    H->>D: appendFileSync(YYYY-MM-DD.jsonl)<br/>超 4 MB 换 .2；30 天 / 96 MB 封顶

    Note over U,H: ② 用户出问题时打开「运行日志」
    U->>H: GET journal/summary
    H-->>U: 事件数 / 体积 / 最早最新 / 日志目录
    U->>H: GET journal/download?days=7（或 30）
    H->>D: readJournalRecords(dir, days)
    H->>H: 首行 journal.meta（环境 + 版本）<br/>超 12 MB 从最旧丢，标 truncated
    H-->>U: application/x-ndjson 附件<br/>X-Journal-Events / X-Journal-Truncated
    U->>M: 用户把下载的文件发给支持同学（手动）

    Note over M,H: ③ 页面异常也带回
    M->>H: POST journal/log（只收 client.* / ui.*）
    H->>D: 同上：消息 + 栈前 6 行，不记页面内容

    Note over H,D: ④ 排查：按 UID / planId / historyId / 时间 grep
```

**记哪些事件**（`kind` 字段）：

| 事件 | 何时 | 关键字段 |
|---|---|---|
| `tool.invoke` / `tool.done` / `tool.fail` | 每次工具调用 | `tool`、`uid`、`ms`、`ok`、参数与结果的摘要 |
| `http` | 每条本机 API 请求 | `method`、`path`、`status`、`ms` |
| `cloud.login` / `cloud.logout` / `cloud.sync` / `cloud.device.*` | 云端账号动作 | `email`（打码）、`revision`、`ok` |
| `egmp.token.mint` / `egmp.needs-account` / `egmp.logout` | 换票与账号门 | `accountId`、`baseUrl`、`refreshed` |
| `approval.wait` / `decide` / `timeout` / `cancelled` / `busy` | 审批窗 | `planId`、`verdict`、`waitedMs` |
| `plan.created` / `apply.run` | 计划与执行 | `planId`、`status`、`steps`、`failed`、`verified` |
| `analysis.run` | 对象分析（工具与页面共用） | `query`、`object`、`baseUrl`、`fields`、`warnings`、`ms` |
| `error.api` / `error.tools` | 路由与工具抛错 | `message`、`code`、`traceId`、`path` |
| `client.*` / `ui.*` | 页面请求与操作、`ui.error` | `path`、`message`、`stack`（前 6 行） |
| `app.start` | host 装载插件 | `plugin`、`node`、`platform`、`routes`、`dshHome` |

**边界**：保留 30 天；单文件 4 MB、每天最多 5 个；总量 96 MB；单次导出 12 MB（超出从最旧丢）。
写盘前统一打码：敏感 key（password/token/secret/authorization…）→ `[redacted]`，JWT/Bearer → 占位符；
**不写请求体原文**。日志写不进去会在 `journal.record` 内部吞掉——宁可少一条，也不能让工具调用失败。

**怎么读**：下载文件是 NDJSON，第一行 `kind: "journal.meta"`（format/version/days/events/插件版本/DSH_HOME/Node/系统），
其余每行一条记录，按 `ts` 升序。常用查法：`grep '"kind":"tool.invoke"'` 看模型调了什么；
`grep '"planId":"pln_'` 追一张计划的审批与执行；`grep '"historyId"'` 找某次分析的产物；先看 `ui.error` 再看时间相邻的 `http`。

页面路径：一级导航 **运行日志** → 概览（事件数/体积/时间范围/目录）→「下载 7 天」「下载 30 天」，
另可「复制日志目录」直接到资源管理器里翻原始文件。

## 8. 出错时怎么对号

| 返回 / 错误 | 什么时候出现 | 平台是否被写过 | 下一步 |
|---|---|---|---|
| `status: needs-account` | 账号条为空、账号不在配置池、换不到票 | 没有 | 账号条连接账号（或让用户点「添加账号」）后重试 |
| `status: busy` | 已有一条审批在等回复 | 没有 | 先处理屏幕上那条审批 |
| `status: denied` | 人点了「拒绝」 | 没有 | 问用户原因，改蓝图重新出计划 |
| `status: supplement` | 人点了「补充」，`note` 是意见 | 没有 | 按 `note` 改蓝图，重新 plan 后重新审批 |
| `status: approval-timeout` | 审批窗 8 分钟没人回应 | 没有 | 重新调用 apply，会重新弹窗 |
| `status: cancelled` | 工具调用被中断（没有收到同意） | 没有 | 重新调用 apply |
| 「这个计划还没有经过人工审批」 | 计划没有审批记录（绕过工具直调引擎） | 没有 | 走工具，在弹窗里点同意 |
| 「审批记录与计划内容不一致」 | 审批指纹与计划文件不一致（计划被改过） | 没有 | 重新 plan，重新审批 |
| 「这个计划的审批已经用过了」 | 这张审批已经被消费过 | 没有 | 重新 plan，重新审批 |
| 「这个计划没有记录目标环境与账号」 | 旧计划没有 `target` | 没有 | 重新 plan |
| 「计划的目标环境与当前会话不一致」或「计划绑定的账号与当前账号不一致」 | 计划 target 与当前会话不符 | 没有 | 换回计划绑定的账号，或重新 plan |
| 「这个计划没有绑定账号」 | 计划没有 `accountId` | 没有 | 账号条选好账号后重新 plan |
| 「计划已过期（30 分钟）」 | plan 太久没执行 | 没有 | 重新 plan |
| `status: failed` | 某步写入报错且回读也没确认，已停 | **可能写过**（前面的步骤） | 看 `results` 与 audit；不要重跑同一计划，重新 plan |
| 每步 `unverified` | 能力目录里没有回读判据（`NO_CRITERIA`），写请求本身没报错 | 可能已写 | 人工回读核对；它能继续跑，但有判据的写入回读不上会直接 `failed` 并停 |
| `status: no-approval-needed` | 计划/任务里全是只读查询，或调试模式关了审批 | 没有写入 | 正常结果，不是错误；`approval.reason` 说明是 `read-only` 还是 `disabled` |
| `reviewRegenerated: true` | 传的 `reviewPath` 已过期/指纹不符/文档已结束，host 按当前计划补了一份新文档 | 没有 | 正常结果，旧文档保留；把新路径告诉用户再决定要不要继续 |
| 返回里的 `issues[]` 非空 | 回读晚一拍、瞬时 5xx 重试成功、幂等写入自动重放、没有回读判据的降级（`NO_CRITERIA`）、写报错但已生效 | 视条目而定 | 不是失败；按 `code`（`VERIFY_EVENTUAL` / `TRANSIENT_RETRY` / `AUTO_REPAIR` / `AUTO_REPAIR_OK` / `NO_CRITERIA` / `WRITE_ERROR_BUT_APPLIED` 等）判断要不要人工复核 |

> 表里引号中的中文是工具返回的 `error` 文本；对应的英文 code（`APPROVAL_REQUIRED`、
> `TARGET_CHANGED` 等）是引擎内部的 `EgmpError.code`，不随工具返回，排查时按中文文本 grep。

HTTP 直调（页面/调试口）也过同一套门：`POST /api/akso-workbench/config/invoke` 的写类能力
默认 403（`AKSO_WORKBENCH_ALLOW_DIRECT_WRITE=1` 仅本机调试），`POST .../config/apply` 同理；
只读但用 POST 的查询（`readonly: true`）放行。

## 9. 排查一次调用

按这个顺序，通常三步内能定位：

1. **模型侧**：返回值里的 `_uid` 和工具列表 description 末尾的 `UID: …` 对上，确认调的是哪个工具。
2. **host 日志**：`grep "[AKW-TOOL-CONFIG-APPLY-V1]"`，看 `invoke` / `done ok=` / `failed`；
   `done` 的耗时能区分「卡在审批」还是「卡在平台」。
3. **落盘文件**（状态/日志在 `<dshHome>/akso-workbench/`，生成物在工作区 `akso-workbench-data/`）：
   - `akso-workbench-data/config-plans/pln_*.json`：计划原文（含 `target`、`hash` 输入、每步 `params`）
   - `akso-workbench-data/config-tasks/tsk_*.json`：任务原文（阶段、指纹、过期时间）
   - `config-approvals/pln_*.json`：`hash`、`at`、`usedAt`（有 `usedAt` = 已消费，日志系统数据）
   - `config-audit/`：`<YYYY-MM>.jsonl` 每步一条（`status` / `traceId` / `durationMs`），
     `approvals-<YYYY-MM>.jsonl` 审批决策一条（日志系统数据），
     `issues-<YYYY-MM>.jsonl` 执行期的小问题台账（`code` / `stepId` / `message`）
   - `akso-workbench-data/verifications/验证-*.md`：事后确认报告（`akso_workbench_config_verify` 或 apply 结束时自动生成）
   - `akso-workbench-data/reviews/<任务名>-<YYYYMMDD-HHmmss>.md`：创建/调整任务的审核文档（工作区下，不入库）；
     任务成功后状态改为「已结束（审核文件作废）」并附执行结果；已结束文档不可再改
   - `akso-workbench-data/reviews/<任务名>-更新-<YYYYMMDD-HHmmss>.md`：已执行任务的小范围更新文档，
     篇幅更短，带关联的已执行文件与调整原因
   - `<dshHome>/akso-workbench/analysis-history/<id>.json`：分析结果全文
   - `egmp-session.json`：只有票与来源账号，没有口令

审批窗不出现时：先看 host 是否在跑（`configApprovalPending` 每 2 秒轮询，失败只打 `console.debug`），
再看 `state.needsAccount` 是否把界面引到选账号面板了——没有账号时计划根本不会生成，也就没有审批。

## 10. 与代码的对应

| 文件 | 职责 |
|---|---|
| `plugin/src/tool-uids.ts` | UID 登记、`withToolUid()` 包装（日志 / `_uid` / 未登记抛错） |
| `plugin/src/tool.ts` | `akso_workbench_accounts` 的实现与返回字段裁剪 |
| `plugin/src/config/tools.ts` | `config_plan` / `config_apply` / `config_verify` 工具；审批报告与业务摘要 |
| `plugin/src/config/engine.ts` | 读现状、diff、计划文件、`applyPlan` 复验、逐步执行与回读、`verifyAppliedPlan` 事后确认、审计 |
| `plugin/src/config/approval-policy.ts` | 审批判定单一来源：什么算写入、要不要弹窗、`approval{needed,reason,writes,readonly,skipped}` |
| `plugin/src/config/issues.ts` | 小问题台账：`IssueRecord`、`issues-<YYYY-MM>.jsonl`、`issue.recorded` 事件 |
| `plugin/src/config/verification.ts` | 事后确认报告：`VerificationReport`、Markdown 渲染、写 `akso-workbench-data/verifications/` |
| `plugin/src/config/approval.ts` | 审批门：单条、8 分钟超时、abort 结算 |
| `plugin/src/config/approval-review.ts` | 生成 `create` / `update` 两类审核文档、计划指纹校验、任务结束作废；已结束文档禁止再改，更新另起新文件 |
| `plugin/src/index.ts` | HTTP 路由、`snapshot`、`ensureEgmpSession` / `openWriteSession`、工具注册 |
| `plugin/src/analysis-tool.ts` | `object_analysis` 工具外壳与返回裁剪 |
| `plugin/src/analysis.ts` | 对象/状态/动作/工作流的只读读取 |
| `plugin/src/analysis-history.ts` | 分析历史（最多 50 条） |
| `plugin/src/client/strip.tsx` | 审批窗：2 秒轮询、同意/拒绝/补充 |
| `plugin/src/journal.ts` | 运行日志：打码、切天、轮转、保留期、导出 |
| `plugin/src/client/logs.tsx` | 页面「运行日志」：概览 + 下载 7/30 天 |
| `plugin/src/cloud.ts` | akso-cloud HTTP（`/api/ui/state`、`/api/accounts/:id/egmp-token` 等） |
| `plugin/src/credentials.ts` | 凭据来源：云端代登录，口令不出云端 |

## 11. 什么时候要改这篇

- 新增/改名/删除工具：先登记 UID（`plugin/src/tool-uids.ts`），再补一张图和 §1 的表。
- 改审批判定（新增 verdict、改超时、改「一次只挂一条」、改「哪些步骤算写入」）：改 5.1、`approval-policy.ts` 的说明与 §8。
- 改执行期的容错（新增 issue 码、改重试策略、改回读判据）：改 5.2 与 §8，并同步 `AGENTS.md` 的那节。
- 新增/改事后确认（`verifyAppliedPlan`、`config_verify` 的入参或结论口径）：改 5.5。
- 改会话来源（不再只认账号条、换票端点变化）：改 §2。
- 新增会写平台的路径（新工具、新 HTTP 直调口）：补图，并确认它过 `applyPlan` 的同一道门。
- 新增落盘文件：补 §9。
- 新增/改运行日志事件（`recordEvent`、`track`、新路由）：改 §7 的事件表与图 8，并同步 `docs/knowledge/日志与排查.md`。
- 改完图重渲染 §12 的 PNG，否则图片会和 Mermaid 源码对不上。

## 12. 渲染版（PNG）

**Mermaid 源码是唯一事实来源**；PNG 只是渲染快照，给不方便渲染 Mermaid 的场合（邮件、PPT、聊天）
用。改图先改上面的代码块，再重渲染，不要把 PNG 当成源文件手改。

| 图 | 文件 |
|---|---|
| 1 一次工具调用的外壳 | [fig-1.png](diagrams/agent-flows/fig-1.png) |
| 2 公共前置：账号门与换票 | [fig-2.png](diagrams/agent-flows/fig-2.png) |
| 3 `akso_workbench_accounts`：读快照 | [fig-3.png](diagrams/agent-flows/fig-3.png) |
| 4 `akso_workbench_config_plan`：只出计划 | [fig-4.png](diagrams/agent-flows/fig-4.png) |
| 5 `akso_workbench_config_apply`：审批 | [fig-5.png](diagrams/agent-flows/fig-5.png) |
| 6 `akso_workbench_config_apply`：引擎执行 | [fig-6.png](diagrams/agent-flows/fig-6.png) |
| 7 `akso_workbench_object_analysis`：只读分析 | [fig-7.png](diagrams/agent-flows/fig-7.png) |
| 8 运行日志：留痕 → 概览 → 下载 | [fig-8.png](diagrams/agent-flows/fig-8.png) |

重渲染（仓库根目录，需要本机有 Chrome；`mmdc` 会把每个 Mermaid 块输出成 `fig-1`…`fig-8`）：

```powershell
npx -y @mermaid-js/mermaid-cli -i docs/knowledge/agent调用时序.md -o docs/knowledge/diagrams/agent-flows/fig.png
```

下载不到 Chromium 的环境，加 `-p puppeteer.json` 指向本机 Chrome（文件内容：
`{"executablePath":"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe","args":["--no-sandbox"]}`）。
