# 变更记录

## 0.2.29（2026-11-27）

**运行日志页能检查更新；capture 监听框架的翻译扩展与 akso-pass 4.6.6 同步**

- 现象：① 插件更新一直靠人工记版本，用户不知道有没有新正式版；② capture 监听框架里的翻译扩展还在用两台已经不可用的引擎（TranSmart 服务停运、Lingva 返回 403），演示时按 Ctrl+X 经常等满 8 秒再失败。
- 改法：
  - 检查更新：新增 `plugin/src/update.ts`（`checkForUpdate` / `downloadUpdate`）与 `plugin/src/version.ts`；`ROUTES` 加 `update/check`（`?force=1` 跳缓存）与 `update/download`；运行日志页头部加版本号 chip、「检查更新」按钮、更新横幅与下载结果面板（路径 / sha256 / 替换步骤），页面顶部另有全局横幅。判据是 **MINOR 变大**才算正式版（`0.2.28 → 0.3.0`），同线 patch 只轻提示；来源只有 akso-cloud 的 `web/downloads/workbench/latest.json`，**不兜底 GitHub Releases**（tag 里混着早期桌面安装器线）。不是热更新：下载后需退出 DSH 重开。
  - capture 翻译扩展 1.1.0：移除 TranSmart / Lingva，改「有道查词（3s）→ MyMemory 翻整句（8s）」；有道是词典，遇到 `xx / yy`、`sth`/`sb` 这类词典式结果判为不可用继续下一路；词典查不到整句时由 MyMemory 接手。短词（单行 ≤12 字、译文 ≤4 词）输出 `snake_case`，整句保持英文句子；成功提示带译文预览，演示时不用先粘贴就能读。
- 测试：`plugin/test/host.test.ts` 加四条（semver 判据、清单拿不到不抛错、哈希/大小校验后才落盘、路由常量）；`npm run check` 全绿（插件 166/166 + build）；`api-guide --check` 66/66、0 告警。capture 扩展只跑 `node --check` 与真实网络 smoke（有道 260ms / 32ms 命中，MyMemory 整句 0.9s）。
- 诚实边界：`latest.json` 里 `zipUrl/zipSha256/zipBytes` 目前是 `null`（还没把 zip 传 Release 资产），所以客户端只能提示版本与步骤，不能一键下载；akso-cloud 侧清单要部署后才生效。翻译扩展属于监听工具，不进 workbench 发布物。

## 0.2.17（2026-11-26）

**配置引擎硬门禁：旧计划拒绝、回读失败阻断、审批前查重/查签名**

- 现象：工作流用了至少 8 次间断审批，发布后才发现 `action_pending` 仍指向结束；apply 结果把关键写入标成 `unverified` 却继续后续步骤；拉取 0.2.16 后只 build 未 reload，运行时仍是旧引擎。
- 改法：
  - `engine.ts` 新增 `CONFIG_ENGINE_BUILD='2026-11-26.1'`，每个 Plan 写入 `engineBuild`；`applyPlan` 对旧构建生成或旧任务直接抛 `ENGINE_STALE`。`task.ts` 的 Task 同样带 `engineBuild`。
  - 新增 `STRICT_VERIFY_CAPABILITIES` / `requiresVerifiedWrite`：workflow.create/update/delete、start/task/decision/action.update、task/decision/action.add、status.change、lifecycle.useraction/enteraction create/update 的回读 `unverified` 视为失败，立即停止，不再继续发布。
  - `buildPlan` 增加 `preflightPlan`：`workflow.create` 按 name/code 查重（`WORKFLOW_DUPLICATE`）、`lifecycle.useraction.create` 按状态查重（`USER_ACTION_DUPLICATE`）、审核任务每个按钮必须 `isHaveSign && isRequireSign`（`TASK_SIGNATURE_MISSING`）。
  - 新增 `lifecycle.useraction.update`（PATCH `/api/config/lifecycle/UserAction/Update`），已发布按钮改走计划/审批，不再直连 PATCH。
- 测试：旧计划/旧任务拒绝、严格/非严格能力分类、任务级 output binding 端到端；`npm run check` 全绿（capture 31/31、插件 146/146、build）。
- 诚实边界：`preflightPlan` 的查重依赖 `workflow.page.list` 的 keywords 命中；极大数据量下可能分页漏检，但正常命名查重足够。

## 0.2.16（2026-11-26）

**配置计划不再对「没有判据」的 step 一律报人工确认：从 5 类扩到 14 类写能力都能回读**

