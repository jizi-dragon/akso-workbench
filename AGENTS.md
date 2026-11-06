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

## 其他既有约束

- 配置写入只走「模型工具 + 宿主审批」，HTTP 直连默认关闭
  （`AKSO_WORKBENCH_ALLOW_DIRECT_WRITE=1` 仅本机调试）。
- 口令不离开 akso-cloud；host 只拿「账号 id」换 eGMP 令牌，日志/响应里不得出现 token 原文。
- 改完跑：`npm run typecheck:plugin`、`npm --prefix plugin test`、`npm --prefix plugin run build`。
