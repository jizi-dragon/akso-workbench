# akso-workbench 开发规则

本文件是仓库级约束；改代码、加工具、加接口前先读一遍。

## 工具 UID（DSH 可调用的工具）

**任何注册给 DSH 的工具都必须有稳定 UID。** 这是硬门禁，不是命名建议。

- **格式**：`AKW-TOOL-<SLUG>-V<n>`，全大写、连字符分隔，例如
  `AKW-TOOL-ACCOUNTS-V1`、`AKW-TOOL-CONFIG-PLAN-V1`。
- **登记位置**：`plugin/src/tool-uids.ts` 的 `TOOL_UIDS`。新增/改名工具时**先登记，再注册**。
- **注册方式**：`withToolUid(definition, logger)`。未登记 UID 的工具会在注册期直接抛错，
  不允许“先上线再补”。
- **可观测性**：包装后
  - 工具 `description` 末尾追加 `UID: …`，agent 在工具列表里能直接看到；
  - 每次 `execute` 的返回对象带 `_uid`；
  - host 日志打 `[UID] invoke/done/failed <tool>`，DSH 侧一次调用能与 host 日志对上。
- **稳定性**：UID 一旦用于排查就**不复用**到别的工具；工具删除时保留 UID 并注明 retired，
  避免历史日志对错工具。
- **验证**：`plugin/test/host.test.ts` 断言五个注册工具的 UID 齐全、唯一、格式正确，
  且未登记工具会被拒绝；加工具时必须同步更新该断言。
- **调用链文档**：五个工具一次调用的完整链路（账号门 / 审批窗 / 引擎复验 / 分析读取 / 运行日志）见
  `docs/knowledge/agent调用时序.md`；改工具、改审批判定、改会话来源、改 journal 事件时**同一提交里改图**，
  并按该文档 §12 重渲染 `docs/knowledge/diagrams/agent-flows/` 的 PNG。

## 日志与审计

- **四类记录分开写，不要混在一起**：
  - 写入审计 `config-audit/<YYYY-MM>.jsonl`：每步一条，含 `planId/env/baseUrl/accountId/capabilityId/status/traceId/durationMs`；**不记 params**（敏感内容不进日志）。
  - 审批决策 `config-audit/approvals-<YYYY-MM>.jsonl`：含 `at/planId/env/baseUrl/accountId/hash/verdict/note`；拒批、补充、超时、中断都要留痕，不能只记"写了什么"。
  - HTTP access：`[http] METHOD PATH -> STATUS Nms`，一条请求一行；2xx/4xx 走 info，5xx 走 warn。围栏挡掉的 403 也要有。
  - 运行日志 `$DSH_HOME/akso-workbench/journal/<YYYY-MM-DD>.jsonl`（见下）：给**用户下载回来**用的使用记录，与上面三类不重复写。
- **运行日志（journal）**：`plugin/src/journal.ts` 写盘，页面「运行日志」页读概览并下载 7/30 天（`journal/summary|download|log` 三条路由）。
  - 内容：`tool.invoke/done/fail`、`http`、`cloud.*`、`egmp.*`、`approval.*`、`plan.created`、`apply.run`、`analysis.run`、`error.*`、`client.*`、`ui.*`、`app.start`。
  - 保留 30 天；单文件 4 MB 换 `.2…`（每天最多 5 个）；总量 96 MB；单次导出 12 MB（从最旧丢并在 meta 标 `truncated/dropped`）。
  - **写盘前统一打码**（`redactValue`：敏感 key → `[redacted]`，JWT/Bearer → 占位符）。新增事件只传摘要：不传 token、口令、请求体原文、完整账号表；工具参数只记形状（`summarizeArgs`）。
  - 浏览器侧 `track()` 只允许 `client.*` / `ui.*` 前缀；未捕获的 `window.onerror` / `unhandledrejection` 记 `ui.error`（消息 + 栈前 6 行）。
- **工具日志**：`[UID] invoke/done/failed`；`done` 要带耗时与 `ok=<bool>`（业务失败但没抛异常时 `ok=false`），不能把 `denied`/`needs-account` 记成成功。
- **不许静默吞异常**：浏览器打开失败、审批窗轮询失败、审计读取失败，至少落一条 warn/debug。
  **唯一例外是 journal 本身**：日志写不进去不能影响工具与页面（`journal.record` 内部吞错），这是有意的，改它之前先想清楚会不会把请求打挂。
- **写日志的铁律**：不记 token、口令、请求体原文；审计记录"谁在什么环境对哪一版计划做了什么"，内容细节用 `planId` 回查计划文件。

## 版本与发布

完整规则见 [`VERSIONING.md`](VERSIONING.md)。硬约束：

