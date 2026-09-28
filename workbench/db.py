"""SQLite 访问层：连接管理、迁移（兼容旧 Tauri/sqlx 的 _sqlx_migrations 表）。

设计要点：
- 单连接 + 线程锁（本地单用户场景，足够且最简单）
- 迁移机制沿用旧版的 _sqlx_migrations 表，保证同一个 mogul.db 在
  旧 Tauri 版与本 Python 版之间无缝互认（版本 1-4 已含知识工作台全部表）
- 迁移 1-6 为 fork 历史（含已下线的知识库表结构），按不可变纪律保留；
  迁移 7-9 为 Workbench 新增（账号库/任务台账/Agent 审计）；
  迁移 10-12 为账号中心扩展（分配池/盒子/页签名）；迁移 13 为列表查询索引。
- 交付形态为单机单人，故不引入 MySQL/Redis（详见 docs/adr/0001-sqlite-over-mysql-redis.md）；
  SQLite 特有构造收拢在下方「方言边界」一节
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from typing import Any, Iterable

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "create_conversations_messages_settings", """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""),
    (2, "create_knowledge_base", """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    document_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS knowledge_edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (source_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES knowledge_nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_title ON knowledge_nodes(title);
"""),
    (3, "add_embedding_column", """
ALTER TABLE knowledge_nodes ADD COLUMN embedding TEXT;
"""),
    (4, "create_workbench_tables", """
CREATE TABLE IF NOT EXISTS doc_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_document ON doc_chunks(document_id);
CREATE TABLE IF NOT EXISTS faq_cards (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    category TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_metrics (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    hit TEXT NOT NULL DEFAULT 'none',
    feedback INTEGER,
    latency_ms INTEGER,
    created_at INTEGER NOT NULL
);
"""),
    (5, "add_dingtalk_sync_fields", """
ALTER TABLE documents ADD COLUMN source_id TEXT;
ALTER TABLE documents ADD COLUMN origin_url TEXT;
ALTER TABLE documents ADD COLUMN source_updated_at INTEGER;
ALTER TABLE documents ADD COLUMN synced_at INTEGER;
ALTER TABLE doc_chunks ADD COLUMN chunk_hash TEXT;
ALTER TABLE doc_chunks ADD COLUMN valid_to INTEGER;
CREATE INDEX IF NOT EXISTS idx_documents_source_id ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_valid ON doc_chunks(valid_to);
"""),
    (6, "drop_knowledge_graph_and_faq", """
DROP TABLE IF EXISTS knowledge_edges;
DROP TABLE IF EXISTS knowledge_nodes;
DROP TABLE IF EXISTS faq_cards;
"""),
    (7, "create_unified_accounts", """
CREATE TABLE IF NOT EXISTS platform_env (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS account (
    id TEXT PRIMARY KEY,
    env_id TEXT NOT NULL,
    username TEXT NOT NULL,
    password_enc TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'idle',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (env_id) REFERENCES platform_env(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_account_env ON account(env_id);
"""),
    (8, "create_task_runs_and_logs", """
CREATE TABLE IF NOT EXISTS blueprint_jobs (
    id TEXT PRIMARY KEY,
    module TEXT NOT NULL DEFAULT 'akso-auto',
    command TEXT NOT NULL,
    blueprint_path TEXT NOT NULL DEFAULT '',
    env_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    exit_code INTEGER,
    log_path TEXT NOT NULL DEFAULT '',
    artifact_dir TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS insight_runs (
    id TEXT PRIMARY KEY,
    module TEXT NOT NULL DEFAULT 'akso-cc',
    command TEXT NOT NULL,
    objects TEXT NOT NULL DEFAULT '',
    env_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    exit_code INTEGER,
    log_path TEXT NOT NULL DEFAULT '',
    artifact_dir TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    line TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_blueprint_jobs_status ON blueprint_jobs(status);
CREATE INDEX IF NOT EXISTS idx_insight_runs_status ON insight_runs(status);
"""),
    (9, "create_agent_audit", """
CREATE TABLE IF NOT EXISTS agent_audit (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ok',
    result_summary TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_audit_tool ON agent_audit(tool);
"""),
    (10, "add_account_pool", """
ALTER TABLE account ADD COLUMN pool TEXT NOT NULL DEFAULT '';
"""),
    (11, "add_account_box", """
ALTER TABLE account ADD COLUMN box TEXT NOT NULL DEFAULT '';
"""),
    (12, "add_account_tab_name", """
ALTER TABLE account ADD COLUMN tab_name TEXT NOT NULL DEFAULT '';
"""),
    (13, "add_created_at_indexes", """
CREATE INDEX IF NOT EXISTS idx_agent_audit_created_at ON agent_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_blueprint_jobs_created_at ON blueprint_jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_insight_runs_created_at ON insight_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_account_box ON account(box);
"""),
    (14, "add_insight_workspace_and_coverage", """
ALTER TABLE insight_runs ADD COLUMN workspace_dir TEXT NOT NULL DEFAULT '';
ALTER TABLE insight_runs ADD COLUMN coverage_status TEXT NOT NULL DEFAULT '';
"""),
    (15, "rename_insight_module_id", """
-- 模块 id 由上游项目名（`akso-cc`）改为本工作台自己的模块名 `insight`。
-- 历史行必须一并改写，且这个字段**历史上已经有过两个名字**：
--   · `akso-cc`     —— 建表时的列默认值（迁移 8）
--   · `egmp-native` —— 原生化之后 routes_insight 实际写入的值
-- 只改其中一个会留下"同一个模块三个名字"，模块维度的统计/筛选会静默漏行。
-- 注意：这**不是**改历史迁移——迁移 1–14 一行未动，本条只改写数据行。
UPDATE insight_runs SET module = 'insight' WHERE module IN ('akso-cc', 'egmp-native');
"""),
    (16, "tool_plugin_layer", """
-- 工具插件层（2026-09-15）：让 agent_audit 能表达"这条调用来自哪个插件的哪一级风险"，
-- 并给"危险操作需人确认"一个留痕表。
--
-- 为什么给 agent_audit 加列而不是建新表：审计的语义没变（还是"谁在什么时候调了什么、
-- 结果如何"），只是维度变多了；拆表会让"按时间列出全部工具调用"变成三表 UNION。
-- 旧行留空串即可——**不回填**，因为历史调用确实没有插件/风险概念。
ALTER TABLE agent_audit ADD COLUMN plugin TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_audit ADD COLUMN risk TEXT NOT NULL DEFAULT 'read';
ALTER TABLE agent_audit ADD COLUMN meta TEXT NOT NULL DEFAULT '{}';

-- 待确认操作留痕（权威状态在内存，见 workbench/approvals.py 的模块文档）。
-- `status`: pending | approved | rejected | expired（consumed 不单独记，令牌一次性即可）。
CREATE TABLE IF NOT EXISTS pending_action (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    plugin TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT 'write',
    args TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    decided_at INTEGER NOT NULL DEFAULT 0,
    decided_by TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pending_action_status ON pending_action(status);
CREATE INDEX IF NOT EXISTS idx_pending_action_created_at ON pending_action(created_at);
"""),
    (17, "agent_cache_layer", """
-- Agent 缓存层（2026-09-15）：让 DeepSeek 的上下文硬盘缓存从"碰运气"变成可测可优化。
--
-- ① conversation_context：**冻结的历史摘要**。改造前摘要每轮重算 —— 既慢又烧 token，
--    更要命的是让请求前缀每轮都变，缓存必然 miss（官方要求"完整匹配一个缓存前缀单元"）。
--    `summarized_upto` 是冻结边界（已折叠的消息条数），只前进不回退 ——
--    这就是「已发出的前缀永不改写」的来源。
CREATE TABLE IF NOT EXISTS conversation_context (
    conversation_id TEXT PRIMARY KEY,
    summary TEXT NOT NULL DEFAULT '',
    summarized_upto INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

-- ② deepseek_usage：官方在 usage 里回 prompt_cache_hit_tokens / prompt_cache_miss_tokens
--    （Responses API 口径为 input_tokens_details.cached_tokens），此前这些字段**被整个丢掉**，
--    于是"缓存到底命中没有"完全没有答案。命中价是未命中价的 1/50，必须可度量。
CREATE TABLE IF NOT EXISTS deepseek_usage (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    calls INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    summary_calls INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deepseek_usage_created_at ON deepseek_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_deepseek_usage_conversation ON deepseek_usage(conversation_id);
"""),
    (18, "rename_factory_module_id", """
-- 模块 id 由上游项目名（`akso-auto`）改为本工作台自己的模块名 `factory`。
-- 与迁移 15（`akso-cc` → `insight`）同一处理，理由也相同：能力已是本工作台的，
-- 界面与接口上都不该再出现上游项目名。
--
-- `blueprint_jobs.module` 历史上出现过两个值：
--   · `akso-auto`    —— 建表时的列默认值（迁移 8）
--   · `egmp-native`  —— 原生化之后 routes_factory 实际写入的值
-- 只改其中一个会留下"同一个模块三个名字"，模块维度的统计/筛选会静默漏行。
-- 注意：这**不是**改历史迁移——迁移 1–17 一行未动，本条只改写数据行。
UPDATE blueprint_jobs SET module = 'factory' WHERE module IN ('akso-auto', 'egmp-native');
"""),
    (19, "sandbox_run_audit", """
-- 沙箱执行审计（2026-09-17，决策见 docs/adr/0014-sandbox-for-untrusted-code.md）。
--
-- ## 为什么单独一张表，而不是并进 agent_audit
--
-- `agent_audit` 是**工具层**的表：主键是"一次工具调用"。而沙箱执行的粒度更细——
-- 一个工具可能在一次调用里跑多个脚本，且沙箱**不只服务工具**（将来 Skill 安装、
-- MCP stdio 都走它）。并进去会让 `agent_audit` 的语义变浑。
--
-- ## 与 agent_audit 怎么关联
--
-- `audit_id` 与 `agent_audit.id` **同名同义**：工具层与执行层各写一行，用同一个 id 串起来。
-- 不设外键——两层可以独立写入（沙箱可能被非工具路径直接调用，那时没有 agent_audit 行）。
--
-- ## 为什么字段这么细
--
-- 这张表的唯一读者是"事后回答三个问题"：**谁跑的？当时用的是哪份策略？树杀干净没有？**
-- 所以 policy_fingerprint 与 tree_kill 必须在表里，不能只落日志——日志会被轮转掉。
CREATE TABLE IF NOT EXISTS sandbox_run (
    id TEXT PRIMARY KEY,
    audit_id TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    policy TEXT NOT NULL DEFAULT '',
    policy_fingerprint TEXT NOT NULL DEFAULT '',
    cwd TEXT NOT NULL DEFAULT '',
    argv_summary TEXT NOT NULL DEFAULT '',
    code INTEGER NOT NULL DEFAULT -1,
    ok INTEGER NOT NULL DEFAULT 0,
    timed_out INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    stdout_chars INTEGER NOT NULL DEFAULT 0,
    stderr_chars INTEGER NOT NULL DEFAULT 0,
    output_truncated INTEGER NOT NULL DEFAULT 0,
    tree_kill TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sandbox_run_created_at ON sandbox_run(created_at);
CREATE INDEX IF NOT EXISTS idx_sandbox_run_audit ON sandbox_run(audit_id);
"""),
    (20, "skill_library_audit", """
-- Skill 共享库的操作留痕（2026-09-17）。
--
-- ## 为什么单独一张表，而不是并进 agent_audit
--
-- 这张表记的是**能力供给链**上的动作（上传/安装/卸载/删除），
-- 与"某次工具调用"（agent_audit）、"某次沙箱执行"（sandbox_run）是三个不同的时间尺度：
-- 供给链的动作一天可能只有几次，但它**改变了以后模型能做什么**——
-- 出问题时第一个要问的就是"这个工具是哪个 Skill 带进来的、什么时候装的"。
--
-- ## 至于 Skill 文件本身
--
-- **不入库也不入 git**：落在 `<DATA_DIR>/skills/{library,installed}/`（用户明确要求）。
-- 表里只存"元数据 + 动作"，文件是唯一的真源（manifest.json 在同目录下）。
-- 这样即便表被清掉，也能靠扫描目录重建；反过来则不行——所以文件是权威。
CREATE TABLE IF NOT EXISTS skill_audit (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    capability_id TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    verdict TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    high_findings INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_audit_created_at ON skill_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_skill_audit_capability ON skill_audit(capability_id);
"""),

    (21, "attachments", """
-- 用户交给 agent 的文件/图片（2026-09-22，用户下达「核心 agent 能力增强」）。
--
-- ## 为什么单独一张表，而不是把内容塞进 messages.content
--
-- `messages.content` 是 TEXT，而模型要收的是**多模态 content parts**（图片是 image_url）。
-- 塞进去会同时打破三件事：DB 的类型、`estimate_tokens()` 的估算、
-- 以及**前缀缓存**（I1：已发出的 messages 前缀永不改写，每轮变一次 = 每轮全 miss，
-- 成本 ×50）。所以分工是：
--
--   messages.content  → 仍然只是**文本**，里面留一句引用：`[附件 att-3f2a：培训记录.xlsx]`
--   attachments       → 文件的**元数据 + 解析产物**（正文在磁盘上，这一行只存坐标与规模）
--   请求构建那一刻（唯一扼流点 `routes_chat._model_history`）→ 把**最新一轮**的引用展开成 parts
--
-- ## 磁盘上的正文是唯一真源
--
-- 与 skill_audit 同一条纪律：文件落在 `<RUNTIME_DIR>/attachments/<会话>/<id>__<安全名>`，
-- 表里只存元数据。表被清掉还能靠扫目录重建；反过来不行。
--
-- ## locator 为什么是 JSON 文本
--
-- 它是"回答能指到出处"的坐标（页/表/行/标题）。不同文件类型坐标不同（PDF 是页、
-- xlsx 是表+区域、docx 是段落序号），用一张稀疏表去表达会立刻长出四张表；
-- 而它**只被工具读**、不参与查询条件 ⇒ 存 JSON 文本，读侧统一 `json.loads`。
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    stored_name TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'stored',
    parse_note TEXT NOT NULL DEFAULT '',
    text_chars INTEGER NOT NULL DEFAULT 0,
    locator TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attachments_conversation ON attachments(conversation_id);
CREATE INDEX IF NOT EXISTS idx_attachments_created_at ON attachments(created_at);
"""),
    (22, "summary_boundary_excludes_system", """
-- I15：摘要边界改为「不含首条 system」计数（2026-09-24）。
--
-- ## 修的是什么
--
-- 旧口径把首条 system 算作正文的第 0 条。而 `harness.context.split_turns` 只按
-- `role == "user"` 切轮，system 后面紧跟 user ⇒ **system 自成第一个 turn** ⇒
-- `cache._split_keep` 把它算进 `older` ⇒ 它被并进摘要 ⇒ 之后每次请求里只剩
-- `{"role":"system","content":"历史对话摘要：…"}`，**原始提示词永久消失**。
-- 失败形态是"长会话越用越不听话"，且**不报错**。
--
-- ## 为什么"整体减 1"是精确的，不是近似
--
-- 旧值 k 表示"`conversation[0:k]` 已折叠"，那个集合 = `{system} ∪ conversation[1:k]`。
-- 新值 k-1 表示"`body[0:k-1]` 已折叠" = `conversation[1:k]`，而 system 改由
-- `cache._pin_system` **恒常置顶**（永不折叠）。
-- ⇒ **被折叠的正文消息逐条不变**，只是 system 不再参与计数。
--
-- 只前进的语义不变（I2）：减 1 是同一个单调序列的平移，不产生回退。
UPDATE conversation_context SET summarized_upto = summarized_upto - 1 WHERE summarized_upto > 0;
"""),
    (23, "messages_kind", """
-- `messages.kind`：把「真实对话内容」与「系统记录」分开（2026-09-24，计划 §5.4b）。
--
-- ## 修的是什么
--
-- `routes_chat` 在收尾时把错误也写成一条 assistant 消息：
--     final_content = f"错误：{error_message}" if error_message else result["content"]
--     storage.insert_message(conversation_id, "assistant", final_content)
-- 而 `_model_history` 会把整段历史原样翻给模型 ⇒ **模型读到"自己说过"一句它从没说过的话**。
-- 后果不是报错，是模型据此继续推理（例如把一次网络错误当成自己"已经回答过了"），
-- 表现为"它怎么突然开始胡说"。刷新页面后那条错误还在气泡里，看起来完全正常。
--
-- ## 为什么用 `kind` 而不是新 role
--
-- `role` 是**发给模型的协议字段**（DeepSeek/OpenAI 只认 system/user/assistant/tool），
-- 加一个 `role='error'` 会让 `_model_history` 那条"未知角色丢掉"的兜底逻辑
-- 变成唯一防线 —— 而它一旦漏掉，**整个请求会被 400 掉**（不是少一条，是全废）。
-- `kind` 是**我们自己的**分类维度，与协议无关，风险面小得多。
--
-- ## 历史数据回填（启发式，说清楚它的边界）
--
-- 既有库里那批错误行只能靠前缀认出来。这是**启发式**：
-- 唯一的写入方是 `routes_chat` 那一行，所以 `role='assistant' AND content LIKE '错误：%'`
-- 在实践中是精确的；但如果模型**自己**答出以"错误："开头的一句真话，它会被误判成
-- 错误记录、从而不再进模型上下文。那个代价是**温和且自愈的**：消息仍在界面上，
-- 只是模型下一轮看不见它（相比"错误被当成模型的话"那种会把它带偏的后果，这个方向更安全）。
ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'message';
UPDATE messages SET kind = 'error'
 WHERE role = 'assistant' AND content LIKE '错误：%';
"""),
    (24, "agent_run_and_span", """
-- Agent 运行记录（计划 §6.1 / ADR-0021）：把**一次用户回合**记成一个实体，把里面每一步记成 span。
--
-- ## 修的是什么
--
-- 此前"一次用户回合"在数据里**不是一个实体**：`agent_audit` 记单次工具调用、
-- `deepseek_usage` 记一次回答的用量，两者只能靠**时间戳猜**关联。于是
-- "这一回合失败了"没有载体、"这次为什么慢"只能翻日志、跨步因果表达不了。
--
-- ## 三张表的分工（不要合并）
--
--   agent_audit        审计：谁对哪个对象做了什么。有合规含义，只增不改。
--   deepseek_usage     计费：token 与缓存命中。成本口径的唯一事实源。
--   agent_run/span     轨迹：一次回合里每一步的耗时/状态/用量。
--
-- ★ 三者是"同一个事件的三份投影"，**权威各自独立**（铁律 28）。所以 `attrs_json`
-- 只放与该步有关的最小内容 + 指向审计的 `auditId`，**不复制参数与账号**。
--
-- ## 为什么不与 agent_audit 合并
--
-- 粒度不同：一次回合里的多次模型调用**没有**审计记录（审计只覆盖工具），
-- 而工具调用在时间线上需要一层"它属于哪一步"的父子关系 —— 那是 span 的职责。
-- 合并会让审计表被写入大量非审计行，而它的合规含义依赖于"一行 = 一次有副作用的操作"。
CREATE TABLE IF NOT EXISTS agent_run (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    steps INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    summary_calls INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_run_conversation ON agent_run(conversation_id);
CREATE INDEX IF NOT EXISTS idx_agent_run_started_at ON agent_run(started_at);

CREATE TABLE IF NOT EXISTS agent_span (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    span_kind TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT NOT NULL DEFAULT '',
    attrs_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (run_id) REFERENCES agent_run(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_span_run ON agent_span(run_id, step);
"""),
]