- 现象：审批日志里 `workflow.start.update`、`menu.submit` 这类 `call` 步骤一律写「暂无稳定回读方式，需人工确认」，但能力目录里明明有 `workflow.step.get`、`workflow.page.list`、`layout.detail`、`lifecycle.useraction.list`、`Menu/GetMenuList`（用户两次指出：这些查询工具本来就该用上，且同类遗漏不止一处）。
- 根因：`verifyStep` 的 `call` 分支只对 `object.save` / `picklist.save` 做了回读，其余全部落到兜底文案；回读是「有没有写成功」的唯一证据，兜底等于把证据丢了。
- 改法（`engine.ts` 的 `verifyCallCapability()` 按「写能力 → 目录里可用的只读接口」逐类补齐）：
  - 节点/流程：四个 `*Step.update` 回读 `workflow.step.get`；`task.add`/`decision.add`/`action.add`/`step.copy` 回读 `workflow.steps.list`；`workflow.create`/`update` 回读 `workflow.page.list`（按 name 搜再按 code 挑，`filters` 不生效）；`workflow.delete`/`step.delete` 以「读不到」为成功；`status.change` 回读 `workflow.get` 比 status。
  - 状态/按钮/进入动作：`lifecycle.status.create`/`update` 回读 `query.lifecycle.status.config.list`（客户端按 lifecycleId 过滤）；`enteraction.create` 回读 `lifecycle.enteraction.list`（按 name 认，接口无 code）。
  - 布局：`layout.save` 回读 `layout.detail`；`listlayout.save`/`columns.set`/`filter.save` 分别回读 `listlayout.list`/`columns.get`/`filter.get`。
  - 其它：`notice.template.create`/`update` 回读 `notice.template.list`；`approvalmatrix.rule.add` 回读 `approvalmatrix.rule.list`；`field.save`（call 方式）回读 `object.field.page`。
  - 能力目录新增 `menu.list`（`GET /api/platform/Menu/GetMenuList`）：`GetUserMenuPermission` 只返回当前用户可见的菜单，回读要用全量 `GetMenuList`（standard-val 实测 1050 条、`menuType=1` 1041 条）；`menu.submit` 按 code 比 parentMenuId/objectId/两套布局 id。
  - 确实没有查询接口的只剩 3 个，仍如实报「没有回读判据…需人工确认」：`approvalmatrix.create`、`approvalmatrix.matchfield.add`、`layout.stable.ids`（辅助步骤）。
- 判据：`host.test.ts` 两条新测试（假平台按 path 回不同 data，正反两面都断言：节点/发布/布局/按钮/菜单；工作流本体/三类节点/删节点与删流程/状态/列表列/模板/矩阵规则/字段）；`api-guide --check` 61/61、0 告警；`npm run check` 全绿（143 个测试）。
- 诚实边界：回读比的是「提交值 vs 平台回读值」里的关键子集，平台自己补的默认值不参与比对；`workflow.page.list` 与 `lifecycle.status.config.list` 都是「先全量/按名搜、再客户端挑」，数据量大时慢但不会误判；回读读到的若是同 code 的另一条旧记录（同名不同 code 不在此列），仍可能放过。
## 0.2.15（2026-11-26）

**修「工作流节点回写」的两个静默坑：摊平形状被原样回传、全零 GUID 存进流程**

- 现象：按 `workflow.step.get` 的结果改完节点、发布成功，真跑流程报 `Object reference not set to an instance of an object.`（standard-val，天气预报播报审批流）。
- 根因一（形状）：`workflow.step.get` 返回的控件是**摊平**形态（`ctrlType/code/value/id/participantConfig/signatureConfig/…` 都在控件对象顶层），而四个 `*Step.update` 要 UI 的**包裹**形态 `{id,sort,name,controlConfig:{…},participantConfig,signatureConfig}`。原样回传，服务端在保存节点时抛同一个 NRE。
- 根因二（id）：任务节点 `stepTaskConfig.workflowConfigId/stepId`、`stepTaskApprovals[].workflowConfigId/stepId`、开始节点 `signatureConfig.signatureMeanOptions[].id` 允许全零保存，**运行时**建任务/取签名含义才抛 NRE。参照 UI 建的「变更关闭审核」逐键比对才看出来（结构层完全一致，语义层只这几处不同）。
- 改法：
  - 能力卡片：`workflow.start.update`（包裹形态 + 签名含义 option id 真实 GUID）、`workflow.task.update`（真实 workflowConfigId/stepId + 控件形状 + 与 UI 对齐的 `isRequireSign/isRequireObjectField/isApprovalShowAsButton`）、`workflow.decision.update`/`workflow.action.update`（控件形状）、`workflow.step.get`（返回摊平形态的提醒）各补 notes。
  - 文档：`基础API使用指南.md` 五节同步；`标准配置使用规范.md` 的 `workflow.create` 校验小节加 4 条。
  - 工具：`engine.ts` 新增 `normalizeStepCall()`，`call` 资源引用四个 Step update 时自动把摊平控件包成包裹形态、全零 GUID 用流程/节点 id 补上、签名含义 option 补随机 GUID，改动一律进 plan 的 warning 提示，不静默。