- 日常提交：**PATCH +1**（`0.1.0 → 0.1.1`），只提交与构建，不打 tag、不发 Release、不改下载清单。
- 用户明确要求「打 tag / 打包 release / 发布正式版」：**MINOR +1**、PATCH 归零（`0.1.4 → 0.2.0`）。
- **MAJOR +1** 只在项目架构发生重大变化且用户明确要求时进行（`0.10.0 → 1.0.0`）。
- 根 `package.json`、`plugin/package.json`、两个 `package-lock.json` 必须同版本；客户端版本从 `plugin/package.json` 读取。
- **发 release 时上传的资产只装构建产物**：`plugin/{package.json,cordis.patch.yml}` + `plugin/lib/index.js` + `plugin/lib/client.js` + `docs/knowledge/**` + 安装说明；**不装** `plugin/src/**`、`plugin/test/**`、`node_modules/`、`_pack/`（口径与四条判据见 [`VERSIONING.md`](VERSIONING.md) §Release 资产）。

## 其他既有约束

- 配置写入只走「模型工具 + 对话内审批窗」：`engine.applyPlan` 是唯一的门，写入前必须有人
  在弹窗里点「同意」（`config-approvals/<planId>.json` 存计划指纹，一次性）；HTTP 直连默认关闭
  （`AKSO_WORKBENCH_ALLOW_DIRECT_WRITE=1` 仅本机调试，开了也仍过引擎的审批门）。
  审批要求 = 环境变量 `AKSO_WORKBENCH_REQUIRE_APPROVAL` > 页面开关 > 默认要求。
- 审批前必须生成审核文档（问题 / 执行计划 / 预期结果 checklist）：host **只写**
  `<当前工作区>/akso-workbench-data/reviews/`（不入库）；调用方必须传 `review.workspaceDir`，或由
  `exec` 提供工作区根。两者都没有时返回 `review-unavailable`，**不回落 profile、不写仓库内的旧
  `.akso-workbench` 或 `docs/analysis`**；`AKSO_WORKBENCH_REVIEW_DIR` / `AKSO_WORKBENCH_APPROVAL_DIR` 仅作测试/显式覆盖。
  弹窗显示任务概述、文档地址与「在右侧打开」，用 DSH 的 `sidebarRight.openResource` 打开。
  文档同时写计划指纹与执行明细（方法/路径/参数），`config_apply` 在审批前校验文档存在且指纹匹配；
  **指纹不一致或文档已结束时自动按当前计划补一份新文档**（见下节），只有文档根本写不出来
  （没有工作区根）才 `review-unavailable`，**不进入审批、平台一步不写**。
  可传 `review{kind,taskName,problem,approach,expected[]}` 补充内容。
- 两步审批：先 `config_apply({planId, review, prepareOnly:true})` 只生成审核文档并返回路径/地址
  （`status:'prepared'`，不弹窗、不写平台）；assistant 先把目标/方案/结果和文件链接发给用户，
  再用 `config_apply({planId, reviewPath})` 正式进入审批，复用同一份文档，不能重新生成另一份。
- **任务级审批（多段默认走这条）**：一条任务 = 多个有序阶段，整条任务只弹**一次**窗。
  用 `config_plan({task:{name, stages:[{name, blueprint}]}})` 出任务（返回 `taskId`），
  再用 `config_apply({taskId, …})` 执行；审批指纹 `taskHash` 覆盖环境、账号与全部阶段（含顺序）。
  同意后逐段**现算**计划再执行（所以字段/布局能用上前面刚建出来的对象），任一段失败即停，
  已执行段留在 `results` 里作 checkpoint。任务 **60 分钟**过期；失败/中断后必须重新出任务、重新审批，
  不能拿同一张审批续跑。单段写入仍可用 `planId`，行为不变。
- **输出绑定（2026-11-26）**：参数需要引用前序步骤刚生成的 id 时，不要把假 id 写死；用单键对象
  `{ "$ref": "steps.s1.after.data.id" }`（同计划）或 `{ "$ref": "stages.1.steps.s1.after.data.id" }`
  （任务跨阶段，阶段号从 1 开始）。只能引用更早步骤/已执行阶段；引用模板会进 `planHash`/`taskHash`,
  执行时按真实返回解析，缺失/未来步骤/未来阶段/坏路径都在计划或执行前报错。任务级多段依赖（工作流拿
  上一步的 id、字段拿对象 id）应装进一条任务、只审一次，不要为每个 id 重开一张审批。
- **配置引擎硬门禁（2026-11-26）**：计划/任务都带 `engineBuild`，与当前源码常量不一致时拒绝执行；
  工作流/状态按钮等严格写入的 `unverified` 回读视为失败并立即停止，不再继续发布。`buildPlan` 还会预检：
  `workflow.create` 按 name/code 查重（`WORKFLOW_DUPLICATE`）、`lifecycle.useraction.create` 按状态查重
  （`USER_ACTION_DUPLICATE`）、`workflow.task.add/update` 的审核按钮必须 `isHaveSign && isRequireSign`
  （`TASK_SIGNATURE_MISSING`）。改已发布按钮用 `lifecycle.useraction.update`，不要再直接 PATCH 绕过审批。
