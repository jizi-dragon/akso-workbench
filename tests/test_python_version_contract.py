"""解释器版本契约：`.python-version` == CI 钉的版本（P0-5，2026-09-17 立）。

## 为什么需要它（一条真实造成过误判的漂移）

本机的项目 `.venv` 是 **conda 的 Python 3.14.6**（`.venv/pyvenv.cfg` 的 `home = D:\\software\\conda`），
而 CI 用 `actions/setup-python@v5` 钉的是 **3.12**。两边跑同一份测试，结论却可能不同：

- **跳过项不同**：CI 的 runner 没有 chromium，`tests/test_browser_parallel.py` 的 4 项 P0 验收
  是 **skip**；开发机装了 chromium，是 **pass**。于是"本机全绿"推不出"CI 全绿"，
  而 CHANGELOG 里"CI 从建立那天起就没绿过"这起事故正是被这个差集掩盖了九次运行。
- **语义不同**：3.14 起 `from __future__ import annotations` 的注解已不再于运行时求值，
  所以"注解里写了未导入的名字"这类错误**只在 3.12 上炸**（`routes_insight.py` 的 `Any` 就是这么潜藏的，
  见 CONTRIBUTING §1.1）。ruff 的 F821 是唯一防线，但它拦不住所有形态。

## 本文件钉什么

1. `.python-version` 存在且是一个具体版本（`3.12`，不是 `>=3.12` 这类范围——
   uv 用它选解释器，范围表达式会让"本机建出来的 venv 版本"再次不确定）；
2. **它与 CI 的 `python-version` 逐字一致**：两处版本号分居 YAML 与文本文件，
   没有任何机制保证它们同步，所以由本用例守住。
   ⚠ 本仓**没有** `.python-version` 之前，这条契约根本无从表达——这正是它漂移至今的原因。

> 本文件**不**断言"当前解释器 == 3.12"：那会让在 3.14 上开发的协作者直接红一片，
> 而项目的真实约束是 `pyproject.toml` 的 `requires-python = ">=3.12"`（3.14 是**合法**的开发环境）。
> 想在本地按 CI 的口径复现，用：`uv venv .venv312 --python 3.12`（见 CONTRIBUTING §1.1）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_VERSION_FILE = ROOT / ".python-version"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT = ROOT / "pyproject.toml"


def _pinned_version() -> str:
    """`.python-version` 的内容（uv 的 pin 文件，单行）。"""
    assert PY_VERSION_FILE.is_file(), (
        "缺少 .python-version —— uv 会退回'挑一个满足 requires-python 的解释器'，"
        "本机与 CI 的解释器版本就此失去唯一真源（P0-5）"
    )
    return PY_VERSION_FILE.read_text(encoding="utf-8").strip()


def test_python_version_pin_is_a_concrete_version() -> None:
    pinned = _pinned_version()
    assert re.fullmatch(r"3\.\d+", pinned), (
        f".python-version 必须是具体版本（形如 3.12），当前是 [{pinned}]。"
        " 范围表达式（如 '>=3.12'）会让 uv 选出的解释器随机器而变，等于没钉。"
    )


def test_python_version_pin_matches_ci() -> None:
    """**本文件的核心断言**：`.python-version` 与 CI 的 `python-version` 必须一致。

    分居两个文件、无任何机制同步 —— 一旦漂移，"本地复现 CI"这件事就悄悄失效了，
    而失败形态是"CI 红了但本地绿"，最难排查的那一类。
    """
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    m = re.search(r'python-version:\s*"([^"]+)"', workflow)
    assert m, "CI 工作流里找不到 python-version 声明（门禁失去锚点）"
    ci_version = m.group(1)
    assert _pinned_version() == ci_version, (
        f"解释器版本漂移：.python-version = {_pinned_version()}，CI = {ci_version}。"
        " 两处必须一致，否则'本地绿'不代表'CI 绿'。"
    )


def test_requires_python_allows_the_pinned_version() -> None:
    """钉的版本必须满足 `requires-python`（否则 uv 建不出环境，报错却指向别处）。"""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*">=(\d+)\.(\d+)"', text)
    assert m, "pyproject.toml 里找不到 requires-python"
    floor = (int(m.group(1)), int(m.group(2)))
    pinned = tuple(int(x) for x in _pinned_version().split("."))
    assert pinned >= floor, f"钉的 {pinned} 低于 requires-python 的下限 {floor}"


def test_current_interpreter_satisfies_requires_python() -> None:
    """跑测试的**这个**解释器必须满足 `requires-python`。

    与上面两条的分工：那两条守"配置自洽"，这条守"当前环境可用"。
    本机 3.14 与 CI 3.12 都合法 —— 差异本身由文档与 `.python-version` 表达，
    不在这里判红（否则会惩罚合法的开发环境）。
    """
    assert sys.version_info >= (3, 12), f"当前解释器 {sys.version.split()[0]} 低于 requires-python 下限"


def test_python_version_pin_is_not_gitignored() -> None:
    """.python-version 必须**入库**（它是团队共享口径，不是个人偏好）。

    与 .vscode/ 相反：那个是编辑器/个人配置，这个是"用哪个解释器"的项目事实。
    若它被 gitignore 吞掉，每位协作者都会各自漂移回自己的默认解释器。
    """
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    patterns = [ln.strip() for ln in gitignore.splitlines() if ln.strip() and not ln.startswith("#")]
    assert ".python-version" not in patterns, ".python-version 不得被忽略（它定义了团队的解释器口径）"
