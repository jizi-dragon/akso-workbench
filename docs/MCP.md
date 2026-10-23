# MCP 接入（MCP）

> **这份文档回答**：怎么把外部 MCP server 接进工作台、它有哪些安全默认值、坏了怎么排。
> **实现**：`workbench/services/mcp.py`（stdio + Streamable HTTP JSON-RPC）＋ `workbench/mcp_registry.py`（工具注册）
> ＋ `workbench/api/routes_mcp.py`（状态/重连）。**状态**：v0.4.1 之后新增，**最小可用**，不是完整 MCP 客户端。

## 1. 它能做什么

| 能力 | 支持 | 说明 |
|---|---|---|
| stdio 传输 | ✅ | 一行一个 JSON-RPC 2.0 消息（MCP stdio 规范） |
| Streamable HTTP | ✅ | `POST` JSON-RPC；响应支持 `application/json` 与请求级 `text/event-stream`；使用 `Mcp-Session-Id` |
| `initialize` / `notifications/initialized` | ✅ | 默认报 `2025-06-18`；服务端可回别的版本，按回的版本记录 |
| server `instructions` | ✅ | 握手返回的说明会以「外部 MCP server 说明」追加到系统提示词（≤8k 字符，不可覆盖本仓规则） |
| `tools/list` / `tools/call` | ✅ | 工具在运行期注册进 `ToolHub` 的 **installed** 层；支持 `nextCursor` 分页 |
| `ping` / 健康检查 | ✅ | 每 30 秒巡检；进程退出 / ping 失败自动重连 |
| `notifications/tools/list_changed` | ✅ | 收到后重新 `tools/list` 并重新注册（不用手动重连） |
| 用户级配置 `mcp.json` | ✅ | stdio 条目与 Claude Desktop 同形；HTTP 条目加 `transport` / `url` / `headers` |
| 状态 / 手动重连 / 单 server 重连 | ✅ | 设置页「MCP 服务器」或 `POST /api/mcp/servers/{name}/reconnect` |
| 延迟加载 | ✅ | `lazy: true` 的 server 启动不连接；点「加载」/ `POST .../activate` 后才注册工具 |
| 按工具 risk 覆盖 | ✅ | `risk: {default, tools}`（见 §2） |
| 远程 HTTP server | ✅ | `transport: "streamable-http"` + `url`；不需要本地桥接 |
| `resources` / `prompts` / `sampling` | ❌ | 本版只接 tools；server 反向请求一律回 `-32601` |
| OAuth / 密钥托管 | ❌ | 需要凭据的 server 由用户在 `env` / `headers` 里显式给 |
| 长连接 / 服务端主动推送 | ❌ | 不做常驻 SSE 流；`tools/list_changed` 依赖 stdio 通知或手动重连 |

## 2. 配置

文件：`<数据目录>/mcp.json`（Windows 默认 `%APPDATA%\Workbench\mcp.json`）。
设置页顶部会显示**实际路径**；这个文件在 `config.SENSITIVE_PATH_PARTS` 里，
`read_local_file` 不会把它交给模型（它可能含 webhook/token）。

```json
{
  "mcpServers": {
    "wecom": {
      "command": "uvx",
      "args": ["wecom-bot-mcp-server"],
      "env": { "WECOM_WEBHOOK_URL": "https://example.invalid/webhook" },
      "risk": { "default": "write", "tools": { "whoami": "read" } },
      "timeout_s": 60
    },
    "local-notes": {
      "command": "python",
      "args": ["C:/tools/notes_mcp.py"],
      "enabled": true
    },
    "remote-search": {
      "transport": "streamable-http",
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer <token>" },
      "risk": "read",
      "timeout_s": 30
    }
  }
}
```

| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `transport` | — | `stdio`（有 `url` 时自动视为 `streamable-http`） | `stdio` / `streamable-http` |
| `command` | stdio 必填 | — | 可执行文件；**只从配置文件来，模型不能指定** |
| `url` | http 必填 | — | Streamable HTTP 端点，必须是 `http(s)` 绝对地址 |
| `headers` | — | `{}` | 附加请求头（可含 token）；**状态面与日志不返回** |
| `lazy` | — | `false` | 延迟加载：启动不连接/不注册工具，设置页点「加载」或 `POST /api/mcp/servers/{name}/activate` 后才进入工具面 |
| `args` | — | `[]` | 字符串数组 |
| `env` | — | `{}` | **只追加**这些变量；宿主环境按白名单继承（见 §3） |
| `cwd` | — | 继承 | 必须是已存在目录 |
| `enabled` | — | `true` | `false` = 保留配置但不启动 |
| `risk` | — | `write` | 字符串 `read`/`write`/`dangerous`，或 `{default, tools:{工具名: 级别}}` 按工具覆盖 |
| `timeout_s` | — | `60` | 单次 `tools/call` 上限，1–600 |
| `protocolVersion` | — | `2025-06-18` | 握手时请求的版本；必须是 `YYYY-MM-DD` 形状 |
| `description` | — | `""` | 设置页展示用 |

配置写错时：**坏条目只跳过自己**，其余 server 照常连接；错误显示在设置页与
`GET /api/mcp/servers` 的 `errors[]` 里。

## 3. 安全模型（比"能连上"重要）

| 项 | 做法 | 为什么 |
|---|---|---|
| 谁能启动进程 / 指定 URL | 只有 `mcp.json` 的 `command` / `url`（人写的） | 模型若能让宿主执行任意命令或访问任意 URL，MCP 就变成 RCE/SSRF 通道 |
| 环境变量 | 白名单继承 PATH/TEMP/用户目录等；`WORKBENCH_*` 与宿主凭据**不传** | 规则 13：`dict(os.environ)` 全量继承 = 把凭据送出去 |
| 返回值信任 | `Trust.UNTRUSTED`（I18） | MCP 返回的是外部系统写的内容，不是我们边界内的数据 |
| 风险分级 | 默认 `Risk.WRITE`；server 级或按工具显式覆盖 | MCP 工具能干什么**声明不了**；默认必须过确认门 |
| 可逆性 | `IRREVERSIBLE` | 第三方工具无法被静态证明可撤 |
| 审计 | `agent_audit.meta` 只放 `mcpServer` / `mcpTool` | 参数可能含凭据（规则 20） |
| 进程清理 | 应用退出时 `reset_registry()` 关连接 | Windows 子进程不会因父退出自动消失 |
| 日志 | stderr 只进 DEBUG 日志；状态页不返回 `env`/`args`/`headers` | 配置里可能有 token |

> **它不是沙箱。** MCP server 是用户显式安装的本地程序，按启动它的用户权限运行；
> `sandbox/` 只约束 **Skill 脚本**，不约束 MCP 进程。这条边界必须让用户知道。

## 3.1 稳定性：健康检查与自动重连

| 事件 | 行为 |
|---|---|
| 每 30 秒 | 对每个已连接 server 发一次 `ping`（3 秒超时）；成功记 `lastPingAt` |
| 进程退出 / ping 失败 | 关旧连接 → 摘旧工具 → 重新握手 / 列工具 / 注册；`restarts++` |
| `notifications/tools/list_changed` | 同一套重连流程（工具清单变了，旧 `ToolSpec` 必须重建） |
| 重连失败 | 指数退避：2s、4s、8s… 上限 5 分钟；**成功一次就清零** |
| 手动「重连」 | 立即尝试，重置退避；单个 server 失败不影响其他 server |
| 应用退出 | 停止巡检线程 + 关闭全部子进程 |

★ **监控线程不会碰 `ToolHub._tools`**：它只调 `register_installed` /
`deregister_capability`，派生视图仍由 `_rebuild()` 唯一写（规则 8）。
★ **status 里不返回 `args` / `env` / stderr 正文**：只给 `error` 摘要与
`toolNames`（名称），避免把 webhook/token 带到设置页或日志。

