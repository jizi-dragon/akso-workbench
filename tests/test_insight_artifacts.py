r"""洞察产物读取的**路径守卫** —— 页面链路与工具链路共用同一个函数。

## 为什么必须单独写这一份

这条守卫此前住在 `api/routes_insight.py` 里，于是：

1. **只有 HTTP 那条路能走到它**；`plugins/insight.py` 的三个工具只能去调 HTTP handler
   才用得上（违反 AGENTS.md 规则 7），插件里甚至长出了
   `getattr(response, "body", b"")` 这种"绕着 FileResponse 走"的写法。
2. **它从来没有被判据覆盖过。** 本仓测试里唯一与"路径越界"有关的是
   `tests/test_paths.py` —— 守的是**另一个**东西（工作区输出目录），不是产物读取。

2026-09-24 把它下沉到 `services/egmp/insight/jobs.py::resolve_artifact`，两条链路
第一次调的是同一个函数。**迁一条安全守卫必须补判据**：不然"搬过去了"与"搬漏了 /
搬坏了"在测试输出上长得一模一样 —— 而它坏掉的表现只是"模型能读任意文件"，
不会有任何一条既有断言变红。

## 两个方向都要验

- **守卫拒绝**：`../` 逃逸、空产物目录（`.` 会解析成 CWD，守位退化成"当前目录任意文件"）、
  任务不存在 —— 三条都是**代码注释里写明的**约束，所以逐条钉。
- **守卫对两条链路都生效**：工具侧抛 `ValueError`（规则 11：异常 = 观测），
  HTTP 侧抛 404。只验一边的话，"某条链路绕过了守卫"是看不见的。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from workbench import db
from workbench.api import routes_insight
from workbench.plugins import insight as plugin
from workbench.services.egmp.insight import jobs


@pytest.fixture()
def ready():
    db.connect()
    return db


def _seed_job(artifact_dir: Path | None, job_id: str = "job-guard-1") -> str:
    """插一条 `insight_runs` 行，把产物目录指向给定位置（`None` = 空前缀，模拟退化场景）。"""
    db.execute("DELETE FROM insight_runs WHERE id = ?", (job_id,))
    db.execute(
        "INSERT INTO insight_runs (id, module, command, objects, env_id, status, log_path, "
        "artifact_dir, workspace_dir, coverage_status, created_at, updated_at) "
        "VALUES (?, 'insight', 'spider', 'x', '', 'succeeded', '', ?, '', '', 0, 0)",
        (job_id, str(artifact_dir) if artifact_dir is not None else ""),
    )
    return job_id


# ---------------------------------------------------------------- 守卫本身


def test_a_file_inside_the_artifact_dir_resolves(ready, tmp_path: Path) -> None:
    """正样本：目录内的文件能解析出来（守卫不是"一律拒绝"）。"""
    (tmp_path / "spider-network.md").write_text("# 网络\n", encoding="utf-8")
    job_id = _seed_job(tmp_path)

    target = jobs.resolve_artifact(job_id, "spider-network.md")

    assert target.resolve() == (tmp_path / "spider-network.md").resolve()
    assert jobs.read_artifact_text(target).startswith("# 网络")


def test_images_are_inline_binary_not_decoded_as_text(ready, tmp_path: Path) -> None:
    """图片必须走 FileResponse + inline；按文本读会把 PNG 读坏（§12.4）。"""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    (tmp_path / "shot.png").write_bytes(png)
    job_id = _seed_job(tmp_path)

    response = routes_insight.read_artifact(job_id, "shot.png")

    assert response.media_type == "image/png"
    assert "inline" in response.headers["content-disposition"]
    assert response.path == (tmp_path / "shot.png").resolve()


@pytest.mark.parametrize("name", [
    "../outside.txt",
    "../../workbench/config.py",
    "..\\outside.txt",
    "a/../../outside.txt",
])
def test_escaping_the_artifact_dir_is_refused(ready, tmp_path: Path, name: str) -> None:
    """逃逸一律拒（规则 22：必须用 `is_relative_to`，字符串前缀判包含会漏 `abc_evil`）。"""
    outside = tmp_path / "outside.txt"
    outside.write_text("不该被读到", encoding="utf-8")
    base = tmp_path / "artifacts"
    base.mkdir()
    job_id = _seed_job(base)

    with pytest.raises(jobs.InsightError) as exc:
        jobs.resolve_artifact(job_id, name)

    assert exc.value.status == 404
    assert "产物不存在" in str(exc.value), f"拒绝理由不对：{exc.value}"


def test_sibling_directory_with_a_shared_prefix_is_refused(ready, tmp_path: Path) -> None:
    """★ 回归"字符串前缀比较"这个具体错误：`.../insight/abc` **字符串上包含** `.../insight/abc_evil`。

    所以真正要防的不止 `../`：兄弟目录当名字面量出现时，字符串前缀判断会放行。
    """
    base = tmp_path / "abc"
    evil = tmp_path / "abc_evil"
    base.mkdir()
    evil.mkdir()
    (evil / "secret.md").write_text("兄弟目录的内容", encoding="utf-8")
    job_id = _seed_job(base)

    with pytest.raises(jobs.InsightError):
        # `..` 出去再进兄弟目录 —— 两条判据（is_relative_to + 真实存在）都要拦住它
        jobs.resolve_artifact(job_id, "../abc_evil/secret.md")


def test_empty_artifact_dir_is_refused(ready, tmp_path: Path) -> None:
    """★ 空产物目录必须**单独**拒：否则 `.` 会解析成 CWD，守位退化成"当前目录下任意文件"。

    这是代码注释里写明的退化路径，也是三条拒绝里最容易被"顺手优化"掉的一条
    （看到 `if not dirs: raise` 会觉得多余）。
    """
    job_id = _seed_job(None)  # artifact_dir 为空串 ⇒ dirs 为空

    with pytest.raises(jobs.InsightError) as exc:
        jobs.resolve_artifact(job_id, "anything.md")

    assert exc.value.status == 404
    assert "无可读产物目录" in str(exc.value)


def test_unknown_job_is_refused(ready) -> None:
    with pytest.raises(jobs.InsightError) as exc:
        jobs.resolve_artifact("no-such-job", "x.md")
    assert exc.value.status == 404


# ---------------------------------------------------------------- 两条链路都过守卫


def test_the_tool_path_is_guarded_too(ready, tmp_path: Path) -> None:
    r"""★ 工具链路（`plugins/insight.py` 的 `insight_read_artifact`）被守卫拦住，
    且**错误形态是工具层的词汇**：`ValueError`（规则 11：异常 = 观测，让模型能自我纠正）。

    ## 反证实测（这条断言真的分得出新旧）

    重构前 `plugin.read_artifact` 调的是 `routes_insight.read_artifact`（HTTP handler），
    于是拒绝时抛的是 **`HTTPException`** —— 一个**传输层类型泄漏进工具层**。
    实测：回退 `plugins/insight.py` + `routes_insight.py` 后重跑，
    本条红在 `DID NOT RAISE ValueError`（抛出来的是 HTTPException）。

    ⚠️ 一个容易想当然的点：**"守卫有没有生效"与"错误是什么类型"是两件事**。
    旧代码的守卫**也是生效的**（同一份 `is_relative_to` 检查），所以只断言
    "抛了异常"的话本用例在新旧两边都绿、什么都证明不了。
    真正把它变成判据的是**异常类型** —— 它恰好是"HTTP 形状有没有渗进工具层"的度量。
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("不该被读到", encoding="utf-8")
    base = tmp_path / "artifacts"
    base.mkdir()
    job_id = _seed_job(base)

    args = plugin.ReadArtifactArgs(job_id=job_id, name="../outside.txt")
    with pytest.raises(ValueError) as exc:
        asyncio.run(plugin.read_artifact(args, None))

    assert "产物不存在" in str(exc.value)
    assert not isinstance(exc.value, __import__("fastapi").HTTPException), (
        "工具层不该看到 HTTPException —— 那是传输层类型泄漏进领域/工具层的信号"
    )


