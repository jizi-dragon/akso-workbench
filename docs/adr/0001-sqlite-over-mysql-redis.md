# ADR-0001：数据层继续用 SQLite（不引入 MySQL / Redis）

- **状态**：已采纳（2026-09-11 定稿）
- **决策者**：用户定稿
- **取代**：`docs/数据层决策.md`（已并入本文，原文件归档至 `docs/archive/`）
- **相关**：[SCHEMA.md](../SCHEMA.md)（表结构与迁移）、[ADR-0006](0006-browser-pool-single-thread.md)、[ADR-0007](0007-credentials-never-plaintext.md)

## 背景

交付形态是**单机安装给每个人**：Electron 外壳 + 本机 sidecar 服务（`127.0.0.1:18765`）+ NSIS 安装包，
单用户单进程。曾出现「数据量会变大，是否该换 MySQL / Redis」的动议。

实测依据（一次全量盘点 `tools/db_maintenance.py --report`）：

| 项 | 实测值 |
|---|---|
| `workbench.db` 文件 | 82.02 MB |
| 表数量 / 全部行数 | 14 张 / 3,660 行 |
| `doc_chunks.embedding` | **75,809 KB（占文件 90%）** |
| 真实业务数据 | `account` 4 行、`platform_env` 3 行、`insight_runs` 4 行、`blueprint_jobs` 1 行、`settings` 11 行 —— 合计不到 20 KB |
| 空闲页 `freelist_count` | 63 页（0.25 MB） |

关键判断：**74 MB 是知识库功能下线后的遗留向量数据，运行时代码零处读取**。
全仓检索 `documents` / `doc_chunks` 只命中 `db.py` 的迁移定义与 `storage.py` 的一句注释。
空闲页仅 0.25 MB，说明这 74 MB 不是碎片，而是仍存活但已无人使用的行。

## 决策

**继续使用 SQLite（WAL）；不引入 MySQL，不引入 Redis。**

三条附带结论：

1. `job_logs` 表建了索引却**零处写入**——运行日志走磁盘文件（`log_path`），日志增长不落在数据库里。
   **这个设计是对的，不要改**（见 [SCHEMA.md](../SCHEMA.md) §3.8）。
2. 磁盘上真正在增长的是浏览器缓存与产物文件（`Partitions` 39.8 MB、`Cache` 6.6 MB、
   `runtime/insight` 1.34 MB），与数据层无关。
3. `export_backup()` 导出 JSON（仅账号/环境/盒子），**不导出数据库文件**，
   所以库体积膨胀不会污染用户的备份产物。

## 取舍

### 为什么排除 Redis

1. **不能做系统记录**。Redis 以内存为主，RDB 是间隔快照，AOF `everysec` 最多丢 1 秒。
   本项目最核心的资产是 Fernet 加密凭据（`account.password_enc` + `settings.account_fernet_key`），
   **丢失不可重建**，任何回滚窗口都不可接受。
2. **没有关系约束**。本项目依赖 `ON DELETE CASCADE`（`platform_env → account`、`documents → doc_chunks`）
   与外键完整性，Redis 需在应用层全部重写。
3. **数据必须全驻内存**，成本随数据量线性上升——与「数据量变大」的诉求正好相反。

> 反讽的事实：**项目里已经实现了 Redis 的典型用例，并且刻意没用 Redis。**
> `routes_extension.py` 的指令队列是内存列表 `_commands` + ack 集合，执行面状态是带 60s TTL 的
> 内存字典 `_STATE`，**只有单调递增的 `ext_cmd_seq` 游标落到 settings 表**。
> 这正是「临时数据放内存、只持久化必要游标」的正确做法；为此拉一个 Redis 守护进程进用户的安装包是纯负担。

### 为什么现在也不上 MySQL

**决定性理由是交付形态，不是性能**：

- 上 MySQL 等于在用户机器上多装一个服务端守护进程：安装、服务账号、端口、root 密码、初始化、
  升级、备份、防火墙。当前「一键安装」的卖点会直接损失，装机成功率下降。
- 性能上也无收益：`workbench/main.py` 是 `uvicorn.run(...)`，**默认 `workers=1` 单进程**；
  `db.py` 是单连接 + 全局 `RLock`。单写入进程的负载下，MySQL 相对 SQLite WAL 没有任何优势。
