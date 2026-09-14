"""工作区与 manifest 门禁（离线）。

对应 docs/INSIGHT-SPEC.md §4：产物落专用工作区、地址由用户决定、每次运行自证覆盖度。
两条硬要求各自钉死：**不越界**（用户输入的 code 不得写出工作区）与**不谎报**
（`coverage.status` 三态取代原先无条件 `ok:True`）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from workbench.services.egmp.insight import workspace


@pytest.fixture
def db_ready():
    """设置表需要 DB；用完清掉自定义根，避免串到别的测试。"""
    from workbench import db

    db.connect()
    yield
    try:
        workspace.clear_workspace_root()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 根目录


def test_default_root_is_absolute_and_named() -> None:
    root = workspace.default_workspace_root()
    assert root.is_absolute()
    assert root.name == workspace.WORKSPACE_DIR_NAME
    assert root.parent.name == workspace.APP_SUBDIR


def test_workspace_root_falls_back_to_default(db_ready) -> None:
    workspace.clear_workspace_root()
    assert workspace.workspace_root() == workspace.default_workspace_root()


def test_workspace_root_falls_back_without_db() -> None:
    """纯离线调用（没有 DB）也必须能拿到一个根，而不是抛异常。"""
    assert workspace.workspace_root().is_absolute()


def test_set_and_get_workspace_root(tmp_path: Path, db_ready) -> None:
    target = tmp_path / "我的洞察"
    resolved = workspace.set_workspace_root(target)
    assert Path(resolved).is_absolute()
    assert Path(resolved).is_dir(), "设置时应确保目录存在"
    assert workspace.workspace_root() == Path(resolved)


def test_set_rejects_empty(db_ready) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        workspace.set_workspace_root("   ")


def test_set_rejects_relative_path(db_ready) -> None:
    with pytest.raises(ValueError, match="绝对路径"):
        workspace.set_workspace_root("relative/dir")


def test_set_rejects_existing_file(tmp_path: Path, db_ready) -> None:
    a_file = tmp_path / "not-a-dir.txt"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="不是目录"):
        workspace.set_workspace_root(a_file)


def test_clear_returns_to_default(tmp_path: Path, db_ready) -> None:
    workspace.set_workspace_root(tmp_path / "custom")
    workspace.clear_workspace_root()
    assert workspace.workspace_root() == workspace.default_workspace_root()


# ---------------------------------------------------------------- 运行目录


def test_run_dir_layout(tmp_path: Path) -> None:
    at = datetime(2026, 9, 13, 15, 30, 0)
    target = workspace.run_dir("capa_plan__c", root=tmp_path, at=at)
    assert target.is_dir()
    assert target.parent.name == "capa_plan__c"
    assert target.name == "20260913-153000"
    assert target.parent.parent == tmp_path.resolve()


def test_run_dir_sanitizes_dangerous_code(tmp_path: Path) -> None:
    for code in ("../../evil", "C:/Temp/x", "..", "con"):
        target = workspace.run_dir(code, root=tmp_path,
                                   at=datetime(2026, 9, 13, 15, 30, 0))
        assert target.resolve().is_relative_to(tmp_path.resolve()), f"{code!r} 越界：{target}"


def test_run_dir_keeps_chinese_code_readable(tmp_path: Path) -> None:
    target = workspace.run_dir("培训管理__c", root=tmp_path, at=datetime(2026, 9, 13, 15, 30, 0))
    assert target.parent.name == "培训管理__c"


def test_run_dir_is_idempotent_for_same_stamp(tmp_path: Path) -> None:
    at = datetime(2026, 9, 13, 15, 30, 0)
    first = workspace.run_dir("x__c", root=tmp_path, at=at)
    second = workspace.run_dir("x__c", root=tmp_path, at=at)
    assert first == second


def test_different_objects_do_not_collide(tmp_path: Path) -> None:
    at = datetime(2026, 9, 13, 15, 30, 0)
    a = workspace.run_dir("a__c", root=tmp_path, at=at)
    b = workspace.run_dir("b__c", root=tmp_path, at=at)
    assert a != b


# ---------------------------------------------------------------- 覆盖度三态


def test_coverage_complete_when_all_succeed() -> None:
    assert workspace.coverage_status(total=3, succeeded=3, failures=0) == "complete"


def test_coverage_partial_when_some_fail() -> None:
    assert workspace.coverage_status(total=3, succeeded=2, failures=1) == "partial"
    assert workspace.coverage_status(total=3, succeeded=3, failures=1) == "partial"


def test_coverage_degraded_when_nothing_succeeds() -> None:
    assert workspace.coverage_status(total=3, succeeded=0, failures=3) == "degraded"


def test_build_coverage_carries_counters() -> None:
    coverage = workspace.build_coverage(total=1, succeeded=1, failures=[],
                                        fieldsRead=42, statesRead=5)
    assert coverage["status"] == "complete"
    assert coverage["fieldsRead"] == 42
    assert coverage["statesRead"] == 5
    assert coverage["failureCount"] == 0


def test_coverage_carries_spider_counters() -> None:
    """spider 是唯一在跑的命令，它的四个图计数必须原样进 coverage（产物要能自证规模）。"""
    coverage = workspace.build_coverage(
        total=1, succeeded=1, failures=[],
        objectNodes=1, stateNodes=3, attrEdges=1, relationEdges=0,
    )
    assert coverage["status"] == "complete"
    assert coverage["objectNodes"] == 1
    assert coverage["stateNodes"] == 3
    assert coverage["attrEdges"] == 1
    assert coverage["relationEdges"] == 0
    assert coverage["failureCount"] == 0


# ---------------------------------------------------------------- manifest


def test_manifest_records_artifact_hashes(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    manifest = workspace.build_manifest(
        command="spider", object_code="training_mgr__c",
        coverage={"status": "complete"},
        artifacts=[tmp_path / "a.md", tmp_path / "b.json"],
        environment={"envName": "测试环境"},
    )
    assert manifest["schemaVersion"] == workspace.MANIFEST_SCHEMA_VERSION
    assert manifest["command"] == "spider"
    assert manifest["objectCode"] == "training_mgr__c"
    assert manifest["environment"]["envName"] == "测试环境"
    names = {a["name"] for a in manifest["artifacts"]}
    assert names == {"a.md", "b.json"}
    for entry in manifest["artifacts"]:
        assert entry["bytes"] > 0
        assert len(entry["sha256"]) == 64


def test_manifest_write_read_roundtrip(tmp_path: Path) -> None:
    manifest = workspace.build_manifest(command="spider", object_code="x__c",
                                        coverage={"status": "partial"})
    path = workspace.write_manifest(tmp_path, manifest)
    assert path.name == workspace.MANIFEST_NAME
    back = workspace.read_manifest(tmp_path)
    assert back is not None
    assert back["coverage"]["status"] == "partial"


def test_manifest_write_is_atomic(tmp_path: Path) -> None:
    workspace.write_manifest(tmp_path, {"schemaVersion": 1})
    assert not list(tmp_path.glob("*.tmp")), "不应留下临时文件"


def test_read_manifest_missing_returns_none(tmp_path: Path) -> None:
    assert workspace.read_manifest(tmp_path) is None


def test_read_manifest_corrupt_returns_none(tmp_path: Path) -> None:
    (tmp_path / workspace.MANIFEST_NAME).write_text("{ not json", encoding="utf-8")
    assert workspace.read_manifest(tmp_path) is None


def test_manifest_is_valid_json_on_disk(tmp_path: Path) -> None:
    workspace.write_manifest(tmp_path, workspace.build_manifest(
        command="spider", object_code="", coverage={"status": "degraded"}))
    raw = (tmp_path / workspace.MANIFEST_NAME).read_text(encoding="utf-8")
    assert json.loads(raw)["coverage"]["status"] == "degraded"
    assert raw.endswith("\n")


# ---------------------------------------------------------------- 端到端：真实跑一遍 spider

FIXTURE = Path(__file__).parent / "fixtures" / "egmp" / "spider-training-mgr.json"
OBJECT = "training_mgr__c"


def _client(strict: bool = True):
    from workbench.services.egmp import replay

    return replay.ReplayClient(replay.load_fixture(FIXTURE), strict=strict)


def test_run_spider_writes_into_workspace(tmp_path: Path) -> None:
    """给 workspace_root 时，产物必须落到 <root>/<对象code>/<时间戳>/ 并带 manifest。"""
    from workbench.services.egmp.insight import run_spider

    client = _client()
    result = run_spider(client, [OBJECT], workspace_root=tmp_path,
                        environment={"envName": "测试环境"})

    assert result["ok"]
    assert client.misses == []
    assert len(result["workspaces"]) == 1

    run_path = Path(result["workspaces"][0])
    assert run_path.parent.name == OBJECT, "落点必须按对象归集，否则历次产物无法比对"
    assert run_path.resolve().is_relative_to(tmp_path.resolve())
    names = {p.name for p in run_path.iterdir()}
    assert "spider-network.md" in names
    assert "spider-network.json" in names
    assert "spider-network.drawio" in names
    assert workspace.MANIFEST_NAME in names

    manifest = workspace.read_manifest(run_path)
    assert manifest is not None
    assert manifest["schemaVersion"] == workspace.MANIFEST_SCHEMA_VERSION
    assert manifest["objectCode"] == OBJECT
    assert manifest["environment"]["envName"] == "测试环境"
    assert manifest["coverage"]["status"] == "complete"
    assert manifest["coverage"]["stateNodes"] == 3
    recorded = {a["name"] for a in manifest["artifacts"]}
    assert "spider-network.json" in recorded


def test_run_spider_without_workspace_root_writes_to_output_dir(tmp_path: Path) -> None:
    """不给 workspace_root 时退回直写 output_dir（路由的 runtime 兜底路径就是这样）。"""
    from workbench.services.egmp.insight import run_spider

    result = run_spider(_client(), [OBJECT], output_dir=tmp_path)
    assert result["ok"]
    assert result["workspaces"] == [], "没走工作区契约时不得谎报工作区目录"
    assert (tmp_path / "spider-network.json").exists()


def test_run_spider_reports_workspace_dir_it_actually_used(tmp_path: Path) -> None:
    """回填的目录必须是真实落点，且产物确实在里面——否则任务表会把日志指到空目录。"""
    from workbench.services.egmp.insight import run_spider

    result = run_spider(_client(), [OBJECT], workspace_root=tmp_path)
    run_path = Path(result["workspaces"][0])
    for path in result["artifacts"]:
        assert Path(path).resolve().is_relative_to(run_path.resolve())


@pytest.mark.parametrize("code", ["../../evil", "C:/Temp/x", "..", "con"])
def test_run_spider_dangerous_code_cannot_escape_workspace(tmp_path: Path, code: str) -> None:
    """对象 code 来自用户输入：恶意 code 连**落点目录**都不得逃出工作区根。

    这些 code 都读不到实体（`con` 在夹具里不存在，其余是被净化的非法名），所以这次运行注定
    失败——那正好：**失败路径也必须不越界**，而且要失败得干净（建了目录，但不留半个产物）。
    """
    from workbench.services.egmp.insight import run_spider
    from workbench.services.egmp.insight.spider import SpiderReadErrors

    with pytest.raises(SpiderReadErrors):
        run_spider(_client(strict=False), [code], workspace_root=tmp_path)
    for path in tmp_path.rglob("*"):
        assert path.resolve().is_relative_to(tmp_path.resolve()), f"{code!r} 越界：{path}"
    assert not list(tmp_path.rglob(workspace.MANIFEST_NAME)), "失败不得留下 manifest"


def test_run_spider_missing_object_raises_instead_of_empty_graph(tmp_path: Path) -> None:
    """对象读不到实体 → 必须失败，不得静默产出一张空网冒充成功。

    这是真实咬过的坑：`get_object` 拿到空账时流程会一路走完，产出「1 个对象节点 + 0 状态」
    的空网，而 manifest 还写着 `complete` —— 任务绿的、图是假的。
    落点目录会先被建出来（先定落点再爬），但**没有 manifest**：按约定 manifest 在才算一次
    完整运行，所以半个目录不会被误读成成功。
    """
    from workbench.services.egmp import replay
    from workbench.services.egmp.insight import run_spider
    from workbench.services.egmp.insight.spider import SpiderReadErrors

    client = replay.ReplayClient({"formatVersion": 1, "interactions": []}, strict=False)
    with pytest.raises(SpiderReadErrors) as exc:
        run_spider(client, ["ghost__c"], workspace_root=tmp_path)
    assert "ghost__c" in str(exc.value)
    assert not list(tmp_path.rglob(workspace.MANIFEST_NAME)), "失败不得留下 manifest"


# ---------------------------------------------------------------- 路由状态映射


def test_coverage_maps_to_job_status() -> None:
    """complete→succeeded、partial→partial、degraded→failed；无 coverage 时退回布尔。"""
    from workbench.api.routes_insight import _status_from_coverage

    assert _status_from_coverage({"coverage": {"status": "complete"}}) == "succeeded"
    assert _status_from_coverage({"coverage": {"status": "partial"}}) == "partial"
    assert _status_from_coverage({"coverage": {"status": "degraded"}}) == "failed"
    assert _status_from_coverage({"ok": True}) == "succeeded"
    assert _status_from_coverage({"ok": False}) == "failed"
    assert _status_from_coverage({}) == "failed"


# ---------------------------------------------------------------- 迁移


def test_migration_14_adds_workspace_columns() -> None:
    """迁移只增不改：14 必须在册，且两列可用（历史 1–13 不动）。"""
    from workbench import db

    db.connect()
    versions = {row["version"] for row in db.query("SELECT version FROM _sqlx_migrations")}
    assert 14 in versions, "迁移 14 未登记"
    assert {1, 13}.issubset(versions), "历史迁移被动过？"
    columns = {row["name"] for row in db.query("PRAGMA table_info(insight_runs)")}
    assert {"workspace_dir", "coverage_status"}.issubset(columns)
    assert "artifact_dir" in columns, "旧列必须保留（历史行仍可读）"
