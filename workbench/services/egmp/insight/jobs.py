"""洞察任务的**领域逻辑**（不依赖 FastAPI）。

## 为什么单列这一层（2026-09-24）

`plugins/insight.py` 的三个工具要「跑一次洞察 / 列产物 / 读产物」，而这三件事原先都住在
`api/routes_insight.py` 里 —— 于是插件只能 `from ..api import routes_insight` 去调
**HTTP handler**。那违反本仓的依赖方向契约（`plugins → plugin_api`，**永不反向**；
`plugins/__init__.py` 明写"插件不 import `api/` 里的任何东西"），而更实的后果是：

> **路径守卫的真身住在 `api/` 里。** 插件是靠"复用页面那条路"才拿到它的 ——
> 拆掉 API 层会把守卫一起拆掉，而失败形态只是 `hub.failures` 里多一条（**静默**）。

所以分工改成：**逻辑归这里，`api/` 只剩 HTTP 形状**（路由、状态码、响应类型）。
页面链路与工具链路调的是**同一份实现**，守卫因此不可能只对一边生效。

## 错误口径（这是本模块最重要的形状约定）

这里抛 `InsightError`，**不抛 `HTTPException`** —— 后者是传输层的类型，让服务层依赖
FastAPI 正是本次要拆掉的东西。翻译由各自那侧做：

    api/     InsightError → HTTPException(exc.status, str(exc))
    plugins/ InsightError → ValueError（工具层规则 11：异常 = 观测）

所以 `InsightError.status` 是"**给传输层的建议状态码**"，不是领域概念 —— 它存在只是
为了别让 `api/` 把 404 与 400 压成同一个码（那会改掉已发布的接口行为）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .... import config, db
from ... import accounts as accounts_svc
from ...storage import now_ms
from ..client import EgmpClient
from ..kernel.urls import normalize_base_url
from . import run_login, run_spider
from . import workspace as workspace_mod

#: 可**内联**返回的产物后缀 → media type（其余走附件下载）。
#: `.drawio` 由前端取文本后自己画成 SVG（离线自足，不连官方 viewer）——
#: 所以它必须是内联可读的，而不是"只能下载"。
INLINE_SUFFIXES = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".drawio": "application/xml",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".csv": "text/csv",
}

#: 图片走**内联**二进制（不能让 `PlainTextResponse` 把 PNG 按 UTF-8 读坏）。
#: 只给浏览器 `<img>` 用，不参与 `read_artifact_text`。
INLINE_IMAGE_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

#: 内联返回的正文上限（超出截断并告知；下载不受限）。
MAX_INLINE_CHARS = 2_000_000


class InsightError(Exception):
    """洞察任务的领域错误。`status` 是**给传输层的建议状态码**（见模块文档串）。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class InsightRequest(BaseModel):
    command: str = Field(..., description="login | spider")
    account_id: str = Field(..., description="统一账号库账号 id（凭证来源）")
    objects: str = Field("", description="spider：逗号分隔对象编码")
    env_id: str = Field("", description="token 缓存文件名后缀（可选）")


def _split_codes(raw: str) -> list[str]:
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


def _credentials(account_id: str) -> tuple[dict, str, str, str]:
    account = accounts_svc.get_account(account_id)
    if not account:
        raise InsightError(f"账号不存在：{account_id}")
    base_url = normalize_base_url(account.get("env_base_url") or "")
    if not base_url:
        raise InsightError(f"账号 {account['username']} 所属平台环境未配置 baseUrl")
    return account, base_url, account["username"], accounts_svc.reveal_password(account_id)


def _environment_of(account: dict, base_url: str) -> dict:
    """写进 manifest 的环境信息（不含任何凭据）。"""
    return {
        "envId": account.get("env_id"),
        "envName": account.get("env_name"),
        "baseUrl": base_url,
    }


def _workspace_paths(target: Path, workdir: Path) -> list[str]:
    """产物落点**如实回报**（供 `insight_runs.workspace_dir` 回填）。

    只有真的落在用户工作区时才算工作区；写进任务兜底目录 `runtime/insight/<job_id>/`
    时返回空数组——按产物接口的约定，那时面板应当回落到 `artifact_dir` 去列文件。

    历史缺陷（0.3.8 真机实测）：路由**自己**定落点（先定落点再爬 + 不可写时回退），
    却只把落点当 `output_dir` 交给 spider；而门面 `run_spider` 只在它**自己**解析
    `workspace_root` 时才填 `workspaces` → 走 `output_dir` 这条路它一律返回空 →
    `workspace_dir` 恒空 → 产物面板回落到 runtime 任务目录 →
    **用户在工作区里明明有 8 件产物，界面上「只有一个 proc.log」**。
    """
    return [str(target)] if Path(target) != Path(workdir) else []