- 判据：`host.test.ts` 新增「节点回写：摊平控件包成包裹形态、任务节点全零 GUID 用流程/节点 id 补上」，钉住包裹结果、真 GUID、补 id 与两条 warning；`npm run check` 全绿（142 个测试，`plugin/lib` 重建）。
- 诚实边界：只覆盖「读回→改→写回」这一条路径；UI 里手工点出来的其它形状差异没穷举。修复后用户实测不再报错。

## 0.2.14（2026-11-26）

**把「Release 资产只装构建产物」写成规则**

- 用户口径（2026-11-26）：打包成 release 时，上传的必须是**构建后的产物**，用户拿走后能直接使用；适用于 `akso-pass` 与 `akso-workbench`。
- `VERSIONING.md` 新增「Release 资产只装构建产物」：本仓库发的是 DSH 插件包，必须包含 `plugin/package.json`、`plugin/cordis.patch.yml`、`plugin/lib/index.js`、`plugin/lib/client.js`、`docs/knowledge/**`（须与 `plugin/` 同级）与安装说明；必须排除 `plugin/src/**`、`plugin/test/**`、`node_modules/`、`*.map`、`_pack/`、两个 `package-lock.json`。
- 四条判据：解压后 `plugin/package.json` 与两个 lib 都在 / 递归无 src·test·node_modules / `package.json` 里 `main` 与 `dsh.bundle.patch` 指向的文件齐全且 `docs/knowledge/` 同级 / 干净 DSH 里按说明装一次能看到版本、工具与知识文档。
- `AGENTS.md` §版本与发布 加一行指针指向该节；`npm run check` 141 个测试全过、`plugin/lib` 重新构建。
- 纯文档改动，未触动代码。

## 0.2.13（2026-11-26）

**工作流规则文档校正（v3 实测）**

- 任务审批两个按钮都必填电子签名；同意签名备注可选，拒绝签名备注必填。
- 开始节点电子签名控件：只要控件存在，实际执行即按必填处理；`isRequiredSignature` 回读为 `false` 也不能判为“可选”；签名备注只显示、不必填。
- 节点命名统一为 `【类型】：名称`，动作不能漏“为”；开始/结束保留平台默认名的情况单独标注。
- 编辑并发布已发布流程会生成新 `workflowConfigId`/`versionNo`/step id，必须重新读取，禁止复用旧 id。

## 0.2.12（2026-11-26）

**配置引擎：输出绑定 + 任务级一次审批**

- 计划步骤参数支持 `$ref` 输出绑定：`steps.<id>.after[.path]` 引用同计划更早步骤，
  `stages.<n>.steps.<id>.after[.path]` 引用任务已执行阶段；路径支持属性与数组下标。
- 执行时按前序步骤真实返回解析参数；审批指纹保留引用模板，审核文档增加「输出绑定」段。
- 任务级计划逐阶段执行，阶段输出喂给后续阶段；任一段失败即停，已执行段保留 checkpoint。
- 增加解析、跨步骤/跨阶段校验、任务指纹与端到端执行测试。
- 修复配置计划/任务在工具 exec 缺少 cwd 时无法定位工作区的问题：现在按 sessionId 从
  `$DSH_HOME/storages/workspace.json` 回退，仍不回落到 profile/仓库。

## 0.2.0（2026-11-23）

**正式版：对象分析产物落盘、审批矩阵分析与用户手册**

- 对象分析：页面与 agent 统一走对象分析入口；每次分析把 Markdown、drawio 与 PNG 写入工作区 `akso-workbench-data/analysis/<对象名>-时间戳/`，不再只在临时目录留 JSON。
- 报告口径：只分析启用状态；停用状态及其动作不进入报告；工作流必须挂到启用状态；步骤名称不再只显示 id。
- 审批矩阵：按「矩阵名称 / 状态 / 关联工作流 / 参与者控件 / 应用节点 / 菜单 / 业务数据 / 异常」列表化；检查矩阵菜单缺失与业务数据。
- 审批与数据目录：审核文档统一写入 `<工作区>/akso-workbench-data/reviews`；审批窗支持多组折叠、会话区定位、折叠与右侧打开。
- 文档：新增《用户手册》《对象分析经验与模板》，更新标准对象分析流程与接口指南。

## 0.1.9（2026-11-23）

- 统一对象分析产物落盘路径，补审批矩阵分析基础能力。

## 0.1.8（2026-11-23）

- 强制新建对象开启生命周期与电子签名；字段、布局与数据目录收口。
