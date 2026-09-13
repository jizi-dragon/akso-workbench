# 数据模型与协议契约（SCHEMA）

> **本文档的读者**：要加表/改表的人、要写 SQL 的人、要理解「为什么代码删了表还在」的人。
> 变更频率 O(迁移)，且**数据库迁移只增不改**——改表必须新增一条迁移，历史迁移不可变。
>
> 相关文档：[ADR-0001](adr/0001-sqlite-over-mysql-redis.md)（为何是 SQLite）、
> [CONFIG.md](CONFIG.md)（settings 表键清单）、[API.md](API.md)（表 → 接口的映射）、
> [CONFIG.md](CONFIG.md)（数据目录与维护工具）。

## 一、载体与位置

| 项 | 值 |
|---|---|
| 引擎 | SQLite（WAL + 外键约束） |
| 默认路径 | `%APPDATA%\AksoWorkbench\workbench.db`（Windows）/ `~/.akso-workbench/workbench.db` |
| 覆盖 | 环境变量 `WORKBENCH_DB` / `MOGUL_DB`（见 [CONFIG.md](CONFIG.md) §2） |
| 访问层 | `workbench/db.py` **唯一入口**（`query` / `query_one` / `execute`），单连接 + `threading.RLock` |
| 连接参数 | `check_same_thread=False`、`row_factory=sqlite3.Row` |
| PRAGMA | `journal_mode = WAL`、`foreign_keys = ON`（收拢在 `db._PRAGMAS`） |
| 旧库接管 | `config.bootstrap_database()`：仅当 `workbench.db` **不存在**时，才从旧 mogul 库（`%APPDATA%\MogulWorkbench\mogul.db` 或 `com.example.mogulsimulator`）复制 |

**为什么是单连接**：交付形态是单机单人（Electron 壳 + 本机 sidecar，`uvicorn workers=1`），
单写入进程下 MySQL 相对 SQLite WAL 没有优势。完整论证（含 Redis/MySQL 排除理由与换库触发器）见
[ADR-0001](adr/0001-sqlite-over-mysql-redis.md)。

## 二、迁移机制

迁移沿用**旧 Tauri/sqlx 版的 `_sqlx_migrations` 表**，保证同一个 `mogul.db` 在新旧两版之间互认。

```sql
CREATE TABLE IF NOT EXISTS _sqlx_migrations (
    version BIGINT PRIMARY KEY,
    description TEXT NOT NULL,
    installed_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    success BIGINT NOT NULL DEFAULT 1,
    checksum BLOB,
    execution_time_in_millis BIGINT
)
```

- 启动时取 `MAX(version)`，仅执行 `version > current` 的迁移（`db._migrate`）。
- 记录迁移时**动态读 `PRAGMA table_info`**（`db._record_migration`）：旧 Tauri 库的
  `_sqlx_migrations` 由 sqlx 变体创建（`checksum BLOB NOT NULL`、`execution_time BIGINT NOT NULL`），
  逐列适配并对未知 `NOT NULL` 无默认值列填中性值，避免接管旧库时 IntegrityError。
- `checksum` 按 sqlx 语义取迁移 SQL 的 SHA-384 摘要。

### 迁移账（1–13，全部已应用）

| # | 名称 | 内容 | 血统 |
|---|---|---|---|
| 1 | `create_conversations_messages_settings` | `conversations` / `messages` / `settings` | fork mogul |
| 2 | `create_knowledge_base` | `documents` / `knowledge_nodes` / `knowledge_edges` + 索引 | fork mogul |
| 3 | `add_embedding_column` | `knowledge_nodes.embedding` | fork mogul |
| 4 | `create_workbench_tables` | `doc_chunks` / `faq_cards` / `qa_metrics` | fork mogul |
| 5 | `add_dingtalk_sync_fields` | `documents` / `doc_chunks` 钉钉同步字段 + 索引 | fork mogul |
| 6 | `drop_knowledge_graph_and_faq` | 删 `knowledge_edges` / `knowledge_nodes` / `faq_cards` | fork mogul |
| 7 | `create_unified_accounts` | `platform_env` / `account` + 索引 | 阶段 2A |
| 8 | `create_task_runs_and_logs` | `blueprint_jobs` / `insight_runs` / `job_logs` + 索引 | 阶段 1B |
| 9 | `create_agent_audit` | `agent_audit` + 索引 | 阶段 3 |
| 10 | `add_account_pool` | `account.pool` | 账号中心 |
| 11 | `add_account_box` | `account.box` | 账号中心 |
| 12 | `add_account_tab_name` | `account.tab_name` | 账号中心 |
| 13 | `add_created_at_indexes` | `agent_audit` / `blueprint_jobs` / `insight_runs` 的 `created_at` 索引 + `account(box)` | 数据层维护 |
| 14 | `add_insight_workspace_and_coverage` | `insight_runs` 增 `workspace_dir`（多个工作区目录以**换行分隔**）与 `coverage_status`（三态覆盖度） | 平台洞察工作区 |

