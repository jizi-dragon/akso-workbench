# ADR-0021：Agent 运行记录（`agent_run` / `agent_span`）与 **OTel 导出层**

- **状态**：已采纳
- **日期**：2026-09-24
- **决策人**：维护者（依据 `CORE-AGENT-PLAN.md` §6.1 的核查结论）
- **影响面**：DB **迁移 24**（`agent_run` / `agent_span`）；`harness/trace.py`（新增）、
  `harness/loop.py`（埋点）、`api/routes_agent.py`（selfcheck 暴露）；
  新增 `tools/export_otel.py`（**离线导出，零运行时依赖**）
- **取代**：不取代任何既有 ADR。**收紧了 [ADR-0004](0004-extension-as-execution-plane.md)
  之外的一条边界** —— 见 §5「诚实边界」第 2 条（本决策**不**声称属性名已核实）

---

## 1. 背景：为什么现在需要这个决策

### 1.1 「一次用户回合」在数据里**不是一个实体**

三条各自独立的证据：

1. `agent_audit` 记的是**单次工具调用**，`deepseek_usage` 记的是**一次回答的用量** ——
   两者只能靠**时间戳猜**关联。于是"这一回合失败了"这件事**没有任何载体**：
   §5.4a 修好之前异常出口连用量都不落，修好之后用量有了，但"它失败了"仍然只能
   从"有一条 error 事件"这种**不存在于库里**的东西上推断。
2. **"这次为什么慢"只能翻日志** —— 而日志是给人看的文本，不是可聚合的数据。
   一个真实的场景："最近几次洞察任务越来越慢" —— 在现有数据上**无法回答**
   是模型慢了、还是某个工具慢了、还是等待用户确认的时间变长了。
3. **跨步的因果表达不了** —— "第 3 步的超时导致第 5 步重试"在审计表里是两行无关的记录。

### 1.2 OTel GenAI 语义约定是行业共识，而本仓**零**

`docs/OPENSOURCE-LANDSCAPE.md` 的核查结论：**"行业标准是 OTel GenAI 语义约定
+ Langfuse/Phoenix/LangSmith 三选一"**；而本仓现状是 **"零"**
（`grep` 全仓无 `opentelemetry` / `langfuse` / `trace_id` / `span(`）。

`CORE-ARCHITECTURE.md` §7.5 早就把它列为缺口，并给了一个关键约束：
**"不动现有表（它是审计事实源，且有合规含义），额外导出一层"** —— 本 ADR 采纳它。

---

## 2. 决策

### D-1 新增两张表，**不合并**进审计

```sql
agent_run(id, conversation_id, started_at, ended_at, status, error, steps, model,
          prompt_tokens, cache_hit_tokens, cache_miss_tokens, completion_tokens,
          summary_calls, latency_ms)
agent_span(id, run_id, step, span_kind, name, started_at, duration_ms, status, error,
           attrs_json)   -- span_kind: model | tool | sandbox | confirm
```

**三张表的分工（不要合并）**：

| 表 | 它是什么 | 权威性 |
|---|---|---|
| `agent_audit` | **审计**：谁对哪个对象做了什么 | 有合规含义，只增不改 |
| `deepseek_usage` | **计费**：token 与缓存命中 | 成本口径的唯一事实源 |
| `agent_run` / `agent_span` | **轨迹**：一次回合里每一步的耗时/状态/用量 | 只为"把一次回合当成整体看" |

★ 三者是"同一个事件的三份投影"，**权威各自独立**（铁律 28：两处都写全量 =
第二个会漂的事实源）。所以 `attrs_json` **只放指针**（`auditId` / 插件 / 风险 / 状态），
**不复制参数与账号** —— 判据见 `tests/test_agent_spans.py::test_tool_span_does_not_copy_the_arguments`。

**为什么不与 `agent_audit` 合并**：粒度不同。一次回合里的**多次模型调用没有审计记录**
（审计只覆盖工具），而工具调用在时间线上需要"它属于哪一步"的父子关系 —— 那是 span 的职责。
合并会让审计表被写入大量非审计行，而它的合规含义依赖于"**一行 = 一次有副作用的操作**"。

### D-2 写入点只有两处，且**都在循环层**（对计划的一处偏离）

计划写的是"`loop._one_model_turn` 前后、`ToolHub.execute` 前后"。实际实现**两处都在
`harness/loop.py`**：