- 审核文档分两类：`kind:'create'` 用于大规模创建/调整，文件名 `<任务名>-YYYYMMDD-HHmmss.md`，含完整执行明细；
  `kind:'update'` 用于已执行任务的小范围优化/更新，文件名 `<任务名>-更新-YYYYMMDD-HHmmss.md`，篇幅更短，
  只含任务概述/调整计划/预期 checklist/本次操作清单，可带 `previousReviewPath` 与 `reason`。
- 审核文件生命周期：文档先于审批，是任务的「第一目标」；预期结果 checklist 未完成前不发起第二次审批。
  只有任务成功结束后 host 才把文档标记为「已结束（审核文件作废，仅作历史记录）」并附执行结果；
  拒批、超时、执行失败都不作废。**文档变为已结束后禁止再改原文件**；后续调整必须新建 `kind:'update'`
  文档，注明关联的已执行文件与原因，保证审核文件权威性。
- 写入账号必须来自底部账号条（`prefs.currentAccountId`）且在配置池里；账号条为空时工具返回
  `needs-account`、界面自动弹选账号面板，**不许**回落到磁盘上的旧会话或别的账号。
  计划绑定 `target{baseUrl,accountId}`，apply 前比对，不一致直接 `TARGET_CHANGED`。
- 口令不离开 akso-cloud；host 只拿「账号 id」换 eGMP 令牌，日志/响应里不得出现 token 原文。
  本机不再接受明文口令（`session.login` / `POST config/login` / `auth.login` 能力都已删除）。
- 改完跑：`npm run check`（根 typecheck + capture 测试 + 插件 typecheck / 测试 / 构建）。只改插件时可跑
  `npm run typecheck:plugin`、`npm --prefix plugin test`、`npm --prefix plugin run build`。

## 审批判定 / 容错 / 执行后确认（2026-11-27）

- **审批判定只有一处**：`plugin/src/config/approval-policy.ts` 的 `approvalScope(steps, requireApproval)`。
  `tools.ts`（弹不弹窗、生不生成文档）与 `engine.applyPlan`（要不要审批记录）**必须共用它**，
  各写一份判据就会出现「工具跳过审批、引擎仍要审批记录」的死路。
  只读判据 = 能力目录 `readonly === true` 或 `method === 'GET'`（蓝图 `call` 步骤把它记进 `step.readonly`）；
  `writes === 0` 时 `reason: 'read-only'`，**不弹窗、不生成审核文档**，返回 `status: 'no-approval-needed'`。
  报告里的 `approval{needed,reason,writes,readonly,skipped}` 就是这件事的对外说法。
- **审核文档不再白跑**：`config_apply({reviewPath})` 发现文档指纹与当前计划不符、或文档已结束时，
  按**当前计划重新生成**一份（旧文档原样保留），返回 `reviewRegenerated: true` 并在 message 里说清；
  只有连文档都写不出来（没有工作区根）才 `review-unavailable`。
- **小问题台账**：`plugin/src/config/issues.ts`。执行中「回读晚一拍才生效」「瞬时 5xx 重试成功」
  「写报错但回读证明已生效」都记一条 `IssueRecord`，追加写
  `$DSH_HOME/akso-workbench/config-audit/issues-<YYYY-MM>.jsonl`，同时投一条 `issue.recorded` journal 事件；
  `config_apply` 的返回带 `issues[]` 与一句话摘要，`config_plan` 的返回带 `approval` 预览
  （`needed/reason/writes/readonly`，出计划时就先说清这次要不要弹窗）。
- **执行一次、验证一次（2026-11-27 用户口径）**：每一步写入**立刻**回读，回读不符先就地修一次，
  **仍不符就停在这一步**，后面的步骤一个包都不发（`plan.status` 直接 failed，返回里点명「停在第 N 步」）。
  判据是「这一步有没有可核对的东西」：有判据的写入**一律是门**（不再分严格/非严格）；
  只有能力目录里根本没有对应查询接口的写入才降级——记 `NO_CRITERIA` 继续跑，台账里留痕。
  用户要的是“配完四步工作流节点，就该有四次回读”，不是最后再统一跑一轮。
- **重试只对瞬时错误**：`isTransientFailure` = HTTP 502/503/504 或 `ECONNRESET/ETIMEDOUT/EAI_AGAIN/
  socket hang up/fetch failed`；只重试**一次**。写成功却回 401 的那类由回读兜底，**绝不重试**（会写两遍）。
