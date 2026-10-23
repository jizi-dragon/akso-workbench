# ACP（Agent Client Protocol）适配器

> 面向编辑器 / DeepSeek Harness 的 **stdio JSON-RPC** 入口。
> 实现是 `workbench/acp.py`，判据是 `tests/test_acp.py`。

## 启动

```powershell
.\.venv\Scripts\python.exe -m workbench.acp
```

stdout **只放协议消息**（NDJSON，一行一条）；诊断走 stderr。

## 支持的方法（v1）

| 方法 | 行为 |
|---|---|
| `initialize` | 返回 `protocolVersion: 1`、`loadSession: false`、文本 prompt 能力 |
| `session/new` | 参数 `{cwd}`（必须是已存在的绝对路径）；返回 `{sessionId, cwd}` |
| `session/prompt` | 参数 `{sessionId, prompt:[{type:"text",text}]}`；流式发 `session/update`（`agent_message_chunk`），返回 `{stopReason}` |
| `session/cancel` | 通知；设置本会话的停止标志，循环在下一个检查点收尾 |

不支持：`session/load` / `resume` / `list` / `close` / `delete`、图片/音频附件、
权限卡片、计划卡片。未知方法返回 `-32601`。

## 两条**诚实边界**

1. **没有权限卡片**：ACP 的 `session/request_permission` 需要客户端 UI。本适配器没有实现它，
   所以 `AgentConfig.approval_wait_s=0` —— 写工具（`memory_add` / `memory_delete` 等）
   会**快速失败**，不会把客户端挂死。只读任务不受影响。
2. **会话历史只保留 user/assistant 文本**：工具轨迹留在服务端 session/DB，不回传给客户端；
   下一轮模型看到的是裁剪后的对话。这是 v1 的取舍，不是"完整 ACP"。

## DSH 配置示例

DSH 的 `subagent-acp` 需要把命令指向本仓解释器：

```yaml
- insert:
    - id: subagent-acp
      name: '@deepseek-ai/dsh-subagent-acp'
      config:
        providerName: workbench
        command: 'D:\ai_assistant\Akso-AI\akso-workbench\.venv\Scripts\python.exe'
        args: ['-m', 'workbench.acp']
        permission: reject
    - id: tool-subagent-workbench
      name: '@deepseek-ai/dsh-tool-subagent'
      config:
        provider: workbench
        toolName: workbench
        maxDepth: provider-managed
```

`permission: reject` 与上面的"写工具快速失败"一致：DSH 不会替本适配器回答权限卡片。

## 安全姿态

- **不做认证**：ACP 是本地子进程协议，信任边界 = 本机（与 `/api/*` 同一取舍）。
- API Key 从本仓设置读；没有 Key 时 `session/new` 返回 `-32000 Authentication required`。
- 每轮工具调用仍走服务端的风险门； **没有**因为 ACP 而放宽任何 `Risk` 分级。