> **迁移 1–6 为 fork 历史，按不可变纪律保留**。其中 `documents` / `doc_chunks` / `qa_metrics`
> 的知识库相关功能**源码已删**（表仅留存、不再读写）；`knowledge_nodes` / `knowledge_edges` /
> `faq_cards` 已在迁移 6 删除。**勿因「表还在」就恢复业务代码。**
>
> 实测现状（2026-09-11）：13 条迁移全在册；`documents` 129 行、`doc_chunks` 3,420 行仍占空间
> （其中 `doc_chunks.embedding` 是主要体积来源），`qa_metrics` 1 行。回收见 [CONFIG.md](CONFIG.md) §6。

## 三、表结构（现行读写）

### 3.1 `conversations` — 对话会话（迁移 1）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | UUID4 文本 |
| `title` | TEXT | NOT NULL | 首条用户消息前 30 字符自动生成 |
| `created_at` | INTEGER | NOT NULL | 毫秒时间戳 |
| `updated_at` | INTEGER | NOT NULL | 毫秒时间戳 |

### 3.2 `messages` — 对话消息（迁移 1）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | UUID4 |
| `conversation_id` | TEXT | NOT NULL, FK → `conversations(id) ON DELETE CASCADE` | |
| `role` | TEXT | NOT NULL | `user` / `assistant` / `system` / `tool` |
| `content` | TEXT | NOT NULL | |
| `created_at` | INTEGER | NOT NULL | |

### 3.3 `settings` — 键值设置（迁移 1）

| 列 | 类型 | 约束 |
|---|---|---|
| `key` | TEXT | PK |
| `value` | TEXT | NOT NULL |

读写走 `services/storage.get_setting` / `set_setting`（UPSERT：`ON CONFLICT(key) DO UPDATE`）。
**键清单与语义见 [CONFIG.md](CONFIG.md) §3**（含凭据密钥 `account_fernet_key` 与三处已下线功能残留键）。

### 3.4 `platform_env` — 平台环境（迁移 7）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | |
| `name` | TEXT | NOT NULL | |
| `base_url` | TEXT | NOT NULL DEFAULT `''` | 服务端会 `strip().rstrip("/")` 规范化 |
| `note` | TEXT | NOT NULL DEFAULT `''` | |
| `created_at` / `updated_at` | INTEGER | NOT NULL | |

### 3.5 `account` — 统一账号库（迁移 7 + 10/11/12 加列）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | |
| `env_id` | TEXT | NOT NULL, FK → `platform_env(id) ON DELETE CASCADE` | |
| `username` | TEXT | NOT NULL | |
| `password_enc` | TEXT | NOT NULL | **Fernet 密文**，密钥在 `settings.account_fernet_key` |
| `role` | TEXT | NOT NULL DEFAULT `''` | |
| `tags` | TEXT | NOT NULL DEFAULT `'[]'` | JSON 数组字符串 |
| `note` | TEXT | NOT NULL DEFAULT `''` | |
| `status` | TEXT | NOT NULL DEFAULT `'idle'` | |
| `created_at` / `updated_at` | INTEGER | NOT NULL | |
| `pool` | TEXT | NOT NULL DEFAULT `''`（迁移 10） | 分配池角色逗号串：`config` / `monitor` / `config,monitor` |
| `box` | TEXT | NOT NULL DEFAULT `''`（迁移 11） | 记忆盒子名；**空串 = 默认盒子** |
| `tab_name` | TEXT | NOT NULL DEFAULT `''`（迁移 12） | 页签名（轮盘扇区名优先取它，空则回落 `username`） |

