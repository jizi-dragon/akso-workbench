"""路径安全门禁（离线）。

存在理由（docs/INSIGHT-SPEC.md §4.2）：洞察的对象 code 来自用户输入，此前只做 `strip()`
就拼进文件名。实测 `../../PWNED` 会把产物写到 job 目录之外。本文件把两类保证钉死：

- **不越界**：任何恶意 code 的产物都必须留在目标目录内；
- **不误伤**：正常平台编码（`capa_plan__c` 等）必须**原样**通过，否则会打断既有产物命名。

> 落盘级的端到端验证在 test_workspace.py（`run_spider` + 恶意 code）：`inventory`/`understand`
> 下线后，不再有「以对象 code 命名单个产物文件」的路径，code 只进**目录**名。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.services import paths

# 正常编码必须恒等通过——列错一个都会改变既有产物文件名
NORMAL_CODES = [
    "capa_plan__c",
    "training_mgr__c",
    "change_control__c",
    "deviation__c",
    "standard_val_object__ak",
    "v1.2",
    "a-b_c.d",
]

DANGEROUS = [
    "../evil",
    "../../evil",
    "../../../../workbench/config",
    "..\\..\\evil",
    "..",
    ".",
    "",
    "   ",
    "...",
    "C:/Temp/pwn",
    "C:\\Temp\\pwn",
    "//server/share/x",
    "abc/def",
    "abc\\def",
    "a\x00b",
    "a\x1fb",
    "con",
    "NUL",
    "COM1",
    "lpt9",
    "trailing.",
    "trailing ",
    "a\u202eb",  # 双向覆盖字符
]


# ---------------------------------------------------------------- 恒等性（不误伤）


@pytest.mark.parametrize("code", NORMAL_CODES)
def test_normal_codes_pass_through_unchanged(code: str) -> None:
    assert paths.safe_path_component(code) == code


def test_chinese_codes_are_preserved() -> None:
    """中文在 NTFS 上合法且可读，应原样保留。"""
    assert paths.safe_path_component("培训管理__c") == "培训管理__c"


def test_long_code_is_truncated_but_keeps_prefix() -> None:
    out = paths.safe_path_component("a" * 500)
    assert len(out) <= 80
    assert out.startswith("aaa")


# ---------------------------------------------------------------- 安全性（不越界）


@pytest.mark.parametrize("raw", DANGEROUS, ids=lambda s: repr(s)[:26])
def test_dangerous_input_never_yields_separator(raw: str) -> None:
    comp = paths.safe_path_component(raw)
    assert comp, f"{raw!r} 产出了空成分"
    assert comp not in {".", ".."}
    for bad in ("/", "\\", ":"):
        assert bad not in comp, f"{raw!r} → {comp!r} 仍含 {bad!r}"
    assert not Path(comp).is_absolute()


@pytest.mark.parametrize("raw", DANGEROUS, ids=lambda s: repr(s)[:26])
def test_dangerous_input_stays_exact_depth_under_base(tmp_path: Path, raw: str) -> None:
    """产物必须正好落在 base 的**直接**子级，不多一层也不少一层。"""
    comp = paths.safe_path_component(raw)
    target = paths.resolve_within(tmp_path, comp)
    assert target.parent == tmp_path.resolve(), f"{raw!r} → {target} 逃出了 {tmp_path}"


def test_reserved_device_names_are_neutralized() -> None:
    for raw in ("CON", "con", "NUL", "COM1", "LPT9", "con.txt"):
        comp = paths.safe_path_component(raw)
        assert comp.split(".")[0].upper() not in paths._RESERVED_NAMES


def test_format_control_chars_removed() -> None:
    assert "\u202e" not in paths.safe_path_component("a\u202eb")
    assert "\ufeff" not in paths.safe_path_component("a\ufeffb")


def test_control_chars_replaced_not_kept() -> None:
    comp = paths.safe_path_component("a\x00\x1fb")
    assert "\x00" not in comp and "\x1f" not in comp


def test_empty_input_uses_fallback() -> None:
    assert paths.safe_path_component("") == "item"
    assert paths.safe_path_component(None) == "item"  # type: ignore[arg-type]
    assert paths.safe_path_component("..", fallback="obj") == "obj"


# ---------------------------------------------------------------- resolve_within


def test_resolve_within_accepts_safe_component(tmp_path: Path) -> None:
    assert paths.resolve_within(tmp_path, "capa_plan__c.json").parent == tmp_path.resolve()


def test_resolve_within_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        paths.resolve_within(tmp_path, "..", "outside.json")


def test_resolve_within_rejects_absolute_outside(tmp_path: Path) -> None:
    """绝对路径作为成分时 Windows 上会替换基路径——落在 base 之外必须被拒。"""
    outside = tmp_path.parent / "outside-x" / "x.json"
    assert not str(outside).startswith(str(tmp_path))
    with pytest.raises(ValueError):
        paths.resolve_within(tmp_path, str(outside))


def test_resolve_within_allows_absolute_inside(tmp_path: Path) -> None:
    """绝对路径若本来就在 base 内，应被接受（不是越界）。"""
    inside = tmp_path / "sibling" / "x.json"
    assert paths.resolve_within(tmp_path, str(inside)) == inside.resolve()
