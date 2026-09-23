"""离线回放门禁：用夹具跑通 spider 全链路，取代只在内存假平台上的验证。

对应 docs/INSIGHT-SPEC.md §6.2「离线回放夹具」。两条核心保证：

1. **覆盖度可证**：回放结束时既不得有请求缺失（`misses`），也不得有夹具条目从未被用到
   —— 后者能抓住「夹具腐化」（平台/代码变了但夹具没跟着变）。
2. **全链路语义**：图模型、networkx 分析、drawio、manifest 都从夹具推导出来，且**可复现**（两次跑逐字节一致）。

> **2026-09-14 换轨**：原夹具 `understand-capa-plan.json` 随 `understand` 命令下线一并删除；
> 本文件改挂 `spider-training-mgr.json`。该夹具是**真实形态**夹具：结构取自 `Recorder`
> 对假平台的实跑录制（11 条交互），ID 与业务命名替换为确定性占位值，**不含真实平台数据**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.services.egmp import insight, replay

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "egmp"
GOLDEN = FIXTURE_DIR / "spider-training-mgr.json"
OBJECT = "training_mgr__c"


def _client() -> replay.ReplayClient:
    return replay.ReplayClient(replay.load_fixture(GOLDEN))


def _spider(client: replay.ReplayClient, output_dir: Path) -> dict:
    return insight.run_spider(client, [OBJECT], output_dir=output_dir)


# ---------------------------------------------------------------- 覆盖度


def test_fixture_loads_and_declares_version() -> None:
    fixture = replay.load_fixture(GOLDEN)
    assert fixture["formatVersion"] == replay.FIXTURE_FORMAT_VERSION
    assert fixture["interactions"]


def test_rejects_fixture_with_wrong_format_version(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"formatVersion": 999, "interactions": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="格式版本"):
        replay.load_fixture(bad)


def test_rejects_fixture_without_interactions(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"formatVersion": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="interactions"):
        replay.load_fixture(bad)


def test_full_chain_leaves_no_missing_request(tmp_path: Path) -> None:
    """跑完 spider 不得有夹具未覆盖的请求——这是「夹具够用」的硬证据。"""
    client = _client()
    _spider(client, tmp_path)
    assert client.misses == [], f"夹具缺这些交互：{client.misses}"


def test_fixture_has_no_unused_interaction(tmp_path: Path) -> None:
    """反向覆盖：夹具里每一条都必须被真实调用到，否则夹具已经腐化。"""
    client = _client()
    _spider(client, tmp_path)
    used = set(client.calls)
    declared = set(replay._index(replay.load_fixture(GOLDEN)))
    assert declared - used == set(), f"夹具里这些条目从未被用到（已腐化）：{declared - used}"


def test_missing_interaction_raises_with_actionable_message(tmp_path: Path) -> None:
    """夹具缺一条**爬取途中**的交互时，报错必须点名缺哪条、并列出可用键。

    挑对象元数据（`BasicObject/{code}`）：它不在任何 `try/except` 保护里——缺了就直接炸穿。
    （缺状态表/动作这类被容忍的读会有另一条路径：进 `failures` 且覆盖度降级，见 test_workspace。）
    """
    fixture = replay.load_fixture(GOLDEN)
    fixture["interactions"] = [
        i for i in fixture["interactions"] if "BasicObject/training_mgr__c" not in i["path"]
    ]
    client = replay.ReplayClient(fixture)
    with pytest.raises(replay.ReplayMiss) as exc:
        _spider(client, tmp_path)
    message = str(exc.value)
    assert "BasicObject/training_mgr__c" in message
    assert "夹具内已有" in message, "报错必须列出可用键，便于定位"


def test_non_strict_mode_records_misses_instead_of_raising() -> None:
    client = replay.ReplayClient({"formatVersion": 1, "interactions": []}, strict=False)
    assert client.get("/api/whatever") is None
    assert client.misses == ["GET /api/whatever {}"]


# ---------------------------------------------------------------- 全链路语义


def test_scope_is_taken_from_fixture_base_url() -> None:
    client = _client()
    assert client.scope == "https://demo-val.example.com"


def test_graph_counts_match_fixture(tmp_path: Path) -> None:
    """节点/边计数由夹具推导（2026-09-15 按新契约重校）：

    - 1 个目标对象 + 1 个**相关对象占位节点**（部门，来自 behaviorType=46）= 2 个对象节点；
    - 3 个状态节点；
    - 2 条归因边：用户动作「关闭」描述里解析出的目标状态 + 工作流步骤 behaviorType=9；
    - 1 条关系边：进入动作与工作流步骤里的 behaviorType=46 → 部门状态[已完成]。
    """
    result = _spider(_client(), tmp_path)
    assert result["ok"] is True
    assert result["stats"] == {
        "objectNodes": 2, "stateNodes": 3, "attrEdges": 2, "relationEdges": 1,
    }, "图模型计数与夹具不符——多半是推导逻辑变了，而不是夹具坏了"


def test_cross_object_relation_comes_from_behaviour(tmp_path: Path) -> None:
    """跨对象关联必须来自**行为**（behaviorType=46），不是字段引用。

    回归两件事：① 上一版只读字段引用（`referenceObjectCode`）→ 真机恒空；
    ② 用户定稿：字段级引用很多根本不在生命周期里起作用，只认「生命周期/工作流里真的被改动的相关对象」。
    节点标识用对象名称，并带上被改成的目标状态。
    """
    _spider(_client(), tmp_path)
    model = json.loads((tmp_path / "spider-network.json").read_text(encoding="utf-8"))["model"]
    relation = [e for e in model["edges"] if e["kind"] == "relation"]
    assert len(relation) == 1
    target = next(n for n in model["nodes"] if n["id"] == relation[0]["target"])
    assert target["label"] == "部门", "标识用关系名首段（相关对象名）"
    assert "已完成" in relation[0]["label"], "边标签必须带被改成的目标状态"

    step1 = (tmp_path / "step1-relations.md").read_text(encoding="utf-8")
    assert "修改部门培训状态为[已完成]" in step1, "step1 要带上平台自己的动作描述作为证据"
    assert "进入动作" in step1 and "步骤" in step1, "每条关联都要写清来源（哪个动作 / 哪一步）"
    assert "已完成" in step1, "要写清被改成的目标状态"


def test_status_table_columns_use_real_sources(tmp_path: Path) -> None:
    """四列表必须用真实来源：用户动作带 code、进入动作带描述与目标对象。"""
    _spider(_client(), tmp_path)
    step2 = (tmp_path / "step2-status-table.md").read_text(encoding="utf-8")
    assert "提交审核（Initiate_the_review__c）" in step2, "用户动作要带动作 code"
    assert "发起工作流" in step2, "动作类型要来自 GetUserAction.behaviorTypes"
    assert "将培训负责人添加为负责人" in step2, "进入动作要来自 EnterAction/GetListByStatusId"
    assert "修改相关对象「部门」状态为[已完成]" in step2, "进入动作要补出目标对象与目标状态"
    assert "name__ak 不为空" in step2, "进入条件要把条件对象渲染成可读文本，而不是 JSON"


def test_step_artifacts_cover_all_five_steps(tmp_path: Path) -> None:
    """用户定稿的「有效产物」清单：五步每步都要有落盘产物（空表也要有文件说明为什么空）。"""
    _spider(_client(), tmp_path)
    names = {p.name for p in tmp_path.iterdir()}
    assert {
        "step1-relations.md", "step2-status-table.md", "step3-attribution.md",
        "step4-workflows.drawio", "spider-network.md", "spider-network.json",
        "spider-network.drawio", "manifest.json",
    } <= names
    step2 = (tmp_path / "step2-status-table.md").read_text(encoding="utf-8")
    assert "| 生命周期状态名称 | 进入动作 | 用户动作 | 进入条件 |" in step2, "四列表列口径是写死的契约"
    step4 = (tmp_path / "step4-workflows.drawio").read_text(encoding="utf-8")
    assert "培训审批流" in step4 and step4.startswith("<mxfile")


def test_coverage_is_complete_and_manifest_self_proves(tmp_path: Path) -> None:
    """产物自证覆盖度：manifest 必须记下命令、对象、三态覆盖度与产物哈希。"""
    result = _spider(_client(), tmp_path)
    assert result["coverage"]["status"] == "complete"

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["command"] == "spider"
    assert manifest["objectCode"] == OBJECT
    assert manifest["coverage"]["status"] == "complete"
    assert manifest["coverage"]["workflowGraphs"] == 1
    assert manifest["coverage"]["unexpandedRelations"] == 1, "边界计数要进 coverage（自证）"
    names = {a["name"] for a in manifest["artifacts"]}
    assert names == {
        "spider-network.json", "spider-network.drawio", "spider-network.md",
        "step1-relations.md", "step2-status-table.md", "step3-attribution.md",
        "step4-workflows.drawio",
    }
    assert all(a.get("sha256") for a in manifest["artifacts"]), "产物必须带哈希，否则 manifest 不自证"


def test_artifact_write_is_inside_output_dir(tmp_path: Path) -> None:
    """对象 code 来自用户输入：落点必须被 resolve_within 约束在输出目录内。"""
    result = _spider(_client(), tmp_path)
    root = tmp_path.resolve()
    for path in result["artifacts"]:
        assert Path(path).resolve().is_relative_to(root), f"产物逃出了输出目录：{path}"


def test_networkx_analysis_is_present_in_markdown(tmp_path: Path) -> None:
    _spider(_client(), tmp_path)
    md = (tmp_path / "spider-network.md").read_text(encoding="utf-8")
    assert "networkx" in md
    assert "有向无环" in md


def test_drawio_is_well_formed(tmp_path: Path) -> None:
    _spider(_client(), tmp_path)
    text = (tmp_path / "spider-network.drawio").read_text(encoding="utf-8")
    assert text.startswith("<mxfile")
    assert "<mxGraphModel" in text


def test_two_runs_produce_identical_json_payload(tmp_path: Path) -> None:
    """可复现：两次回放的图模型逐字节一致（键序都不得漂移）。"""
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    _spider(_client(), a_dir)
    _spider(_client(), b_dir)
    left = (a_dir / "spider-network.json").read_text(encoding="utf-8")
    right = (b_dir / "spider-network.json").read_text(encoding="utf-8")
    # generatedAt 是时间戳，属预期差异；比对去掉它之后的正文
    def strip_stamp(text: str) -> str:
        data = json.loads(text)
        (data.get("meta") or {}).pop("generatedAt", None)
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    assert strip_stamp(left) == strip_stamp(right)


def test_replay_returns_deep_copies(tmp_path: Path) -> None:
    """回放必须返回深拷贝：调用方改动不得污染夹具（否则第二次跑就漂了）。"""
    fixture = replay.load_fixture(GOLDEN)
    client = replay.ReplayClient(fixture)
    first = client.get(f"/api/openapi/v1.0/BasicObject/{OBJECT}")
    first["name"] = "被调用方改坏了"
    second = client.get(f"/api/openapi/v1.0/BasicObject/{OBJECT}")
    assert second["name"] == "培训管理"


def test_recorder_roundtrip_via_fake_platform(tmp_path: Path) -> None:
    """Recorder 包住假平台录一遍，再回放——两条路径必须给出同样的结论。"""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from fakes import FakeEgmpClient

    fake = FakeEgmpClient()
    # 数据种子：直接建对象（原先借 `orchestrate.run_full_workflow` 播种，那条链已随
    # 配置工厂移除；本用例测的是**录制/回放一致性**，与播种方式无关）。
    fake.post("/api/platform/BasicObject/SaveBasicObject",
              {"name": "培训管理", "code": OBJECT, "enableLifeCycle": True})
    recorded = replay.make_fixture("roundtrip", base_url="https://fake.example.com", env_name="假平台")
    live = insight.run_spider(replay.Recorder(fake, recorded), [OBJECT], output_dir=tmp_path / "live")
    replayed = insight.run_spider(
        replay.ReplayClient(recorded), [OBJECT], output_dir=tmp_path / "replay")

    assert live["stats"] == replayed["stats"], "录制与回放的图模型结论必须一致"
    assert (tmp_path / "live" / "spider-network.json").exists()
    assert (tmp_path / "replay" / "spider-network.json").exists()


def test_recorder_captures_failures_too() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from fakes import FakeEgmpClient

    fake = FakeEgmpClient()
    fake.fail_paths.add(f"/api/openapi/v1.0/BasicObject/{OBJECT}")
    fixture = replay.make_fixture("fail")
    rec = replay.Recorder(fake, fixture)
    with pytest.raises(RuntimeError):
        rec.get_object(OBJECT)
    recorded = [i for i in fixture["interactions"] if i["ok"] is False]
    assert recorded, "失败也必须入夹具，否则回放不出真实失败"
