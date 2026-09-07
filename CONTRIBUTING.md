# 开发协作规范（CONTRIBUTING）

> 目标：两人协作时不互相踩脚。原则先于工具——**先契约后实现，先台账后重写**。

## 1. 环境搭建（新同事 5 分钟）

```powershell
# 前置：Python 3.12+、Git；uv 没有就先装（pip install uv 或 winget install astral-sh.uv）
uv sync --extra dev                 # 严格按 uv.lock 拉齐依赖（含国内镜像，无需自己配源）
uv run playwright install chromium  # 托管浏览器内核（账号库/自动登录功能需要）
powershell -ExecutionPolicy Bypass -File tools\setup.ps1   # 环境自检
uv run pytest                       # 全绿即环境就绪
```

日常一切命令走 `uv run <cmd>`（自动使用锁定环境），**禁止**直接 pip install 后不落 pyproject。

## 2. 依赖变更流程（问题 1 的核心纪律）

1. 改 `pyproject.toml` 的 `dependencies`；
2. `uv lock && uv sync --extra dev`（lockfile 与代码同一提交）；
3. 原则：lockfile 变更永远单独成提交，方便回溯"哪个依赖引入了什么问题"。

> Docker 的取舍：本项目深绑 Windows 本机生态（原仓库只读路径、pywebview 壳、
> %APPDATA% 数据目录），容器化收益低、摩擦大。**双人协作以 uv.lock 为准**；
> 若未来上 CI/Linux 部署再补 Dockerfile（届时 adapters 的 repoPath 走环境变量注入，
> 机制已备好）。

## 3. 代码规约（问题 3 的硬保障）

- **Lint 门禁**：`uvx ruff check .` 必须全绿才能提交（E4/E7/E9/F/I/B 规则集，配置在 pyproject）。
  fork 存量代码同样受约束，无豁免区。
- **类型注解**：新代码必须带完整类型注解 + `from __future__ import annotations`；
  mypy 门禁规划中（先跑通团队节奏再开，避免第一天就把门槛抬太高）。
- **校验**：API 入参必须用 pydantic BaseModel（FastAPI 自动 422）；响应模型
  （response_model）新代码强制、存量渐进补齐。
- **异常**：服务层抛领域错误（`ProcError/ApiError/AccountError/BrowserError/AccountError`），
  路由层翻译为 HTTPException；`raise ... from exc` 保留因果链。
- **文档字符串**：每个模块头部说明职责 + 知识来源（fork 自谁/迁移自谁）——
  这是本仓库最重要的可读性投资，新代码沿用。

## 4. 开发流程

1. **动代码前**先读 `docs/模块契约.md`（接口契约）与 `docs/迁移台账.md`（哪些能重写、哪些只能封装）；
2. 新路由按契约 §1 挂到 `api/__init__.py::_optional_routers` 或 mogul 五路由同级注册；
3. DB 变更只走迁移（`db.py MIGRATIONS` 追加新版本号，不改历史迁移）；
4. 每完成一个模块：更新 `docs/迁移台账.md` 状态 + `CHANGELOG.md`（Unreleased 段）；
5. 提交信息建议 `阶段/模块: 动作`，如 `2B: 自动登录引擎节奏参数对齐上游`。

## 5. 分工边界（防冲突）

| 区域 | 说明 |
|---|---|
| `workbench/api/*` + `workbench/services/*` | 主战场；同一文件避免两人同时改——契约文档里认领 |
| `workbench/harness/*`、`services/{deepseek,retrieval,embedding,chunking,ddkb,dingtalk_sync}.py` | fork 存量，改动最小化，bugfix 需在 CHANGELOG 记录 |
| 四原项目目录 | **冻结**：任何情况下不写。发现上游缺陷 → 记到迁移台账"上游缺陷"节 |
| `docs/模块契约.md` | 改契约 = 全队评审，不允许悄悄改 |

## 6. 验收纪律

- 阶段验收以 `tests/` 机器验收为准（autologin 9 项断言等），不靠肉眼；
- 真机验收（真实 eGMP 平台）只在测试环境账号上做；
- 测试发现的问题：先在台账记根因，再修——不修"看起来好了"。