- 模型调用：包一层 `_SpanClient`（含摘要那次 `chat()` —— 它也是真实开销）；
- 工具调用：在 `await registry.execute(...)` 之后记，耗时取
  `ToolInvocation.duration_ms`（`ToolHub` 自己测的，**不含**用户等待）。

**偏离的理由**：`plugin_api.py` **不许 import `db`**（AGENTS.md 规则 7 / I4 的同一族），
所以 `ToolHub` 要写 span 就必须再走一次注入（像 `set_audit_writer` 那样）。
而 `ToolHub.execute` 目前的唯一调用方就是循环，注入带来的收益是零、成本是一个新的
注入面与一处启动期装配。**等真的出现第二个调用方时再加注入**（§7 的触发器）。

### D-3 **任何出口都收尾**，且区分「失败」与「被中止」

`RunRecorder` 由 `_accounted`（既有的用量收尾上下文管理器）在 `finally` 里收 ——
与 §5.4a 同一条理由：循环有多个出口（final / error / 预算耗尽 / 上下文超限 /
抛异常 / 消费方断开），收尾交给调用方记得就会重演那两个缺陷。

★ **`failed` 与 `aborted` 刻意分开**：前者是"它真的坏了"，后者是
"用户关掉了页面"（`GeneratorExit`）。两者对用户的含义完全不同 ——
与 §5.4c 的「（已中止）」vs「错误：…」是同一条区分。

### D-4 OTel 对齐靠**导出层**，不靠改表；**只做 OTLP**

`tools/export_otel.py` 把三张表映射到 `gen_ai.*`，输出 OTLP/JSON。
**零运行时依赖** —— 不为一个导出功能把 otel-sdk 拖进 sidecar（那是几 MB 与一棵依赖树）。

**不做 Langfuse 的私有导入格式**：OTLP/JSON 是**标准**（有公开 wire format），
而 Langfuse / Phoenix 的**导入格式我没有核实过** —— 不凭印象造第二份 schema。
接它们应当走各自的 OTLP 入口。

---

## 3. 本期做与不做

**做**：

- 迁移 24、`harness/trace.py`、循环埋点、`test_agent_spans.py`（**15 条**）、
  `tools/export_otel.py`（`--dry-run` / `--out` / `--list-attributes`）。
- 反证：回退 `loop.py` + `db.py` 后 **14/15 红**（`sqlite3.OperationalError` 表不存在、
  `AssertionError: 写失败没有被计数`）。

**不做（明确）**：

- ~~运行时间线视图（计划 §6.2）~~ —— 后端数据已就位，前端面板未做。
- ~~`agent_run` 的 retention / 清理策略~~ —— 与 `deepseek_usage` 同样的处境，
  目前靠 `CONFIG.md` 的手工回收（见其 §6）。
- ~~把 `sandbox` / `confirm` 两类 span 真正填上~~ —— 它们的权威表分别是
  `sandbox_run` 与 `pending_action`，`SPAN_SANDBOX` / `SPAN_CONFIRM` 只留了常量。
- ~~Langfuse 专有格式~~（见 D-4）。

---

## 4. 后果

**好处**：

- 失败回合**第一次有了载体**（`status='failed'` + `error`），而"卡在 `running`"
  与"跑完了"在界面上不再长得一样。
- "这次为什么慢"变成一个 **SQL 查询**：按 `span_kind` 分组求和即可。
- 跨步因果可表达：`step` 在一次 run 内单调递增，比时间戳可靠
  （同一毫秒内的两步在时间戳上分不出先后）。

**代价**：

- 每个工具调用多一次 `INSERT`（一次 40 步的回合 ≈ 40 行）。
  SQLite WAL 下可接受，但**不是零成本** —— 若将来回合数暴涨，这里第一个要加采样。
- `attrs_json` 是一个**没有 schema 的列**。它今天是"该步的最小上下文"，
  明天可能变成什么都能塞的垃圾桶。约束靠代码评审与那条"不许复制参数"的断言。

**风险**：

- 轨迹写库失败会**安静地缺步**。缓解：`_write` 吞异常但**计数**，
  由 `/api/agent/selfcheck` 的 `traceWriteFailures` 暴露 ——
  判据见 `test_trace_write_failure_is_counted_not_swallowed_silently`。

---

## 5. 诚实边界

1. **本决策不保证"时间线完整"**：消费方在流中途断开时，`_SpanClient` 会记一条
   `status="aborted"` 的模型 span，但**工具批次里正在执行的那一步**不会被补记。
   完整的中断语义要等 §5.6（可中止 stop）。
