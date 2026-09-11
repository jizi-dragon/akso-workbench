# ADR-0010：Monitor 危险操作永不回放，解读须有实证来源

- **状态**：已采纳（阶段 3D）
- **相关**：[ADR-0002](0002-browser-allocation-policy.md)、[ADR-0006](0006-browser-pool-single-thread.md)、
  [API.md](../API.md) §13、[SCHEMA.md](../SCHEMA.md) §4

## 背景

Monitor（`services/egmp/monitor/`）**录制**用户在平台上的操作，产出三样东西：

1. **录制日志**（`monitor-log.json`）——请求/响应/DOM 动作；
2. **解读**（`interpreter.py`）——把连续请求切成语义段，推断它在做什么；
3. **复现计划**（`reproduce_plan`）——把段映射成可重放的步骤，`API_MAP` 对位到 `egmp.writers` 的创建器。

它内化自 `akso-auto` 的 Puppeteer 版 Monitor。录制数据来自**真实生产/验证环境**，
里面必然包含真实的业务对象编码、字段值、以及**破坏性操作**（删除对象、批量改状态、
触发审批流、发通知……）。

危险点是显然的：**「录制 → 自动回放」在语义上等价于「在生产上重放用户当时的全部操作」**。
上游已确立安全边界，本项目内化时必须原样继承，不能因为「我们已经有了 writers 层」就放宽。

## 决策

### 1. 危险操作永不回放（硬边界）

`interpreter.py` 对每个解读出的段做分类，**`DANGEROUS` 类永不进入可执行步骤**：
复现计划里保留它的位置与说明，但标记为不可执行，必须由人重新决策。

### 2. `API_MAP` 必须对位到 `egmp/writers` 的实证常量

复现计划把录到的请求端点映射到 writers 的创建器/端点常量
（`egmp/writers/endpoints.py`，自原仓库只读提取并逐条标注来源）。
**不允许凭观察猜端点**：映射不上就不映射（宁可让人工处理），也不要写一个「看起来对」的端点。

### 3. 复现须逐步授权

复现不提供「一键全量回放」。执行由调用方按 `reproduce_plan` 的 `steps` **逐步确认**后调用对应 writers 函数。
这与配置工厂的「环境强制确认」原则同源（`confirmed=true` 才发起，见 [API.md](../API.md) §7）。

### 4. 三级降噪（录制侧的正确性约束，不是优化项）

录制引擎必须把事件分成三级，因为它跑在 [ADR-0006](0006-browser-pool-single-thread.md) 的单线程约束下：

| 级别 | 含义 |
|---|---|
| `dropped` | 与本平台无关的流量（静态资源、埋点、第三方） |
| `trimmed` | 相关但只保留摘要（大响应体只留形状与关键字段） |
| `full` | 完整保留（平台的业务 API） |

**关键约束**：事件回调内**禁止**任何连接调用（`response.text()` / `page.evaluate` 重入 = worker 死锁）。
所以事件只入队，在 worker 的作业间隙由 `monitor.drain()` 统一处理——
这正是三级降噪必须存在的原因：**不能就地读响应体**。

真机验收基线：248 请求捕获 → `dropped 238 / trimmed 2 / full 8`。

### 5. 反馈闭环只写不自动改

`record_feedback` / `append_pitfall_record` / `append_error_record` / `mark_iterated`
把人工解读时发现的坑与错误记成记录（供后续改进解读规则）。
**它们只落盘，不自动修改 `API_MAP` 或复现计划**——规则的改动必须是显式的人工动作。

## 取舍

### 换来什么

- Monitor 可以放心在真环境录制，不会因为「某人点了一下自动回放」而造成生产事故。
- 解读的每一条映射都可追溯到 writers 里的实证常量，而不是模型的猜测。
- 录制本身对平台负载可控（三级降噪把 96% 的流量丢弃）。

### 代价（已接受）

| 代价 | 说明 |
|---|---|
| **复现的自动化程度低** | 每个计划都要人工逐步确认；不能用它做「无人值守的批量重放」 |
| 解读质量依赖人工核对 | `mark_iterated` / `record_feedback` 的价值取决于有人真的去标记 |
| 录制与复现是两套载体 | 录制走 Playwright（Python），最终执行走 httpx writers（Python）——中间靠 `API_MAP` 对齐，映射层是维护面 |
| 长驻录制的资源占用 | 需要保持一个托管会话在线；`monitor_start` 因此可能触发开户（`_submit(timeout=90)`） |

### 明确排除的方案

- **「录制即脚本」**（把请求序列直接重放）：无法处理令牌/时序/条件分支，且必然包含危险操作。
- **让 LLM 直接生成复现计划**：无法保证端点真实性。LLM 只在 `understand` 的职责摘要
  （`_llm_summary_fn`，可选、且失败即回落确定性标注）与蓝图生成里出现——**不进入 Monitor 的映射判定**。

## 后果

- **`DANGEROUS` 分类的正确性是安全关键路径**，但**目前没有自动化测试守住它**：
  `tests/test_native.py` 覆盖了 `monitor_classify_and_query` 与 `monitor_interpret_segment`，
  但它们验的是分类/解读的基本行为，**不是「危险操作不会被误放进可执行步骤」这一断言**。
  这是已知的测试缺口（见 [AGENT.md](../../AGENT.md) §4）。
- **监听的生命周期与托管会话耦合**：录制产物落 `runtime/monitor/<account_id8>-<stamp>/`；
  但关闭会话**不会自动停监听**（见 [API.md](../API.md) §13 与 [AGENT.md](../../AGENT.md) §4）。
- **另有一套同名但无关的东西**：上游扩展曾有 `page-monitor.ts`（Page Monitor，最近页面）。
  它已于 0.2.22 整块撤销，**与本文档的 Monitor 无关**——即使名字里都有 Monitor。
  `services/browser_pool.monitor_start/stop` + `runtime/monitor/` 才是本文档指的那套。