索引：`idx_account_env(env_id)`、`idx_account_box(box)`。

**组合字段的匹配语义**：`pool` 是逗号串，取「包含任一值」用 `db.csv_like()` →
`(',' || col || ',') LIKE ?`（两端包夹逗号保证组合值精确命中）。
用裸 `LIKE '%config%'` 会误命中未来可能出现的 `configX` 角色。

**字段名双语义（务必分清）**：
DB 行是 **snake_case**（`env_base_url`，由 JOIN 带上），
而 `export_backup()` 输出 **camelCase**（`envBaseUrl`，备份文件契约）。
`routes_extension` 曾读错键 → 快照 `host` 恒空 → 扩展静默丢弃全部账号（0.2.4 实锤断点）。

### 3.6 `blueprint_jobs` — 配置工厂任务（迁移 8）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | |
| `module` | TEXT | NOT NULL DEFAULT `'akso-auto'` | 现状写入 `'egmp-native'`（原生化后） |
| `command` | TEXT | NOT NULL | `create` / `orchestrate` |
| `blueprint_path` | TEXT | DEFAULT `''` | |
| `env_id` | TEXT | 可空 | 工厂侧固定写 `'workbench'` 占位 |
| `status` | TEXT | NOT NULL DEFAULT `'pending'` | `running` / `succeeded` / `failed` |
| `exit_code` | INTEGER | 可空 | 原生化后恒为 NULL |
| `log_path` | TEXT | DEFAULT `''` | 日志**落磁盘文件**，不入库（见 §四） |
| `artifact_dir` | TEXT | DEFAULT `''` | |
| `created_at` / `updated_at` | INTEGER | NOT NULL | |

索引：`idx_blueprint_jobs_status(status)`、`idx_blueprint_jobs_created_at(created_at)`。

### 3.7 `insight_runs` — 平台洞察任务（迁移 8）

列同 `blueprint_jobs` 结构，`command` ∈ `login` / `inventory` / `understand` / `spider`，
`module` 现状写 `'egmp-native'`，`objects` 列为洞察特有（逗号分隔对象编码）。
索引：`idx_insight_runs_status`、`idx_insight_runs_created_at`。

### 3.8 `job_logs` — 任务日志行（迁移 8）

| 列 | 类型 | 约束 |
|---|---|---|
| `id` | INTEGER | PK AUTOINCREMENT |
| `job_id` | TEXT | NOT NULL |
| `line` | TEXT | NOT NULL |
| `created_at` | INTEGER | NOT NULL |

> ⚠️ **本表建了索引却零处写入**。运行日志走磁盘文件（`log_path`），
> 因此日志增长不落在数据库里——**这是对的设计，不要「顺手」改成入库**（见 [ADR-0001](adr/0001-sqlite-over-mysql-redis.md) §一附带结论）。
> 它是迁移 8 中唯一使用 `INTEGER PRIMARY KEY AUTOINCREMENT` 的表（迁移历史不可变故保留）；
> **新表不要再用 AUTOINCREMENT，一律 `TEXT PRIMARY KEY`**（UUID 文本主键与方言无关）。

### 3.9 `agent_audit` — Agent 工具审计（迁移 9）

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | TEXT | PK | `uuid4().hex[:12]` |
| `tool` | TEXT | NOT NULL | `login_platform` / `read_config` / `run_insight` / `browser_status` |
| `args` | TEXT | NOT NULL DEFAULT `'{}'` | 入参 JSON，截断 2,000 字符 |
| `status` | TEXT | NOT NULL DEFAULT `'ok'` | `ok` / `bad_request` / `error` |
| `result_summary` | TEXT | NOT NULL DEFAULT `''` | 截断 1,000 字符 |
| `duration_ms` | INTEGER | 可空 | |
| `created_at` | INTEGER | NOT NULL | |

索引：`idx_agent_audit_tool(tool)`、`idx_agent_audit_created_at(created_at)`。

