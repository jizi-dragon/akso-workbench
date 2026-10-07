# ADR-0005：四个原项目能力全部原生化，运行时零依赖原仓库与 Node

> **合订说明（2026-10-07）**：本文是**主题合订本**。仓库在 2026-10-07 把 22 篇 ADR
> 按主题合并成 8 篇（用户授权：「快速合并历史的 ADR」「未来的开发存在变更是正常的」）。
> 本组只吸收了 **原 ADR-0005** 一篇，内容除链接与标注同步外**未改写**。
> ★ **原有的「补记（这段是后补的）」标记原样保留** —— 让"决策当时的认知"与"后来的认知"都能看见。
> 合并口径见 [AGENTS.md](../../AGENTS.md) §5。

- **状态**：已采纳（阶段 3 完成；`adapters/*.json` 降级为只读参考存档）
- **相关**：[ADR-0003](0003-electron-desktop-shell.md)、[ADR-0007](0007-credentials-sandbox-and-trust.md)、
  [SCHEMA.md](../SCHEMA.md)、[CONFIG.md](../CONFIG.md) §2、§6

## 1. 背景

本项目的起点是**四个既有项目的优点融合**（用户定稿的方向）：

| 原项目 | 能力 | 原始形态 |
|---|---|---|
| `akso-cc` | 平台配置洞察（登录 / 盘点 / 理解 / 蜘蛛爬取 / 报告） | Node CLI（`dist/main.js` + 子命令） |
| `mogul_simulator` | 知识工作台（对话 / 设置 / 检索） | Python（本项目 fork 基线） |
| `akso-auto` | 配置自动化（蓝图校验 / 创建 / 编排 / Monitor） | Node CLI（`scripts/main.js` + 子命令） |
| `quick-login` | 多账号统一库 + 自动登录节奏门控 | Chrome MV3 扩展（TS） |

融合期（阶段 1–2）的形态是**子进程聚合**：`workbench/services/proc.py` 封装 Node 子进程，
Workbench 侧传 `--url/--user/--pass` 内联凭据调两个原仓库的 CLI。这带来三个硬问题：

1. **终端用户安装包必须带 Node**（或用 PyInstaller 夹带一个 Node 运行时）。
2. **凭据要经命令行参数传递**（出现在进程列表里），与「凭据不落明文」的纪律冲突。
3. **产物与错误口径全在对方手里**：一次 CLI 参数变更就会静默改变行为，而对方仓库不在本仓库的
   测试范围内（`adapters/*.json` 只是路径声明，没有任何拉齐机制）。

同时四个原项目被要求**冻结不动**：新项目对它们只做只读引用与知识迁移，绝不修改原文件。

## 2. 决策

**把四个项目的能力全部内化到本仓库（Python + 扩展），运行时不再调用任何原仓库、不再依赖 Node。**

落地形态：

| 原能力 | 内化位置 | 载体 |
|---|---|---|
| akso-cc 读路径 | `workbench/services/egmp/insight/`（11 模块） | Python + httpx |
| akso-auto 写路径 | `workbench/services/egmp/writers/`（9 模块） | Python + pydantic |
| akso-auto 编排/复杂度/蓝图生成 | `egmp/orchestrate.py` / `complexity.py` / `generate.py` | Python（DeepSeek 融合） |
| akso-auto Monitor | `workbench/services/egmp/monitor/` | Playwright（载体从 Puppeteer 换成 Playwright-Python） |
| quick-login 账号库 + 节奏门控 | `services/accounts.py` + `services/autologin.py` + 扩展 | Python + MAIN-world JS |
| 四项目共用的 HTTP 内核 | `egmp/client.py`（信封 `code==0` / 分页 / 55min token 缓存） | httpx |

`adapters/akso-cc.json` / `adapters/akso-auto.json` 保留但**降级为只读参考存档**：
`mode: "native-python-internalized"`、`frozen: true`、`deprecation` 字段写明原生化事实。
模块体检（`/api/modules`）对它们只做「Python 依赖 + 原仓库是否可达（信息项，缺失不影响功能）」。

**内化时的纪律：实证资产不得丢失。** 原仓库里用大量探测得出的常量与口径被逐一搬进代码并注明来源，
例如：eGMP 登录端点与响应信封形状、分页参数（`pageIndex` 从 1、`pageSize` 默认 1000、`data.hasNext` 续页、
`datas`/`items` 兼容）、Token TTL 55 分钟、进入动作结构化条件**无公开读接口**
（`UNRESOLVED_ACTION_READ_PROBES`，6 轮 48+ 路径探测全部 404/405/500）、写路径端点常量
（`egmp/writers/endpoints.py`，自原仓库只读提取）、自动登录五重门的全部时长参数。

## 3. 取舍

### 换来什么

- **终端用户零 Node 依赖**：安装包 = Electron 运行时 + PyInstaller sidecar + chromium + 扩展。
- **凭据不再出现在命令行**：凭证从统一账号库内联注入（Fernet 解密仅在内存），见
  [ADR-0007](0007-credentials-sandbox-and-trust.md)。
