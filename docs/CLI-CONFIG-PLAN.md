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

## 2. 前置条件（三步，**已全部具备**）

```powershell
gws config set service <你的服务地址>     # ✅ 已定：https://standard-val.aksoegmp.com（标准验证环境）
gws config set-app                        # ✅ 已拿到 AppId + SecretKey（2026-09-22）
gws auth authorize -o                     # ✅ 已完成（浏览器授权，**需要人点一下**；身份 = liyulong）
gws doctor                                # ⚠️ 只能当信息：它的退出码不可用，见 §6 缺陷 ③
```

**⚠️ 凭据落点（按决策卡 C2）**：全程只经 `WORKBENCH_GWS_SERVICE` / `_APP_ID` / `_APP_SECRET`
环境变量下传，**不写 `~/.gws/config.yaml`**（那是明文落盘，与
[ADR-0007](adr/0007-credentials-never-plaintext.md) 冲突）。
适配层把这三个变量**翻译**成 gws 认的 `HOST`/`APP_ID`/`APP_SECRET` 再注入子进程——
于是"哪个变量进了 gws 的环境"是**我们决定的**，不是继承来的（见 §4.3 约束 ②）。

**⚠️ 二进制落点**：`%APPDATA%\Workbench\runtime\gws\gws.exe` 那份**已被 Defender 拦住不能执行**（缺陷 ⑮）。
当前跑通用的是解包目录里的副本，靠 `WORKBENCH_GWS_BIN` 指定。

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

### 3.1 配置包的三跳（**CLI 的响应体自己说出来的**，不是从手册抄的）

| 步 | 端点 | 响应 | 性质 |
|---|---|---|---|
| 导出 | `POST /component/migration/export`（体：`{summary, description, owner_name}` 全选填） | `{name, package_id, file_path}` | 写：在平台上**创建一条配置包记录**。`file_path` 是**下载标识**，不是本地路径 |
| 导入 | `POST /component/migration/import`（multipart，字段名 `file`，**只收 zip**） | `{package_id, name, type, deploy_status}` | 写：变成**入站配置包**，**尚未生效** |
| 部署 | `POST /component/migration/deploy/{package_id}` | **`{code, message}` —— 没有 `data`** | 🔴 写：**配置在这里才真正生效** |

> **这三跳正好印证 [FACTORY-PLATFORM-COGNITION](FACTORY-PLATFORM-COGNITION.md) §7 的 E4**
> ——「入站包导入 ≠ 生效」，中间还隔着一次部署。`import` 回的那个 `deploy_status`
> 就是这个"还没生效"的状态位。
>
> ⚠️ 两处**必须记住的未知**：① `type` / `deploy_status` 的**取值表我们不知道**
> （只见过示例值 `type=3` / `deploy_status=1`，无真机样本）；
> ② **`deploy` 的成败无法由它自己的响应确认**（只回成功标志，PITFALLS #62 同形）
> ⇒ 判断是否生效**只能回读平台配置**。适配层因此**不给 `deploy` 编任何返回值**（`-> None`），
> 并由 `tests/test_gws_client.py::test_migration_deploy_has_nothing_to_confirm_with` 钉住。

---

## 4. 本分支已落地的东西（2026-09-22）

| 文件 | 是什么 | 判据 |
|---|---|---|
| `workbench/services/gws/client.py` | **适配层**：定位二进制 / 最小环境 / 只读白名单 / 信封拆解 / 错误分层（5 类） | `pytest tests/test_gws_client.py`（**43 条**） |
| `workbench/services/gws/shape.py` | **形状对照引擎**：把"两套线格式像不像"变成可复算的三个数（交集 / 仅参考侧 / 仅候选侧）+ 分诊档位 | `pytest tests/test_gws_shape.py`（**14 条**，含真机基线） |
| `workbench/services/gws/__init__.py` | 导出面 | —— |
| `tools/probe_gws.py` | **只读探针**：S0 二进制 → S1 组件类型表 → S2 各类实例数 → S3 单实例全形状 → S4 对象字段 → **S5 与 `factory-tuning` 录到的真机形状逐字对照 → S5b 判定（A/B/C）** | 未配置时 exit **2**（"没跑"，区别于"跑了但失败"的 exit 1） |
| `tests/test_gws_client.py` | 43 条单测，**用真子进程跑一个假 CLI** | 见下 |
| `tests/test_gws_shape.py` | 14 条：纯逻辑 + **用真机录制数据钉住的基线** | 见下 |