def _status_from_coverage(result: dict) -> str:
    """覆盖度三态 → 任务状态：complete→succeeded、partial→partial、degraded→failed。

    取代原先「取 result['ok'] 布尔」的写法——那样部分成功也会被记成 succeeded。
    """
    coverage = (result or {}).get("coverage") or {}
    status = coverage.get("status")
    if status in {"complete", "partial", "degraded"}:
        return {"complete": "succeeded", "partial": "partial", "degraded": "failed"}[status]
    return "succeeded" if result.get("ok") else "failed"


def run_job(req: InsightRequest, workdir: Path) -> dict:
    """同步执行（原生 Python，无子进程）。"""
    log_path = workdir / "proc.log"

    def on_log(line: str) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # 先落一行「要做什么」：**失败也要留现场**。历史缺陷——`on_log` 只在 login 成功之后
    # 才被调用，于是任何 login 失败（地址错、网关 405/502、超时）都会让 proc.log
    # 根本不存在，界面只剩一句无从下手的「无法连通」。
    on_log(f"分析命令：{req.command}" + (f"（对象：{req.objects}）" if req.objects else ""))

    try:
        account, base_url, username, password = _credentials(req.account_id)
        environment = _environment_of(account, base_url)
        on_log(f"平台环境：{base_url}")
        token = None
        from ..client import login as _login
        token = _login(base_url, username, password, req.env_id)

        def run_spider_into(codes: list[str]) -> dict:
            """spider 落点：用户可配工作区根优先，不可用则退回任务目录。

            先定落点再爬——不做事后重试，否则平台调用会被重放一遍。
            """
            try:
                target = workspace_mod.run_dir(",".join(codes))
                on_log(f"产物落点：{target}")
            except OSError as exc:
                on_log(f"工作区不可写（{exc}），产物改落任务目录")
                target = workdir
            outcome = run_spider(client, codes, output_dir=target,
                                 on_log=on_log, environment=environment)
            # **落点如实回报**：这里自己定的落点（先定落点再爬 + 不可写时回退），所以
            # `workspaces` 也得自己填——门面只在它**自己**解析 workspace_root 时才填，
            # 走 output_dir 这条路它一律返回空（见 `_workspace_paths` 的历史缺陷）。
            outcome["workspaces"] = _workspace_paths(target, workdir)
            # 任务日志留在任务目录（同一 job 的既有位置不变），另在工作区留一份副本，
            # 好让产物面板能在同一处列出「日志 + 产物」。
            if target != workdir and log_path.is_file():
                try:
                    (target / "proc.log").write_bytes(log_path.read_bytes())
                except OSError:
                    pass
            return outcome

        started = now_ms()
        with EgmpClient(base_url, token) as client:
            if req.command == "login":
                result = run_login(client, base_url, username, password, req.env_id)
                on_log(f"登录成功：{base_url}")
            elif req.command == "spider":
                codes = _split_codes(req.objects)
                if not codes:
                    raise InsightError("spider 需要 objects（对象编码，逗号分隔）")
                result = run_spider_into(codes)
            else:
                raise InsightError(f"未知命令：{req.command}（login/spider）")
    except Exception as exc:  # noqa: BLE001 —— 失败必须留痕后再抛
        on_log(f"执行失败：{type(exc).__name__}: {exc}")
        raise

    status = _status_from_coverage(result)
    coverage = result.get("coverage") or {}
    return {
        "status": status,
        "exit_code": 0 if status == "succeeded" else 1,
        "duration_ms": now_ms() - started,
        "result": {k: v for k, v in result.items() if k != "ok"},
        "workdir": str(workdir),
        "workspaces": result.get("workspaces") or [],
        "coverage_status": coverage.get("status", ""),
    }


def start_job(req: InsightRequest) -> tuple[str, Path]:
    job_id = uuid.uuid4().hex[:12]
    workdir = config.RUNTIME_DIR / "insight" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    db.execute(
        "INSERT INTO insight_runs (id, module, command, objects, env_id, status, log_path, "
        "artifact_dir, created_at, updated_at) VALUES (?, 'insight', ?, ?, ?, 'running', ?, ?, ?, ?)",
        (job_id, req.command, req.objects, str(req.env_id or ""), str(workdir / "proc.log"),
         str(workdir), now_ms(), now_ms()),
    )
    return job_id, workdir