## 4. 工具面

- 命名：`mcp__<server>__<tool>`（与 Claude Code / Codex / DSH 同形），非法字符替换为 `_`，总长 ≤64；同 server 内重名加 `_2`。
- 描述：`[MCP:<server>] <原描述>`，模型据此选择。
- 入参：从 server 的 `inputSchema` **动态生成 pydantic 模型**；类型不认识的字段落 `Any`，
  但必填/类型仍会校验（参数错 → `BAD_ARGS` 观测，不会静默发出去）。
- 层级：走 `ToolHub.register_installed`（**installed 层**），不覆盖核心工具；
  重连时先摘旧能力再注册，不会留下"删了但还在表里"的幽灵工具。
- 数量：工具总数受 `plugin_api.MAX_TOTAL_TOOLS = 40` 的**警报线**约束；超了只在
  `selfcheck.failures` 留一条，不拒装（与本仓对坏插件的语义一致）。

## 5. 使用

1. 在数据目录新建/编辑 `mcp.json`；
2. 打开「设置 → MCP 服务器」，点「重新连接」；
3. 状态行显示 `已连接 · N 个工具 · MCP 2025-06-18` 后，工具已经进入对话的 function calling 面
   （「工具」面板也能看到）；单个 server 掉线时行内「重连」可立即重试；
4. 默认 `write`：模型调用时会走确认门，用户放行后才执行；只想让某个只读工具免确认，
   在 `risk.tools` 里把它单独设为 `read`。

## 6. 排障

| 现象 | 先看什么 |
|---|---|
| 状态“连接失败” | `error` 行；命令是否在 PATH（Windows 上 `uvx`/`npx` 常见） |
| 卡在“后台装载中…” | 启动是后台线程；server 首次拉包可能要几十秒，点重连看结果 |
| `tools/list` 为空 | server 是否真的声明了 tools；看应用日志 DEBUG `MCP[name]` |
| 中文变 U+FFFD | 客户端按 UTF-8 解码；本仓给 Python server 设了 `PYTHONUTF8=1`/`PYTHONIOENCODING=utf-8`，自定义 server 请自己输出 UTF-8 |
| 调用超时 | 调大 `timeout_s`；stdio server 在等外部网络时也会超时 |
| 自动重连一直失败 | 看 `servers[].error` 与 `restarts`；退避上限 5 分钟，命令行里手动跑一次 server 看 stderr |
| 工具清单更新了但模型看不到 | 正常会在 `tools/list_changed` 后自动刷新；没有通知的 server 点行内「重连」 |
| 改了配置没生效 | 必须点「重新连接」（本版不监听文件变化） |

## 7. 判据

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_mcp.py tests\test_mcp_http.py -o addopts=""
```

`tests/fixtures/mcp_echo_server.py` 是一个最小 stdio server（echo/add/env_probe/fail/slow/
notify_change），覆盖握手、列工具、调用、错误、环境隔离、坏配置隔离、重连摘旧工具、
`ping`、`tools/list_changed`、进程死亡自动重连、按工具 risk、并发调用。

## 8. 还没做的（诚实边界）

- 不支持 `resources` / `prompts` / `sampling` / 进度通知的**消费**（通知会忽略）。
- Streamable HTTP 只做**请求级** JSON/SSE 响应；不做常驻 SSE 长连接，也不消费服务端主动推送的 `tools/list_changed`。
- 不做 OAuth 发现/刷新；需要凭据的 HTTP server 由用户把 token 放进 `headers`（或本地 stdio server 的 `env`）。
- 不做 MCP 进程的**内核级**限制（不放进沙箱）；它的权限等于当前用户。
- 不监听 `mcp.json` 变更；重连是显式动作（进程掉线是自动的）。
- 不做工具清单的持久化缓存：重启后重新握手（这避免"配置删了但工具还在"）。

对标 WorkBuddy 的完整判断见 [AGENT-BENCHMARK.md](AGENT-BENCHMARK.md)。