### 4.0 配置迁移（M4）已落地

`migration_export / migration_import / migration_deploy` 三个方法 + `MigrationPackage`，
按 §3.1 的三跳实现。两条承重的设计：

1. **`import` 在起子进程之前就 Fail-Closed**：路径不存在 / 不是文件 / 不是 `.zip` —— 一律拒，
   不拿一个坏路径去换一条 CLI 的报错回来；
2. **`deploy` 返回 `None`，且不给它编任何"成功"返回值** —— 它的响应里没有 `data`，
   编一个就等于伪造证据。

### 4.1 形状对照引擎：两条守卫是**承重的**（有反证）

`shape.compare_shapes` 的产出（A/B/C）会**决定整个改造路线的形状**，
所以一个会撒谎的判定比没有判定更糟。两条守卫各有一个实测反证：

| 守卫 | 天真实现会怎样 | 反证结果 |
|---|---|---|
| **空侧不是"不吻合"** | 把空的候选键集直接丢进集合运算 ⇒ 交集 0 ⇒ **判 C（完全对不上）** | 实测：天真实现给出 `common=0 / jaccard=0.00 ⇒ C`；现实现给出 `verdict=?` + 一条"先确认取到了实例再判"。**一个假 C 会把方向从「换传输层」推到「重新取证」** |
| **必须报形状直方图，不能只报并集** | 只报并集 ⇒ 同一个数组里"1 个 8 键元素 + 1 个 23 键元素"的并集是 23，而"全是新建"的并集是 8 ⇒ **被读成"模型不一样"** | 实测：`hist={'8':1,'23':1}` vs `hist={'8':1}` 一眼看清真相是"这次全是新建元素" |

> 第二条不是假想——**早期报告正是这么读错的**（把"整份看着精简/完整"当成两种请求体），
> 2026-09-22 段 2 已更正为"**逐元素**：同一请求里新建精简、已存在完整"。
> 真机数据复核（25 条 `SaveLayoutDetail`）：**7 条在同一数组内并存两种形状**；
> 控件元素形状大小只出现过 `{8, 14, 15, 16, 23}`，部分是 `{5, 62}`。

### 4.2 为什么单测要用**真子进程**（而不是打桩 `subprocess`）

本模块最该被验的三件事，全都长在"真的起了一个子进程"上，打桩就全测没了：

1. **凭据不经 argv** —— argv 在进程列表里对**同机所有用户**可见；
2. **环境走白名单、不全量继承** —— 不打桩才看得见子的环境块；
3. **stdin 必须 `DEVNULL`** —— 这条**只有真进程**才验得出来（见 §6 缺陷 ①）。

`GwsClient(argv_prefix=[sys.executable, <假 CLI>])` 这个参数存在的**唯一**理由就是它。

