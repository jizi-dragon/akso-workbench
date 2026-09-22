"""配置工厂 × Agent 的接口验收（对应 docs/FACTORY-AGENT-GAP.md 的四条问题）。

## 本文件钉住的是**那条断掉的链路**

改造前：agent 能校验蓝图、能执行创建，**但不能把校验通过的东西交给执行**——
`blueprint_id` 只能由网页上的 multipart 文件上传产生。于是校验工具的返回值只能教模型去求人：

    "校验通过：把蓝图暂存为 blueprint_id 后才能 create
     （暂存走配置工厂页面上传，或让用户把文件拖进页面）"

这不是"模型不够聪明"，是**动词缺失**。下面第一条测试就是它的回归：
**从一份蓝图 JSON 到 blueprint_id，全流程不许出现"让用户去另一个页面"。**
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from workbench import approvals, db
from workbench import plugin_api as pa
from workbench.plugins import factory as factory_plugin

GOOD_BLUEPRINT = {
    "description": "培训管理：一个对象 + 两个字段",
    "objects": [{
        "name": "培训记录",
        "code": "training_record__c",
        "fields": [
            {"name": "培训主题", "code": "topic__c", "dataType": 1, "isRequired": True},
            {"name": "参训人数", "code": "attendee_count__c", "dataType": 2, "min": 0, "max": 999},
        ],
        "formLayout": {"sections": [
            {"name": "基本信息", "code": "training_basic__l", "columnsNum": 2,
             "fields": ["topic__c", "attendee_count__c"]},
        ]},
        "listLayout": {"columns": [
            {"fieldCode": "topic__c", "sort": 1},
            {"fieldCode": "attendee_count__c", "sort": 2},
        ]},
    }],
}

BAD_BLUEPRINT = {
    "objects": [{
        "name": "坏对象",
        "code": "BAD",
        "fields": [{"name": "选项字段", "code": "opt__c", "dataType": 4}],
    }],
}


@pytest.fixture()
def hub(monkeypatch):
    db.connect()
    approvals.reset_store()
    fresh = pa.ToolHub()
    approvals.install(fresh)
    fresh.load(_load_all)
    monkeypatch.setattr(pa, "_hub", fresh)
    return fresh


def _load_all(h: pa.ToolHub) -> None:
    import workbench.plugins as plugins

    plugins.load_all(h)


def _call(hub: pa.ToolHub, name: str, args: dict):
    return asyncio.run(hub.execute(name, args))


def _payload(inv: pa.Invocation) -> dict:
    assert inv.status is pa.ToolStatus.OK, f"{inv.status}: {inv.content}"
    return json.loads(inv.content)


# ---------------------------------------------------------------- 问题②：闭环

def test_blueprint_becomes_a_blueprint_id_without_leaving_the_chat(hub) -> None:
    """**核心回归**：一份蓝图 JSON 进来，一个 blueprint_id 出去，全程不问用户要网页。

    改造前这条必然 FAIL——那时根本没有"暂存"这个动词，`next_step` 明写
    "暂存走配置工厂页面上传，或让用户把文件拖进页面"。
    """
    payload = _payload(_call(hub, "factory_preview_blueprint",
                             {"blueprint": json.dumps(GOOD_BLUEPRINT)}))
    assert payload["valid"] is True
    blueprint_id = payload["blueprint_id"]
    assert blueprint_id, "预览必须产出 blueprint_id，否则闭环又断了"

    # 产出物真的落盘了（不只是返回值里有个字符串）
    staged = factory_plugin.staged_dir(blueprint_id) / "blueprint.json"
    assert staged.is_file(), f"没有暂存：{staged}"

    # 而且**紧接着就能创建**（`factory_create` 的前置条件已经满足）
    assert factory_plugin.staged_dir(blueprint_id).joinpath("blueprint.json").is_file()
    listed = _payload(_call(hub, "factory_list_blueprints", {}))
    assert blueprint_id in [b["blueprint_id"] for b in listed["blueprints"]]

    # 一句"去页面上传"都不许有
    text = json.dumps(payload, ensure_ascii=False)
    for banned in ("文件拖进页面", "页面上传", "让用户上传"):
        assert banned not in text, f"又绕回去了：{banned}"


def test_preview_refuses_to_stage_a_broken_blueprint(hub) -> None:
    """校验不过**不暂存**：半成品留在运行时目录里，用户还得自己分辨哪份能用。"""
    payload = _payload(_call(hub, "factory_preview_blueprint",
                             {"blueprint": json.dumps(BAD_BLUEPRINT)}))
    assert payload["valid"] is False
    assert payload["error_count"] >= 1
    assert "blueprint_id" not in payload, "校验不过却给了 id —— 下一步就能拿它去创建"
    assert payload["field_hints"], "错误里要带上相关键的写法（否则模型只能再调一次规格工具）"


def test_preview_is_idempotent_on_identical_content(hub) -> None:
    """同一份蓝图重复预览得到**同一个 id**，不在运行时目录里堆垃圾。"""
    args = {"blueprint": json.dumps(GOOD_BLUEPRINT)}
    first = _payload(_call(hub, "factory_preview_blueprint", args))["blueprint_id"]
    second = _payload(_call(hub, "factory_preview_blueprint", args))["blueprint_id"]
    assert first == second

    changed = json.loads(json.dumps(GOOD_BLUEPRINT))
    changed["objects"][0]["fields"].append({"name": "备注", "code": "note__c", "dataType": 17})
    third = _payload(_call(hub, "factory_preview_blueprint",
                           {"blueprint": json.dumps(changed)}))["blueprint_id"]
    assert third != first, "内容变了还是同一个 id —— 用户会拿旧蓝图去创建"


# ---------------------------------------------------------------- 问题③：审阅材料在审批之前

def test_review_material_is_produced_before_the_approval(hub) -> None:
    """给用户看的清单**在预览这一步**就有，不是等执行时才落盘。

    改造前 `generate_review_spec` 只在 `run_full_workflow` 内部被调用——用户审批时
    手里没有那份东西，而它存在的全部目的就是给他审批用。
    """
    payload = _payload(_call(hub, "factory_preview_blueprint",
                             {"blueprint": json.dumps(GOOD_BLUEPRINT)}))
    review = payload["review_markdown"]
    assert "培训记录" in review and "topic__c" in review, review[:200]
    assert payload["checklist_markdown"].startswith("# 执行清单")
    assert payload["summary_line"].startswith("将创建")
    assert payload["highlights"]["objectCount"] == 1
    assert payload["highlights"]["fieldCount"] == 2
    # 文件也落了（页面/排障要看）
    for key in ("spec", "checklist"):
        assert pathlib.Path(payload["files"][key]).is_file()


def test_create_confirmation_summary_talks_about_the_blueprint(hub) -> None:
    """危险操作的确认摘要要说"将要建出什么"，而不是复述 `blueprint_id`。

    ★ 2026-09-22 用户下达：「弹出的蓝图/审核界面，请你不要放一堆文字上去，请设计排版，
    言简意赅，用户在执行时没有时间去仔细看 UI 上比蚂蚁还小的字」
    ⇒ 摘要的形状**本身**也是契约（见 `plugin_api.ToolSpec.summary_fn`）：
    **第一行标题 + `·` 开头的要点，最多 5 行、每行不超过 90 字**。弹窗按它排版，
    所以这里同时钉住"说什么"和"长什么样"。
    """
    blueprint_id = _payload(_call(hub, "factory_preview_blueprint",
                                  {"blueprint": json.dumps(GOOD_BLUEPRINT)}))["blueprint_id"]
    spec = hub.get("factory_create")
    args = spec.args_model(blueprint_id=blueprint_id, account_id="acc-1")
    text = approvals.summarize(spec, {}, model=args)

    # 说什么：对象名 + 编码、字段数量、布局
    assert "培训记录" in text and "training_record__c" in text, text
    assert "字段 2 个" in text, text
    assert "布局" in text and "列表 2 列" in text, text
    assert "blueprint_id" not in text, "又把参数名当摘要了"

    # 长什么样：标题 1 行 + 要点若干，总行数 ≤5、每行 ≤90 字（"不堆文字"的正面判据）
    lines = [line for line in text.split("\n") if line.strip()]
    assert len(lines) <= 5, f"摘要 {len(lines)} 行，弹窗一屏读不完：{lines}"
    assert not lines[0].startswith("·"), "第一行必须是标题，不是要点"
    for line in lines[1:]:
        assert line.startswith("· "), f"要点行必须以 `· ` 开头（弹窗按它排版）：{line}"
    for line in lines:
        assert len(line) <= 90, f"这一行 {len(line)} 字，弹窗里会变成一坨：{line}"


def test_create_summary_falls_back_instead_of_breaking_the_gate(hub, tmp_path) -> None:
    """摘要失败必须**退回通用口径**，绝不能让确认框弹不出来。"""
    spec = hub.get("factory_create")
    args = spec.args_model(blueprint_id="does-not-exist", account_id="acc-1")
    text = approvals.summarize(spec, {"account_id": "acc-1"}, model=args)
    assert text, "摘要为空会让确认卡显示空白"
    assert "dangerous" in text  # 通用口径带风险级


# ---------------------------------------------------------------- 问题②：规格交给 agent

def test_spec_is_served_and_generated_from_the_models(hub) -> None:
    """规格工具返回的东西必须**与校验器同源**，并且不超工具结果上限。"""
    from workbench.plugins.factory import PLUGIN  # noqa: F401  （确保插件被导入）

    payload = _payload(_call(hub, "factory_blueprint_spec", {}))
    assert payload["dataTypes"]["4"] == "选项"
    assert payload["referenceDataTypes"] == [13, 15, 16, 23]
    assert payload["engine_pipeline"], "执行顺序要写给模型看"
    # 关键键名必须在（改造前它们在 plugins/ 里零命中，模型只能猜）
    text = json.dumps(payload, ensure_ascii=False)
    for key in ("referenceObjectCode", "picklistCode", "bindToStatusCode", "renameStatuses"):
        assert key in text, f"规格里缺 {key}"

    from workbench.plugin_api import MAX_RESULT_CHARS

    assert len(text) < MAX_RESULT_CHARS, (
        f"规格 {len(text)} 字已超工具结果上限 {MAX_RESULT_CHARS}——"
        "会在返回路上被静默截断（模型拿到半份规格，比没有更坏）")


def test_spec_example_actually_validates() -> None:
    """规格里的示例必须真能过校验——否则模型照抄一份就用不了。"""
    from workbench.services.egmp.writers.blueprint import validate_blueprint
    from workbench.services.egmp.writers.spec import MINIMAL_EXAMPLE

    issues = validate_blueprint(MINIMAL_EXAMPLE)
    assert not [i for i in issues if i.level == "error"], [i.message for i in issues]


def test_every_model_field_is_annotated() -> None:
    """模型加了字段却没补注解 → 这里红。

    要求"完整覆盖"而不是"重要字段就够"：漏一条的后果是模型对着一个没有解释的键名猜语义，
    而**注解缺失是静默的**——规格照样生成得出来，只是少一行字。
    """
    from workbench.services.egmp.writers import spec as spec_mod

    missing = [f"{model.__name__}.{field}"
               for _, model, _ in spec_mod._MODEL_GROUPS
               for field in model.model_fields
               if f"{model.__name__}.{field}" not in spec_mod._NOTES]
    # 顶层 Blueprint 也要覆盖
    missing += [f"Blueprint.{f}" for f in spec_mod.Blueprint.model_fields
                if f"Blueprint.{f}" not in spec_mod._NOTES]
    assert missing == [], f"这些字段没有写给模型看的说明：{missing}"


# ---------------------------------------------------------------- 问题④：第二套需求→蓝图已删除

def test_orchestrate_command_is_gone() -> None:
    """旧的「需求→LLM 生成蓝图」路径必须拒绝，且**说清该走哪条路**。"""
    from fastapi.testclient import TestClient

    from workbench.api import app

    client = TestClient(app)
    resp = client.post("/api/factory/run", json={
        "blueprint_id": "x", "account_id": "y", "command": "orchestrate", "confirmed": True,
    })
    assert resp.status_code == 400
    assert "create" in resp.json()["detail"]
    assert "对话" in resp.json()["detail"], "要让调用方知道改用什么，而不是只报'未知命令'"


def test_generation_modules_are_deleted() -> None:
    """`generate.py` / `complexity.py` 连同它们的 LLM 循环一起删掉，不留空壳。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("generate.py", "complexity.py"):
        assert not (root / "workbench" / "services" / "egmp" / name).exists(), (
            f"{name} 还在——留着第二个 LLM 循环，早晚有人用它")


