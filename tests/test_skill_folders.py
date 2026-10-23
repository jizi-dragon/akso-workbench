"""Agent Skills 标准：`SKILL.md` 文件夹发现与按需读取。"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.skills import folders


def _make(root: Path, name: str, *, description: str = "测试技能",
          body: str = "按步骤做。") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def skill_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setenv("WORKBENCH_SKILLS_DIRS", str(root))
    monkeypatch.chdir(tmp_path)
    return root


def test_discovers_standard_skill_folder(skill_root: Path) -> None:
    _make(skill_root, "write-report", description="写监管报告")
    catalog = folders.catalog()
    assert catalog[0]["name"] == "write-report"
    assert catalog[0]["description"] == "写监管报告"
    assert catalog[0]["source"] == "folder"
    assert "body" not in catalog[0]


def test_reads_body_on_demand(skill_root: Path) -> None:
    _make(skill_root, "write-report", body="先查法规，再写结论。")
    text = folders.read("write-report")
    assert "先查法规，再写结论。" in text
    with pytest.raises(KeyError):
        folders.read("missing")


def test_invalid_folder_is_skipped_not_fatal(skill_root: Path) -> None:
    _make(skill_root, "good")
    bad = skill_root / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    assert [item["name"] for item in folders.catalog()] == ["good"]


def test_duplicate_name_uses_first_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _make(first, "same", description="第一个")
    _make(second, "same", description="第二个")
    monkeypatch.setenv("WORKBENCH_SKILLS_DIRS", f"{first}{__import__('os').pathsep}{second}")
    catalog = folders.catalog()
    assert len(catalog) == 1
    assert catalog[0]["description"] == "第一个"


def test_symlink_directory_is_skipped(skill_root: Path, tmp_path: Path) -> None:
    target = _make(tmp_path / "elsewhere", "linked")
    link = skill_root / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("本机不允许创建目录符号链接")
    assert folders.catalog() == []


def test_list_and_read_skills_are_model_tools(skill_root: Path) -> None:
    import asyncio

    from workbench.hub import get_hub

    _make(skill_root, "write-report", description="写监管报告", body="先查法规。")
    hub = get_hub()
    listed = asyncio.run(hub.execute("list_skills", {}))
    assert listed.status.name == "OK"
    assert "write-report" in listed.content
    read = asyncio.run(hub.execute("read_skill", {"name": "write-report"}))
    assert read.status.name == "OK"
    assert "先查法规。" in read.content
    missing = asyncio.run(hub.execute("read_skill", {"name": "missing"}))
    assert missing.status.name != "OK"
    assert "没有这个文件夹 Skill" in missing.content