> ⚠️ `GET /api/agent/audit` 目前 `SELECT *` 全列回吐（含 `args` 原始 JSON）。
> 表当前 0 行，但**与「凭据不落明文」口径不一致**——见 [CODEBASE_OVERVIEW.md](CODEBASE_OVERVIEW.md) 的风险台账。

### 3.10 已下线但表仍在（勿恢复业务代码）

| 表 | 来源迁移 | 现状 |
|---|---|---|
| `documents` | 1（+5 加列） | 129 行，知识库/钉钉同步下线后**不再读写** |
| `doc_chunks` | 4（+5 加列） | 3,420 行；`embedding` 列是文件体积主因，**不再读写** |
| `qa_metrics` | 4 | 1 行；`conversations` 对话度量仍写（`insert_qa_metric` / `update_qa_feedback`） |

## 四、数据目录与产物（非数据库状态）

数据库只管**结构化业务数据**。运行时产物落在 `config.RUNTIME_DIR`（= `数据目录/runtime`），
浏览器缓存由壳自管。完整路径规则见 [CONFIG.md](CONFIG.md) §5，此处列与表强相关的部分：

| 路径 | 归属表/功能 | 说明 |
|---|---|---|
| `runtime/insight/<job_id>/` | `insight_runs.artifact_dir` | 洞察产物：`inventory.json/md`、`{code}.json/md`、`*.drawio`、`proc.log` |
| `runtime/factory/<blueprint_id>/` | `blueprint_jobs.artifact_dir` | 蓝图暂存 `blueprint.json`、审阅件 `spec.md` / `checklist.md`、`checkpoint.json`、`proc.log` |
| `runtime/browser-states/<account_id>.json` | 无表 | Playwright `storage_state`（免密直达）；`has_saved_session` = 文件存在 |
| `runtime/monitor/<account_id8>-<stamp>/` | 无表 | Monitor 录制产物（`monitor-log.json` / `checkpoints` / `filter-stats`） |
| `runtime/egmp-tokens/token-<env_id>.json` | 无表 | eGMP 登录 token 缓存（TTL 55 分钟） |

**为什么这些不入库**：产物是「一次性大文件 + 需要直接给用户下载」的形态，
塞进 SQLite 会同时劣化备份体积与下载路径。BLOB 化还会让 `VACUUM` 代价不可控。

## 五、扩展协议契约（快照 / 事件）

> 这不是数据库 schema，而是**跨进程的数据契约**——与服务端表结构强耦合（`export_backup` 的输出即快照载荷），
> 所以放本文档。端点定义见 [API.md](API.md) §11，机制说明见 [EXTENSION-PLANE.md](EXTENSION-PLANE.md) §4。

### 5.1 快照（`GET /extension/snapshot`）

```jsonc
{
  "format": "akso-workbench-snapshot",
  "version": 1,                  // 快照格式版本（与项目版本号无关）
  "generatedAt": 1789120603425,
  "snapshotId": "<sha256[:16]>",  // accounts + boxes 的规范 JSON 内容哈希
  "desktopVersion": "0.3.4",
  "fernetKey": "<urlsafe-b64 32B>",
  "accounts": [
    {
      "desktopId": "<account.id>",
      "host": "demo-val.example.com",  // 由 env_base_url 解析，保留端口
      "scheme": "https",
      "tabName": "T0901",                   // 页签名优先，空则回落 username
      "username": "dev-admin",
      "passwordEnc": "<Fernet token>",      // 密文；明文只在扩展内存中出现
      "box": ""
    }
  ],
  "boxes": { "remembered": [], "defaultName": "默认盒子", "disabled": [] },
  "sites": ["demo-val.example.com"]
}
```

- `snapshotId` 是**幂等键**：扩展比对未变即整批跳过。
- `host` 由 `routes_extension._host_of()` 解析，**保留端口**（内网非标端口站点常见）；
  `scheme` 由 `_scheme_of()` 解析，供扩展 `parallelStore.create` 决定站点协议。
- **字段来源是 `export_backup()`（camelCase 语义）**，勿改成直接从表行读 snake_case 键（0.2.4 实锤）。

### 5.2 备份文件格式（`GET /api/accounts/export`，DataBackup v1）

