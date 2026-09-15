# akso-mogul「核心 / 业务插件分离」可行性调研

- 对象：`workbench/`（Python 3.12 + FastAPI，~11.4k 行）+ `desktop/`（Electron 33）+ `extensions/quick-login/`（MV3）
- 目标：把通用「agent 核心」剥离为上游项目/依赖，公司专属能力（`services/egmp/`）以插件形式演进
- 约定：**【事实】**=有公开出处；**【推测】**=未找到公开出处，按工程经验判断；**【建议】**=我的推荐，非事实。**B 部分不构成法律意见**，只做条款与风险的事实梳理，法律结论必须由执业律师给出。
- 方法：本报告结论均来自 web_search 检索到的公开资料（内联链接）；仓库结构判断来自本机实测（`workbench/api/__init__.py`、`workbench/services/modules.py`）。

## 0. 结论速览

- 【事实】你的拆分点已经被你自己铺好了：`workbench/api/__init__.py:34-65` 已是一个显式路由注册表（`registrations` 元组 + 单个失败不拖垮应用），`workbench/services/modules.py:23` 已是一个基于 `adapters/*.json` 的**清单式模块注册表**，`workbench/harness/tools.py:121` 已有一个 `Tool` 注册表。**"插件发现"这件事在你仓库里已有三处现成形态**，不需要引入新范式。
- 【事实】已知耦合只有 10 + 4 处、集中在 4 个文件，属于"低成本可拆"区间；这是可行性判断的主要依据。
- 【建议】不要做 VS Code / Grafana 式的"宿主 + 第三方插件市场"；做 **A3 的"上游库 + 下游私有包"**（依赖方向倒置）。你只有一个插件作者（你自己），"发现机制"的收益接近零，"依赖方向 + 版本边界"才是你真正要买的东西。
- 【事实】法律上，**委托开发（外包）默认归受托人**，与**员工职务开发默认归单位**方向相反（著作权法第 19 条 / 软件保护条例第 11 条 vs 第 13 条）。但"著作权归属"只是风险面之一；**商业秘密、平台服务条款/爬取合规、保密义务**是三条独立于著作权的风险路径，且不受"代码是我写的"影响。见 B8/B10。
- 【风险排序建议】"分离"本身低风险；**"把含平台实测知识的部分开源"是高风险动作**，应最后做，且先做 B10 的确认。

---

# A. 技术模式

## A1 Python 生态主流插件机制对比

