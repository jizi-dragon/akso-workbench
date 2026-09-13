"""平台知识层门禁：端点表必须是**唯一事实来源**，语义词典必须单点定义。

对应 docs/INSIGHT-SPEC.md §3.1/§3.2。这一层的价值全在「不再各写一遍」——
所以本文件用**源码扫描**把它钉死，而不是只测运行时行为：

- `insight/` 与 `egmp/` 顶层**不得出现硬编码的 `/api/` 字面量**（历史上有约 14 处绕过端点表）；
- 带 `{code}` 的模板端点**必须经 `render()` 展开**（否则路径里会留下字面 `{code}`）；
- 后缀约定**只能定义在 kernel**（历史上 `auth.py`/`crawler.py`/`annotate.py` 各写了一遍）；
- 旧调用点通过垫片继续可用（搬家不该变成跨模块改动）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from workbench.services.egmp.kernel import endpoints, semantics

REPO = Path(__file__).resolve().parent.parent
EGMP = REPO / "workbench" / "services" / "egmp"
INSIGHT = EGMP / "insight"

# 允许出现字面量的地方：kernel 自己是定义处；replay.py 的夹具键需与录制一致但也走 kernel；
# writers/endpoints.py 是转发垫片（没有字面量）；tools/ 与 tests/ 不在扫描范围。
ALLOWED_LITERAL_FILES = {EGMP / "kernel" / "endpoints.py"}


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


# ---------------------------------------------------------------- 唯一事实来源


def test_no_hardcoded_endpoint_literals_in_insight() -> None:
    """insight/ 下不得再出现 `/api/...` 字面量——必须引用端点表。"""
    offenders: list[str] = []
    for path in _py_files(INSIGHT):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'["\']/api/', line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}  {line.strip()}")
    assert not offenders, "硬编码端点（应改用 kernel.endpoints）：\n  " + "\n  ".join(offenders)


def test_no_hardcoded_endpoint_literals_in_egmp_toplevel() -> None:
    """egmp/ 顶层（client/queries/auth/cache/replay…）同样不得硬编码。"""
    offenders: list[str] = []
    for path in _py_files(EGMP):
        if path.parent != EGMP or path in ALLOWED_LITERAL_FILES:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'["\']/api/', line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}  {line.strip()}")
    assert not offenders, "硬编码端点（应改用 kernel.endpoints）：\n  " + "\n  ".join(offenders)


def test_kernel_is_the_only_definer_of_endpoint_strings() -> None:
    """除 kernel/endpoints.py 外，不得再有模块「定义」端点常量。"""
    pattern = re.compile(r'^[A-Z][A-Z0-9_]*\s*=\s*["\']/api/')
    offenders: list[str] = []
    for path in _py_files(EGMP):
        if path in ALLOWED_LITERAL_FILES:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}  {line.strip()}")
    assert not offenders, "端点常量被重复定义：\n  " + "\n  ".join(offenders)


def test_insight_used_endpoints_are_all_declared() -> None:
    """insight 引用的每个 `endpoints.X` 都必须在表里真实存在。"""
    used: set[str] = set()
    for path in _py_files(INSIGHT):
        used.update(re.findall(r"endpoints\.([A-Z][A-Z0-9_]*)", path.read_text(encoding="utf-8")))
    missing = sorted(name for name in used if not hasattr(endpoints, name))
    assert not missing, f"引用了表里没有的端点：{missing}"
    assert used, "扫描不到任何端点引用——测试本身失效了？"


def test_table_has_no_duplicate_values() -> None:
    """同一个路径不该有两个**规范**常量名（兼容别名不算，它们按定义就是重复值）。"""
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for name in dir(endpoints):
        if name in endpoints.LEGACY_ALIASES:
            continue
        value = getattr(endpoints, name)
        if name.isupper() and isinstance(value, str) and value.startswith("/api/"):
            if value in seen and seen[value] != name:
                dupes.append(f"{value} ← {seen[value]} / {name}")
            seen.setdefault(value, name)
    assert not dupes, "端点表存在重复值：\n  " + "\n  ".join(dupes)


def test_legacy_aliases_are_declared_and_excluded() -> None:
    """别名集合本身要如实声明，否则上面的排除会变成藏问题。"""
    assert endpoints.LEGACY_ALIASES
    for name in endpoints.LEGACY_ALIASES:
        assert hasattr(endpoints, name), f"别名集合声明了不存在的名字：{name}"
        assert name not in endpoints.ALL_ENDPOINTS or getattr(endpoints, name) in endpoints.ALL_ENDPOINTS


def test_all_endpoints_index_is_populated_and_sorted() -> None:
    assert len(endpoints.ALL_ENDPOINTS) > 40
    assert list(endpoints.ALL_ENDPOINTS) == sorted(endpoints.ALL_ENDPOINTS)
    assert len(set(endpoints.ALL_ENDPOINTS)) == len(endpoints.ALL_ENDPOINTS)


# ---------------------------------------------------------------- 模板展开


def test_render_substitutes_path_param() -> None:
    assert endpoints.render(endpoints.OBJECT, code="capa_plan__c") == \
        "/api/openapi/v1.0/BasicObject/capa_plan__c"


def test_render_url_encodes_path_param() -> None:
    """对象编码来自用户输入：空格/斜杠/非 ASCII 必须编码，否则会改变路径结构。"""
    encoded = endpoints.render(endpoints.OBJECT, code="a b/c")
    assert " " not in encoded and encoded.endswith("/a%20b%2Fc")


def test_render_encodes_non_ascii() -> None:
    assert "%" in endpoints.render(endpoints.OBJECT, code="培训__c")


def test_template_endpoints_still_contain_placeholder() -> None:
    """模板常量本身保留 `{code}`；展开由 render 负责（两件事不要混）。"""
    assert "{code}" in endpoints.OBJECT
    assert "{code}" in endpoints.OBJECT_FIELD
    assert "{code}" in endpoints.OBJECT_LIFECYCLE


@pytest.mark.parametrize("path", [p for p in _py_files(INSIGHT)])
def test_templates_are_never_passed_unrendered(path: Path) -> None:
    """直接把模板常量喂给 get()/post() 会把字面 `{code}` 拼进 URL——必须经 render。"""
    text = path.read_text(encoding="utf-8")
    offenders = re.findall(
        r"\.(?:get|post|paged_post)\(\s*endpoints\.(OBJECT|OBJECT_FIELD|OBJECT_LIFECYCLE|"
        r"OBJECT_PICKLIST|WORKFLOW)\s*[,)]",
        text,
    )
    assert not offenders, f"{path.name} 传了未展开的模板：{offenders}"


# ---------------------------------------------------------------- 命名空间归属


def test_namespace_classification() -> None:
    assert endpoints.namespace_of(endpoints.OBJECT) == endpoints.NS_OPENAPI
    assert endpoints.namespace_of(endpoints.FIELD_PAGE) == endpoints.NS_PLATFORM
    assert endpoints.namespace_of(endpoints.STATUS_DETAIL) == endpoints.NS_CONFIG
    assert endpoints.namespace_of("/whatever") == "unknown"


def test_versioned_flag_separates_public_from_private() -> None:
    """私有端点在平台升级时最先断——排障要能一眼区分。"""
    assert endpoints.is_versioned(endpoints.AUTH)
    assert not endpoints.is_versioned(endpoints.WORKFLOW_STEPS)
    assert not endpoints.is_versioned(endpoints.USER_ACTION_LIST)


# ---------------------------------------------------------------- 兼容垫片


def test_writers_endpoints_shim_reexports() -> None:
    from workbench.services.egmp.writers import endpoints as shim

    assert shim.SAVE_BASIC_OBJECT == endpoints.SAVE_BASIC_OBJECT
    assert shim.FIELD_PAGE == endpoints.FIELD_PAGE
    assert shim.STEP_TYPE_NAMES == semantics.STEP_TYPE_NAMES


def test_auth_shim_reexports_semantics_and_client() -> None:
    from workbench.services.egmp import auth

    assert auth.INIT_STATUS_SUFFIX == semantics.INIT_STATUS_SUFFIX
    assert auth.is_reference_field(15) is True
    assert callable(auth.login)


def test_legacy_endpoint_aliases_still_resolve() -> None:
    """旧名（MENUGROUP_QUERY / OPENAPI_*）必须仍可用，否则会打断既有调用点。"""
    assert endpoints.MENUGROUP_QUERY == endpoints.MENU_GROUP_QUERY
    assert endpoints.OPENAPI_OBJECT == endpoints.OBJECT
    assert endpoints.OPENAPI_STATUS == endpoints.OBJECT_LIFECYCLE


# ---------------------------------------------------------------- 语义单点定义


def test_suffix_conventions_defined_only_in_kernel() -> None:
    """后缀约定不得在 insight 里再定义一份（历史上三处各写一遍，容易漂移）。"""
    for path in _py_files(INSIGHT):
        text = path.read_text(encoding="utf-8")
        assert "_table__ak" not in text or "import" in text, f"{path.name} 又写了一遍布局后缀"
        assert not re.search(r'^OBJECT_SUFFIX_CANDIDATES\s*=', text, re.M), \
            f"{path.name} 又定义了一遍对象后缀候选"
        assert not re.search(r'^LAYOUT_CODE_SUFFIXES\s*=', text, re.M), \
            f"{path.name} 又定义了一遍布局后缀"


def test_object_suffix_of_covers_all_known_suffixes() -> None:
    assert semantics.object_suffix_of("x__c") == "__c"
    assert semantics.object_suffix_of("x__ak") == "__ak"
    assert semantics.object_suffix_of("x__sys") == "__sys"
    assert semantics.object_suffix_of("plain") == ""


def test_unknown_codes_are_visible_not_silently_dropped() -> None:
    """未登记的枚举值必须留下痕迹（回 `dataType=<n>`），不得静默消失。"""
    assert semantics.data_type_name(99) == "dataType=99"
    assert semantics.data_type_name(None) == "dataType=None"
    assert semantics.behavior_type_name(999) == "behaviorType=999"
    assert semantics.step_type_name(777) == "stepType=777"


def test_known_codes_render_chinese_names() -> None:
    assert semantics.data_type_name(15) == "对象"
    assert semantics.behavior_type_name(7) == "发起工作流"
    assert semantics.step_type_name(70) == "判断"


def test_behavior_constants_match_dictionary() -> None:
    assert semantics.BEHAVIOR_TYPE_NAMES[semantics.BEHAVIOR_LAUNCH_WORKFLOW] == "发起工作流"
    assert semantics.BEHAVIOR_TYPE_NAMES[semantics.BEHAVIOR_UPDATE_STATUS] == "修改状态"
    assert semantics.EXECUTE_TYPE_NAMES[semantics.EXECUTE_ENTER_ACTION] == "进入状态自动执行"


def test_discovery_probe_endpoints_are_declared() -> None:
    """发现链第 3 步的候选探针：上游 README 给了端点，此前移植版只在注释里写了。"""
    assert endpoints.OBJECT_PROBES == (
        endpoints.OBJECT_PROBE_QUERY_LIST,
        endpoints.OBJECT_PROBE_PAGE_LIST,
        endpoints.OBJECT_PROBE_PAGE,
    )
    assert all(p in endpoints.ALL_ENDPOINTS for p in endpoints.OBJECT_PROBES)