- **小错就地修（`AUTO_REPAIR`）**：回读没过、或写入报错时，若这一步属于**幂等写入**
  （`canAutoRepair`：field/picklist/layout/listlayout/lifecycle.status.update/lifecycle.useraction.update/
  workflow.update/workflow.status.change/四类 `workflow.*Step.update`），引擎按**原审批范围**重放一次
  （等 400 ms 后重放 + 重新回读），不回到审批窗、不扩大写入范围；通过记 `AUTO_REPAIR_OK`，
  仍不过记 `AUTO_REPAIR_FAILED`。新建类（`*.create` / `*.add`）**绝不自动重放**。
- **严格验证必须对得上判据**：单测断言「严格集 ⊆ 有回读判据的能力集」（`hasReadbackCriteria`），
  否则会出现「要求严格回读 → 却没判据 → 永远 failed」的死锁。新增严格能力时必须同时补判据。
- **接口的“假成功”要写进判据**：`lifecycle.status.update` 的 `sort` 平台不落库（2026-11-26 三次实测），
  现在提交了 `sort` 就必须核回来，核不上记成不一致（非严格，不阻断，台账留痕）。
- **执行后确认**：`engine.verifyAppliedPlan(session, plan)` 用全新查询上下文把每一步重新读一遍，
  产出 `VerificationReport`；apply 结束自动跑一次并写 `plan.verification`。菜单已有回读判据
  （`menu.list` 按 code 核对父菜单与绑定对象），不再「需人工在控制台确认」；工作流另外跑
  `workflow.steps.list` 核对节点链（开始 `stepType 10`、结束 `20`、计划里的 step id 是否都在链上）。
- **确认工具** `akso_workbench_config_verify`（`AKW-TOOL-CONFIG-VERIFY-V1`，只读、不弹窗）：
  传 `planId` / `taskId` 复验并把报告写到 `<当前工作区>/akso-workbench-data/verifications/验证-*.md`；
  传 `workflowConfigId` 只列节点链。**执行完配置要向用户报结论时用它**（或读 apply 返回的
  `verification`），不要凭印象说“应该已经好了”。

## 监听数据边界（2026-11-23 用户口径）

- **监听只用于发现接口与流程步骤**，不是当前配置的真相。对象、工作流、审批矩阵、布局的现状必须通过查询工具/只读 API 读取；捕获只能作为历史证据。
- 引用捕获必须带 `session` 与采集时间；默认不使用超过 24 小时的会话（`--allow-stale` 仅限明确只读旧证据）。禁止拿旧 JSONL 判断当前平台配置。
- `capture` 启动浏览器默认加载 `capture/extension`（最小翻译扩展，仅 Ctrl+X 翻译并复制，无账号/计数/换源）；完整 akso-pass 只在显式 `--extension` 时加载。不能加载时用 Chromium/Chrome for Testing，或在 `chrome://extensions` 手动加载解压目录。
- 监听目录 `capture/` 属于接口发现体系，不参与插件运行时；不要把捕获文件提交到仓库。

## 结构边界（2026-11-17 清理后）

- **`plugin/` 是产品本体**：host 半边（路由 / 工具 / 审批 / 会话 / 日志）+ 浏览器半边（五个模块）。
  根目录不再放平台客户端；Python 移植版 `src/egmp/client.ts` 与 `test/client.test.ts` 已移除，
  eGMP 的域逻辑只在 `plugin/src/` 里维护，不要把旧客户端搬回来。
- **`capture/` 是接口监听体系，必须保留**：`capture/bin/` + `capture/src/` + `capture/test/` 与
  `docs/knowledge/API-DISCOVERY.md` 是一个整体（只连回环地址）。它不参与插件运行时，但删掉/改坏就
  没法再用真实流量扩展能力目录；根 `package.json` 的 `test` 与 `tsconfig.json` 的 `include` 都覆盖它。
  `capture/extension/` 是给 `launch` 用的最小翻译扩展（仅 Ctrl+X 翻译并复制，无账号/计数/换源），
  不属于产品运行时，也不要打包进 workbench 发布物。
- **文档分层**：`docs/knowledge/` 放现行规范与手册，`docs/reference/` 放外部资料与只读样本，
  `docs/archive/` 放带日期的历史台账（只证明当时测过）。
  新增文档前先看 `docs/README.md` 的索引与维护约定。
- **清理死代码的判据**：无入链的文件、无引用的导出/组件/CSS 类，先用 `tsc --noUnusedLocals
  --noUnusedParameters` + 引用计数双确认再删；删完跑 `npm run check`。历史证据不要直接删，
  移到 `docs/archive/` 并加「历史记录」抬头。