```jsonc
{
  "format": "akso-workbench-backup",
  "version": 1,
  "exportedAt": 1789120603425,
  "fernetKey": "<key>",          // ⚠ 密钥随文件走（原 cryptoSeed 语义）
  "envs":     [{ "id", "name", "baseUrl", "note" }],
  "boxes":    { "remembered": [], "defaultName": "" },
  "accounts": [{ "id", "envBaseUrl", "username", "passwordEnc", "box", "role", "tags", "tabName" }]
}
```

> ⚠️ **备份文件本身即凭据**（含 Fernet 密钥 + 全部密文口令），交付用户自行保管。
> 导入（`POST /api/accounts/import-backup`）用文件内密钥解密 → 本地密钥重加密 → 同站同名去重；
> 解不开的条目**跳过并计数**（损坏/篡改/密钥不符），不整体失败。

### 5.3 指令（`GET /extension/commands`）

```jsonc
// 响应
{
  "commands": [{ "seq": 77, "type": "par.open|wheel.toggle", "payload": {"accountId": "..."}, "at": 1789120603425 }],
  "cursor": 77,       // 当前最大 seq
  "longPoll": true    // 是否走了长轮询挂起（false = wait=0 立即返回）
}
```

- **`seq` 跨服务端重启单调递增**（持久在 `settings.ext_cmd_seq`）。
  若重置，扩展侧持久游标会让所有新指令被 `seq > after` 永久吞掉（0.2.4 实锤）。
- ack（`POST /extension/ack {seqs:[N]}`）：扩展持久化游标后回执；服务端仅在**全部未回收指令都已 ack** 时清空队列。

### 5.4 状态上报（`POST /extension/state`）

```jsonc
{ "items": [{ "desktopId": "...", "tabs": 1, "hasToken": true }],
  "extVersion": "0.3.4", "desktopVersion": "0.3.4" }
```

服务端存内存 `_STATE`（TTL 60s，`_STATE_TTL_MS`），供账号中心四态徽标。
`extVersion` **无条件**接收并更新 `_ext_meta`（映射为空也要能传出版本，否则旧版检测永远失效）。

## 六、改表清单（做对这一步只需照抄）

1. **新增一条迁移**（追加到 `db.MIGRATIONS` 末尾，版本号 +1，**不修改任何历史条目**）。
   - 加列用 `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT '<中性值>'`（SQLite 要求有默认值）。
   - 建索引用 `CREATE INDEX IF NOT EXISTS`；新表用 `TEXT PRIMARY KEY`（勿用 AUTOINCREMENT）。
2. 若新列会被 `SELECT a.*` 带出：同步检查 `services/accounts._sanitize()`（**`password_enc` 必须继续被 pop 掉**）
   与 `export_backup()` 的 camelCase 映射。
3. 若新列需要在接口契约里出现：更新 [API.md](API.md) 对应端点与本文档的表定义。
4. 若新列是配置项：更新 [CONFIG.md](CONFIG.md) §3。
5. 跑 `.\\.venv\\Scripts\\python.exe -m pytest`（`tests/conftest.py` 用临时库，迁移会在测试库上全新执行一遍）
   与 `\\.venv\\Scripts\\python.exe tools\\db_maintenance.py --report`（确认真实库迁移到位）。

## 七、维护工具

```powershell
# 只读诊断（默认行为）：表规模、大列体积、已下线功能残留 settings 键
.\.venv\Scripts\python.exe tools\db_maintenance.py --report

# 一致性备份（VACUUM INTO）
.\.venv\Scripts\python.exe tools\db_maintenance.py --backup

# 瘦身：置空 doc_chunks.embedding（保留正文）→ 回收文件空间
.\.venv\Scripts\python.exe tools\db_maintenance.py --trim
.\.venv\Scripts\python.exe tools\db_maintenance.py --vacuum   # 需独占写锁：先关桌面端
.\.venv\Scripts\python.exe tools\db_maintenance.py --all      # 备份 + 瘦身 + 回收
.\.venv\Scripts\python.exe tools\db_maintenance.py --purge-legacy-settings
```

- **刻意不做成自动迁移**：迁移历史不可变，且「自动删除用户数据」的迁移风险过高。
- ⚠️ `--vacuum` 需独占写锁，否则可能 `SQLITE_BUSY`；脚本会先复检（`--force` 可跳过复检）。
