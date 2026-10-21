"""沙箱的**工作区边界与权限**（2026-10-19 新增）。

## 它守的是什么

沙箱此前只有一个写死的根（`DATA_DIR/sandbox`）和三条策略；"权限"只存在于
策略声明里（`allow_network` / `allow_subprocesses`），**没有用户可设的边界**。
这组用例钉住四件事：

1. **工作区根可配、可校验**：绝对路径、不能是盘符根、不能命中敏感表、不能是系统目录；
2. **权限是预检闸门**：只读模式拒绝声明会写的策略；联网/派生进程按开关拒绝；
3. **执行器真的用了新根**：`cwd` 越出工作区根 ⇒ 拒；
4. **API 形状**：GET/PUT/reset 的行为与 400 映射。

★ 这些是"权限 + 工作区边界"的**本仓可强制执行的那一半**；内核级只读不在本仓范围
（见 `layout.describe_workspace()['honest_limit']`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from workbench import config
from workbench.api import routes_sandbox
from workbench.sandbox import layout, policy, runner


@pytest.fixture()
def workspace_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把配置文件指到 tmp；不碰用户真实数据目录。"""
    cfg = tmp_path / "sandbox-workspace.json"
    monkeypatch.setattr(layout, "CONFIG_PATH", cfg)
    yield cfg
    if cfg.exists():
        cfg.unlink()


def test_default_workspace_is_conservative(workspace_config: Path) -> None:
    settings = layout.workspace_settings()
    assert settings.root == layout.SANDBOX_ROOT.resolve() or settings.root == layout.SANDBOX_ROOT
    assert settings.mode == "read-write"
    assert settings.allow_network is False
    assert settings.allow_subprocess is False


def test_malformed_config_falls_back_without_crashing(workspace_config: Path, tmp_path: Path) -> None:
    workspace_config.write_text(
        '{"root": "%s", "mode": "bogus", "allowNetwork": "false", '
        '"allowSubprocess": "true", "updatedAt": "not-a-number"}'
        % str(tmp_path / "ws").replace("\\", "\\\\"),
        encoding="utf-8",
    )
    settings = layout.workspace_settings()
    assert settings.mode == "read-write"
    assert settings.allow_network is False
    assert settings.allow_subprocess is True
    assert settings.updated_at == 0


def test_save_and_reload_workspace(workspace_config: Path, tmp_path: Path) -> None:
    target = tmp_path / "agent-workspace"
    saved = layout.save_workspace(
        root=str(target), mode="read-only", allow_network=True, allow_subprocess=False,
    )
    assert saved.root == target.resolve()
    assert saved.mode == "read-only"
    assert saved.allow_network is True
    assert workspace_config.exists()

    again = layout.workspace_settings()
    assert again.root == target.resolve()
    assert again.mode == "read-only"


def test_root_validation_rejects_unsafe_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="绝对路径"):
        layout.validate_root("relative/path")
    with pytest.raises(ValueError, match="盘符|根"):
        layout.validate_root(str(Path(tmp_path.anchor)))
    with pytest.raises(ValueError, match="敏感"):
        layout.validate_root(str(config.DATA_DIR / "workbench.db"))
    if not layout._FORBIDDEN_ROOTS:
        pytest.skip("这台机器没有可识别的系统目录")
    forbidden = layout._FORBIDDEN_ROOTS[0]
    with pytest.raises(ValueError, match="系统目录"):
        layout.validate_root(str(forbidden / "definitely-not-a-workspace"))


def test_read_only_mode_refuses_write_policies(workspace_config: Path, tmp_path: Path) -> None:
    layout.save_workspace(root=str(tmp_path / "ws"), mode="read-only")
    assert "只读" in layout.permission_error(policy.get_policy("skill-script"))
    assert layout.permission_error(policy.get_policy("strict-readonly")) == ""


def test_network_and_subprocess_are_gated(workspace_config: Path, tmp_path: Path) -> None:
    layout.save_workspace(
        root=str(tmp_path / "ws"), mode="read-write",
        allow_network=False, allow_subprocess=False,
    )
    assert "联网" in layout.permission_error(policy.get_policy("mcp-stdio"))

    layout.save_workspace(allow_network=True, allow_subprocess=False)
    assert "派生进程" in layout.permission_error(policy.get_policy("mcp-stdio"))

    layout.save_workspace(allow_network=True, allow_subprocess=True)
    assert layout.permission_error(policy.get_policy("mcp-stdio")) == ""


def test_runner_uses_the_configured_workspace_root(
    workspace_config: Path, tmp_path: Path,
) -> None:
    root = tmp_path / "workspace-root"
    layout.save_workspace(root=str(root), mode="read-write")
    work = layout.run_dir(layout.new_run_id())
    assert root.resolve() in work.parents

    result = runner.run(runner.ExecRequest(
        argv=[sys.executable, "-c", "print('ok')"],
        policy_name="skill-script", cwd=work, timeout_s=10,
    ))
    assert result.ok, result.error
    assert str(root.resolve()) in " ".join(result.notes)


def test_runner_rejects_cwd_outside_workspace(workspace_config: Path, tmp_path: Path) -> None:
    layout.save_workspace(root=str(tmp_path / "ws"), mode="read-write")
    outside = tmp_path / "outside"
    outside.mkdir()
    result = runner.run(runner.ExecRequest(
        argv=[sys.executable, "-c", "print('no')"],
        policy_name="skill-script", cwd=outside,
    ))
    assert not result.ok
    assert "工作区根" in result.error


def test_runner_rejects_write_policy_in_read_only_mode(
    workspace_config: Path, tmp_path: Path,
) -> None:
    layout.save_workspace(root=str(tmp_path / "ws"), mode="read-only")
    work = layout.run_dir(layout.new_run_id())
    result = runner.run(runner.ExecRequest(
        argv=[sys.executable, "-c", "print('no')"],
        policy_name="skill-script", cwd=work,
    ))
    assert not result.ok
    assert "只读" in result.error


def test_runner_fails_closed_when_workspace_is_unresolvable(
    workspace_config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout.save_workspace(root=str(tmp_path / "ws"), mode="read-write")

    def boom() -> Path:
        raise OSError("磁盘掉了")

    monkeypatch.setattr(layout, "workspace_root", boom)
    result = runner.run(runner.ExecRequest(
        argv=[sys.executable, "-c", "print('no')"],
        policy_name="skill-script", cwd=tmp_path,
    ))
    assert not result.ok
    assert "工作区根不可用" in result.error


def test_api_workspace_roundtrip(workspace_config: Path, tmp_path: Path) -> None:
    got = routes_sandbox.get_workspace()
    assert got["root"]
    assert got["modes"] == ["read-only", "read-write"]
    assert got["honest_limit"]

    updated = routes_sandbox.set_workspace(routes_sandbox.WorkspaceIn(
        root=str(tmp_path / "api-ws"), mode="read-only",
        allowNetwork=False, allowSubprocess=False,
    ))
    assert updated["mode"] == "read-only"
    assert updated["root"] == str((tmp_path / "api-ws").resolve())

    reset = routes_sandbox.reset_workspace()
    assert reset["mode"] == "read-write"
    assert reset["root"] == str(layout.SANDBOX_ROOT)