- **工作区数据目录（2026-11-22，2026-11-25 收紧）**：akso-workbench 产出的文件统一放
  `<当前工作区>/akso-workbench-data/`，不入库；审批文档在 `reviews/`，对象分析产物在 `analysis/`，
  配置计划/任务在 `config-plans/`、`config-tasks/`，捕获证据在 `captures/`。没有工作区根时直接拒绝，
  不静默回落到 `$DSH_HOME`。**日志系统例外**：`journal/`、`config-audit/`、`config-approvals/` 仍留
  `$DSH_HOME/akso-workbench/`；`cloud.json`/`egmp-session.json`/`pool.json`/`prefs.json` 是账号/会话状态，
  也留 `$DSH_HOME`，不得写进项目工作区。**分析历史例外**：它只是「时间/对象/成功与否/关联分析文件」的
  索引，不是分析产物，固定留 `$DSH_HOME/akso-workbench/analysis-history/`，不随工作区迁移。
- **对象分析产物不入库**：统一放 `<当前工作区>/akso-workbench-data/analysis/`；产物是交付件、
  不是源码；生成后留在本机，不要 `git add`。便笺本、`lifecycle-action-types.md`、样例报告属于源码，放在
  `docs/reference/`，必须入库（改路径要同步 `plugin/src/shared/sticky-notes.ts` 与测试）。
- **对象分析必须走工具并留痕（2026-11-23 用户口径）**：页面/agent 的每一次对象分析都必须经
  `runObjectAnalysis` / `akso_workbench_object_analysis`；成功时由 host 写
  `<当前工作区>/akso-workbench-data/analysis/<对象名>-时间戳/`（md/drawio/png），失败也要写分析历史。
  禁止手工 Node 查询后只在临时目录留 JSON；不得绕过工具直接拼报告。
- **布局规则阅读器（2026-11-23）**：表单布局的 `rules[]` 用
  `npm --prefix plugin run layout-rules -- --capture <抓包.jsonl>`（或 `--file <LayoutDetail.json>`）翻成
  条件 / 效果人话；`--check` 有 error 时退出码 1，`--json` 给脚本吃。枚举映射维护在
  `docs/knowledge/系统常用映射表.md` 的「表单布局联动规则」五组，改枚举要同步
  `plugin/src/shared/layout-rules.ts`。它只读不写；写规则仍走 `layout.save` 整份回写。
  规则只覆盖 `value` 里的属性，其余取组件自带值；阅读器会标出「组件默认 …」并把与默认相同的效果
  标成「等于空操作」（提醒级，不影响退出码）。
- **生命周期/工作流流程图固定用便笺本模块**：唯一图形源是 `docs/reference/便笺本.xml`（用户提供），代码样式集中在 `plugin/src/shared/sticky-notes.ts`。生命周期状态=浅蓝 `#bdd7ee` 矩形；用户动作=橙色 `#ed7d31` 胶囊；用户任务=橙色 `#ed7d31` 矩形；判断=橙色 `#f4b183` 菱形；开始/结束=`#70ad47` 圆形；连线=黑色箭头。生成 drawio 不得自创配色或圆角；`plugin/test/analysis-artifacts.test.ts` 会断言源文件存在且 drawio 只含这些颜色。
- **新建对象硬规则（2026-11-22 用户口径）**：新建对象必须同时 `enableLifeCycle: true` 和 `enableSignatures: true`；缺一不可，配置引擎在建对象前直接拒绝。已有对象不受此规则影响。
- **字段类型规则（2026-11-09 管理员口径）**：一律用文本 `dataType: 1`；**长文本 17、富文本 18 已禁用**，
  不再新建、不再使用（`node plugin/scripts/api-guide.mjs --check` 会扫文档里的 17/18 并报错）。
  字段建完不能改类型、也不能删，选错只能改名停用 + 新建。
- **字段必填规则（2026-11-21 用户口径）**：新建字段时，除对象自带的 `name__ak` 外，其余字段一律
  `isRequired: false`；是否必填在**表单布局**里控制，不要在建字段时设置必填。
- **工作流触发（2026-11-09 管理员口径）**：工作流发布后不会自己跑。审批流由对象/状态上的动作
  （`behaviorType: 7`，`behaviorValue.workflowId` 填 workflowBasicId）拉起；提醒流由**进入动作**拉起，
  且必须把工作流级 `isAllowTrigger` 置 true（界面「允许从进入动作和事件动作自动启动」，勾选并启用后
  **不可取消**）。同一对象可有多条已发布流程，互不冲突。
- **审批工作流标准骨架**：开始（10）→ 任务（30）→ 判断（70）→ 已通过/已退回动作（90）→ 结束（20）。
  判断规则用 `logicType: 40`，`logicValue` 是**带引号的同意按钮 id**；`userControlIds` 是字符串
  （传数组 400）；审批人从对象字段取时，开始节点 `selectUserType: 40` + `fielsObjectIds`。