def test_the_tool_path_returns_an_observation_shape_on_success(ready, tmp_path: Path) -> None:
    """工具成功时返回的形状（前端/模型消费的字段）不能因为重构而变。"""
    (tmp_path / "spider-network.md").write_text("# 网络\n内容", encoding="utf-8")
    job_id = _seed_job(tmp_path)

    args = plugin.ReadArtifactArgs(job_id=job_id, name="spider-network.md")
    payload = json.loads(asyncio.run(plugin.read_artifact(args, None)))

    assert payload["job_id"] == job_id
    assert payload["name"] == "spider-network.md"
    assert payload["suffix"] == ".md"
    assert payload["chars"] == len(payload["text"]) > 0


def test_the_http_path_is_guarded_too(ready, tmp_path: Path) -> None:
    """HTTP 链路同样被拦住，且**状态码不变**（404）—— 接口行为不许因重构而漂。"""
    from fastapi import HTTPException

    from workbench.api import routes_insight

    outside = tmp_path / "outside.txt"
    outside.write_text("不该被读到", encoding="utf-8")
    base = tmp_path / "artifacts"
    base.mkdir()
    job_id = _seed_job(base)

    with pytest.raises(HTTPException) as exc:
        routes_insight.read_artifact(job_id, "../outside.txt")

    assert exc.value.status_code == 404
