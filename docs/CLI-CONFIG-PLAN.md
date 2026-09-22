# 基于 gws CLI 重建配置能力 · 方案与台账（**进行中**）

> **状态**：进行中 · 分支 `cli-config` · 2026-09-22 起
> **分支策略（用户 2026-09-22 定）**：**短期并行分支**，用户近期重心都在这条线上；
> main 上的配置工厂**保持可用**，本次改造在分支上完成后再决定怎么合。
>
> **上游输入**：平台研发发布的 `gws` CLI v1.0.0（发布包 15 MB / 解包 37 MB，含
> windows / linux / darwin 三平台二进制 + 使用教程 + `agents/ag-gws.md`）。
> 用户已确认：**目标平台版本在 3.8.1 之上**（⇒ `component` 与 `migration` 两条通道总开关为开）；
> AppId / SecretKey **待用户确认**。

---

## 0. 一句话

**配置工厂的"传输层"（58 个私有、无版本、逆向自浏览器流量的端点）是它最脆弱的地方；
gws 把它换成一个公开、版本化的通道。但工厂真正的资产是"线格式知识 + 编排 + 闸门"，那些不换。**

---

## 1. 为什么要做这件事（工厂的三个已知脆弱点）

| # | 脆弱点 | 证据 |
|---|---|---|
| 1 | **端点全是私有的、无版本的** | `kernel/endpoints.py` 共 **58 个**常量：`/api/platform/*` **37** + `/api/config/*` **15** + `/api/openapi/v1.0` **6**。模块自己的文档串就写着「私有 web 控制台接口，**无版本**」——平台升级时最先断的就是这一批 |
| 2 | **线格式靠真机取证，成本极高** | F20–F66 是 7 段真机录制（段 0–段 6）逐键对照换来的；仅"默认布局加控件"一条链路就撞出并修掉 8 个缺陷（D-A…D-I + #82/#83） |
| 3 | **平台侧的"另一个客户端"才是权威** | 多处结论的直接依据是"平台自己的客户端这么发"（`AddColumns` 的 `listlayoutColumns`、`SaveField` 从不发 `isRequired`、`GetStableComponentIds` 是独立一步）。**我们一直在逆向一个本来就有正规出口的东西** |

而 gws 提供的是：**66 个内置端点**、每条都有路由与参数表（`gws apis list` / `gws api describe`，
**离线可用**）、公开版本号（`v3.6.4` / `v3.8.1`）、以及平台研发自己维护的兼容承诺。

---

## 2. 前置条件（三步，**目前卡在第 2 步**）

```powershell
gws config set service <你的服务地址>     # ✅ 可知（就是已有的环境地址）
gws config set-app                        # ⬜ 待用户确认：AppId + SecretKey（eGMP 后台 →「应用管理」申请）
gws auth authorize -o                     # ⬜ 浏览器授权，**需要人点一下**（首次无法全自动）
gws doctor                                # 环境诊断（注意：它的退出码不可用，见 §6）
```

**已排除的阻塞**：平台版本 ≥ 3.8.1（用户确认）。
**未排除的**：AppId / SecretKey。在拿到之前，§4 的探针**跑不了**——它会以 exit 2（"未配置，没跑"）收尾，
并把上面这三步写进报告。

---

## 3. 通道长什么样（离线实测的全部事实）

| 项 | 实测 |
|---|---|
| 唯一的配置**写**入口 | `POST /openapi/v3.8.1/component/instances/save` |
| 写体 | `{component_type: string, instances: array, operation_type: 10新增\|20修改\|30覆盖(默认)}` |
| 写响应 | `{code, message}` —— **不回 id、不回显内容** |
| 实例**寻址** | `full_path`，形如 `object.aa__c`（`<component_type>.<code>`） |
| 组件类型 | **运行期**从 `GET /component/metadata` 取（`[{component_type, name_cn, name_en, sort, parent_code}]`，示例 `object`/对象/`sort 700`）；二进制里能看到的 token：`object` / `layout` / `section` / `control` / `status` |
| 缓存表 | `plat_component_object` |
| 元数据命令 | **全部 GET 只读**（`metadata object/object-type/lifecycle/picklist`） |
| 配置包 | `migration export / import / deploy`（`/openapi/v3.8.1/component/migration/*`）——**工厂完全没有这一块** |
| 工作流 | **零**：`apis list` 无 workflow 路由，二进制里 `workflow` token **0 命中** |

---

## 4. 本分支已落地的东西（2026-09-22）

| 文件 | 是什么 | 判据 |
|---|---|---|
| `workbench/services/gws/client.py` | **适配层**：定位二进制 / 最小环境 / 只读白名单 / 信封拆解 / 错误分层（5 类） | `pytest tests/test_gws_client.py`（**34 条**） |
| `workbench/services/gws/__init__.py` | 导出面 | —— |
| `tools/probe_gws.py` | **只读探针**：S0 二进制 → S1 组件类型表 → S2 各类实例数 → S3 单实例全形状 → S4 对象字段 → **S5 与 `factory-tuning` 录到的真机形状逐字对照** | 未配置时 exit **2**（"没跑"，区别于"跑了但失败"的 exit 1） |
| `tests/test_gws_client.py` | 34 条单测，**用真子进程跑一个假 CLI** | 见下 |