# ------------------------------------------------------------------ 方言边界
# 交付形态是单机单人（Electron + 本机 sidecar），因此保留 SQLite。为了让将来
# 万一改判为「服务端多人部署」时是一次受控迁移而不是全库搜索替换，SQLite 特有
# 构造全部收拢在下面这一节，以及 MIGRATIONS 里标注的少数几处：
#   1. PRAGMA 语句            → _PRAGMAS
#   2. 逗号串包含匹配          → csv_like()
#   3. `?` 占位符              → 全库统一（psycopg/PyMySQL 为 %s，改 wrapper 即可）
#   4. `INTEGER PRIMARY KEY AUTOINCREMENT`（迁移 8 的 job_logs.id）
#      —— 迁移历史不可变，保留；新表不要再用它，改用 `TEXT PRIMARY KEY`（本项目
#      其余表均为 UUID 文本主键，本身就与方言无关）
_PRAGMAS = (
    "PRAGMA journal_mode = WAL;",
    "PRAGMA foreign_keys = ON;",
)


def csv_like(column: str, values: list[str]) -> tuple[str, list[Any]]:
    """逗号串列「包含任一值」的匹配片段（pool / tags / boxes 这类组合字段）。

    SQLite 写法为 `(',' || col || ',') LIKE '%值%'`，两端包夹逗号保证组合值命中。
    MySQL 默认 `||` 是逻辑或、PostgreSQL 需 `LIKE ANY(...)`，换库时只改本函数。
    """
    conditions = " OR ".join(f"(',' || {column} || ',') LIKE ?" for _ in values)
    return conditions, [f"%,{value}%" for value in values]


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = config.bootstrap_database()
            _conn = sqlite3.connect(str(path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            for pragma in _PRAGMAS:
                _conn.execute(pragma)
            _migrate(_conn)
        return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _sqlx_migrations (
            version BIGINT PRIMARY KEY,
            description TEXT NOT NULL,
            installed_on TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            success BIGINT NOT NULL DEFAULT 1,
            checksum BLOB,
            execution_time_in_millis BIGINT
        )"""
    )
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM _sqlx_migrations"
    ).fetchone()
    current = int(row["v"])
    for version, description, sql in MIGRATIONS:
        if version <= current:
            continue
        started = time.perf_counter()
        conn.executescript(sql)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        _record_migration(conn, version, description, sql, elapsed_ms)
        conn.commit()


def _record_migration(
    conn: sqlite3.Connection,
    version: int,
    description: str,
    sql: str,
    elapsed_ms: int,
) -> None:
    """写入迁移记录，按实际表结构适配列差异。

    新库由上方 CREATE TABLE 建表（execution_time_in_millis 可空）；
    旧 Tauri 库的表由 sqlx 变体创建（checksum BLOB NOT NULL、execution_time BIGINT NOT NULL）。
    动态读取 PRAGMA table_info，对存在的已知列赋值，对未知 NOT NULL 无默认值列填充中性值，
    避免接管旧库时逐列触发 IntegrityError。
    """
    candidates: dict[str, Any] = {
        "version": version,
        "description": description,
        "success": 1,
        # checksum 按 sqlx 语义取迁移 SQL 的 SHA-384 摘要
        "checksum": hashlib.sha384(sql.encode("utf-8")).digest(),
        "execution_time": elapsed_ms,
        "execution_time_in_millis": elapsed_ms,
    }
    names: list[str] = []
    params: list[Any] = []
    for row in conn.execute("PRAGMA table_info(_sqlx_migrations)"):
        _cid, name, ctype, notnull, dflt, _pk = tuple(row)
        if name in candidates:
            names.append(name)
            params.append(candidates[name])
        elif notnull and dflt is None:
            names.append(name)
            params.append(0 if (ctype or "").upper() in {"BIGINT", "INTEGER", "INT", "BOOLEAN"} else "")
    conn.execute(
        f"INSERT INTO _sqlx_migrations ({', '.join(names)}) "
        f"VALUES ({', '.join('?' for _ in names)})",
        params,
    )


def query(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with _lock:
        conn = connect()
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    """执行写语句并提交，返回受影响行数。"""
    with _lock:
        conn = connect()
        cursor = conn.execute(sql, tuple(params))
        conn.commit()
        return cursor.rowcount