2. ★ **OTel 属性名没有逐条核实**。写这份映射时（2026-09-24）网络不可达
   （`github.com` / `raw.githubusercontent.com` 均 DNS 失败）。本仓此前也明记着
   这条边界（`CORE-ARCHITECTURE.md` §7.5："具体属性名未逐条核"）。
   **处置**：把映射集中成一张表（`export_otel.OTEL_ATTRS`），每条带核实状态，
   `--list-attributes` 打出来逐条核 —— 于是"未核实"从一句空泛提醒变成
   **一条可执行的检查**。**不要**因为"看起来对"就把 `verify` 改成 `ok`：
   那会让"这个属性名对不对"从**已知的未知**变成**未知的未知**。
3. **`duration_ms` 的口径不统一**：模型 span 是墙钟（含网络），工具 span 是
   `ToolInvocation.duration_ms`（不含用户等待）。这是**刻意的** ——
   把用户思考时间算进"工具慢"会让那个问题的答案完全失真；
   但它意味着**两条 span 的 `duration_ms` 不能直接相加**来解释总时长。

---

## 6. 外部参照

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| [OpenTelemetry GenAI 语义约定](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | 为 LLM/agent/tool 调用定义 `gen_ai.*` 属性与 span 结构 | **一致（方向）／未核实（细节）** | 方向采纳：span 分层、属性化、OTLP 输出。**具体属性名未逐条核** —— 见 §5 第 2 条。★ 这一栏刻意把"方向"与"细节"分开写：前者可信，后者**此刻不可信** |
| [OTLP/JSON 协议](https://opentelemetry.io/docs/specs/otlp/) | 公开的 wire format：`resourceSpans` → `scopeSpans` → `spans`，纳秒时间戳为**字符串**，属性值显式标注类型 | 一致 | 直接跟随。判据落在**形状**上（`test_otel_export_produces_a_structurally_valid_payload`），因为形状错了通用工具就读不进去 |
| 本仓既有的 `agent_audit` / `sandbox_run` / `skill_audit` | 自建审计表，可 SQL 查询、零依赖 | **分歧（部分）** | 我们**保留**它们并**额外**加一层导出，而不是改用 OTel SDK 做事实源。理由：审计有**合规含义**（一行 = 一次有副作用的操作），而这个语义 OTel 的 span 模型不承载 |
| Langfuse / Phoenix / LangSmith | 各自有导入格式，也各自宣称支持 OTLP | **分歧** | 只做 OTLP。它们的**导入格式我没核实过**，不凭印象造第二份 schema —— 那会是一条没人敢质疑的假共识 |

**由本次对照得出的结论**：

- **可以简化的**：span 的分层（agent → model / tool）与 OTLP 的输出形状 —— 行业已统一，直接采用，不必自己发明。
- **必须自己定的**：**"审计"与"轨迹"要不要分家**。行业（OTel）只有 span，
  而我们有合规驱动的审计表。本案的答案是"分家 + 导出层桥接"，
  这个取舍取决于**我们的合规语境**，没有可抄的答案。
- **未核实**：`gen_ai.*` 的**具体属性名**（11 个，全部标着 `verify`）。
  这是本 ADR 最重要的一条待办，而它现在**有一条命令可以推进**。

---

## 7. 重新评估的触发器

| 触发器 | 到时再考虑 |
|---|---|
| OTel GenAI 属性名逐条核实完（`--list-attributes` 归零） | 把 `verify` 全改 `ok`，并在本 ADR §6 补核对日期 —— 届时 `test_otel_export_does_not_fabricate_attribute_confidence` 会红，提醒写在这里 |
| 出现 `ToolHub.execute` 的**第二个调用方**（例如界面直接触发工具） | D-2 的偏离失效：那时该加 `set_span_writer` 注入，而不是让第二个调用方没有 span |
| 回合数暴涨 / `agent_span` 体积成为问题 | 加采样或滚动清理；**优先砍 `attrs_json`**，`step`/`duration_ms`/`status` 是骨架 |
| §5.6（可中止 stop）落地 | §5 第 1 条边界消失：中断时也能补记"正在执行的那一步" |
| 换模型/供应商导致 `gen_ai.provider.name` 语义变化 | 导出层的 provider 映射要重核 |
| 前端时间线视图（§6.2）落地 | `recent_runs` / `run_spans` 的返回形状会变成**接口契约**，改它们要按"前端字段是 Python→JS 的无类型接口"处理 |