### 4.1 为什么单测要用**真子进程**（而不是打桩 `subprocess`）

本模块最该被验的三件事，全都长在"真的起了一个子进程"上，打桩就全测没了：

1. **凭据不经 argv** —— argv 在进程列表里对**同机所有用户**可见；
2. **环境走白名单、不全量继承** —— 不打桩才看得见子的环境块；
3. **stdin 必须 `DEVNULL`** —— 这条**只有真进程**才验得出来（见 §6 缺陷 ①）。

`GwsClient(argv_prefix=[sys.executable, <假 CLI>])` 这个参数存在的**唯一**理由就是它。

### 4.2 四条硬约束（写进代码注释，因为它们各自防一个具体的坑）

| # | 约束 | 防的是什么 |
|---|---|---|
| ① | **凭据绝不经 argv**，走 `APP_ID` / `APP_SECRET` 环境变量 | argv 对同机所有用户可见（任务管理器 / WMI / 崩溃转储 / 我们自己的日志行） |
| ② | **环境变量走白名单**，不全量继承 | gws 认的变量名太通用（`HOST` / `TIMEOUT` / `APP_ID`），全量继承会把**别的服务的地址**喂给它，而表现是"连不通"——**不报错**（铁律 13 在这条链路上的具体后果） |
| ③ | **stdin 一律 `DEVNULL`** | **实测**：未配置时 gws 进入交互式提问，给了管道不关就会挂满超时 |
| ④ | **写入必须显式声明 `operation_type`** | CLI 默认 **30（覆盖）**；把"整份覆盖"设成默认，与工厂踩过的 D-B/D-F「全量替换却不读旧」是同一个坑的另一种写法 |

**永不封装**：`gws api call`（任意方法 + 任意路径的逃逸口）。一包成 Agent 工具，
本仓的 `Risk` 分级、确认门、`precheck` 会被**整体绕过**。

---

## 5. ★ 决定性未知（**这一条决定整个方案的形状**）

`component instance save` 的 `instances` 是**「动态配置」**——CLI **不校验、不提示、不给 schema**：

> **没有一份真实的组件实例样本，就写不出一个正确的 `save` 请求体。**
> 不是"写不好"，是**写不出来**——键名从哪来？

而工厂那套知识（17 键布局体、控件 8/23 键、`AK*` 七值对九类型、`GetStableComponentIds` 的**增量**语义）
**全部是在 `/api/platform/Layout/SaveLayoutDetail` 这个语义下测出来的**。
`component` 是**另一条通道**，能不能迁移**必须用样本证明，不能假设**。

**判据就是探针的 S5**（一条命令，只读，不写平台）：

```powershell
.\\.venv\\Scripts\\python.exe tools\\probe_gws.py --type layout --full-path <layout.xxx__ak>
```

三种结局，事前定死怎么读：

| 结局 | 含义 | 下一步 |
|---|---|---|
| **A** 键集与录制的 17 键**高度重合** | 工厂知识**可迁移** | 这是"替换传输层"：改 `writers/` 的 HTTP 层，风险集中在认证与错误映射 |
| **B** 只有**少数几个键**对得上 | 是**另一套规范化模型** | F20–F66 需**重新取证**，但新模型很可能更干净（公开 API 通常比私有控制台接口规整） |
| **C** **完全对不上** | 缺的是"官方的实例 schema" | 先向研发要 component 实例的 schema 文档，再谈替代 |

⚠️ **不要拿"接口回 `code: 0`"当"改对了"的证据** —— 与本仓 PITFALLS #62 同形（接口只回成功标志）。
**写完必须回读**（`component instance show`）。

---

## 6. 工具自身的缺陷（**报给研发**，全部 2026-09-22 实测）

| # | 现象 | 判据（可复跑） |
|---|---|---|
| ① | **未配置 service 时进入交互式提问**（打印「服务地址: 」等 stdin），不是干净报错 ⇒ **无头 / Agent 调用会挂住** | `gws component metadata list`（无配置、stdin 打开）= 停在提示。建议：非 TTY 时报错退出 2 |
| ② | **`doctor` 报的配置文件路径是错的**：报 `~/.gws.yaml`，实际是 `~/.gws/config.yaml` ⇒ **配置好了 doctor 也一直说"未找到配置文件"**；且 `doctor --config <path>` **不生效** | `gws config show` vs `gws doctor`，两处路径不一致 |
| ③ | **`doctor` 在「部分检查未通过」时 exit=0** ⇒ 退出码不能当健康闸门，自动化只能解析文本 | `gws doctor; $LASTEXITCODE` = 0 |
| ④ | **`config init --config <path>` 创建失败**：`missing configuration for 'configPath'`，并给出**误导提示**「配置文件可能已存在」（实际不存在） | `gws config init --config <tmp>\config.yaml` → exit 1、文件未生成 |
| ⑤ | **CLI 自身口径冲突**：`aql --help` 的示例用 `select *` 与 `limit`，而随包 `ag-gws.md` 硬约束第 3/4 条要求 `top` ≤100 且**禁止 `select *`** | 对比 `gws aql --help` 与 `agents/ag-gws.md` §2 |
| ⑥ | **二进制未签名**（`NotSigned`）⇒ SmartScreen 拦截、企业终端管控可能禁止 | `Get-AuthenticodeSignature bin\windows\gws.exe` |
| ⑦ | **`save` 默认 `operation_type=30`（覆盖）**，无 dry-run、无校验、响应不回显 ⇒ **破坏性默认值** | `gws component instance save --help`（`default 30`） |
| ⑧ | **关键 flag 不在 `apis list` 的参数表里**（`api describe` 只列 body 三个字段，`operation_type` 的取值说明缺失） | `gws api describe POST /openapi/v3.8.1/component/instances/save` |

