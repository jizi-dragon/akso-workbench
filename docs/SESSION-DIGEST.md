# 会话状态压缩摘要（供后续上下文快速恢复 · 更新于 2026-09-07）

## 项目定位
- 仓库：`D:\ai_assistant\akso-mogul` → https://github.com/jizi-dragon/akso-mogul（main，本地代理失效时用 `git -c http.proxy= push`）
- Akso Workbench：四项目融合（akso-cc 洞察 / mogul_simulator 知识工作台 / akso-auto 配置工厂 / quick-login 账号+自动登录），**运行时零依赖原仓库与 Node**（全部内化到 Python）
- 技术栈：Python 3.12+ / FastAPI / SQLite(迁移1-11) / Playwright / Fernet / networkx / pywebview；uv 管理（清华镜像已配）

## 版本规则（用户定稿·方案A）· 当前 v0.1.1
- 真源 `pyproject.toml` version；`tools/bump.py push|build|show` 双写 `workbench/__init__`
- push=PATCH+1；build=MINOR+1且PATCH重置1（0.0.2→0.1.1→0.2.1）；构建后推送从新基线续增

## 架构速查
- `workbench/api/routes_*`：conversations/settings/chat（fork mogul，SSE 对话）、modules（体检）、insight/factory（egmp 原生）、accounts（盒子/池/备份/导入）、browser（open默认headful/focus/close/forget/saved）、agent（工具+审计表）
- `services/browser_pool.py`：**专职工作线程**（Playwright sync 单线程单实例约束，全部操作走 Future 队列）；双模式浏览器（headful 默认/headless）；storage_state 持久化（免密直达）；自愈 heal_count≤2；bring_to_front 聚焦；手动关窗检测
- `services/autologin.py`：节奏门控引擎（30s/4次/2错误让位/800ms轮询/100ms去抖/500ms回读/3500ms观察/srcdoc iframe/cross-realm setValue）+ TokenCapture(JWT)
- `services/accounts.py`：Fernet（密钥存 settings 表）、环境/账号 CRUD、盒子（rename 空目标=并入默认、create 空盒）、分配池（config/monitor，`,包夹 LIKE` 匹配）、备份导出导入（fernetKey 随文件，同站同名去重）
- `services/egmp/`：client（信封 code==0 / paged_post / 55min token 缓存）、insight（crawler发现链/understand三层报告/spider五步+networkx）、writers（blueprint pydantic两层校验+规范化+审阅件 / idempotency / objects/fields/picklists/lifecycle/workflows a-i管道 / layouts 全量替换语义 / menus / endpoints 实证常量）、orchestrate（拓扑排序+checkpoint）、generate（DeepSeek 蓝图生成融合）、monitor（录制三级降噪/查询层/参数推断/解读+reproduce-plan API_MAP，DANGEROUS 不回放）
- 前端：`static/pages/{insight,factory,accounts}.html` + `modules.css v3`（Akso 蓝白令牌）+ `wheel.css`（Alt+Q 半透明轮盘 v3.9 几何：R240/R118/2°缝/Hub切盒/数字键）
- 账号中心（合并原两页）：盒子过滤芯片+内联管理行 → 卡片墙（会话+凭据+池芯片）→ 分配池 → 环境/新增 → 备份/导入
- 桌面壳 `shell/shell.py`：源码态=pythonw+uvicorn 子进程；打包态=自身 exe `--server` 重入；finally kill_tree（taskkill /T /F 防孤儿 chromium）

## 已下线（源码已删，勿恢复）
知识库/钉钉同步全链路（routes_knowledge/sync、chunking/embedding/retrieval/ddkb/dingtalk_sync、harness 知识工具、前端 knowledge 视图）；DB 迁移 1-6 按不可变纪律保留

## 打包分发
- 一键：`powershell -File tools\build.ps1`（bump build → release commit+push → uv sync → PyInstaller onedir(~863MB 含 chromium 本体) → ISCC → `dist/installer/AksoWorkbench-<ver>-setup.exe`）
- shell.spec：collect_all playwright/webview/clr_loader/pythonnet + chromium 本体 + static/adapters datas；config 用 `_MEIPASS` 适配（PROJECT_ROOT/ADAPTERS_DIR）；browser_pool frozen 态设 PLAYWRIGHT_BROWSERS_PATH
- Inno Setup 6.7.3 已装（ISCC 在 Program Files (x86)）；简中 isl 在 tools/（官方已移出默认分发）

## 验收基线
54 项 pytest 全绿；ruff 全绿；真机通过：原生 login/understand(1.4MB模型)/spider(networkx)、双账号并行 headful 2/2 在线、免密直达（引擎 idle）、自愈 heal_count=1、打包态同上

## 环境坑清单（高价值，勿重踩）
1. Playwright sync 单线程单实例 → 所有操作必须走 BrowserPool 专职线程队列；测试勿另起 chromium 夹具
2. ES 模块严格模式重复函数声明 = 整模块加载失败（页面停在静态"加载中"）——accounts.js isOnline 事故
3. PS5.1：.ps1 必须 UTF-8 **BOM**；不支持 `??`；heredoc `<<` 不可用；中文经命令行参数传 curl 会乱码（写文件 + `--data-binary @file`）
4. pydantic BaseModel 默认忽略多余字段（AccountPatch 漏 box 字段导致静默失效）
5. build.ps1 禁用 `$ErrorActionPreference=Stop`（PS5.1 把 git/uv 的 stderr 进度当终止错误）
6. 关窗回收：kill_tree 防孤儿 chromium；同线程禁止第二个 sync_playwright
7. 网络：GitHub/PyPI 间歇抖动 → 重试循环；uv 清华镜像已配；**全局 git 代理 127.0.0.1:7890 常失效**，push 用 `git -c http.proxy=`
8. 自愈计数成功时不可清零（掩盖已自愈事实）
9. 桌面壳只开一个实例（第二实例端口冲突只出窗口不连服务）
10. Inno Setup 注释符是 `;`；简中 isl 需自带

## 待办/可选（未做）
- P2：Monitor 接入账号卡片（"开始监听录制"，复用可见会话）
- egmp writers/monitor 真机首跑验证（create 写配置需测试环境授权）
- pre-push 钩子自动 bump（可选）；安装器静默装需 UAC（未落盘验证）
- 若需"关主窗后浏览器会话常驻"：服务与壳解耦为独立进程（小升级）

## 运维速记
- 启动：`uv run --extra desktop python shell\shell.py` 或双击 `tools\start-desktop.vbs`
- 构建：`powershell -File tools\build.ps1`；测试：`uv run pytest`；lint：`uvx ruff check .`
- 账号中心真机账号：dev-admin / dev-user（标准验证 + example-config 环境）
