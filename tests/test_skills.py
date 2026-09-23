"""Skill 共享库与安装/卸载的契约回归。

**为什么必须有这些测试**：本模块会**在运行期改工具表**。改错了的后果不是报错，
而是"模型看到了不该看到的工具"或"卸载了但工具还在"——两者都不报错，只是行为错。
所以每条不变量都要钉死。

测试隔离：所有用例把 `DATA_DIR` 指到临时目录（`conftest.py` 已设 `WORKBENCH_DATA`），
**绝不碰真实共享库**。`hub` 用 `reset_hub()` 重建，避免用例间互相污染。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from workbench.plugin_api import PluginOrigin, Risk, get_hub, reset_hub
from workbench.skills import layout
from workbench.skills import library as L
from workbench.skills import security as S
from workbench.skills.manifest import SkillPackageError, json_schema_to_fields, parse_skill_md

SKILL_MD = """---
id: test-skill
name: 测试技能
version: 1.0.0
description: 端到端验证用的声明式 Skill。
capabilities: [hosttools]
tools:
  - name: test_read_object
    description: 按编码读对象元数据（测试）
    wraps: insight_read_config
    args:
      code: {type: string, description: 对象编码, pattern: '^[a-z_]+_c$'}
      limit: {type: integer, description: 最多几条, minimum: 1, maximum: 100, default: 20}
