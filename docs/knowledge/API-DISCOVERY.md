# 用监听扩接口（API Discovery）

配置工具的能力目录不是天上掉下来的：一部分来自平台文档与 jar 反编译，
另一部分只能从**页面真实调用**里捞。这篇记录怎么捞、怎么判断、怎么入库。

## 1. 什么时候需要监听

- 平台文档里没有，但页面某个按钮会发请求（保存布局、拖拽排序、批量启用、导入导出）。
- 文档里有，但参数形状说不清（嵌套数组、动态字段、FullPath 之类的平台生成值）。
- 想确认「这个接口到底改了系统里的什么」——监听能看到请求体、响应体和状态码。

监听**不替代**验证台账：抓到的只是「页面这么调过一次」，
能不能给人用、参数是否稳定，仍要按 `docs/knowledge/EGMP-CONFIG-BASICS.md` 的层级去试。

## 2. 工具

`capture/` 目录，零依赖，Node ≥ 24：

```bash
node capture/bin/akw-capture.mjs launch --url https://standard-val.aksoegmp.com
# 在这个独立浏览器里登录
node capture/bin/akw-capture.mjs attach --tab aksoegmp --host aksoegmp.com
# 在页面里操作要观察的功能；Ctrl+C 结束
node capture/bin/akw-capture.mjs endpoints
node capture/bin/akw-capture.mjs show 5
node capture/bin/akw-capture.mjs promote 5
```

细节见 `capture/README.md`。

## 3. 从「一条记录」到「一条能力」

`promote` 打印的是草稿，不是成品。入库前逐项过：

| 检查项 | 判据 |
| --- | --- |
| 方法/路径 | 与记录一致；路径里的 id 段换成 `{id}` 或写清来源 |
| 参数 | 每个参数的 `in`（path/query/body）、类型、是否必填；必填性要靠「少传一个会不会报错」验证 |
| 响应 | `code === 0` 的信封；`data` 的形状（对象/数组/null） |
| 系统变化 | 写类接口必须能指出「新增了什么/改了哪个字段」，并能回读确认 |
| 风险 | 是否可逆、是否影响线上、是否需要审批；拿不准就标 `high` 并留 notes |
| 稳定性 | 至少两次调用形状一致；平台生成值（FullPath、traceId）不能当入参 |

只有「输入 → 输出 → 系统变化」三件都写清楚，才把它加进
`plugin/src/config/capabilities.ts`，并在 `docs/CAPABILITY-VERIFICATION-2026-11-09.md`
补一条台账（或新建一份台账）。

## 4. 常见坑

- **拿不到响应体**：`Network.getResponseBody` 对已释放的体无效。记录里只有状态与头，
  这时用 `show` 看请求，响应形状去页面控制台/再操作一次。
- **同一接口散成很多行**：路径里带业务 code（如 `akwv1109__c`）不会被折成 `{id}`，
  这是有意的——不同 code 往往是不同资源。用 `endpoints` 看 `count` 与 `statuses` 判断。
- **预检请求**：`OPTIONS` 也会被记；它没有业务意义，`--all-types` 时更明显。
- **令牌被脱敏**：这是设计，不是 bug。监听数据不能用来重放，重放走审批过的工具。
- **静态资源噪音**：默认跳过 Image/Script/Stylesheet/Font 等；真要抓再加 `--all-types`。

## 5. 安全

- 只连 `127.0.0.1:9222`；不要把调试端口暴露到局域网。
- 捕获文件在 `$DSH_HOME/akso-workbench/captures/`，**不进仓库**。
- `Authorization`/`Cookie`/`password`/`token` 一类值只记长度；JWT 在文本里也会被打码。
- 分享 `export` 出来的清单前，仍要扫一眼 host 与路径是否含内部信息。
