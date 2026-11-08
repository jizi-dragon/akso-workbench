# capture —— 用监听补「接口地图」

配置工具里的 44 条能力，只是平台接口的一部分。页面里还有很多调用没有公开文档、
或者只在特定交互里出现（保存布局、拖拽排序、批量操作）。**监听**就是把这些调用抓下来，
变成可复用的证据，再由人决定要不要提成能力。

它和 `plugin/src/config/capabilities.ts` 的关系：

```
浏览器真实调用 ──监听──> captures/<session>.jsonl ──去重──> 接口清单 ──promote──> 能力草稿
                                                                              │
                                                              人工确认参数/风险后粘进 capabilities.ts
```

**不做的事**：不回放、不自动改代码、不代替审批。草稿永远是草稿。

## 快速开始

```bash
# 1. 起一个带调试端口的独立浏览器（默认 profile 在 captures/chrome-profile）
node capture/bin/akw-capture.mjs launch --url https://standard-val.aksoegmp.com

# 2. 在这个浏览器里登录（独立 profile，需要重新登一次）

# 3. 在页面里点几下要观察的功能，同时开始监听
node capture/bin/akw-capture.mjs attach --tab aksoegmp --host aksoegmp.com

# 4. 另开一个终端看结果
node capture/bin/akw-capture.mjs list
node capture/bin/akw-capture.mjs endpoints
node capture/bin/akw-capture.mjs promote 3
```

也可以直接复用**已经开着调试端口的** Chrome：`tabs` 找到目标，`attach --tab <url 片段>`。
Chrome 136+ 不允许在默认 profile 上开 `--remote-debugging-port`，所以 `launch` 用独立 profile。

## 命令

| 命令 | 作用 |
| --- | --- |
| `tabs` | 列出 `127.0.0.1:9222` 上可录的标签页 |
| `launch` | 起独立浏览器（`--url`、`--port`、`--chrome`、`--profile`） |
| `attach` | 开始监听（`--tab`、`--host`、`--all`、`--all-types`、`--duration`、`--verbose`） |
| `list` | 原始记录（`--method`、`--grep`、`--limit`、`--json`） |
| `show` | 一条完整记录（已脱敏） |
| `endpoints` | 按 `method + 路径模板 + 查询键名` 去重 |
| `export` | 导出 Markdown 清单（`--out`） |
| `promote` | 打印 `capabilities.ts` 草稿（`--id`、`--domain`），**不写文件** |
| `sessions` | 本机的捕获会话 |

默认只记 `aksoegmp.com` 的 XHR/Fetch/Document；`--all` 放开 host，`--all-types` 连静态资源也记。
`--duration 60` 录 60 秒后自动停；不填就 Ctrl+C。

## 数据长什么样

位置：`$DSH_HOME/akso-workbench/captures/capture-cap_<YYYYMMDDhhmmss>_<xxxx>.jsonl`
（`DSH_HOME` 默认 `~/.dsh`；可用 `AKW_CAPTURE_DIR` 覆盖。）

一行一条记录，关键字段：

```json
{
  "v": 1, "id": "cap_...:1234", "sessionId": "cap_...", "at": 1762...,
  "method": "POST", "url": "https://standard-val.aksoegmp.com/api/platform/ObjectPicklist/save",
  "host": "standard-val.aksoegmp.com", "path": "/api/platform/ObjectPicklist/save",
  "pathTemplate": "/api/platform/ObjectPicklist/save",
  "query": {}, "status": 200, "mimeType": "application/json",
  "requestHeaders": { "Authorization": "«redacted:len=412»", "Content-Type": "application/json" },
  "requestBody": { "kind": "json", "preview": "{ ... }", "truncated": false, "bytes": 182 },
  "responseBody": { "kind": "json", "preview": "{ ... }", "truncated": false, "bytes": 96 },
  "durationMs": 42, "page": { "url": "https://...", "title": "..." }
}
```

## 安全边界

- **只连 127.0.0.1**：调试端口不开到网上。
- **令牌/口令不落原文**：`Authorization`、`Cookie`、`Set-Cookie` 及 `password`/`token`/`secret`
  一类键的值只记 `«redacted:len=N»`；文本里的 JWT / Bearer 也会打码。
- **体截断 64KB**；目录 0700、文件 0600。
- **数据不进仓库**：默认写到 `$DSH_HOME`，仓库里的 `.gitignore` 不含这个目录，也不该把捕获文件提交上来。

## 已知限制

- 只能看到**这个浏览器标签页**的流量；Service Worker 拦截、其他标签页、原生客户端都看不到。
- `Network.getResponseBody` 对 Already-evicted 的体拿不到，此时记录只有状态与头。
- 路径模板只折 UUID/纯数字/长 hex；带业务语义的短 code（`akwv1109__c`）保持原样。
- 不做回放：平台接口有副作用，回放应由审批过的工具执行，而不是监听器。
