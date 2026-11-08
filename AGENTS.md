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
- **验证**：`plugin/test/host.test.ts` 断言三个注册工具的 UID 齐全、唯一、格式正确，
  且未登记工具会被拒绝；加工具时必须同步更新该断言。

## 日志与审计

- **三类记录分开写，不要混在一起**：
  - 写入审计 `config-audit/<YYYY-MM>.jsonl`：每步一条，含 `planId/env/baseUrl/accountId/capabilityId/status/traceId/durationMs`；**不记 params**（敏感内容不进日志）。
  - 审批决策 `config-audit/approvals-<YYYY-MM>.jsonl`：含 `at/planId/env/baseUrl/accountId/hash/verdict/note`；拒批、补充、超时、中断都要留痕，不能只记"写了什么"。
  - HTTP access：`[http] METHOD PATH -> STATUS Nms`，一条请求一行；2xx/4xx 走 info，5xx 走 warn。围栏挡掉的 403 也要有。
- **工具日志**：`[UID] invoke/done/failed`；`done` 要带耗时与 `ok=<bool>`（业务失败但没抛异常时 `ok=false`），不能把 `denied`/`needs-account` 记成成功。
- **不许静默吞异常**：浏览器打开失败、审批窗轮询失败、审计读取失败，至少落一条 warn/debug。
- **写日志的铁律**：不记 token、口令、请求体原文；审计记录"谁在什么环境对哪一版计划做了什么"，内容细节用 `planId` 回查计划文件。

## 其他既有约束

- 配置写入只走「模型工具 + 对话内审批窗」：`engine.applyPlan` 是唯一的门，写入前必须有人
  在弹窗里点「同意」（`config-approvals/<planId>.json` 存计划指纹，一次性）；HTTP 直连默认关闭
  （`AKSO_WORKBENCH_ALLOW_DIRECT_WRITE=1` 仅本机调试，开了也仍过引擎的审批门）。
  审批要求 = 环境变量 `AKSO_WORKBENCH_REQUIRE_APPROVAL` > 页面开关 > 默认要求。
- 写入账号必须来自底部账号条（`prefs.currentAccountId`）且在配置池里；账号条为空时工具返回
  `needs-account`、界面自动弹选账号面板，**不许**回落到磁盘上的旧会话或别的账号。
  计划绑定 `target{baseUrl,accountId}`，apply 前比对，不一致直接 `TARGET_CHANGED`。
- 口令不离开 akso-cloud；host 只拿「账号 id」换 eGMP 令牌，日志/响应里不得出现 token 原文。
  本机不再接受明文口令（`session.login` / `POST config/login` / `auth.login` 能力都已删除）。
- 改完跑：`npm run typecheck:plugin`、`npm --prefix plugin test`、`npm --prefix plugin run build`。
