# capture —— 用监听补「接口地图」

配置工具里的 47 条能力，只是平台接口的一部分。页面里还有很多调用没有公开文档、
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
#    默认自动加载 capture/extension（最小翻译扩展），输入框 Ctrl+X 可直接翻译并复制
node capture/bin/akw-capture.mjs launch --url https://standard-val.aksoegmp.com

# 2. 在这个浏览器里登录（独立 profile，需要重新登一次）

# 3. 在页面里点几下要观察的功能，同时开始监听
node capture/bin/akw-capture.mjs attach --tab aksoegmp --host aksoegmp.com

# 4. 另开一个终端看结果
node capture/bin/akw-capture.mjs list
node capture/bin/akw-capture.mjs endpoints
node capture/bin/akw-capture.mjs promote 3
```

`launch` 默认优先使用 Playwright 的 Chromium/Chrome for Testing：Chrome 137+ 的品牌版会忽略
`--load-extension`，Chromium 才能稳定加载扩展。默认扩展是 `capture/extension`（只保留 Ctrl+X
翻译，无账号/计数/换源）；也可以显式指定其他扩展目录：

```bash
node capture/bin/akw-capture.mjs launch --extension D:\DDUP\Akso_AI\akso-pass\dist
node capture/bin/akw-capture.mjs launch --no-extension
```

**监听不是当前配置真相**：监听只用于发现接口、流程步骤和参数形状。对象/工作流/矩阵/布局的
当前状态必须用查询工具（对象分析、`config_plan` 的只读查询等）确认；捕获结论必须带会话 id 和
时间，不能拿几天前的 JSONL 判断今天的平台配置。CLI 默认拒绝超过 24 小时的会话：

```bash
node capture/bin/akw-capture.mjs endpoints --max-age 120
node capture/bin/akw-capture.mjs list --allow-stale   # 仅在明确只读旧证据时使用
```

也可以直接复用**已经开着调试端口的** Chrome：`tabs` 找到目标，`attach --tab <url 片段>`。
Chrome 136+ 不允许在默认 profile 上开 `--remote-debugging-port`，所以 `launch` 用独立 profile。

## 命令

| 命令 | 作用 |
| --- | --- |
| `tabs` | 列出 `127.0.0.1:9222` 上可录的标签页 |
| `launch` | 起独立浏览器（`--url`、`--port`、`--chrome`、`--profile`、`--extension`、`--no-extension`） |
| `attach` | 开始监听（`--tab`、`--host`、`--all`、`--all-types`、`--duration`、`--verbose`） |
| `list` | 原始记录（`--session`、`--max-age`、`--allow-stale`、`--method`、`--grep`、`--limit`、`--json`） |
| `show` | 一条完整记录（已脱敏） |
| `endpoints` | 按 `method + 路径模板 + 查询键名` 去重 |
| `export` | 导出 Markdown 清单（`--out`） |
| `promote` | 打印 `capabilities.ts` 草稿（`--id`、`--domain`），**不写文件** |
| `sessions` | 本机的捕获会话（含年龄、标签页、是否已结束） |

默认只记 `aksoegmp.com` 的 XHR/Fetch/Document；`--all` 放开 host，`--all-types` 连静态资源也记。
`--duration 60` 录 60 秒后自动停；不填就 Ctrl+C。

## 数据长什么样

位置：`$DSH_HOME/akso-workbench/captures/capture-cap_<YYYYMMDDhhmmss>_<xxxx>.jsonl`
（`DSH_HOME` 默认 `~/.dsh`；可用 `AKW_CAPTURE_DIR` 覆盖。）

每个会话旁有 `capture-<session>.meta.json`，记录 **开始时间、标签页、host 过滤、扩展路径、结束时间**；
`list/show/endpoints/export` 先读它判断会话年龄。没有 sidecar 的旧会话退回文件创建时间。

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
- 品牌版 Chrome 137+ 会忽略命令行 `--load-extension`；`launch` 会优先选 Chromium/Chrome for Testing。
  仍加载不了时，在 `chrome://extensions` 手动「加载已解压的扩展程序」指向 `capture/extension`。
- `capture/extension` 只做 Ctrl+X 翻译并复制：有选中文本翻译选中部分，否则翻译整个输入；不改原输入，
  用户自己 Ctrl+V。翻译方向自动判断：含中文→英文，否则英文→中文。
- 捕获只证明「某次操作这样调过」，不能证明当前对象/工作流/矩阵/布局还是这个状态。