# ---------------------------------------------------------------- 问题①：预算可配

def test_max_steps_is_configurable_and_clamped() -> None:
    """步数预算是"旋钮"不是"校验"：越界值夹到范围内，坏值回默认。"""
    from workbench.harness.types import DEFAULT_MAX_STEPS, MAX_MAX_STEPS, MIN_MAX_STEPS
    from workbench.services import settings, storage

    assert DEFAULT_MAX_STEPS == 40
    assert settings.load_max_steps() == DEFAULT_MAX_STEPS  # 未设置时

    assert settings.save_max_steps(60) == 60
    assert settings.load_max_steps() == 60

    assert settings.save_max_steps(99999) == MAX_MAX_STEPS, "上限之外要夹住，不能变成无限刹车"
    assert settings.save_max_steps(1) == MIN_MAX_STEPS

    storage.set_setting(settings.MAX_STEPS_KEY, "not-a-number")
    assert settings.load_max_steps() == DEFAULT_MAX_STEPS, "坏值必须回默认，不能让对话起不来"
    storage.set_setting(settings.MAX_STEPS_KEY, str(DEFAULT_MAX_STEPS))


def test_chat_route_reads_max_steps_from_settings() -> None:
    """路由必须**真的接线**：改造前 `AgentConfig` 只传了 temperature 与 system_prompt，
    于是设置页无论放什么滑块都不生效（这正是"轮次少且改不了"的成因）。"""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "workbench" / "api" / "routes_chat.py").read_text(encoding="utf-8")
    assert "max_steps=load_max_steps()" in src