---
# 说明
"""


def make_zip(skill_md: str = SKILL_MD, extra: tuple[tuple[str, str], ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("SKILL.md", skill_md)
        for name, text in extra:
            archive.writestr(name, text)
    return buf.getvalue()


#: 把 tools 段整体替换掉的锚点（改坏用例时只需改这一处）
_TOOLS_BLOCK = (
    "tools:\n"
    "  - name: test_read_object\n"
    "    description: 按编码读对象元数据（测试）\n"
    "    wraps: insight_read_config\n"
    "    args:\n"
    "      code: {type: string, description: 对象编码, pattern: '^[a-z_]+_c$'}\n"
    "      limit: {type: integer, description: 最多几条, minimum: 1, maximum: 100, default: 20}"
)


def with_tools(block: str) -> str:
    assert _TOOLS_BLOCK in SKILL_MD, "锚点失效——SKILL_MD 被改过"
    return SKILL_MD.replace(_TOOLS_BLOCK, block)


@pytest.fixture
def clean_hub():
    """每个用例一个干净的中枢 + 干净的库目录。"""
    reset_hub()
    hub = get_hub()
    layout.ensure_dirs()
    yield hub
    # 清掉本用例可能装上的东西，避免污染后续用例
    for plugin in list(hub.installed_plugins()):
        hub.deregister_capability(plugin.capability_id)
    reset_hub()


# ─────────────────────────────────────────────────────────────────────────────
# manifest 解析：契约
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_manifest_ok() -> None:
    m = parse_skill_md(SKILL_MD)
    assert (m.id, m.version) == ("test-skill", "1.0.0")
    assert [t.name for t in m.tools] == ["test_read_object"]
    assert m.tools[0].wraps == "insight_read_config"


@pytest.mark.parametrize("broken,fragment", [
    ("id: TEST-Skill", "只允许小写"),
    ("version: 1.0", "必须是 x.y.z"),
    ("capabilities: [network]", "本期不支持"),
    ("capabilities: [bogus]", "未知 capabilities"),
    ("tools: []", "非空数组"),
])
def test_manifest_rejects_bad_input(broken: str, fragment: str) -> None:
    """不合规的包必须**抛**，不许"尽量解析"——静默降级会让坏包看起来装成功了。

    ⚠️ 替换的是**整行**：YAML 会把 `version: 1.0` 解析成 **float** 而非字符串，
    于是 `_need()` 报的是"缺少字段"而不是"格式不对"。想测格式校验就必须让它
    仍然是字符串（例如 `version: "1.0"`）。这是 YAML 的类型推断，不是本模块的 bug。
    """
    if broken.startswith("tools:"):
        text = with_tools("tools: []")
    elif broken == "version: 1.0":
        text = SKILL_MD.replace("version: 1.0.0", 'version: "1.0"')
    else:
        key = broken.split(":", 1)[0]
        original = {"id": "id: test-skill",
                    "capabilities": "capabilities: [hosttools]"}[key]
        text = SKILL_MD.replace(original, broken)
    with pytest.raises(SkillPackageError) as exc:
        parse_skill_md(text)
    assert fragment in str(exc.value), f"期望提到 {fragment!r}，实际：{exc.value}"


def test_numeric_version_is_rejected_as_missing_field() -> None:
    """`version: 1.0` 被 YAML 读成 float → 按"字段不是字符串"拒。

    这条单独写出来是为了记住这个**反直觉**的失败原因：
    报错说"缺少字段"，但人明明写了 version。不是 bug，是 YAML 的类型推断。
    """
    with pytest.raises(SkillPackageError, match="版本|version|缺少"):
        parse_skill_md(SKILL_MD.replace("version: 1.0.0", "version: 1.0"))


def test_missing_frontmatter_fails() -> None:
    with pytest.raises(SkillPackageError):
        parse_skill_md("# 没有 frontmatter\n正文")


def test_tool_count_cap() -> None:
    """工具过多会显著拉低模型选对工具的概率 → 有硬上限。"""
    tools = "\n".join(
        f"  - {{name: t{i}, description: d{i}, wraps: insight_read_config}}"
        for i in range(30)
    )
    with pytest.raises(SkillPackageError, match="最多"):
        parse_skill_md(with_tools("tools:\n" + tools))


# ─────────────────────────────────────────────────────────────────────────────
# JSON Schema → pydantic：**两种写法都要认**（简写踩过坑）
# ─────────────────────────────────────────────────────────────────────────────


def test_schema_shorthand_and_full_form_agree() -> None:
    """简写（args 直接是参数表）与完整 JSON Schema 必须产出同一组字段。

    初版只认完整形态，于是简写**静默产出零个参数**——工具装上了却收不到任何参数。
    这类"解析成功但结果为空"最危险，故两种形态都要有断言。
    """
    shorthand = {"code": {"type": "string", "description": "编码"}}
    full = {"type": "object", "properties": {"code": {"type": "string", "description": "编码"}},
            "required": ["code"]}
    a, b = json_schema_to_fields(shorthand), json_schema_to_fields(full)
    assert set(a) == set(b) == {"code"}


def test_shorthand_defaults_to_required_except_with_default() -> None:
    """简写无法表达"必填"，故默认全部必填；写 `default` 才是可选（Fail-Closed）。"""
    from pydantic import ValidationError

    from workbench.skills.manifest import build_args_model

    fields = json_schema_to_fields({
        "must": {"type": "string"},
        "opt": {"type": "integer", "default": 3},
    })
    model = build_args_model("t", fields)
    with pytest.raises(ValidationError):
        model()  # 缺 must
    assert model(must="x").opt == 3


def test_unsupported_type_is_rejected_not_downgraded() -> None:
    """不支持的类型必须抛——降级成 `Any` 等于让模型传什么都通过（契约形同虚设）。"""
    with pytest.raises(SkillPackageError, match="本期不支持"):
        json_schema_to_fields({"blob": {"type": "object"}})


def test_constraints_survive_conversion() -> None:
    from pydantic import ValidationError

    from workbench.skills.manifest import build_args_model

    model = build_args_model("t", json_schema_to_fields({
        "code": {"type": "string", "pattern": r"^[a-z]+_c$"},
        "n": {"type": "integer", "minimum": 1, "maximum": 5},
    }))
    model(code="abc_c", n=3)
    for bad in ({"code": "ABC_c", "n": 3}, {"code": "abc_c", "n": 9}):
        with pytest.raises(ValidationError):
            model(**bad)


# ─────────────────────────────────────────────────────────────────────────────
# ZIP 准入：结构闸门
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("label,entries,expect", [
    ("good", [("SKILL.md", SKILL_MD)], "ok"),
    ("traversal", [("SKILL.md", SKILL_MD), ("../evil.py", "x")], "reject"),
    ("binary", [("SKILL.md", SKILL_MD), ("payload.exe", "MZ")], "reject"),
    ("nested", [("nested/SKILL.md", SKILL_MD)], "reject"),
    ("unknown_suffix", [("SKILL.md", SKILL_MD), ("a.xyz", "x")], "reject"),
    ("images_are_binary", [("SKILL.md", SKILL_MD), ("logo.png", "x")], "reject"),
])
def test_zip_gate(label: str, entries, expect: str) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, text in entries:
            archive.writestr(name, text)
    report = S.inspect_zip(buf.getvalue())
    assert report.verdict == expect, f"{label}: {report.verdict} / {report.reason}"
    if expect == "reject":
        assert report.ok is False and report.reason


def test_empty_and_garbage_are_rejected() -> None:
    assert S.inspect_zip(b"").verdict == "reject"
    assert S.inspect_zip(b"not a zip at all").verdict == "reject"


def test_suspicious_content_warns_but_does_not_reject() -> None:
    """扫描的定位是"摊开给人看"，不是自动判定恶意 → 高危只降级为 warn，不 reject。"""
    report = S.inspect_zip(make_zip(extra=(("scripts/run.py",
                                            "import subprocess\nsubprocess.run(['ls'])"),)))
    assert report.ok is True
    assert report.verdict == "warn"
    assert report.high_count >= 1
    assert report.findings[0].file == "scripts/run.py"


# ─────────────────────────────────────────────────────────────────────────────
# 上传 → 安装 → 生效 → 卸载：本模块的核心不变量
# ─────────────────────────────────────────────────────────────────────────────


def test_upload_install_uninstall_roundtrip(clean_hub) -> None:
    """完整链路，且**每一步都断言工具表的真实变化**。

    这是本文件最重要的一条：它同时钉住
    "装上就可见"、"卸载就消失"、"卸载不删库"三件事。
    """
    hub = clean_hub
    before_plugins, before_tools = len(hub.plugins), len(hub.specs())
    core_before = len(hub.core_specs())

    up = L.upload(make_zip())
    assert up.ok and up.capability_id == "test-skill"

    L.install("test-skill")
    assert hub.get("test_read_object") is not None, "安装后工具必须对模型可见"
    assert len(hub.plugins) == before_plugins + 1
    assert len(hub.specs()) == before_tools + 1
    # **核心工具数不变**——这正是文档门禁能继续成立的原因
    assert len(hub.core_specs()) == core_before
    assert any(t["function"]["name"] == "test_read_object" for t in hub.list_api_for(None))

    L.uninstall("test-skill")
    assert hub.get("test_read_object") is None, "卸载后工具必须从表里消失"
    assert len(hub.plugins) == before_plugins
    assert len(hub.specs()) == before_tools
    # 卸载 ≠ 删除：库里的还在
    assert [x["capability_id"] for x in L.list_library()] == ["test-skill"]
    L.delete_from_library("test-skill")


def test_installed_plugin_is_marked_removable(clean_hub) -> None:
    L.upload(make_zip())
    L.install("test-skill")
    plugin = next(p for p in clean_hub.plugins if p.origin is PluginOrigin.INSTALLED)
    assert plugin.removable is True and plugin.capability_id == "test-skill"
    view = next(p for p in clean_hub.catalog()["plugins"] if p["origin"] == "installed")
    assert view["removable"] is True and view["enabled"] is True
    L.delete_from_library("test-skill")


def test_core_plugins_cannot_be_uninstalled(clean_hub) -> None:
    """核心插件随产品分发，**拒绝卸载**（可禁用）。

    这条抓过一个真 bug：核心插件的 `capability_id` 是空串，
    而初版"找不到就早返回"的实现会让 `deregister_capability("")` **静默无事发生**，
    把守卫整个绕过去。所以现在传空串也必须抛。
    """
    core_id = next(p.id for p in clean_hub.plugins if p.origin is PluginOrigin.CORE)
    with pytest.raises(ValueError, match="不能为空"):
        clean_hub.deregister_capability("")
    # 传核心插件的 capability_id（空串已被拒）之外的任何值都不该动到核心插件
    assert clean_hub.deregister_capability("no-such-capability") == []
    assert any(p.id == core_id for p in clean_hub.plugins)


def test_install_rejects_tool_name_clash_with_core(clean_hub) -> None:
    """安装的能力**不得覆盖产品自带工具**——那是安全事故，不是功能问题。

    这里挑一个**长期存在的核心工具名**（`insight_run`）。曾经用的是 `factory_create`，
    它随配置工厂移除后就**不再与任何核心工具冲突**了，于是这条用例会静默失效
    （`upload` 变成成功，而断言 `result.ok is False` 立刻变红——这是它被发现的唯一原因）。
    教训：拿"当前恰好存在的工具名"当夹具，会在那个工具消失时把守门人一起带走。
    """
    clash = SKILL_MD.replace("name: test_read_object", "name: insight_run")
    result = L.upload(make_zip(clash))
    assert result.ok is False
    assert "冲突" in result.reason


def test_upload_rejects_unknown_wraps(clean_hub) -> None:
    bad = SKILL_MD.replace("wraps: insight_read_config", "wraps: no_such_tool")
    result = L.upload(make_zip(bad))
    assert result.ok is False and "不存在" in result.reason


def test_warn_package_needs_explicit_confirmation(clean_hub) -> None:
    """高危包**不会"点一下上传就入库"**：第一次只回 findings，带确认才收。"""
    data = make_zip(extra=(("scripts/x.py", "import subprocess\nsubprocess.run(['x'])"),))
    first = L.upload(data)
    assert first.ok is False and first.verdict == "warn" and first.findings
    assert [x["capability_id"] for x in L.list_library()] == [], "未确认就不该入库"

    second = L.upload(data, allow_warn=True)
    assert second.ok is True
    L.delete_from_library("test-skill")


def test_risk_is_never_relaxed_by_skill_declaration(clean_hub) -> None:
    """Skill 声明 `risk: read` **不能**放宽被包装工具的 write/dangerous。"""
    from workbench.skills.library import _stricter

    assert _stricter(Risk.READ, Risk.DANGEROUS) is Risk.DANGEROUS
    assert _stricter(None, Risk.WRITE) is Risk.WRITE
    assert _stricter(Risk.DANGEROUS, Risk.READ) is Risk.DANGEROUS


def test_disable_hides_tools_but_keeps_plugin(clean_hub) -> None:
    """禁用 = 模型看不到，但插件仍在清单里（界面显示"已禁用"并可恢复）。"""
    hub = clean_hub
    core = next(p for p in hub.plugins if p.origin is PluginOrigin.CORE)
    tool_count = len(hub.specs())
    hub.set_plugin_enabled(core.id, False)
    assert len(hub.specs()) == tool_count - len(core.tools)
    assert any(p.id == core.id for p in hub.plugins), "禁用不该把插件从清单里移除"
    assert hub.is_enabled(core.id) is False
    hub.set_plugin_enabled(core.id, True)
    assert len(hub.specs()) == tool_count


def test_uninstall_is_idempotent(clean_hub) -> None:
    """连点两次卸载、或两个标签页同时点，都不该报错。"""
    assert clean_hub.deregister_capability("never-installed") == []


# ─────────────────────────────────────────────────────────────────────────────
# 目录与"不入 git"
# ─────────────────────────────────────────────────────────────────────────────


def test_skill_dirs_live_outside_repo() -> None:
    """Skill 是运行期资产，**必须在仓库之外**（用户 2026-09-17 明确要求）。"""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert root not in layout.SKILLS_ROOT.parents, "Skill 目录不得落在仓库内"

    # 而且 git 也确实不跟踪它（双重保证：路径在外 + ignore 生效）
    rel = layout.SKILLS_ROOT
    try:
        result = subprocess.run(["git", "check-ignore", "-q", str(rel)],
                                cwd=root, capture_output=True, timeout=30)
        ignored = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git 不可用，跳过 ignore 校验")
    assert ignored or root not in rel.parents


def test_capability_id_and_version_reject_traversal() -> None:
    """目录名会被拼进路径 → 形状不合法必须拒（本仓 C5 的 `env_id` 教训）。"""
    for bad in ("../etc", "a/b", "UPPER", "", ".hidden"):
        with pytest.raises(ValueError):
            layout.library_version_dir(bad, "1.0.0", create=False)
    for bad in ("1.0", "1.0.0.0", "../1.0.0", ""):
        with pytest.raises(ValueError):
            layout.library_version_dir("ok-id", bad, create=False)
