"""离线回放门禁：用夹具跑通洞察全链路，取代只在内存假平台上的验证。

对应 docs/INSIGHT-SPEC.md §6.2「离线回放夹具」。两条核心保证：

1. **覆盖度可证**：回放结束时既不得有请求缺失（`misses`），也不得有夹具条目从未被用到
   —— 后者能抓住「夹具腐化」（平台/代码变了但夹具没跟着变）。
2. **全链路语义**：成果表格、工作流触发状态、drawio 都从夹具推导出来，且**可复现**（两次跑逐字节一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workbench.services.egmp import replay
from workbench.services.egmp.insight import drawio
from workbench.services.egmp.insight import understand as understand_mod

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "egmp"
GOLDEN = FIXTURE_DIR / "understand-capa-plan.json"


def _client() -> replay.ReplayClient:
    return replay.ReplayClient(replay.load_fixture(GOLDEN))


def _understand(client: replay.ReplayClient) -> dict:
    return understand_mod.understand_object(client, "capa_plan__c")


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


def test_full_chain_leaves_no_missing_request() -> None:
    """跑完 understand 不得有夹具未覆盖的请求——这是「夹具够用」的硬证据。"""
    client = _client()
    understanding = _understand(client)
    assert client.misses == [], f"夹具缺这些交互：{client.misses}"
    assert understanding["meta"]["objectCode"] == "capa_plan__c"


def test_fixture_has_no_unused_interaction() -> None:
    """反向覆盖：夹具里每一条都必须被真实调用到，否则夹具已经腐化。"""
    client = _client()
    _understand(client)
    used = set(client.calls)
    declared = set(replay._index(replay.load_fixture(GOLDEN)))
    assert declared - used == set(), f"夹具里这些条目从未被用到（已腐化）：{declared - used}"


def test_missing_interaction_raises_with_actionable_message(tmp_path: Path) -> None:
    fixture = replay.load_fixture(GOLDEN)
    fixture["interactions"] = [
        i for i in fixture["interactions"]
        if i["path"] != "/api/openapi/v1.0/BasicObject/lifecycleStatus/capa_plan__c"
    ]
    client = replay.ReplayClient(fixture)
    with pytest.raises(replay.ReplayMiss) as exc:
        _understand(client)
    message = str(exc.value)
    assert "lifecycleStatus" in message
    assert "夹具内已有" in message, "报错必须列出可用键，便于定位"


def test_non_strict_mode_records_misses_instead_of_raising() -> None:
    client = replay.ReplayClient({"formatVersion": 1, "interactions": []}, strict=False)
    assert client.get("/api/whatever") is None
    assert client.misses == ["GET /api/whatever {}"]


# ---------------------------------------------------------------- 全链路语义


def test_lifecycle_table_columns_are_derived_from_fixture() -> None:
    """四列表的三列内容必须与夹具一致（进入动作 / 用户动作 / 进入条件）。"""
    lifecycle = _understand(_client())["lifecycle"]
    md = _understand(_client())["markdown"]

    assert "| 生命周期状态名称 | 进入动作 | 用户动作 | 进入条件（描述推断） |" in md

    # st-1（开启）：用户动作里有「驳回」（behaviorType=9 改状态），不含任何进入动作
    st1_user = [e["button"] for e in lifecycle["userActionEdges"] if e["source"] == "st-1"]
    assert st1_user == ["驳回"], f"st-1 的用户动作应为驳回，实际 {st1_user}"
    assert [e for e in lifecycle["enterActionEdges"] if e["source"] == "st-1"] == []
    reject = next(e for e in lifecycle["userActionEdges"] if e["button"] == "驳回")
    assert reject["targetName"] == "关闭" and reject["mechanism"] == "button"

    # st-2（待审批）：进入动作里唯一「改状态」的是「自动完成」（经描述正则 → 关闭）。
    # 「自动送审」虽然也是 executeType=2，但它的行为是**发起工作流**，因此进 launchEdges 而非本列。
    st2_enter = {e.get("action") for e in lifecycle["enterActionEdges"] if e["source"] == "st-2"}
    assert st2_enter == {"自动完成"}, f"st-2 的进入动作（改状态类）应只有「自动完成」，实际 {st2_enter}"
    st2_user = [e["button"] for e in lifecycle["userActionEdges"] if e["source"] == "st-2"]
    assert st2_user == [], "进入动作混进了用户动作列"

    auto_done = next(e for e in lifecycle["enterActionEdges"] if e.get("action") == "自动完成")
    assert auto_done["targetName"] == "关闭" and auto_done["evidence"] == "description"
    assert auto_done["mechanism"] == "enterAction"


def test_enter_condition_is_description_and_marked_as_inferred() -> None:
    md = _understand(_client())["markdown"]
    assert "描述推断" in md
    assert "修改状态为[关闭]" in md, "进入条件应取 description 原文"


def test_workflow_trigger_edges_from_lifecycle_status_ids() -> None:
    lifecycle = _understand(_client())["lifecycle"]
    triggers = {(t["statusName"], t["workflowName"]) for t in lifecycle["workflowTriggerEdges"]}
    assert triggers == {("开启", "CAPA审批流"), ("待审批", "CAPA审批流")}
    # 夹具里刻意放了一个不存在状态 id，必须被丢弃
    assert all(t["statusId"] != "st-ghost" for t in lifecycle["workflowTriggerEdges"])


def test_launch_edges_cover_button_and_enter_action_channels() -> None:
    lifecycle = _understand(_client())["lifecycle"]
    by_mech = {e["mechanism"] for e in lifecycle["launchEdges"]}
    assert by_mech == {"button", "enterAction"}, f"发起工作流的两条通道未覆盖：{by_mech}"
    button_launch = next(e for e in lifecycle["launchEdges"] if e["mechanism"] == "button")
    assert button_launch["source"] == "st-1" and button_launch["button"] == "提交"
    enter_launch = next(e for e in lifecycle["launchEdges"] if e["mechanism"] == "enterAction")
    assert enter_launch["source"] == "st-2"


def test_involved_workflow_gets_a_graph_and_drawio() -> None:
    """键空间（basicId vs 行 id）配对正确时，工作流才会真的取到图与 drawio。"""
    lifecycle = _understand(_client())["lifecycle"]
    involved = lifecycle["involvedWorkflows"]
    assert involved, "应有被发起的工作流"
    workflow = involved[0]
    assert workflow["name"] == "CAPA审批流"
    assert workflow["graph"] and workflow["graph"]["nodes"], "工作流图没取到（basicId/行 id 键空间错配？）"


def test_relations_drop_tree_only_self_reference() -> None:
    """enableTree=false 时自引用父对象应被丢弃（夹具刻意如此构造）。"""
    relations = _understand(_client())["relations"]
    targets = {e["target"] for e in relations["edges"]}
    assert "capa_plan__c" not in targets
    assert "user__c" in targets, "普通引用（dataType 15）应保留"


def test_unknown_data_type_does_not_crash() -> None:
    understanding = _understand(_client())
    assert any(f["code"] == "future__c" for f in understanding["fields"])


# ---------------------------------------------------------------- 可复现


def test_two_runs_produce_identical_markdown() -> None:
    a = _understand(_client())["markdown"]
    b = _understand(_client())["markdown"]
    assert a == b, "同一夹具两次跑出的报告必须逐字节一致"


def test_two_runs_produce_identical_json_payload() -> None:
    a = understand_mod._json_dumps(_understand(_client()))
    b = understand_mod._json_dumps(_understand(_client()))
    assert a == b


def test_replay_returns_deep_copies() -> None:
    """调用方会就地改动作栏行（打 _consumed 标记）；共用同一份对象会让第二次回放结果不同。"""
    client = _client()
    ids = {"ids": ["ua-1", "ua-4", "ua-2", "ua-3"]}
    first = client.post("/api/config/lifecycle/UserAction/GetActionBar", ids)
    first[0]["_consumed"] = True
    first[0]["name"] = "被改坏了"
    second = client.post("/api/config/lifecycle/UserAction/GetActionBar", ids)
    assert second[0]["name"] == "提交"
    assert "_consumed" not in second[0]


def test_artifact_write_is_inside_output_dir(tmp_path: Path) -> None:
    understanding = _understand(_client())
    written = understand_mod.write_understanding_artifacts(understanding, tmp_path / "out")
    root = (tmp_path / "out").resolve()
    assert written
    for path in written:
        assert path.resolve().is_relative_to(root)


def test_drawio_from_fixture_is_well_formed(tmp_path: Path) -> None:
    understanding = _understand(_client())
    written = understand_mod.write_understanding_artifacts(understanding, tmp_path / "out")
    drawio_files = [p for p in written if p.suffix == ".drawio"]
    assert drawio_files, "夹具里被发起的工作流应产出 drawio"
    for path in drawio_files:
        drawio.validate(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 录制回路


def test_recorder_roundtrip_via_fake_platform(tmp_path: Path) -> None:
    """Recorder 包住假平台录一份夹具，再回放——两条路径必须给出同样的结论。"""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    import test_native
    from fakes import FakeEgmpClient

    from workbench.services.egmp import orchestrate

    platform = FakeEgmpClient()
    orchestrate.run_full_workflow(platform, test_native._sample_blueprint())

    fixture = replay.make_fixture("roundtrip", base_url="https://fake")
    recorder = replay.Recorder(platform, fixture)
    direct = understand_mod.understand_object(recorder, "training_mgr__c")

    assert fixture["interactions"], "应录到交互"

    dump = tmp_path / "rec.json"
    replay.save_fixture(fixture, dump)
    replayed = understand_mod.understand_object(replay.ReplayClient(replay.load_fixture(dump)),
                                                "training_mgr__c")

    assert replayed["markdown"] == direct["markdown"], "回放结论与直连不一致"


def test_recorder_captures_failures_too() -> None:
    """平台上真实发生的失败也必须录下来，否则回放会「凭空成功」。"""
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from fakes import FakeEgmpClient

    platform = FakeEgmpClient()
    platform.fail_paths.add("/api/platform/Menu/GetMenuList")
    recorder = replay.Recorder(platform, replay.make_fixture("with-failure"))
    with pytest.raises(RuntimeError):
        recorder.list_menus()
    entry = recorder.fixture["interactions"][-1]
    assert entry["ok"] is False
    assert "模拟失败" in entry["error"]


def test_replay_reproduces_recorded_failure() -> None:
    fixture = replay.make_fixture("failing")
    fixture["interactions"].append({
        "method": "GET", "path": "/api/platform/Menu/GetMenuList",
        "ok": False, "error": "模拟失败：菜单", "errorType": "RuntimeError",
    })
    client = replay.ReplayClient(fixture)
    with pytest.raises(replay.ReplayRecordedError, match="模拟失败"):
        client.list_menus()


def test_scope_is_taken_from_fixture_base_url() -> None:
    """回放也有作用域，缓存隔离在回放路径上同样成立。"""
    assert _client().scope == "https://demo-val.example.com"
    other = replay.ReplayClient({"formatVersion": 1, "baseUrl": "https://d.example.com",
                                "interactions": []})
    assert other.scope != _client().scope