| 机制 | 启动时如何被发现 | 依赖注入 | 版本兼容约束 | 适用场景 | 出处 |
|---|---|---|---|---|---|
| `importlib.metadata.entry_points`（setuptools entry points） | 安装期在发行版元数据里声明，宿主启动时按 group 扫描 | 无内置；宿主把上下文对象传给插件工厂（`ep.load()(...)`） | 规范只定义发现，不含版本约束，须宿主自行读版本比对 | **安装即可发现**的通用插件；PyPA 标准 | [entry points spec](https://packaging.python.org/en/latest/specifications/entry-points/)、[importlib.metadata](https://docs.python.org/3/library/importlib.metadata.html) |
| `pluggy`（pytest 内核） | 不负责发现，`PluginManager.register()` 显式注册（常与 entry points 组合） | 通过 hook 函数签名实现（参数名即依赖）；支持 hookwrapper/tryfirst 调优先级 | 无内置 | 宿主与插件是**多钩子协作**而非"注册路由"（pytest 全生态） | [pluggy API](https://pluggy.readthedocs.io/en/latest/api_reference.html) |
| `stevedore` | OpenStack 对 entry points 的封装（DriverManager / ExtensionManager） | 通过 `invoke_on_load`、`on_load_failure_callback` 注入与容错 | 无内置，靠宿主校验 | 需要"坏插件不炸宿主"的驱动器式扩展 | [stevedore 加载教程](https://docs.openstack.org/stevedore/latest/user/tutorial/loading.html) |
| 命名空间包（PEP 420） | 只是让多个发行版共享 `akso.*` 包路径 | 不解决 | 不解决 | 多包共享命名空间；**不解决注册与版本** | [PEP 420](https://peps.python.org/pep-0420/)、[PyPA namespace guide](https://packaging.python.org/en/latest/guides/packaging-namespace-packages/) |
| 显式注册表 / 配置文件清单 | 宿主代码里写死列表，或启动时读 JSON 清单 | 宿主完全控制 | 宿主完全控制 | **单/少插件场景最省事**；PyInstaller 友好 | 本仓库实例：`workbench/api/__init__.py:48`、`services/modules.py:23` |
| `__subclasses__` 自注册 | import 副作用触发注册 | 隐式 | 无 | 隐式、顺序敏感；**未找到权威规范出处，属工程惯例**【推测】 | — |

**要点**
- 【事实】entry points 是这些机制里唯一被 PyPA 标准化、且以"安装即发现"为设计目标的机制；其规范明确只描述元数据与发现，不提供兼容性保证。
- 【事实】它在**打包场景**有已知坑：打包后 `importlib.metadata.entry_points()` 找不到元数据，需要随包携带 distribution 元数据或显式声明 hidden imports —— 这正是你的 PyInstaller sidecar + NSIS 发行链会踩的地方（[pyinstaller discussion #8325](https://github.com/orgs/pyinstaller/discussions/8325)）。
- 【建议】给你仓库的最小改动：把 `api/__init__.py` 的 `registrations` 元组改成"从一个插件清单/entry point group 迭代"，保留现有"单个装配失败只 warning 不崩"的语义（这一段你已经写对了）。

## A2 真实先例：宿主 + 平台适配器插件

| 项目 | 扩展模型 | 版本兼容手段 | 规模前提 / 已知代价 | 出处 |
|---|---|---|---|---|
| VS Code | 扩展独立进程；API 分 stable/proposed；`engines.vscode` 声明兼容区间 | 提案 → Insiders 的 proposed API → 稳定化；proposed API 不允许发布扩展 | 面向**大量第三方作者**；代价是**新 API 上线周期长**，"稳定 API"一旦发布极难收回 | [Extension API Process](https://raw.githubusercontent.com/microsoft/vscode-wiki/main/Extension-API-process.md)、[proposed API 安装限制](https://github.com/microsoft/vscode/issues/233234)、[兼容区间声明讨论](https://github.com/microsoft/vscode-discussions/discussions/2437) |
| Eclipse / OSGi | 模块（bundle）级精确版本 + 区间解析 | OSGi 语义化版本策略 | 代价是经典 **JAR/依赖地狱**：版本区间对不上即运行失败 | [Red Hat Fuse: OSGi versioning](https://docs.redhat.com/de/documentation/red_hat_fuse/6.2.1/html/managing_osgi_dependencies/versioning)、[Eclipse 论坛：依赖与缓存问题](https://www.eclipse.org/forums/index.php/t/1109013/) |
| Jenkins | 插件依赖核心 API | **2015 年才提出** API 弃用策略 | 说明"先做生态、后补弃用策略"会长期背债 | [Jenkins 2.0 提案：API 弃用策略](https://www.jenkins.io/blog/2015/10/25/jenkins-2-0-proposal-introduce-a-policy-for-api-deprecation/)、[JENKINS-31035](https://github.com/jenkinsci/jenkins/issues/21492) |
| Grafana | 插件需**签名**（未签名默认可能被禁用）；前端 API 破坏性变更是持续议题 | 官方提供 runtime checks（按宿主版本条件化走不同代码） | 面向第三方市场；"插件 API 破坏性变更"至今仍在 RFC 讨论 | [Plugin signatures](https://grafana.com/docs/grafana/latest/administration/plugin-management/plugin-sign/)、[Mitigate breaking changes in Plugin APIs](https://github.com/grafana/grafana/discussions/40586)、[runtime checks](https://grafana.com/developers/plugin-tools/how-to-guides/runtime-checks) |
| Home Assistant | custom integration 大量依赖内部 API | 核心按版本弃用/移除内部 API | **实时案例**：`via_device` 弃用在 2026.9.0b0 直接导致自定义集成崩溃；另一例：`async_forward_entry_setup` 在 2025.6 停用击穿第三方集成 | [core#180469](https://github.com/home-assistant/core/issues/180469)、[hass-dmp#21](https://github.com/amattas/hass-dmp/issues/21) |
| Ansible | **collection 是分发单元**（`namespace.name`），版本独立于 ansible-core | 独立版本号 | 面向社区分发的插件集合 | [Distributing collections](https://docs.ansible.com/projects/ansible-core/2.16/dev_guide/developing_collections_distributing.html) |
| Airflow providers | provider 包**独立版本、独立发布流程** | SemVer + 弃用周期 | 拆出 provider 后，版本矩阵与发布流水线成本显著上升 | [README_RELEASE_PROVIDERS](https://github.com/apache/airflow/blob/056e24e0/dev/README_RELEASE_PROVIDERS.md)、[release process & version policy](https://airflow.staged.apache.org/docs/apache-airflow/stable/release-process.html) |
| Terraform providers | **独立二进制 + gRPC 协议 + 协议版本协商** | 协议版本 + 角色协商 | 换来语言无关与崩溃隔离，代价是协议冻结、兼容层维护 | [Terraform Plugin Protocol](https://third-party-mirror.googlesource.com/terraform/+/6a3c811625035624e0fb8a0ebbb7d03982d6303c/v1.2.3/docs/plugin-protocol/README.md) |

### A2.1 版本地狱的三种典型失败模式（均为【事实】）

1. **宿主移除内部 API → 插件直接崩溃**：Home Assistant 的 `via_device` 弃用在 2026.9.0b0 击穿 custom integration（[core#180469](https://github.com/home-assistant/core/issues/180469)）；`async_forward_entry_setup` 在 2025.6 停用同样击穿第三方集成（[hass-dmp#21](https://github.com/amattas/hass-dmp/issues/21)）。教训：**插件依赖宿主内部实现 = 把宿主的每次重构变成插件的破坏性变更**。
2. **依赖区间不可满足 → 解析/启动失败**：OSGi 的精确版本区间解析导致 JAR 冲突与启动失败（[Red Hat Fuse versioning](https://docs.redhat.com/de/documentation/red_hat_fuse/6.2.1/html/managing_osgi_dependencies/versioning)、[Eclipse 论坛](https://www.eclipse.org/forums/index.php/t/1109013/)）；Grafana 数据源插件与后端版本不匹配亦有实例（[grafana-zabbix#1914](https://github.com/grafana/grafana-zabbix/issues/1914)）。教训：**版本约束要么由宿主统一校验，要么就不要声明**。
3. **宿主加了安全/治理门槛 → 既有插件被拒**：Grafana 引入插件签名机制，未签名插件默认可能被禁用（[Plugin signatures](https://grafana.com/docs/grafana/latest/administration/plugin-management/plugin-sign/)）。教训：**生态治理机制一旦引入，会变成下游的迁移工作**。

**"什么规模才值得"的证据**
- 【事实】FSE 2019 论文《How to break an API: cost negotiation and community values in three software ecosystems》研究 npm / R(CRAN) / Eclipse 三个生态，核心发现是：破坏性变更的成本由生态各方"协商"分摊，且**维护者通常低估下游成本**（[par.nsf.gov 全文](https://par.nsf.gov/biblio/10038310/media/xml)）。
- 【事实】上述语境的共同前提是"宿主背后有**第三方**作者群"，需要营销、签名、沙箱、弃用日历这类配套。
- 【推测】你的场景是"宿主作者 = 唯一插件作者 = 同一人"，因此 A2 的绝大部分成本项（沙箱、签名、市场、对外 API 承诺）对你没有对应收益；上表的价值仅在于**抄袭它们的"边界纪律"**，而不是它们的"生态机制"。

## A3 反向做法（更贴合你）：宿主做库、业务做私有包

| 模型 | 宿主暴露扩展点的方式 | 依赖方向 | 独立版本 | 对你的适配度 |
|---|---|---|---|---|
| Django reusable app | `INSTALLED_APPS` 显式登记 + AppConfig | 应用 → Django | 应用独立发行 | 与你的 `registrations` 元组同构，**最贴近**（[Django 可复用应用教程](https://django.readthedocs.io/en/6.0.x/intro/reusable-apps.html)） |
| Rails engine | Gem + 挂载点 | engine → Rails | Gem 独立版本 | 偏重量级（[Rails Engines](https://guides.rubyonrails.org/v6.0/engines.html)） |
| pytest 插件 | entry point 组 `pytest11` + hook | 插件 → pytest | 独立发行 | 证明"入口点 + hook"在单人维护下也可行 |
| **FastAPI APIRouter 组合** | `include_router` 显式装配 | 业务 → 框架 | 随包 | **你已经在用**（`workbench/api/__init__.py`） |
| Terraform provider 协议 | 进程外 gRPC | provider → 协议 | 独立二进制 | 隔离最强、成本最高，**不建议** |

【建议】把 `workbench` 做成"库 + 装配器"两层：`akso-core`（harness + 通用 services + 通用 api + db/config，独立 repo、独立版本号）与 `akso-egmp`（当前 `services/egmp/` 全部 + 平台专属路由，独立 repo/包），`workbench`（应用）只负责按清单装配二者。对应的依赖方向：`akso-egmp → akso-core`，**永不反向**。

## A4 FastAPI 落地方案（可直接施工）

1. **注册 router**：沿用 `APIRouter` + `include_router`；把硬编码元组换成遍历插件清单，每个插件暴露 `router` 或 `register(app, ctx)` 工厂函数。参考社区做法：[fastapi-plugins](https://github.com/madkote/fastapi-plugins)。
2. **注册后台任务**：用 `lifespan` 启动/停止插件自有任务（`create_app()` 已用 `lifespan`：`workbench/api/__init__.py:68-72`）；参考实现见 [fastapi-plugins 的 ApschedulerPlugin](https://github.com/madkote/fastapi-plugins/blob/3758af5e/docs/scheduler.md)、[启动前投递后台任务的讨论](https://stackoverflow.com/questions/76080465/how-to-push-background-task-before-starting-fastapi-application)。
3. **注册前端静态页**：插件自带静态目录 → `app.mount("/plugins/<id>", StaticFiles(directory=...))`；注意 mount 顺序（更具体的路径先挂，通配后挂）。现宿主写死 `app.mount("/static", ...)`（`workbench/api/__init__.py:85`）（[FastAPI Static Files](https://fastapi.tiangolo.com/tutorial/static-files/)）。
4. **注册配置项**：插件只声明 schema 与默认值，**由 core 统一做校验+持久化+迁移**；不要每个插件各自写文件（你现在 `services/settings.py` + `services/storage.py` 已经承担这个角色，把它变成"插件配置的宿主服务"就顺带收口了两处耦合）。
5. **注册工具**：`harness/tools.py:121` 的 `Tool` 注册表是最贴合"agent 核心"的扩展点——比 router 更值得作为插件 API 的**唯一公开面**。
6. **版本约束**：插件包声明 `requires = "akso-core>=1.2,<2"`；宿主启动时读发行版版本、用 `packaging.specifiers` 校验，不满足则 skip + warning（对应 stevedore 的 `on_load_failure`）。可参考 grafana runtime checks 的"按宿主版本条件化"思路（[runtime checks](https://grafana.com/developers/plugin-tools/how-to-guides/runtime-checks)）。
7. **PyInstaller 两选一**：(a) 插件**不进包**，以源码目录/zip 放在用户数据目录启动时加载（推荐，绕开元数据坑）；(b) 进包则显式携带 distribution 元数据（[pyinstaller #8325](https://github.com/orgs/pyinstaller/discussions/8325)）。

**最小注册骨架（【建议】，示意代码，非实测）**

```python
# workbench/plugin_loader.py
from importlib.metadata import entry_points, version
from packaging.specifiers import SpecifierSet

GROUP = "akso.plugins"          # 插件在 pyproject.toml 的 [project.entry-points."akso.plugins"] 声明
CORE = "akso-core"
REQUIRES = SpecifierSet(">=1.0,<2")

def load_plugins(app, ctx) -> list[str]:
    loaded: list[str] = []
    core_ver = version(CORE)
    for ep in entry_points(group=GROUP):
        declared = [r for r in (ep.dist.requires or []) if r.startswith(CORE)]  # 插件元数据里的依赖声明
        if declared and not REQUIRES.contains(core_ver):
            logging.warning("跳过插件 %s：核心版本 %s 不满足 %s", ep.name, core_ver, declared)
            continue
        try:
            mod = ep.load()
            if hasattr(mod, "register"):
                mod.register(app, ctx)        # 工厂式注入：宿主把 app 与上下文交给插件
            elif hasattr(mod, "router"):
                app.include_router(mod.router)  # 与现有 api/__init__.py:59-65 的语义一致
            loaded.append(ep.name)
        except Exception:
            logging.warning("插件 %s 装配失败，已跳过", ep.name, exc_info=True)
    return loaded
```

要点：**宿主拥有装配权**（插件只交 `router` 或 `register` 工厂）；**版本不满足时静默降级而非崩溃**；**所有插件失败都被宿主隔离**。这三条与你会话中已存在的 `_optional_routers` 设计意图一致（`workbench/api/__init__.py:34-65`）。

## A5 单人/小团队维护"上游核心 + 下游插件"的真实成本


**【事实】**
- Fowler《MonolithFirst》：先用单体、等模块边界在真实使用中被验证稳定后再拆，是主流经验（[martinfowler.com](https://martinfowler.com/bliki/MonolithFirst.html)；中文版 [InfoQ](https://www.infoq.cn/article/2015/06/ThoughtWorks-MonolithFirst)）。
- Sandi Metz《The Wrong Abstraction》：**错误的抽象比重复代码更贵**，因为后续所有改动都被迫穿过错误边界（[sandimetz.com](https://sandimetz.com/blog/2016/1/20/the-wrong-abstraction)）。
- 【事实】插件生态的长期债往往来自"API 表面一旦公开就收不回"：Jenkins 到 2015 年才补弃用策略；Grafana 至今仍在讨论如何降低插件 API 破坏性变更；VS Code 用 proposed API 流程来拖延公开承诺（出处同 A2）。
- 【事实】国内工程界关于"过度设计"的同类经验总结：[《谈谈过度设计：因噎废食的陷阱》](https://developer.aliyun.com/article/1092076)、[When Should You Invest in Extensibility?](https://community.beach.io/t/when-should-you-invest-in-extensibility/132)。

**【建议】给你的取舍**
- 值得做：仓库/包的物理边界、单向依赖、版本号、接口抽象（4 个抽象基类/Protocol 足够）、CI 边界检查。这些是**低成本、且同时服务技术解耦与权利边界**（见 B9）。
- 不值得做：自动发现（除非你确实要发第三方插件）、进程沙箱、插件市场/签名、插件运行时热插拔、面向第三方的稳定 API 承诺。
- 【推测】以 11.4k 行、单一业务方、单人维护的现状，**过早引入通用插件协议会让每次业务需求变更都要改两处（core 的扩展点 + 插件实现）**，这正是 Metz 所描述的"错误抽象税"；你现在 10+4 处耦合的规模，用"目录/包边界 + 单向依赖"就能拿到 80% 收益。

## A6 演进路线（【建议】，三阶段，可逐段回滚）

| 阶段 | 动作 | 验收标准 | 回滚成本 |
|---|---|---|---|
| 1. 边界（1–2 天，零行为变更） | 目录/包边界标注 + CI 黑名单（core 内禁止出现平台名/端点/公司术语）+ 建立"能力来源说明"文档 | CI 通过且无跨边界 import | 删规则文件即可 |
| 2. 清单化（3–5 天） | 把 `registrations` 元组（`api/__init__.py:48`）与 `BUILTIN_MODULES`（`services/modules.py:23`）统一为读 `plugins/*.json` 清单——即把你**已有的 `adapters/*.json` 机制**推广到路由与模块 | 增删插件只改 JSON、不改 core 代码；单个插件损坏不影响启动 | 保留原元组作 fallback |
| 3. 独立包/独立版本（可选） | `akso-core` 独立仓库与版本号；插件用 `pyproject` 声明依赖区间；**只有确实要对外分发 core 时**才启用 entry points 自动发现 | 两个包可装配出同一应用；PyInstaller sidecar + NSIS 产物体积与启动时间不退化 | 回到 monorepo + path 依赖 |

【建议】不要跳过阶段 1 直接做阶段 3。阶段 1 的收益（技术单向依赖 + 权利边界可举证）与阶段 3 的收益（可分发性）**不同源**，而阶段 1 的成本约为阶段 3 的十分之一。

---

# B. 权利边界（事实梳理与风险提示，非法律意见）

## B6 中国法下的默认归属规则（条款要点）

| 条款 | 要点 | 出处 |
|---|---|---|
| 《著作权法》(2020 修正) 第 18 条 **职务作品** | 自然人为完成单位工作任务创作的作品：**一般著作权归作者**，单位在业务范围内有优先使用权；作品完成 **2 年内**未经单位同意，作者不得许可第三人以与单位相同方式使用。但第二款所列三类（含"主要是利用单位的物质技术条件创作、并由单位承担责任的……**计算机软件**等职务作品"，以及合同约定归单位的情形）**作者仅保留署名权，其他著作权归单位** | [国家版权局 著作权法全文](https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html)、[修改前后对照表](http://www.iprdaily.cn/news_26255.html)、[职务作品归属解读](https://www.js.gov.cn/art/2024/10/11/art_90838_6454.html) |
| 《著作权法》第 19 条 **委托作品** | 受委托创作的作品，归属**由合同约定**；**未约定或未订合同 → 著作权属于受托人** | 同上（[著作权法全文](https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html)） |
| 《计算机软件保护条例》第 11 条 **委托开发软件** | 归属由**书面合同**约定；**无书面合同或未明确约定 → 著作权由受托人享有** | [国务院公报·计算机软件保护条例](https://www.gov.cn/gongbao/content/2011/content_1860736.htm)、[法信 2013 修订本](https://www.faxin.cn/v2/flfg/gjfl/content.html?gid=A190391&nid=8916) |
| 《计算机软件保护条例》第 13 条 **职务开发软件** | **在法人/其他组织中任职期间**开发的软件，符合三情形之一者著作权归单位：(1) 针对本职工作明确指定的开发目标；(2) 开发是本职工作的可预见或自然结果；(3) **主要使用了单位的资金、专用设备、未公开的专门信息等物质技术条件**开发并由单位承担责任 | 同上 |
| 《著作权法》/软件条例的**约定优先** | 上述默认规则均以"合同没有约定"为前提；合同约定效力优先 | [关于软件开发合同的知识产权问题](https://www.ciplawyer.cn/articles/132158.html) |

**关键区别（事实）**
- "职务作品/职务开发"的默认归属**指向单位**；"委托作品/委托开发"的默认归属**指向受托人（=外部开发者）**。二者方向相反，**适用哪一条取决于你与公司之间的真实关系定性**（劳动合同/劳务 vs 委托/承揽），而第 13 条的前提是"任职期间"。
- 【事实】软件条例第 13 条第 (3) 项里的"**未公开的专门信息**"是单位最可能援引的抓手；其构成要件（是否"主要使用"、是否由其"承担责任"）需要事实认定。→ 列入 B10 问题清单。
- 【事实】著作权归属与"是否构成商业秘密"是**两条独立路径**：即使著作权归你，平台未公开的端点与语义仍可能被主张为对方的商业秘密（[《反不正当竞争法》2025 年修订本](https://www.cnipa.gov.cn/art/2026/5/20/art_104_206437.html)；注意 2025 年修订后**条文序号已调整，须以现行文本核对**，我不在此断言条号）。

## B7 软件著作权登记的证明力

- 【事实】《计算机软件保护条例》第 7 条：软件著作权人可以向登记机构办理登记，**"软件登记机构发放的登记证明文件是登记事项的初步证明"**（[国务院公报](https://www.gov.cn/gongbao/content/2011/content_1860736.htm)）——即登记**不是确权**。
- 【事实】《最高人民法院关于审理著作权民事纠纷案件适用法律若干问题的解释》（2020 修正）第七条第一款：当事人提供的底稿、原件、合法出版物、**著作权登记证书**、认证机构出具的证明、取得权利的合同等，"**可以作为证据**"（[司解全文（律所转载）](https://www.ciplawyer.cn/articles/146568.html)、[检察日报法规库 第七条](http://zyzk.jcrb.com/link/link.html?type=linkRmfyalk&gid=A296686&tiao=7)；裁判文书中对该条的援引实例见 [维基文库判决书](https://zh.wikisource.org/zh-hans/%E6%9E%97%C3%97%C3%97%E3%80%81%E4%B8%8A%E6%B5%B7%C3%97%C3%97%E5%85%AC%E5%8F%B8%E7%AD%89%E8%91%97%E4%BD%9C%E6%9D%83%E6%9D%83%E5%B1%9E%E3%80%81%E4%BE%B5%E6%9D%83%E7%BA%A0%E7%BA%B7%E6%B0%91%E4%BA%8B%E4%BA%8C%E5%AE%A1%E6%B0%91%E4%BA%8B%E5%88%A4%E5%86%B3%E4%B9%A6)）。
- 【事实】实务文章普遍指出登记证书**只能初步证明权属、可被反证推翻**（[《证书只能初步证明软件著作权权属》](https://china.findlaw.cn/lawyers/article/d570730.html)、[《不可信的软件著作权登记证书》案例讨论](http://www.szloline.com/anlipingxi/2017/0928/3740.html)）。注：这两条来源是律所/媒体文章，**非裁判文书原文**，仅作实务倾向参考。
- 【事实】登记证书形式与内容有过官方调整说明（[版权中心关于调整软件版权登记证书有关事项的解读](http://www.hbsea.org.cn/portal/article/index/id/943/cid/18.html)）。

## B8 开源许可的陷阱（事实 + 风险提示）

**【事实】**
- 许可人**只能授予自己实际拥有的权利**；权利链有瑕疵时，下游依据 MIT 等许可证取得的授权无法对抗真正的权利人。上游知识产权纠纷波及下游使用者，是已被系统研究的现象（案例研究：["Spoiled code? SCO v. Linux: ... implications of upstream intellectual property disputes for software end users"](https://searchworks.stanford.edu/articles/edsfra__edsfra.15402635)）。
- 中国司法对开源协议的抗辩也有先例与讨论："基于开源协议'不洁之手'抗辩首案"（[国浩案例](https://www.grandall.com.cn/guohaoanli/info.aspx?itemid=34078)）、[人民法院报《为"开源"自由拨云见日》](https://www.rmfyb.com/mobile/202503/02/article_966061_1390915017_5914107.html)、开源法律资料库（[开放原子开源法律社区](https://www.openatom.org/law/database)）。
- 宽松许可（MIT/Apache-2.0）对企业与下游的常见风险面（专利、商标、合规审计、许可证传染性误判）已有中文系统梳理（[《开源许可证风险地图》](https://www.sectrend.com.cn/insights-open-source-license-risk-map.html)、[中伦《大模型时代的开源许可证》](https://www.zhonglun.com/research/articles/56280.html)）。

**【风险提示，非结论】**
- 【推测/需律师确认】若被主张你的代码含他人权利或违反保密义务：(a) 你对已发布版本作出的 MIT 授权**不能治愈你自己权利上的瑕疵**；(b) 下游善意使用者可能被真正权利人主张侵权，而你的许可证无法为其提供"权利担保"；(c) 已发布副本的**授权撤回在事实与法律上都很难彻底**，因此"先开源、后谈判"的顺序会显著抬高你的敞口。
- 【事实/方法边界】clean-room（清洁室）是**工程方法**（信息源隔离、由未接触原实现的人依据书面规格重写），在中国法下**没有法定名称、也没有法定免责效果**；软件侵权判定普遍围绕"**接触 + 实质性相似**"展开，clean-room 的作用主要是削弱"接触"这一要件（[恒都《无代码比对情形下计算机软件著作权侵权的司法认定规则》](https://hengdulaw.com/Category30-1896)；英文语境下的机制讨论：[Marks & Clerk, "Clean room as a service"](https://www.marks-clerk.com/insights/latest-insights/102mp7s-can-ai-legally-clone-open-source-unpacking-clean-room-as-a-service/)、[Lexology 转载](https://www.lexology.com/library/detail.aspx?g=ff325ca4-8ce4-48a8-89b1-aa9db5f7f67c)；抽象-过滤-比较法源的背景材料 [CA v. Altai 相关论文 PDF](http://patentlyo.com/media/docs/2009/08/08-964-awakenip.pdf)）。
- 【推测】clean-room 对"**保密义务/商业秘密**"基本无效：你自己签署过的保密义务不会因为你换了一套实现方式就消失；这一点是逻辑推论，**必须由律师确认**。

## B9 工程上的"权利边界隔离"（可操作清单）

以下均为**【建议】**，目标是让"哪部分属于通用能力、哪部分源于公司专属环境"在**仓库层面可证明、可审计**；它们**不是**法律手段，也不代替 B10 的确认。

1. **仓库与包分层**：`akso-core`（`harness/` + 通用 `services/` + 通用 `api/` + `db.py`/`config.py`）、`akso-egmp`（`services/egmp/` 全部 + `routes_insight/factory/agent` 中的平台部分）、`workbench`（装配 + `desktop/` + 前端）。各自独立 repo、独立版本号、独立 CHANGELOG。
2. **依赖方向单向化的"守卫"**：在 CI 加一条最便宜的规则——`akso-core` 中禁止出现平台名/平台端点/公司术语（正则黑名单），违反即失败。这条规则同时是技术边界和"来源说明"的自动证据。
3. **接口层用抽象声明**：为平台侧能力定义 `Protocol`/ABC（auth / client / writers / queries / 录制回放 / 编排），core 只依赖抽象；`services/egmp/**` 作为实现。这与你现有 `harness/tools.py` 的 `Tool` 注册表天然对齐（端口-适配器思路的公开表述见 [PLUGIN_ARCHITECTURE 指南](https://github.com/MatthewKerns/software-development-best-practices-guide/blob/main/03-clean-architecture/PLUGIN_ARCHITECTURE.md)）。注意：**把你实测出的端点常量和语义留在 `akso-egmp` 内，绝不进 core**。
4. **清单化 + 宿主下发句柄**：把 `adapters/*.json`（`services/modules.py:50-70`）规范化为插件清单（id / version / requires / entry）。插件**不得自行决定数据目录与配置存储位置**，改为由 core 下发路径与存储句柄——这一步正好消掉你 4 处耦合中的 `services.paths` 与 `services.storage` 两处（`services.queries` 随 `egmp` 一起搬走，`workbench` 版本号改为 core 的依赖声明）。
5. **许可证与 NOTICE 的组织**：core 单独 `LICENSE`；插件包用独立 `LICENSE`（专有/内部）与 `NOTICE`；若 core 开源，按 Apache 的 LICENSE/NOTICE 组装规范与 REUSE/SPDX 逐文件标注（[Apache licensing-howto](https://infra.apache.org/licensing-howto.html)、[REUSE Specification 3.0](https://reuse.software/spec-3.0/)、[SPDX Annex E](https://raw.githubusercontent.com/spdx/spdx-spec/a05c12a2dd4652b1396fd2659f2cd3ea1f37faba/chapters/using-SPDX-short-identifiers-in-source-files.md)）。
6. **来源留痕**：在 core 仓库维护一份"能力来源说明"，把功能分成三档：(a) 仅凭平台公开文档可得；(b) 依赖实测得到的未公开信息（→ 全部划入 `akso-egmp`）；(c) 你自己原创的通用机制。Git 历史与独立仓库时间线本身是可用的证据材料（司法解释把"底稿、原件、取得权利的合同"列为证据，见 B7；**如何使用举证策略属律师判断**）。
7. **明确不做**：不在 core 放平台术语/端点；不做插件沙箱与市场；不承诺面向第三方的稳定 API；不把"分离完成"当成"权利已经干净"。

## B10 需要与律师确认的问题清单（本报告不给结论）

1. 你与该公司的**全部书面文件**（合同、订单、补充协议、邮件/IM 确认）中，关于"开发成果知识产权归属""通用工具/框架/既有技术保留""保密""竞业"的条款原文是什么？有无"乙方保留其通用工具与框架权利"的例外？
2. 双方关系的**真实定性**：委托/承揽服务，还是构成劳务/事实劳动关系？这决定适用《著作权法》第 18/19 条还是《软件保护条例》第 13 条（任职期间）。
3. 通用核心代码是否"**主要使用了对方的资金、专用设备、未公开的专门信息**"？开发是否占用其工作时间/设备/账号？"未公开的专门信息"包括哪些（端点、字段语义、流程规则）？
4. `services/egmp/` 中的端点常量与语义是否构成对方**商业秘密**（秘密性/价值性/保密措施三要件）？你签过 NDA 吗，期限与范围如何？
5. 平台的**服务条款/用户协议**是否禁止自动化爬取、录制回放、批量写入？是否存在受保护的技术措施（可能牵涉规避技术措施的专门规定）？我方是否需要书面授权或白名单？
6. 若计划开源 core：是否需要**先做权利处置**（取得书面确认/放弃主张/明确许可范围）？"先确权后发布"与"先发布后协商"在责任与谈判地位上的差异？
7. 分发方式（Electron + electron-updater 从 GitHub Releases 自动更新、NSIS 安装包）是否会被认定为"向不特定第三方提供"，从而改变责任性质？
8. 现有/计划中的**软件著作权登记**登记在谁名下、登记时提交的源代码范围是否包含通用核心？登记能否被对方用于对你主张权利？你是否应对 core 单独登记？
9. 你的**主体身份与适用法**：个人 vs 境外/境内公司主体，涉外委托开发的准据法与管辖约定是什么？
10. 是否需要（并如何）为通用核心建立独立的**在先使用/独立开发证据链**（时间戳、独立仓库、第三方存证）？

---

## 附：本报告未覆盖 / 查不到公开出处的事项

- 没有找到权威的"单人项目插件化负面案例"量化研究；A5 的反面证据以 Fowler/Metz 的抽象成本经验与生态维护者（Jenkins/Grafana）的公开讨论替代，属**【事实】的间接支撑**。
- 没有找到中国法下"clean-room 免责"的成文规定或权威裁判要旨，B8 的表述限于"工程方法与"接触+实质性相似"要件的互动"。
- 没有找到《反不正当竞争法》2025 年修订本商业秘密条款的**准确条号**（仅有修订本全文链接），故 B6 未标注条号。
- A4 中"插件配置由宿主统一校验与持久化"的具体实现范式，未找到权威公开参考实现，属**【建议】**。