### 4.3 四条硬约束（写进代码注释，因为它们各自防一个具体的坑）

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
| ⑨ ★ | **`auth login` / `auth set` 根本不存在，但 `apis list` 把它们当命令宣传**（ID 5 / ID 4）。`gws auth login` 打出来的是 auth 组的总帮助，`Available Commands` 里没有 `login` | `gws auth login --help` 的输出 == `gws help auth` 的输出 |
| ⑩ ★ | **未知子命令 `exit=0`**：`gws auth login`（不存在的命令）退出码是 **0**，只打了父组帮助 ⇒ **自动化分不出"执行了"与"没这个命令"** | `gws auth login; $LASTEXITCODE` = 0 |
| ⑪ 🔴 | **`auth authorize` 把 app secret 明文打进 stderr，并带在请求 URL 的 query 里**：`GET …/access_token?appid=…&secret=<明文>&code=…`。而 CLI 自己有请求日志（`log.max_days` 默认 3 天）⇒ **密钥可能落进 `~/.gws/logs`** | 看 `auth authorize` 的 stderr；`gws config show` 反倒会把 secret 打成 `***`（同一个工具两处口径相反） |
| ⑫ | **`--redirect-uri` 是摆设**：帮助里写着默认 `http://localhost:8888/callback`，运行时却提示「自定义 redirect_uri 暂不支持，将使用自动分配的端口」，实际回调落在**随机端口** | `gws auth authorize -o` 的输出 vs `gws auth authorize --help` |
| ⑬ | **授权窗口只有 60 秒**（`⏳ 等待浏览器授权（60 秒超时）`），且**超时后没有重试提示** | `gws auth authorize -o` 的输出 |
| ⑭ | **`config test` 对 HTTP 418 也报「✔ 连接成功」** ⇒ 它只判"有没有响应"，不判"是不是平台"。本机实测 tonbridge 回 **418** 仍显示成功 | `gws config test`（对 `https://tonbridge-config.aksoegmp.com`） |
| ⑮ 🔴 | **Windows Defender 把 `gws.exe` 判为 `Trojan:Win32/Bearfoos.A!ml`**（ML 启发式，严重级别 5）。实测后果：`%LOCALAPPDATA%\gws\gws.exe` **被隔离删除**，`%APPDATA%\…\runtime\gws\gws.exe` 仍在但**无法执行**（Python `subprocess` 报 `OSError [WinError 225]`）。**无论真假阳性，这都使该二进制当前不能作为产品依赖** | `Get-MpThreat \| Where ThreatID -eq 2147731250`；以及用 `subprocess` 起它 |

> **⑮ 是本次评估最重要的一条**：未签名 + 被 Defender 判毒 = 终端用户装上去会被拦、会被隔离，
> 而**失败形态是"程序起不来"**，与"我们的代码写错了"长得一模一样。
> 处置建议（择一，需研发决策）：**代码签名证书** / 向 Microsoft 提交误报申诉 / 改变打包方式（如避免触发 ML 特征的壳）。

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
| M0 | 分支建立 + 适配层 + 只读探针 + 单测 | `pytest tests/test_gws_client.py -o addopts=""` 全绿 | ✅ **2026-09-22 完成** |
| M4 | **接入 `migration export/import/deploy`**（与 component schema 无关） | 三跳按 §3.1 实现；`import` 起进程前 Fail-Closed；`deploy` 不编返回值 ⇒ 见 §4.0 | ✅ **2026-09-22 完成**（适配层级；**尚未接成 Agent 工具、未真机验**） |
| M1 | **拿到 AppId/SecretKey，跑通 S1（组件类型全表）** | `tools/probe_gws.py` 产出 `component-metadata.json`，类型数 > 0 | ✅ **2026-09-22 完成**：**47** 个组件类型，见 §10 |
| M2 | **S5b 判出结论（§5 的 A / B / C）** | `SUMMARY.md` 里出现 `S5b · 判定` 段与三组数字（交集 / 仅参考侧 / 仅候选侧） | ✅ **2026-09-22 完成**：判定 **B（但理由与字面不同）**，见 §10.2 |
| M3 | 按 M2 结论决定改造面，并更新本文档 | 本文档 §5 的结局被标注为已判定 | ⬜ **需要一次真机写入才能定案**（见 §10.3） |
| M5 | 把 `migration` 三跳接成 Agent 工具（`Risk` 分级 + 确认门） | 按铁律 6/9：`deploy` 必须 `DANGEROUS` 且过门 | ⬜ 待 M3 |

> **M4 已完成的部分与未完成的部分要说清**：适配层的方法、Fail-Closed、单测都在了；
> **没做**的是"接成 Agent 工具"（那要动 `plugins/`，会连带 `_MODULES` / `server.spec` /
> `REQUIRED_TOOLS` / [TOOLS.md](TOOLS.md) 四处，属 M5），也**没有真机跑过**
> ——三跳的响应形状来自 CLI 自带的 `api describe`（离线可读），不是真机实测。

---

