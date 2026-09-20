# factory-tuning —— 配置工厂调优用的采集与分析（**不属于产品**）

## 这个目录是干什么的

配置工厂的蓝图 schema **本来就是从监听里得来的**：看人在平台上正常配一遍对象/字段/布局/
生命周期，平台实际发了哪些请求、请求体长什么样，然后照着建模。

现在工厂已经能跑了，但"建出来的东西跟人在平台上手配的到底差在哪"这件事**没有数据支撑**。
这个目录就是为了补上它：**把真实的配置操作录下来 → 分析 → 反推蓝图 schema 的缺口**。

## 为什么单独一个文件夹（而不是做进产品）

用户 2026-09-20 定稿：

> 「监听功能本不属于本项目的功能，只是为了优化而做，请单独起文件夹。」

所以这里的每一样东西**都不进产品**：

| | 产品 | 本目录 |
|---|---|---|
| 工具 | `workbench/plugins/*.py` 的 `ToolSpec` 才会被模型看到 | **不注册任何工具** |
| 界面 | `workbench/static/` 才是产品界面 | 无界面，只有命令行脚本 |
| 打包 | `server.spec` 的 `hiddenimports` | **不进打包**（`build.ps1` 不碰它） |
| 依赖 | 产品代码不许 import 本目录 | 本目录**可以**只读地 import `workbench`（见下） |

一句话：**删掉这个目录，产品一行都不会变。**

## 数据从哪来（用的是现成能力，没有新增采集代码）

平台的请求录制**产品里已经有了**：`workbench/services/egmp/monitor/monitor.py`
（三级降噪：DROP / 裁剪 / 陌生 API 全量保留；每条请求还带最近 5 次页面点击与输入）。

本目录只做两件事：**把它调起来**、**把它收进来分析**。

## 一条命令跑通

```powershell
# 0) 先让工作台跑着（cd desktop; npm start  或  python -m workbench.main）

# 1) 开监听（会在应用内置 Chromium 里打开该账号的平台页，并挂上录制）
.\.venv\Scripts\python.exe factory-tuning\tools\capture.py start --account <账号id>
#    → 打印 log 路径；然后你在那个浏览器里**正常配一遍**（建对象、加字段、拖布局、加状态…）

# 2) 停止并落盘
.\.venv\Scripts\python.exe factory-tuning\tools\capture.py stop --account <账号id>

# 3) 收进本目录（**这一步做脱敏**，见下）
.\.venv\Scripts\python.exe factory-tuning\tools\ingest.py --latest

# 4) 出报告
.\.venv\Scripts\python.exe factory-tuning\analyze\report.py --latest
#    → factory-tuning/reports/<日期>-<会话>.md

# 5) 提交前兜底扫一遍
.\.venv\Scripts\python.exe factory-tuning\tools\scan_secrets.py
```

没录过数据也想先看链路通不通：给第 4 步加 `--fixtures`（用合成样例，**不是真实数据**）。

账号 id 从哪来：产品界面「账号中心」里那一列，或
`capture.py list`，或 `curl.exe -s http://127.0.0.1:18765/api/accounts`。

## 自检（改完本目录请跑一次）

```powershell
.\.venv\Scripts\python.exe factory-tuning\tests\selfcheck.py
```

它**不进仓库的 pytest**（`pyproject.toml` 的 `testpaths = ["tests"]`），所以要手动跑。
不是形式：`redact.py` 写成的头十分钟里就暴露了两个真 bug（脱敏不足 + 过度脱敏），
两个都是靠"跑一遍真数据形状"发现的，而不是靠读代码。

## 脱敏：**在落盘那一刻做，不是提交前补**

⚠️ **这是本目录唯一一条硬规矩。**

平台流量里带着 JWT / 会话令牌 / 口令。这个仓库是要推到 GitHub 的，
原始流量一旦进了 git 历史**很难真正删掉**——那既是安全事故，也直接违反本仓铁律
19「凭据不落明文」与 26「凭据永不入日志」。