- **审批意见签名（2026-11-26 v3 实测）**：任务审核的两个按钮都 `isHaveSign: true`、
  `isRequireSign: true`（**两个按钮都必填电子签名**）；同意 `isRequiredResons: false`（签名备注可选），
  拒绝 `isRequiredResons: true`（签名备注必填）。两者都 `isShowResons: true`、`reasonInputType: 20`、
  `resonTitle: "签名备注"`、`isShowResponsibilities: true`、`isRequireResponsibilities: true`、
  `responsibieTitle: "签名含义"`、`isRequirePosition: true`。任务级 `stepTaskConfig.isRequireSign`
  仍为 false，签名落在按钮上，不要用任务级开关代替按钮开关。
- **提醒工作流**：任务节点配 `isNotifyTaskType: true` + `isRequireApproval: false` + 空
  `stepTaskApprovals`，任务名直接写提醒语；`notifyType: -10` 是「使用提醒策略」。
- **生命周期**：默认的「开启 / 关闭」两个状态要改名复用（头尾），中间态按流转顺序排 sort；状态不能删、
  只能停用；工作流动作里的 `statusId` 必须是启用中的状态。
- **表单/列表布局**：新组件 id 必须先经 `Layout/GetStableComponentIds` 换成稳定 id 再 `layout.save`；
  **新增分区必须顺位追加**：sections 数组保持现有顺序，新分区放数组最后、`sort` 取现有最大 `sort + 1`，
  不能插到第一位（平台按数组顺序归一化 sort；2026-11-21 实测）；
  关联控件 `AKSelectorObject` 的 `listLayoutId` 要填**被关联对象**的列表布局（填全零会空白/列错乱）：
  没特殊要求就改默认布局，不新建。

## 工作流配置规则（2026-11-22 用户口径）

- **开始节点不是空入口（2026-11-26 实测）**：开始节点要配参与者 + 电子签名；**用户动作挂载的审批流必须配开始电子签名**。参与者表示“谁能够参与”，不是“谁能够发起”；谁能够发起由状态上的用户动作/进入动作与入口权限决定，不能从开始节点参与者推断。签名控件 `ctrlType: 60`，`signatureConfig` 至少包含 `isRequirePosition: true`、`isShowSignatureMean: true`、`isRequiredMean: true`、`isShowSignatureRemark: true`、`reasonInputType: 20`、`signatureMeanName: "签名含义"`、`signatureRemarkName: "签名备注"` 和一个含义选项（本次为“提交授权账号处理结果”）；本次回读 `isRequiredSignature: false`，但**只要开始节点存在电子签名控件，实际执行即按必填处理**（用户 2026-11-26 口径），不能因参数回读 false 就判断“可选”；`isShowSignatureRemark: true` 只显示备注、备注不必填。
- **节点命名（2026-11-26 实测）**：中间节点统一 `【类型】：名称`；动作统一 `【动作】：修改状态为‘X’`（“为”不能漏），任务 `【任务】：X`，判断 `【判断】：X`。本次开始/结束保留平台默认“开始”“结束”；若平台允许改名再统一，不擅自假设。
- **编辑已发布流程会生成新版本（2026-11-26 实测）**：在 UI 编辑已发布流程，`workflowBasicId` 不变，但 `workflowConfigId`、`versionNo` 和全部 step id 会变（本次 v1 `3a4ec3aa-124c-597d-b32b-3a25266fb331` → v2 `8f10d3f8-9aff-6a8b-6eb9-3a2526928a36` → v3 `fb875bdb-1f7d-a876-63d6-3a2526a36838`，旧 step id 全失效）。保存/发布后必须重新读取 workflowConfigId 与全部 step id；禁止缓存旧 id。
- **发起入口单独检查**：`isAllowTrigger` 只控制是否允许进入动作/事件动作自动启动；审批流仍要挂用户动作（`behaviorType: 7`）作为触发器。初始状态绑定 `lifecycleStatusIds` 不等于触发器。
- **创建任务节点前，只判断开始节点中有无对应参与者**：有对应参与者控件就把任务 `userControlIds` 指向它；没有就回开始节点补。矩阵规则、角色成员、通知接收人是否齐全，属于发布前/运行风险检查，不作为创建任务节点的前置门槛。
- **参与者控件只允许两种（2026-11-22 用户口径）**：`使用字段作为参与者`（`selectUserType: 40`，配 `fielsObjectIds`）或 `使用审批矩阵作为参与者`（`selectUserType: 50`，配审批矩阵）。不得使用指定用户、指定用户组、指定用户+用户组、使用角色、使用自定义动作等其它类型；已有流程如用了其它类型，另行评估迁移，不在新建/调整时继续使用。
- **配置工作流前先出三张清单**：参与者清单（只能是字段/审批矩阵）、节点清单（开始/状态/任务/判断/动作/结束）、节点关系图（含判断分支与拒绝回退）；执行顺序固定为「先建参与者 → 再建节点 → 最后收口节点关系」。当前 `workflow.*.add/update` 的 `nextStepId` 是必填，节点可先带临时指向占位，节点全部建完后再用 `workflow.*.update` 统一收口；最终必须回读 `nextStepId/nextStepList` 与判断分支，不能把临时指向留在发布版本里。
- **审批矩阵两个配置不能混**：`constraintRoleIds` 是“应用角色约束”（限制哪些角色可参与）；`constraintsCondition` 是“使用的条件”（特殊情况下的更原始字段/角色过滤）。两者不是互相替代关系。
- **工作流分析顺序**：对象/生命周期/状态/字段 → 开始节点参与者/签名 → 任务参与者/矩阵 → 步骤/判断/动作 → 拒绝回退 → 触发绑定；最后检查所有分支、无人审批、无触发、无最终状态等问题。
- **对象分析的工作流范围（2026-11-23 用户口径）**：只分析已挂载到生命周期状态、且该状态为启用的工作流；只挂停用状态或没有状态绑定的流程不进入报告。审批矩阵必须以列表呈现“矩阵名称 / 关联工作流 / 参与者控件 / 控件应用到的任务节点”，再检查菜单缺失与业务数据；不能只给矩阵计数。
- **`else` 必须明确语义**：不要把“非通用变更”默认当设计变更；新增类型前先复核兜底分支。
- **流程结束在中间状态时要注明承接方**：结束在“分配责任部门/项目计划/报告”等状态，不等于对象已完成；后续执行/关闭流程要单独列出。
- 详细知识见 `docs/knowledge/标准配置使用规范.md`、`docs/knowledge/基础API使用指南.md`、`docs/knowledge/标准对象分析流程.md`。

