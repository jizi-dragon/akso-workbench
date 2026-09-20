"""`factory-tuning/` 与产品之间的**隔离**：它必须能整体删掉而不影响产品。

## 为什么钉这条

`factory-tuning/` 是"为了优化配置工厂而做的取证工具"，**不属于产品**
（用户 2026-09-20 定稿："监听功能本不属于本项目的功能，只是为了优化而做，请单独起文件夹"）。

这个隔离**不会自己保持**：某天有人图方便在 `workbench/` 里 `import` 一下
那边的分析函数，产品就多了一个非产品依赖——而它不会报错，只会在
"删掉 factory-tuning"或"打包时没带上它"的那一刻炸。

所以边界要写成断言：

1. `workbench/`（产品的全部）里**零引用** `factory-tuning`；
2. `factory-tuning/` **不在打包清单**里（`server.spec` 的 `hiddenimports` 与
   `build.ps1` 都不该提到它）；
3. 那边**不注册任何工具**（产品工具面只有 `plugins/*.py` 一处定义，见 AGENTS.md 规则 6）。
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TUNING = ROOT / "factory-tuning"


def test_factory_tuning_exists_and_is_self_contained() -> None:
    assert TUNING.is_dir(), "factory-tuning/ 不见了？它是调优取证工具，见它的 README"
    assert (TUNING / "README.md").is_file(), "没有 README 就没人知道它不属于产品"
    # README 必须写明"不属于产品"——那是这个目录存在的**前提**，不是补充说明
    readme = (TUNING / "README.md").read_text(encoding="utf-8")
    assert "不属于产品" in readme
    assert "脱敏" in readme, "脱敏规则没写在入口处 —— 而它采集的是真实平台流量"


def test_product_never_imports_factory_tuning() -> None:
    """产品侧零引用。**这是"能整体删掉"的唯一判据。**"""
    offenders: list[str] = []
    for path in (ROOT / "workbench").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "factory-tuning" in text or "factory_tuning" in text:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], (
        f"产品代码引用了调优目录：{offenders}——"
        "那样它就删不掉了，而这不是产品功能")


def test_factory_tuning_is_not_packaged() -> None:
    """打包清单里不许出现它（它不进安装包）。"""
    spec = (ROOT / "workbench" / "server.spec").read_text(encoding="utf-8")
    assert "factory-tuning" not in spec and "factory_tuning" not in spec
    build = (ROOT / "tools" / "build.ps1").read_text(encoding="utf-8", errors="replace")
    assert "factory-tuning" not in build


def test_factory_tuning_registers_no_tools() -> None:
    """它不许往工具面里加东西。

    产品工具面只有一处定义（`workbench/plugins/*.py` 的 `ToolSpec`，AGENTS.md 规则 6）；
    调优目录若注册了工具，模型就会看到一个"为了优化而存在"的工具——
    而用户明确说过那不是产品功能。
    """
    for path in TUNING.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "ToolSpec(" not in text, f"{path.name} 注册了工具——那不是产品功能"
        assert "add_plugin" not in text, f"{path.name} 挂了插件——那不是产品功能"
