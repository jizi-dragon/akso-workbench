# 文档目录

`docs/` 分四层，按「现在该信哪个」排序：

| 目录 | 放什么 | 能不能当规范用 |
|---|---|---|
| `knowledge/` | 现行规范、操作手册、排查指南 | ✅ 以这里的为准 |
| `reference/` | 外部资料、平台接口清单、只读样本 | ⚠️ 只当资料查，不当流程规范 |
| `archive/` | 带日期的实测台账、历史证据 | ❌ 只证明「当时测过」 |
| 工作区数据目录 | `<工作区>/akso-workbench-data/` 下的 `reviews/`、`analysis/` | ❌ 不入库，只做交付 |

## knowledge/：现行规范

| 文件 | 解决什么问题 |
|---|---|
| `基础API使用指南.md` | 平台接口怎么调、请求/响应长什么样；工具与 agent 的能力目录都从这来 |
| `标准配置思路规范.md` | 拿到一个配置目标后，蓝图怎么拆（对象/字段/布局/生命周期/工作流/菜单） |
| `标准配置使用规范.md` | 已沉淀的标准方案（recipes），照抄即可的写法 |
| `系统常用映射表.md` | 状态码、枚举值、行为类型等对照表 |
| `标准对象分析流程.md` | 对象分析怎么读字段/生命周期/动作/工作流，产物怎么写 |
| `对象分析经验与模板.md` | 对象分析实战口径、矩阵/菜单/业务数据检查、报告与审核文档模板 |
| `用户手册.md` | 从使用者角度介绍 akso-workbench 各功能与操作步骤 |
| `agent调用时序.md` | agent 调插件四条工具的完整时序、错误对照、日志；改调用链必须先改这篇 |
| `日志与排查.md` | 运行日志有哪些事件、怎么让用户下载、常见问题怎么 grep |
| `API-DISCOVERY.md` | 用 `capture/` 监听真实浏览器流量，扩展能力目录 |
| `EGMP-CONFIG-BASICS.md` | 配置域入门（v0 草稿；与上面规范冲突时以上面为准） |
| `模板参数配置.md` | Word/Aspose 运行报告（页面模板）占位符语法 |
| `运行报告配置.md` | 用户表单 → 默认布局 → 运行报告的配置逻辑、变更样例映射与流程 |

## reference/：资料与样本

- `Akso平台内部API接口清单.md` / `.xlsx`：平台接口总表（能力目录的原始依据）。
- `dsh-*.md`、`cordis-README.md`：DSH / Cordis 的插件机制说明。
- `便笺本.xml`、`便笺本模块展示.drawio/.png`：drawio 图形库的唯一来源。
- `lifecycle-action-types.md`：221 项 behaviorType 码表。
- `homework2__c-lifecycle.md`：一份完整的对象分析样例报告。

## archive/：历史记录

- `CAPABILITY-VERIFICATION-2026-11-09.md`：能力目录逐条实测（44 条 → 47 条）。
- `CAPABILITY-VERIFICATION-2026-11-11.md`：生命周期三能力实测。

这两份只用于追溯「某条接口当年怎么验的」；当前能力状态看 `plugin/src/config/capabilities.ts`
与 `plugin/src/shared/approved-tools.ts`。

## 维护约定

1. 新增现行规范进 `knowledge/`，并在上表加一行；带日期的证据进 `archive/`。
2. 外部拷贝的资料进 `reference/`，不要在里面改写流程结论。
3. 审批文档与对象分析产物统一放 `<工作区>/akso-workbench-data/`（`reviews/`、`analysis/`），
   不进 git；不放到仓库内、也不放 DSH profile。
4. 改工具、审批、会话、日志时，同步改 `knowledge/agent调用时序.md` 的图与事件表。