## 运行报告（页面模板 / 入口已开放，2026-11-24）

- 用户口径的「页面模板 / 运行报告」= Word 报告模板 + DTE 占位参数；完整生成流程见
  `docs/knowledge/运行报告生成.md`，Aspose 语法见 `docs/knowledge/模板参数配置.md`，
  变更对象样例见 `docs/knowledge/运行报告配置.md`。
- 页面入口：`plugin/src/client/page.tsx` 的 `MODULES` 里 `runs` 已渲染 `RunReportTab`
  （`plugin/src/client/runs.tsx`）；入口只负责收集目标对象/原始表单路径、生成并复制 Agent
  指令、索引资料，不伪造成品，不写死 CAPA 字段。
- 制作顺序固定：原始表单 → 中间态（`<<字段名称>>`）→ 用户逐段审核 → 最终 `<<[code]>>`
  + `if`/`foreach`。中间态未确认前不得替换 code。
- 相关对象表**有且仅有两行**：第一行表头，第二行字段映射值；第二行每格只放一个字段；
  `foreach` 写在数据行内，不把多个字段塞进一个单元格。
- 签字/日期只用电子签名关系（如 `capa_plan_esignature_parent_id__sysr`），不能写普通
  `<<签字/日期>>` 占位；无法映射的格子留空并写入“待确认”，**禁止猜 code / `__cr` / `__sysr`**。
- 生成物落 `<当前工作区>/akso-workbench-data/reports/`；中间态和字段映射都要留档，
  最终模板必须用真实数据渲染 + 校验后再交付。

## 配置任务怎么做（查存量 → 蓝图 → 顺序 → 一次审批 → 判规模修）

细节在 `docs/knowledge/标准配置思路规范.md`，开工前必读。以下是不可跳过的硬规则：

- **先查存量，再出蓝图**：对象 `code` / 对象名 / 字段名 / 状态名 / 流程名 / 菜单名 / 动作名，逐个查过再写进蓝图
  （`query.object.get`、`query.fields.list`、`query.lifecycle.status.list`、`workflow.page.list`、
  `lifecycle.useraction.list`），结论连 id 记下来。同名不是"照建"——`object.save` 按 code upsert，
  会改写现有对象；字段重名会直接报错。
- **先出蓝图，再调接口**。蓝图 = 七张表：表 0 范围与环境，加对象 / 字段 / 状态 / 工作流 / 挂载 / 布局与菜单。
  字段表要标清「进表单 / 进列表」，状态表要标清每个状态的出口——填不满就是没想清楚。
- **审核稿写业务语言**：一屏结论 +「建什么 / 怎么流转 / 谁从哪里进来」；**不贴 JSON**、不列 method / path / params，
  要拍板的做成选择题（§3.3）。弹窗不是设计评审，设计评审必须在弹窗之前。
