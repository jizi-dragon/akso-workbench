"""进程级工具中枢的**装配器**（宿主侧）。

## ★ 为什么它从这个文件里搬出来（2026-09-24）

它原先住在 `workbench/plugin_api.py` 里，而那个模块的文档串**逐字**写着：

> `plugin_api` 是被**注入**的：它不能认识宿主的任何一部分（连 `db` 都不行）。
> ……这就是"插件可以整体拆除而不碰宿主"能成立的原因。

而搬出来之前，`get_hub()` 在里面做的是：

```python
from . import approvals as approvals_mod  # 本地导入：避免模块循环
from . import plugins as plugins_pkg
approvals_mod.install(_hub)
plugins_pkg.load_all(_hub)
```

⇒ **它 import 了 `workbench.plugins` 与 `workbench.approvals`** —— 前者是
[`CORE-ARCHITECTURE.md`](../docs/CORE-ARCHITECTURE.md) §5 的 **I4** 明确禁止的方向
（`plugins → plugin_api`，**永不反向**），规则 7 也逐字写着「import 插件……都是错的」。

★★ **它是怎么活下来的**：那两行是**局部导入**（函数体内），注释写着「避免模块循环」——
作者**知道**这是反向依赖，用局部导入把环绕开了。而 I4 的判据只解析**模块级**能看到的
`_violations(...)`，**而且对 `from . import plugins` 这种形态算出来是空模块名**
（它只取 `node.module`，不看 `node.names`）⇒ **两重看不见**。

★★★ **它是被反证翻出来的**（2026-09-24）：给 I4 的判据补反证时，往 `plugin_api.py`
加一行 `from .. import db` —— 判据**照样绿**。修好解析之后，`get_hub()` 那两行
**当场变红**。那正是反证存在的理由：**"全绿"与"判据瞎了"在输出上长得一模一样。**

## 为什么是**这个**文件（而不是 `plugins/__init__.py`）

`get_hub()` 要 import `plugins` 与 `approvals`。而 `tests/test_dependency_direction.py`
的 `test_harness_does_not_know_http_or_concrete_tools` **也禁止 `harness/**` import
`workbench.plugins`** —— 而 `harness/tools.py`（兼容垫片）要再导出 `get_hub`。
⇒ 放在 `plugins/__init__.py` 会让那条判据变红。

所以：**一个扁平的顶层模块**（也符合"扁平化"那一项诉求）。

## 依赖方向（搬完之后）

```
plugin_api          ← 谁都不依赖（除了标准库与 pydantic）
plugins  → plugin_api
approvals → plugin_api, db
hub      → plugins, approvals          ← 装配器在宿主侧，方向对
harness/tools.py → hub                 ← 垫片，允许
```

★ `plugin_api.py` 现在**一处宿主 import 都没有** —— 这是 I4 要买的东西。
"""

from __future__ import annotations

import logging

from .plugin_api import ToolHub

logger = logging.getLogger(__name__)

_hub: ToolHub | None = None


def get_hub() -> ToolHub:
    """进程级工具中枢（唯一装配点）。

    ⚠️ **局部导入 `approvals` / `plugins` 是刻意的**，但理由与从前不同：
    从前是"避免模块循环"（那时本函数在 `plugin_api` 里，方向是反的）；
    现在是**装配顺序**——`approvals.install()` 必须在 `load_all()` 之前跑完。
    ★ 方向已经是对的（`hub → plugins/approvals`），局部导入只是把"装配"这个动作
    压在**首次调用**时才发生，避免 import 期就拉起整个插件面。
    """
    global _hub
    if _hub is None:
        hub = ToolHub()
        from . import approvals as approvals_mod
        from . import plugins as plugins_pkg

        approvals_mod.install(hub)
        plugins_pkg.load_all(hub)
        _hub = hub
        logger.debug("工具中枢装配完成")
    return _hub


def reset_hub() -> None:
    """清空单例（仅测试用）。

    ⚠️ 本函数的语义在 2026-09-17 变了：**生产路径现在确实有"运行期增删"了**
    （用户从共享库安装/卸载 Skill，见 `ToolHub` 类文档的两层表），
    但那是走 `register_installed` / `deregister_capability`，
    **不是**走这个函数。这里仍然是"把单例整个丢掉重建"，只给测试隔离用。
    """
    global _hub
    _hub = None
