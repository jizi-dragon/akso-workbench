r"""源码卫生：源码里**不许有非法转义序列**（`SyntaxWarning` 扫全仓）。

## 为什么单列一条

`"\p"` 这类转义写在 **docstring** 里时，Python 不报错、不抛异常、ruff 默认也不管 ——
它只发一条 `SyntaxWarning`，而警告在 pytest 输出里是**淹没在末尾的一片黄字**。

代价有两面：

1. **它是定时炸弹**：`SyntaxWarning` 在 Python 3.12 起是对"未来会变成 `SyntaxError`"
   的预告（PEP 的弃用路径），而现在它已经在**编译期**产生噪声。
2. **它污染新信号**：本次实录 —— 写依赖方向门禁时看到 `<unknown>:21` 的警告，
   第一反应是"我新写的守卫有问题"，实际是 `workbench/plugins/insight.py` 的
   docstring 里 `workbench\plugins\*.py` 那个 `\p`。**当一个警告源长期存在时，
   新出现的同类警告就失去了提示作用** —— 这与本仓"日志不复制审计内容、
   两处都写全量 = 第二个会漂"是同一类问题（信号被稀释）。

## 判据的形状

逐文件 `ast.parse`（不是 import）并捕获 `SyntaxWarning`：这样**不需要真的导入**，
因而没有副作用，也能覆盖不会有测试去 import 的文件。
"""

from __future__ import annotations

import ast
import pathlib
import warnings

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 扫这些目录下的全部 .py（不含依赖与产物目录）。
SCAN_DIRS = ("workbench", "tests", "tools")


def test_no_invalid_escape_sequences_in_sources() -> None:
    """全仓源码 `ast.parse` 一遍，逐条报出非法转义序列。"""
    offenders: list[str] = []
    scanned = 0
    for dirname in SCAN_DIRS:
        for path in sorted((ROOT / dirname).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            scanned += 1
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except SyntaxError as exc:  # 语法错误另有归属（ruff / import 会先炸）
                    offenders.append(f"{path.relative_to(ROOT)}:{exc.lineno} 语法错误：{exc.msg}")
                    continue
            for item in caught:
                if issubclass(item.category, SyntaxWarning):
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{item.lineno} {item.message}"
                    )

    assert scanned > 100, f"只扫到 {scanned} 个文件，判据的扫描范围可能已被改坏"
    assert not offenders, (
        "源码里有非法转义序列（docstring 里最常见）：\n  " + "\n  ".join(offenders) + "\n"
        r"处置：把那个字符串改成原始字符串（`r\"\"\"…\"\"\"`），或把 `\` 写成 `\\`。"
    )