- **施工顺序固定**：对象 → 字段 → 表单布局 → 列表布局 → 菜单 → 生命周期状态 → 工作流 → 挂载。
  计划步骤按蓝图 `resources` 数组顺序生成，跨阶段没有自动兜底，顺序由蓝图保证。
  按钮 / 进入动作要给 `workflowBasicId`，必须在流程创建并发布**之后**挂。
- **蓝图按段施工；多段包成一条任务，只审一次**。跨阶段有依赖（工作流要拿上一步的 id）时，用
  `config_plan({task})` 把各段按顺序装进一条任务、`config_apply({taskId})` 一次审批执行；单段调整仍可
  一段一个计划。审批一次性、绑定指纹、**任务 60 分钟 / 计划 30 分钟**过期、同一时间只挂一条；
  执行中途失败必须重新出任务、重新审批，不能拿旧任务续跑。新增资源 / 不可逆动作（发布、`isAllowTrigger`、
  `enableLifeCycle`、改 `code`）仍要重新确认。
- **出错先判规模，不推倒重来**：字段类型 / 名称错 → 改名（停用）→ 停用 → 新建 → 换控件；布局错 → 直接覆盖；
  状态错 → 改名停用 + 新建 + 重建按钮；对象错 → 先评估能否复用，换对象时旧对象改名停用并从菜单摘入口。
  对象 / 状态 / 字段删不掉，重来只会留脏数据。字段 `dataType` 不可改、工作流发布不可回退。
- **停用不会自动传播**：布局控件、列表列、`taskFields`、动作节点 `statusId`、菜单引用都要手动换；
  状态上的按钮没有 update / 删除能力，状态建错就得在新状态上重建按钮。

## 接口卡片文案（三份文档）

- **三份文档各管一段**：`docs/knowledge/基础API使用指南.md` 管「这条接口是什么」（介绍 / 返回 / 要点 /
  **一条**案例）；`docs/knowledge/标准配置使用规范.md` 管「怎么用它配出标准配置」（同一接口可以有多个方案，
  多步骤流程写这里）；`docs/knowledge/系统常用映射表.md` 管「系统里的数字动作是什么」（stepType / status /
  behaviorType / belongType / menuActionType 等码表）。
- **读法**：host 每次 `GET config/capabilities` 现读三份文档（按 mtime + size 缓存），改完刷新页面即生效，
  不用 build，也不用重启 host。环境变量 `AKSO_WORKBENCH_API_GUIDE` / `AKSO_WORKBENCH_RECIPE_BOOK` /
  `AKSO_WORKBENCH_ACTION_MAP` 可以指向仓库外的文档。
- **分工**：结构字段（method / path / params / risk / irreversible）仍以 `plugin/src/config/capabilities.ts`
  为准，文档只负责文案；文档缺章节时回落到目录里的 `description` / `example` / `returns` / `notes`。
  加接口或改接口时，**同一提交里补文档**，不要让卡片文案和目录各说各的。
- **基础指南格式**：`### <capability id> <标题>`；开头 `>` 引用块是介绍；`**返回**：…` 是输出结果；
  `**要点**` 下写列表；`**案例**` 下**只写一条** `#### 案例 · 标题`，节内第一个 ```json 代码块是请求参数，
  代码块前后的散文分别是案例说明与「预期：…」。写第二条时解析器只取第一条并告警——多步骤流程请去写
  标准配置方案，那里可以有任意多条。
- **标准配置规范格式**：`### <capability id> <标题>` 归属某张卡片；`#### 方案 · <名字>` 一个方案一块，
  同一接口可多块；`>` 是方案简介；`**步骤**` 有序列表，每条开头用反引号写接口 id（如 `layout.detail`）；
  `**校验**` 列表；`**示例**` 一个 json 代码块；`**预期**` 散文。文档里没登记方案的接口，卡片上就不出
  「标准配置用法」这一栏——所以"还没标准方案"本身就是一种合法状态，不用凑。
- **动作映射格式**：`## 分组名` + 一行 `作用域：workflow / lifecycle / menu / layout`（用来描述这组码表适用哪些接口域，
  写「通用」表示不分域）+ `| 值 | 含义 | 备注 |` 表。新动作码先在这份文档登记，再改代码。当前页面不渲染
  这张映射表，但 `config/capabilities` 仍返回它，`api-guide.mjs --check` 也会校验格式与空组。
- **校验**：`node plugin/scripts/api-guide.mjs --check` 连三份文档一起校验（缺章节 / 多余 id / 解析告警 /
  方案 id 不在目录，有问题 exit 1）；`--seed` 补基础指南骨架；`--list` 看登记状态。
  `plugin/test/api-guide.test.ts` 与 `plugin/test/config-docs.test.ts` 会断言真文档零告警、目录全覆盖、
  每条恰好一条案例、至少一个标准方案、映射表非空——测试失败时先跑 `--check` 定位。