- **口径可控可测**：`tests/test_native.py`（12 项）用 `FakeEgmpClient` 在内存平台离线验收全链路，
  不依赖任何外部仓库；改行为必须同步改测试。
- **单一语言栈**：服务端全部 Python，异常/日志/超时口径统一。

### 代价（已接受）

| 代价 | 表现 |
|---|---|
| 一次性重写工作量巨大 | `services/egmp/` 约 3.5k 行，含 11 个 insight 模块与 9 个 writers 模块 |
| 与原项目存在**行为漂移风险** | 原仓库继续演进时本项目不会自动跟上。缓解：`adapters/*.json` 作为对照存档 + 本 ADR 记录口径来源 |
| 遗留对照代码未清理 | `services/proc.py`（Node 子进程封装）与 `services/modules.py` 的部分描述仍是遗留口径（如 `proc.py` 的文档字符串指向已删除的 `docs/模块契约.md`）——见 `../CODEBASE_OVERVIEW.md` §九 的风险台账 |
| `tests/test_proc.py`（7 项）仍在测 | 它测的是遗留封装本身（Node 探测/超时/流式日志），**不代表运行时链路**——不要误认为「项目仍依赖 Node」 |

### 明确排除的方案

- **把原仓库作为 git submodule 引入**：仍需 Node 运行时，且违反「原仓库只读」纪律（submodule 会诱使改原文件）。
- **打包一个便携 Node 进安装包**：包体再增，且要维护 Node 版本与两个仓库的依赖树一致性。
- **保留子进程但只传文件路径不传凭据**：仍需 Node，且引入临时凭据文件的落盘面。

## 4. 后果

- **运行时零依赖**成为维护红线：任何「顺手调一下 node 脚本」的改动都不接受。
  新增能力一律落在 `services/egmp/` 或扩展里。
- **原仓库只读**是硬纪律：`adapters/*.json` 指向的目录**任何情况下不修改**
  （`services/accounts.py` 的「原项目 env 导入」也只读扫描）。
- `docs/模块契约.md`、`docs/迁移台账.md` 等融合期文档已删除；代码里仍有指向它们的引用，
  属于待清理的文档债（见 `../CODEBASE_OVERVIEW.md` §九 的风险台账）。

## 5. 诚实边界

> 本组只有一篇 ADR。原 ADR-0005 的诚实边界主要以「代价（已接受）」与「补记」两种形态存在，
> 两者**都原样保留**（补记在 §6）。以下三条是把它们里**最容易被误读的部分**写死，
> 句子取自原文，**未新增结论**。

- **`tests/test_proc.py`（7 项）仍在测遗留封装本身** ——
  它验的是 Node 探测/超时/流式日志，**不代表运行时链路**。
  ★ **不要因为它绿着就认为「项目仍依赖 Node」**，也不要因为它存在就认为"原生化没做完"。
- **`services/proc.py` 与 `services/modules.py` 的遗留口径还在**：`proc.py` 的文档字符串
  仍指向已删除的 `docs/模块契约.md`。这是**文档债，不是功能债**——但它会让读代码的人
  以为存在一个不存在的契约文件。
- **"原仓库只读"靠纪律，不靠机检**：`adapters/*.json` 指向的目录**没有**任何 assert/门禁守着
  （[AGENTS.md](../../AGENTS.md) §6 明确写了"不要给缺失的上游仓库目录加 `assert exists()` 门禁"——
  那会让没克隆原仓库的机器变红，而功能完全正常）。⇒ 这条红线的**唯一**保障是"没人去写它"。

## 6. 补记（**这段是后补的**，2026-09-22）

> 按本仓纪律，ADR 只增不改；下面记的是「决策当时的认知」之后发生的变化。

**四项目内化的决定不变，但 akso-auto 内化出来的「写路径」已整体移除。**

| 本 ADR 里的行 | 现状 |
|---|---|
| akso-auto 写路径 → workbench/services/egmp/writers/（9 模块） | **已删除**（2026-09-22，配置工厂整体移除） |
| akso-auto 编排 → egmp/orchestrate.py | **已删除** |
| akso-auto 蓝图生成 → complexity.py / generate.py | 已于 2026-09-20 删除（第二个「需求→蓝图」循环） |
| akso-auto 监听 → services/egmp/monitor/ | **保留**（录制/查询/解读仍在产品里；复现执行随写路径移除） |
| `adapters/akso-auto.json` | **已删除**（模块 id `factory` 随之消失） |

**「运行时零依赖」「原仓库只读」两条红线不受影响，反而更强**：现在这个仓库连
「写平台数据」的能力都没有了——它对本仓引用的平台**只读**。
内化出来的平台知识没有丢，转成了两份不依赖代码的文档：
`../PLATFORM-OPERATIONS-LEARNED.md`（**该文件已于 2026-09-29 删除**） 与
`../FACTORY-PLATFORM-COGNITION.md`（**该文件已于 2026-09-29 删除**）。