## 9. 决策卡（待用户拍板）

| # | 决策 | 选项 | 影响 |
|---|---|---|---|
| **C1** | ~~M4（migration 封装）要不要现在就做~~ | ✅ **已按建议①执行**（2026-09-22）：适配层级做完，见 §4.0 | 剩余部分（接成工具）见 M5 |
| **C2** | **密钥落点**：走 CLI 自己的 `config.yaml`（明文），还是存我们库里调用时注入？ | ① ★ 存我们库（Fernet 密文）+ env 注入 ② 用 CLI 的 config.yaml（省事，但明文落盘，与 ADR-0007 冲突 ⇒ 需要一篇 ADR 记这次偏离） | 决定要不要新写一篇 ADR |
| **C3** | **合回 main 的时机** | ① M2 出结论就合（把适配层与探针先落地）② 等 M3/M4 一起合 | 决定 main 多快能拿到"适配层 + 探针"这两个**不依赖结论**的资产 |
| **C4** | **main 上的配置工厂**在改造完成后怎么处置 | ① 冻结保留 ② 随改造逐步下线 ③ 长期并存（两条路互为备份） | 决定 `writers/` 那 13 文件 2,353 行的去向 |
| **C5** | **`migration` 三跳要不要接成 Agent 工具**（M5） | ① 现在就接（`export`/`import` = `write`，`deploy` = **`dangerous`** 且过确认门）② 等 M3 结论 | `deploy` 是本项目**唯一一个把配置真正推上平台的动词**，接成工具前要有确认卡文案与人工清单 |
| **C6** ★ | **要不要做一次"真机写入"来定案 M3**（见 §10.3） | ① 做（在 `标准验证环境` 上找一个**已有布局**，把它的实例原样 `save` 回去，`operation_type=20 修改`；改前先导出配置包做回滚点）② 先不做，接受 M3 停在"投影差异 vs 模型差异未分辨" | **这是唯一能把判定从"分诊标签"变成"证明"的动作**（§5 的原话）。风险：平台的 `save` 是**整份提交**语义，写错会改掉那个布局 |

---

## 10. ★ 真机结果（2026-09-22，`liyulong` / 标准验证环境）

> **环境与账号与工厂的真机取证同源**（`https://standard-val.aksoegmp.com`，账号 `liyulong`），
> 所以对照是有效的。凭据只经 `WORKBENCH_GWS_*` 环境变量下发，**没有落进 `~/.gws/config.yaml`**
> （`gws config show` 自己把 secret 打成 `***`）。
> 原始证据：`tmp/gws-probe-20260922-181026/`（全量 47 类）与 `…-181256/`（布局详情 + 判定）。

### 10.1 通道是真的，而且比工厂宽得多

`component metadata list` 回 **47 个组件类型**。挑与工厂直接相关的：

| component_type | 中文 | 工厂现状 |
|---|---|---|
| `object` | 对象 | ✅ 有（真机 2276 个实例） |
| `picklist` | 选项集 | ✅ 有（1581） |
| `layout` | 表单布局 | ✅ 有（2458） |
| `listlayout` | 列表布局 | ✅ 有（2484） |
| `lifecycle` | 生命周期 | ✅ 有（954） |
| **`workflow`** | **工作流** | 🔴 **CLI 的 `apis list` 里没有任何 workflow 路由**，但**组件模型里有**（755 个实例） |
| **`ui_menu` / `ui_menu_group`** | 菜单 / 菜单集 | 工厂有 `MENU_*` 端点但 **T1「菜单节从来没被执行过」**（791 / —） |
| **`permission_set`** | 权限集 | 🔴 **工厂零权限端点**（229）——这正是决策卡 **E1** 的死结 |
| `linkage_rule` / `matching_rule` / `code_rule` / `layout_strategy` / `ui_sys_view` | 联动/匹配/编号/布局权限/表单视图 | 工厂全无 |
| `report_type` / `report_view` / `runreport_template` | 报表族 | 工厂全无 |
| `checklist_type` / `approve_matrix` / `notice_message_template` | 检查表/审批矩阵/消息模板 | 工厂全无 |
| **`ai_agent` / `ai_tool` / `ai_mcp_server`** | **AI 代理 / AI 工具 / AI MCP 服务** | 平台自己也有 agent 配置面（值得单独看一眼） |