**另注（非缺陷，但要知道）**：`install.ps1` 要 **UAC 提权**、写 **Machine PATH**、
并自动把 `ag-gws.md` 装配进 **19 个** AI 工具的用户级目录
（本机检测到 `codebuddy` / `trae-cn` / `workbuddy` 三个"已装未装配"）。
**本仓库不提交 gws 二进制**（9.5 MB 未签名第三方产物），走"操作员放置 / 环境变量指定"。

---

## 7. 红线（本分支不得越过）

| # | 红线 | 理由 |
|---|---|---|
| 1 | **不把 `app_secret` 落进 CLI 的 `~/.gws/config.yaml`** | 那个文件是**明文**的，与 [ADR-0007](adr/0007-credentials-never-plaintext.md)「凭据全程密文，明文只在内存」**直接冲突**。产品形态应当是：密钥存我们自己的库（Fernet 密文），**调用时经 env 注入** |
| 2 | **不封装 `gws api call`** | 任意方法 + 任意路径 ⇒ 绕过 `Risk` 分级与确认门 |
| 3 | **不提交第三方二进制进 git** | 未签名、无法审计、每次升级产生巨大二进制 diff |
| 4 | **分支不动版本号**（`bump.py` 五写） | 分支不发版；两处版本冲突会污染发布链 |
| 5 | **真机写入必须先有用户下达** | 沿用 [FACTORY-VERIFICATION-PLAN](FACTORY-VERIFICATION-PLAN.md) §1 的裁量规则 |

---

## 8. 里程碑与台账

| # | 里程碑 | 交付判据 | 状态 |
|---|---|---|---|
| M0 | 分支建立 + 适配层 + 只读探针 + 34 条单测 | `pytest tests/test_gws_client.py -o addopts=""` 全绿 | ✅ **2026-09-22 完成** |
| M1 | **拿到 AppId/SecretKey，跑通 S1（组件类型全表）** | `tools/probe_gws.py` 产出 `component-metadata.json`，类型数 > 0 | ⬜ **卡在凭据** |
| M2 | **S5 对照出结论（§5 的 A / B / C）** | `SUMMARY.md` 里有 component 实例键集 vs 录制 17 键的逐条差异 | ⬜ 待 M1 |
| M3 | 按 M2 结论决定改造面，并更新本文档 | 本文档 §5 的结局被标注为已判定 | ⬜ |
| M4 | 接入 `migration export/import/deploy`（**与 component schema 无关，可并行**） | 见下 | ⬜ |

> **M4 可以脱离 M1 先做**：`migration` 那一块是工厂**完全没有**的能力，
> 且不依赖 component 实例 schema，正好回答
> [FACTORY-PLATFORM-COGNITION](FACTORY-PLATFORM-COGNITION.md) §7 的 **E4** 决策卡
> （"配置包 ≠ 已生效系统"：导出 → 校验 → 部署三跳）。

---

## 9. 决策卡（待用户拍板）

| # | 决策 | 选项 | 影响 |
|---|---|---|---|
| **C1** | **M4（migration 封装）要不要现在就做**，还是等 M2 的 schema 结论一起？ | ① 现在就做（它不依赖 schema，且是工厂的能力空白）② 等 M2 | 决定这一周里有没有"确定能交付"的产出 |
| **C2** | **密钥落点**：走 CLI 自己的 `config.yaml`（明文），还是存我们库里调用时注入？ | ① ★ 存我们库（Fernet 密文）+ env 注入 ② 用 CLI 的 config.yaml（省事，但明文落盘，与 ADR-0007 冲突 ⇒ 需要一篇 ADR 记这次偏离） | 决定要不要新写一篇 ADR |
| **C3** | **合回 main 的时机** | ① M2 出结论就合（把适配层与探针先落地）② 等 M3/M4 一起合 | 决定 main 多快能拿到"适配层 + 探针"这两个**不依赖结论**的资产 |
| **C4** | **main 上的配置工厂**在改造完成后怎么处置 | ① 冻结保留 ② 随改造逐步下线 ③ 长期并存（两条路互为备份） | 决定 `writers/` 那 13 文件 2,353 行的去向 |