def finish_job(job_id: str, status: str, *, workspaces: list[str] | None = None,
               coverage_status: str = "") -> None:
    """落终态。工作区目录可能多个（每对象一个），用换行分隔存一列。"""
    db.execute(
        "UPDATE insight_runs SET status = ?, updated_at = ?, workspace_dir = ?, "
        "coverage_status = ? WHERE id = ?",
        (status, now_ms(), "\n".join(workspaces or []), coverage_status or "", job_id),
    )


def run_sync(req: InsightRequest) -> dict:
    """开一个任务并**同步跑完**（返回 `{job_id, ...outcome}`）。

    这是"页面 `POST /api/insight/run`"与"工具 `insight_run`"共用的那一条。
    异常语义：`InsightError` 是**可预期的拒绝**（参数/账号问题）；
    其余异常原样抛出，由调用方决定是 502 还是观测。
    """
    job_id, workdir = start_job(req)
    try:
        outcome = run_job(req, workdir)
    except Exception:
        finish_job(job_id, "failed")
        raise
    finish_job(job_id, outcome["status"], workspaces=outcome.get("workspaces"),
               coverage_status=outcome.get("coverage_status", ""))
    return {"job_id": job_id, **outcome}


def _workspace_dirs(row: Any) -> list[Path]:
    """从行里取出工作区目录列表（兼容未跑过迁移 14 语义的历史行）。"""
    try:
        raw = row["workspace_dir"] or ""
    except (KeyError, IndexError, TypeError):
        return []
    return [Path(p) for p in str(raw).split("\n") if p.strip()]


def _artifact_dirs(job_id: str) -> tuple[Any, list[Path]]:
    """取任务行与它的产物目录列表；任务不存在时抛 `InsightError(404)`。

    产物目录优先取工作区列（历史任务可能有），否则回落到 runtime 的 artifact_dir
    （现存命令 login/spider 都走这条）。
    """
    row = db.query_one("SELECT * FROM insight_runs WHERE id = ?", (job_id,))
    if not row:
        raise InsightError(f"任务不存在：{job_id}", 404)
    dirs = _workspace_dirs(row)
    if not dirs and row["artifact_dir"]:
        dirs = [Path(row["artifact_dir"])]
    return row, dirs


def job_artifacts(job_id: str) -> dict:
    """产物清单（页面与工具共用）。"""
    row, dirs = _artifact_dirs(job_id)
    files: list[dict] = []
    for base in dirs:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                files.append({"name": str(path.relative_to(base)),
                              "size": path.stat().st_size, "suffix": path.suffix.lower(),
                              "workspace": base.name})
    return {"job_id": job_id, "status": row["status"], "files": files,
            "coverageStatus": (row["coverage_status"] if "coverage_status" in row.keys() else ""),
            "workspaces": [str(d) for d in dirs]}


def resolve_artifact(job_id: str, name: str) -> Path:
    """把 `(job_id, name)` 解析成一个**经过路径守卫**的真实文件路径。

    ## 这条守卫为什么必须住在这一层

    它此前只在 `api/routes_insight.py` 里，插件是靠"复用页面那条路"才用上它的 ——
    而一旦有人让插件自己拼路径，就等于**给模型开一个任意文件读**。搬到领域层之后，
    两条链路调的是同一个函数，"绕过守卫"不再是一种可能的写法。

    ## 守卫本身的两条硬要求（改之前先读）

    1. **必须用 `Path.is_relative_to()`，不能用字符串前缀比较**：
       `.../insight/abc` 在字符串上"包含" `.../insight/abc_evil`（AGENTS.md 规则 22）。
    2. **空目录要单独拒**：目录列表为空时 `"."` 会解析成 CWD，守位退化成"当前目录下任意文件"。
    """
    _row, dirs = _artifact_dirs(job_id)
    if not dirs:
        raise InsightError(f"任务无可读产物目录：{job_id}", 404)

    for base in dirs:
        root = base.resolve()
        candidate = (root / name).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    raise InsightError(f"产物不存在：{name}", 404)


def read_artifact_text(path: Path) -> str:
    """读内联正文（超长截断并**如实告知**，不静默丢）。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_INLINE_CHARS:
        text = text[:MAX_INLINE_CHARS] + "\n…（超长截断，请下载查看）"
    return text