所以：`ingest.py` 在**写文件之前**就脱敏，磁盘上**从头到尾没有一份原始流量**。
脱敏后的数据才是 `data/` 里被提交的东西。

| 处理 | 规则 |
|---|---|
| 请求/响应体里的凭据字段 | **键名子串命中**（`token`/`password`/`secret`/`authorization`/`cookie`/`apiKey`/…）→ 值换成 `"***"` |
| 只在整键时才可疑的词 | `signature` / `session` / `auth` / `key` / `encrypted` → **整键命中**才打码（否则 `enableSignatures` 这种真实业务字段会被误伤） |
| 值本身像令牌的 | JWT（`eyJ….….…`）、`Bearer xxx`、带 `=` 填充的 base64 长串、32 位以上连续 hex、`token_urlsafe` 形状 → 整段换成 `"***"` |
| URL 里的查询参数 | 同上按键名处理；**主机名整个丢掉**（只留路径，不记录你用哪个环境） |
| 账号 id / GUID / 本机路径 | 换成稳定假名（`acct-<hash8>` / `id-<hash8>` / `<path>`） |
| 对象编码 / 字段名 / 显示名 / 页面操作文本 | **保留**——那正是要分析的东西 |

**过度脱敏与脱敏不足一样是缺陷**：把 `enableSignatures` 打掉不会"更安全"，
只会让采集到的结构缺一块，分析据此得出"平台不发这个键"的错误结论。
所以 `selfcheck.py` 里 `B` 那一组断言专门守"不该打的一个都不许少"。

兜底：`tools/scan_secrets.py` 可以在提交前扫一遍仓库，扫到令牌形状的串就非零退出。
它进不了 CI（本目录不进 CI），但**改完数据后请手动跑一次**。

另外注意：Monitor 自己会把 `Login` / `RefreshToken` 这类端点整条丢掉
（`monitor.py::QUERY_POST_PATTERNS`），所以口令最密集的那几个请求根本不会被录。

## 目录结构

```
factory-tuning/
├── tools/
│   ├── capture.py       起/停监听（调产品的 /api/monitor/*；不改产品代码）
│   ├── redact.py        脱敏规则（唯一事实源，ingest 与 scan 共用）
│   ├── ingest.py        把 runtime/monitor 的产物脱敏后收进 data/
│   └── scan_secrets.py  提交前扫描兜底
├── analyze/
│   ├── model.py         统一数据模型 + 加载器
│   ├── coverage.py      平台端点覆盖率：**哪些真实用到的端点我们没建模**
│   ├── shapes.py        真实请求体的键 vs 蓝图模型 → schema 缺口
│   └── report.py        生成 reports/<会话>.md
├── data/                脱敏后的采集数据（**提交**）
├── reports/             分析报告（**提交**）
└── tests/fixtures/      合成样例（**不是真实数据**，用来证明分析链路能跑）
```

## 刻意不做的事

- **不新增任何采集代码**：用户 2026-09-20 定稿"只用内置 Chromium（现成能力，不改任何代码）"，
  所以不写浏览器扩展、不碰用户自己的 Chrome。
- **不录对话内容**：本目录只分析平台侧的 API 与页面操作（用户同一次定稿）。
- **不注册工具、不加界面、不进打包**：它不是一个产品功能。
- **不做"从采集数据自动生成蓝图"**：先有数据与报告，再谈自动化；
  现在做等于在没看过数据的情况下猜 schema。

## 与产品文档的关系

采集与分析产出的**结论**（该补哪个字段、哪个端点没建模、哪个步骤顺序不对）
应当落进 [docs/FACTORY-VERIFICATION-PLAN.md](../docs/FACTORY-VERIFICATION-PLAN.md)
的台账与 [docs/PITFALLS.md](../docs/PITFALLS.md)，而不是只留在这个目录里——
本目录是**取证工具**，不是知识库。