⇒ **这个通道不只覆盖工厂，它是平台配置面的全集。**

### 10.2 判定：**B** —— 但字面理由（"另一套模型"）是错的

```
顶层:            交集 5  / 仅参考 12 / 仅候选 18  (Jaccard 0.14 · 参考侧覆盖 0.29 · 候选侧命中 0.22 ⇒ B)
顶层（归一后）:  交集 13 / 仅参考  4 / 仅候选 10  (Jaccard 0.48 · 参考侧覆盖 0.76 · 候选侧命中 0.57 ⇒ B)
命名约定落差：⚠️ 归一后 Jaccard 抬升 +0.34（0.14 → 0.48）⇒ 大概率是同一套模型的两种拼写
```

**逐条读**：

1. **命名约定系统性不同**：`objectId`↔`object_id`、`isMain`↔`is_main`、`labelCol`↔`label_col`、
   `activePageCode`↔`active_page_code`、`skipRequiredValidation`↔`skip_required_validation`。
   **17 个参考键里有 13 个能一对一映射过去** ⇒ **不是两套模型，是一次机械转换。**
2. **缺的 4 个恰恰是"内容本体"**：`controls` / `sections` / `pages` / `rules`。
3. **多出来的 10 个全是行元数据**：`created_by` / `created_time` / `modified_by` / `modified_time` /
   `full_path` / `belong_id` / `active` / `sort` / `description` / `rules_json`。

⚠️ **第 2/3 条要小心读**：这次的候选样本来自 `component instance **show**`，
它回的是**行投影**（把嵌套内容压成 `rules_json` 这样的字符串列），
而参考侧是**写入体**。**两边不是同一个投影** —— 所以"内容键缺失"更可能是
**投影差异**，而不是"新模型没有这些字段"。

### 10.3 要把 B 变成证明，只差一步（需要你的下达）

唯一能定案的动作是**一次真机写入**，而它按本仓规矩**必须有你的下达**（决策卡 **C6**）：

- **做什么**：在标准验证环境上挑一个**已有布局**，`component instance show` 读出它，
  用 `save` + `operation_type=20（修改）` **原样写回去**（不改任何值），再回读比对。
- **能回答什么**：`save` 到底收什么形状 —— `controls`/`sections` 是用**同一套 camelCase** 还是
  **snake_case**、还是干脆不吃这两个键。**这一个问题决定 M3 的全部工作量。**
- **风险与处置**：`save` 是**整份提交**语义（工厂已实证）。所以要么挑一个**无关紧要的布局**，
  要么**先 `migration export` 导出一份配置包当回滚点**——而 `migration export` 正好是我们刚接进来的能力。

### 10.4 顺带：我自己的适配层在这次真机里被撞出两个缺陷（都已修 + 有回归）

| # | 缺陷 | 形态 |
|---|---|---|
| A1 | **把裸实例误判成"平台信封"** | `component instance show` 回的是**裸实例**，而实例自带 `code` 字段（布局编码字符串）。旧 `_unwrap` 只看"有没有 `code` 键" ⇒ 把一份**完全正常的响应**报成「平台拒绝：… code=change_hdh_mgt_base_layout__ak」。**平台什么都没做错** |
| A2 | **`from_env(service=None)` 把环境值清成 None** | 探针里写的就是 `service=args.service or None`，于是凭据都在、却报「缺 service」。断言只说了"缺 service"，看起来像配置问题 |

两条都已修，回归在 `tests/test_gws_client.py`（`test_a_bare_object_with_its_own_code_field_is_not_an_envelope` /
`test_config_from_env_ignores_none_overrides`）。**A1 的教训值得单独记**：
"同一个命令族里两种响应形状并存"（`component metadata list` 回信封、`component instance show` 回裸对象）
⇒ **不能靠"这个命令回什么"来记，只能按载荷形状判**，并且判定要选 fail-closed 的那一侧。
