# akso-workbench-ts

akso-workbench 的**服务层**（DSH 插件侧的域能力）。不是重写整个 workbench —— 是把它拆成
"DSH 已经提供的"与"只有我们知道的"两半，只保留后者。

## 保留 / 丢弃（用户要求 2 的落地清单）

| workbench 现有 | 处理 | 理由 |
|---|---|---|
| `harness/` 循环、缓存、上下文 2,262 行 | **丢弃** | DSH 的 agent 循环与上下文管理 |
| `sandbox/` 1,844 行 | **丢弃** | DSH 三档沙箱（read-only / workspace-write / danger-full-access） |
| `skills/` 1,775 行 | **丢弃** | DSH 的 skill（SKILL.md + 脚本） |
| MCP 客户端 1,032 行 | **丢弃** | DSH 内置 MCP 客户端 |
| `static/` 11,041 行 + 对话 UI | **丢弃** | DSH 的 Web GUI / client-ui 插件 |
| `approvals.py` 772 行 | **降到 ~100 行** | 计划文件 + `ask_user_question` 弹窗（见 `docs/10`） |
| `services/egmp/**` 3,851 行 | **移植（本仓）** | 平台语义只有我们知道 |
| `vault.py` / `accounts.py` | **改为调 akso-cloud** | 不再两处实现同一套账号库 |

## 当前进度

| 模块 | 状态 | 判据 |
|---|---|---|
| `src/egmp/client.ts`（登录 / 信封 / 分页 / token 缓存） | ✅ | `test/client.test.ts` **7/7** |
| `src/egmp/*`（kernel / queries / replay） | ⏳ | — |
| 计划文件 + apply（写对象） | ⏳ | 设计见 `docs/10` §5 |
| DSH 工具注册（`defineTool`） | ⏳ | 需要 `@deepseek-ai/dsh-tools`（插件包依赖） |

## 跑

```powershell
npm install
npm test
npm run typecheck
```

零运行时依赖：HTTP 用全局 `fetch`，测试用 `node --test`（Node 24 直接跑 `.ts`）。

## 三条从 Python 版继承的"静默失败"防线

1. **信封校验**：平台回 HTTP 200 但 `code != 0` 仍是失败 —— 漏掉它，"失败"会显示成"没有数据"。
2. **分页硬上限**：`hasNext` 恒 true 的坏代理会无限请求；到 `MAX_PAGES` **抛错**，不返回半截。
3. **token 按 `(base_url, username)` 分键**：不分键会把 A 环境的令牌发到 B 环境（偶发 401）。