> ★ **这份补记本身是一条诚实边界**：§2 那张"落地形态"表里的**四行已经不在仓库里了**，
> 而表还在（保留划线形态而非删除是刻意的）。只读 §2 而不读本节的人会得出错误结论 ——
> 这正是补记存在的理由。

## 7. 外部参照

> ⚠️ **本栏为后补**（2026-10-07 合并时补写；正文其余部分保持决策当时的样子，不改写）。
> 原 ADR-0005 写于 `0000-template.md` 建立之前，**没有这一栏** —— 而本模板明确写着
> **「别省掉那一栏」**。补写的边界：能给出处的写，给不出的写"未核实"，**不凭印象造共识**。

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| **pywebview / Electron 应用普遍把能力留在子进程**（本仓阶段 1–2 的形态） | 宿主 spawn 一个 Node/Python 子进程，CLI 传参调用 | **★ 分歧** | 我们要的三件事它一件都给不了：**安装包不带 Node**、**凭据不进命令行**、**口径可测**。⇒ 改成"能力原生化 + 只是同语言模块间调用" |
| [Electron 官方 · 把 Node 当运行时](https://www.electronjs.org/docs/latest/tutorial/process-model) | Electron 自带 Node 运行时，壳侧可以用 | **一致（但只在开发态）** | ★ 我们的**终端用户**装的是安装包，不是 Electron 开发环境。§1 的三条硬问题里第一条就是"用户机器上没有 Node" |
| [ripgrep](https://github.com/BurntSushi/ripgrep) / [uv](https://github.com/astral-sh/uv) 一类的"把外部工具原生重写" | 把原来 spawn 外部工具的能力**用宿主语言重写**，换来零外部依赖与统一错误口径 | **一致** | ★ 这正是本节决策的形状：`services/egmp/` 就是把两个 Node CLI 的读路径/写路径用 Python 重写。**收益与代价都是同一套**（重写工作量大 ↔ 零运行时依赖 + 口径统一） |
| 把上游仓库作为 **git submodule / vendor 目录** | 保留上游代码，随上游更新 | **★ 分歧** | ① 仍需 Node 运行时；② submodule 会**诱使改原文件**，与"原仓库只读"纪律冲突（§3「明确排除的方案」第一条） |
| MCP 生态里"stdio 传输靠本地进程"的取向 | 把"能起一个本地进程"当作可信边界 | **正交** | 这里讨论的是**运行时依赖**，不是**信任边界**。我们的信任边界问题（本机进程能不能读凭据）在 [ADR-0002](0002-browser-allocation-policy.md) §2.4 与 [ADR-0007](0007-credentials-sandbox-and-trust.md) |

**由本次对照得出的结论**：

- **可以简化的**：把外部 CLI 的能力用宿主语言重写 —— 这条路上有大量先例（ripgrep / uv 一类），
  **"值不值得"不需要论证**，需要论证的只是"这个项目值不值得"。
- **必须自己定的**：① **哪些原仓库资产必须逐条搬进代码并注明来源**（§2 那条"实证资产不得丢失"——
  分页参数、Token TTL、探测失败清单这些**是探测出来的事实，不是文档**）；
  ② **`adapters/*.json` 降级成什么形态**（只读存档 + `frozen: true` + `deprecation` 字段）。
- **未核实**：其他"多来源项目融合"的工程在"原仓库冻结后如何防止行为漂移"上的做法，本次**没有取证**。
  §3 那条"行为漂移风险"的缓解手段（对照存档 + ADR 记口径来源）是本仓自己定的 ——
  而它**确实只是一个缓解**，不是防线。

## 8. 重新评估的触发器

> 原 ADR-0005 写于模板建立之前，**没有触发器栏**。以下为本次合并时按正文与代价归拢
> （触发器内容取自原文的"代价"与"后果"两节，未新增判断）。

| 触发器 | 到时再考虑 |
|---|---|
| 原仓库（akso-cc 等）发生**会改变行为**的演进 | §3「行为漂移风险」：本仓不会自动跟上，需要人工对照 `adapters/*.json` 与本 ADR 记录的口径来源 |
| 需要**写平台数据**的能力回来（产品方向变化） | §6 补记：那等于要重建 `egmp/writers/` 整条写路径 —— 那是**一次新的决策**，不是"把删掉的代码找回来"（写路径当时伴随配置工厂一并移除） |
| 出现"顺手调一下 node 脚本"的改动需求 | §4：**运行时零依赖**是维护红线，不接受。新增能力一律落 `services/egmp/` 或扩展 |
| `docs/模块契约.md` 一类的死引用被反复撞到 | §5 第二条：那是文档债，可以做一次集中清理（**不要**为了它去恢复那份文档） |

---

_本篇由**原 ADR-0005** 独立成篇（未被其它 ADR 并入），沿用原文件名。
「补记（这段是后补的，2026-09-22）」的标记原样保留。_