- 「更大的数据量」目前是**假设而非观测**。按现有写入模式估算年增长：`agent_audit`（每次工具调用 1 行）
  约 20–30 MB/年；`insight_runs` / `blueprint_jobs` 为 KB 级。

## 后果

### 正面

- 一键安装成立：无额外守护进程、无端口/账号/防火墙配置。
- 备份 = 复制一个文件；`VACUUM INTO` 可做一致性热备份。
- 迁移机制沿用 `_sqlx_migrations`，与旧 Tauri 版数据库互认（同一 `mogul.db` 可被两版接续使用）。

### 负面 / 已接受的代价

- **首个会崩的地方是进程内状态，不是数据库**：`routes_extension.py` 的 `_commands` / `_acked` / `_STATE`
  都是进程内状态。一旦开多 worker 或多机部署，指令队列与状态徽标会**静默失效**（不报错，只是丢指令）。
  届时的正确修法是把指令队列落成数据库表（`commands`），**而不是上 Redis**。
- 单连接 + 全局锁：并发写会被串行化。当前负载下无感。

### 为「万一要换」预留的切换缝

`db.py` 是唯一数据访问入口（全部走 `query` / `query_one` / `execute`，统一 `?` 占位符）。
SQLite 特有构造已收拢到四处：

1. `PRAGMA` 语句 → `db._PRAGMAS`
2. 逗号串包含匹配 `(',' || col || ',') LIKE ?` → `db.csv_like()`（`pool` 等组合字段）
3. `?` 占位符（psycopg / PyMySQL 为 `%s`，改 wrapper 即可）
4. `INTEGER PRIMARY KEY AUTOINCREMENT`（仅迁移 8 的 `job_logs.id`；迁移历史不可变故保留。
   其余表均为 UUID 文本主键，本身与方言无关。**新表不要再用 AUTOINCREMENT**）

将来换库 = 改这一节 + 数据搬迁脚本，而不是全库搜索替换。

## 重新评估的量化触发器

**命中任意一条再动手，不要提前**：

1. 需要多进程 / 多机共享数据（`uvicorn workers > 1`，或服务端部署给多人使用）
2. 实测 `SQLITE_BUSY` / `database is locked` 成为常态
3. 数据库超过数 GB，或单机磁盘 / 备份窗口无法满足
4. 需要行级权限、审计合规、BI 直连、PITR / 主从
5. 出现多租户需求

真到那天选谁（**分层，不是二选一**）：

| 层 | 选择 | 理由 |
|---|---|---|
| 系统记录（关系数据） | **PostgreSQL 首选** | JSONB 承载 `tags` / `args` 等半结构化字段、并发写更强、类型严格 |
| 系统记录（备选） | MySQL | 团队/公司既有运维栈是 MySQL 就选 MySQL，对本项目负载差异不致命 |
| 队列 / 实时推送 / 分布式锁 | Redis（仅作补充） | 只在需要跨进程跨机时引入，**永远不替代系统记录** |

## 已执行的维护动作

1. **迁移 13**：补齐列表查询索引 `agent_audit(created_at)`、`blueprint_jobs(created_at)`、
   `insight_runs(created_at)`、`account(box)`。
2. **`tools/db_maintenance.py`**：诊断 / 备份 / 瘦身工具，默认只读。
   `--all` = `VACUUM INTO` 备份 → 置空 `doc_chunks.embedding`（保留正文）→ `VACUUM` 回收。
   **刻意不做成自动迁移**：迁移历史不可变，且「自动删除用户数据」的迁移风险过高。
3. 迁移 13 之前的物理回收需在关闭桌面端后执行（`VACUUM` 需独占写锁）。
4. 遗留项：`%APPDATA%\MogulWorkbench\mogul.db`（约 80 MB 旧库）已不再被读取
   （`config.bootstrap_database()` 仅在 `workbench.db` 不存在时才复制旧库），确认新版数据无误后可自行归档；
   `settings` 中 `dingtalkLastSync` / `embeddingApiKey` / `embeddingModel` / `embeddingBaseUrl`
   为已下线功能残留，可用 `--purge-legacy-settings` 清除。
