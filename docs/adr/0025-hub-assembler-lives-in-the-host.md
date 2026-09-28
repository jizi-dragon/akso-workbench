# ADR-0025：进程级工具中枢的**装配器**移出 `plugin_api`，住进 `workbench/hub.py`

- **状态**：已采纳
- **日期**：2026-09-24
- **决策人**：维护者
- **影响面**：新增 `workbench/hub.py`；`workbench/plugin_api.py` **移除** `_hub` / `get_hub` /
  `reset_hub`；调用点改到 `workbench.hub`（`api/routes_agent.py`、`api/routes_skills.py`、
  `api/__init__.py`、`harness/tools.py`、`skills/library.py`、`tools/gen_tools_doc.py`
  ＋ 16 个测试文件）。**无 DB 迁移、无 API 变化**。
- **取代**：`plugin_api.py` 里"装配器与工具契约**同住一个模块**"这个**从未被写下来的决定** ——
  它是被 [ADR-0014](../adr/0014-sandbox-for-untrusted-code.md) 时代的历史堆出来的，
  而不是被选出来的。★ 本 ADR 把它**显式化并推翻**。

---

## 1. 背景：一个**活的**不变量违规，两重看不见

`CORE-ARCHITECTURE.md` §5 的 **I4** 与 `AGENTS.md` 铁律 7 逐字写着：

> 依赖方向 `plugins → plugin_api`，**永不反向**。
> `plugin_api.py` 里出现平台术语、import 插件、或 import `db` 都是错的
> （它连审计与确认门都靠注入）。

★ 而 `plugin_api.py` 的 `get_hub()` 一直在做这件事：

```python
_hub = ToolHub()
from . import approvals as approvals_mod  # 本地导入：避免模块循环
from . import plugins as plugins_pkg

approvals_mod.install(_hub)
plugins_pkg.load_all(_hub)
```

★★ **它为什么能活这么久 —— 两重看不见：**

| 重 | 机制 |
|---|---|
| ① | 那两行是**局部导入**（函数体内）。注释写着「避免模块循环」—— 作者**知道**这是反向依赖，用局部导入把环绕开了 |
| ② | I4 的判据 `_imports()` **对 `from . import plugins` 这种形态算出来是空模块名** —— 它只取 `node.module`（这里是 `None`），**完全没看 `node.names`**。而那是本仓最常见的写法之一（`from .. import db` / `config` / `cache` 在 `workbench/api/` 下有十几处） |

★★★ **它是被反证翻出来的**（2026-09-24）。给 I4 补反证时，往 `plugin_api.py` 加一行
`from . import db` —— **判据照样绿**。修好 `_imports` 的解析之后，`get_hub()` 那两行
**当场变红**。

> 那正是反证存在的理由：**"全绿"与"判据瞎了"在输出上长得一模一样。**
> （本会话第三次撞到同一件事 —— PITFALLS #112 / #117 / #119。）

## 2. 决策

### D-1 装配器住进 `workbench/hub.py`（一个**扁平的顶层模块**）

`hub.py` 只做一件事：`get_hub()` / `reset_hub()` / `_hub`。

### D-2 `plugin_api.py` **不再提供**这两个名字

★ **刻意不留再导出。** 留一个 `from .hub import get_hub` 就等于把那条反向依赖又请回来了 ——
而"兼容再导出"恰恰是这类违规最容易复活的方式。调用点**全部**改到 `workbench.hub`。

### D-3 为什么**不是** `plugins/__init__.py`

那是"装配入口"最自然的家（`load_all(hub)` 就在那里）。★ **但不行**：`get_hub()` 要 import
`plugins`，而 `tests/test_dependency_direction.py::test_harness_does_not_know_http_or_concrete_tools`
**同样禁止 `harness/**` import `workbench.plugins`** —— 而 `harness/tools.py`（兼容垫片）
要再导出 `get_hub`。⇒ 放那里会让**另一条**判据变红。

```python
# 搬完之后的依赖方向
plugin_api            ← 谁都不依赖（除标准库与 pydantic）
plugins    → plugin_api
approvals  → plugin_api, db
hub        → plugins, approvals        ← 装配器在宿主侧，方向对
harness/tools.py → hub                 ← 垫片，允许
```

### D-4 局部导入**保留**，但理由变了

`hub.get_hub()` 里仍然是局部 import —— 但方向已经是对的（`hub → plugins`）。
现在的理由是**装配顺序**（`approvals.install()` 必须在 `load_all()` 之前跑完）
＋ 把"装配"压在**首次调用**时才发生，避免 import 期就拉起整个插件面。

## 3. 本期做与不做

**做**：新建 `hub.py`；`plugin_api.py` 移除三个符号；改 6 个产品文件 ＋ 16 个测试文件；
修 `_imports()` 的解析缺口；给 I4 / I5 / I7 / I9 / I14 补反证。

**不做（明确）**：

- ~~在 `plugin_api` 留 `get_hub` 的兼容再导出~~ —— D-2
- ~~把装配器放进 `plugins/__init__.py`~~ —— D-3
- ~~顺手把 `plugin_api` 里另外两处宿主导入（`logging_setup` / `approvals`）也搬走~~ ——
  ★ 它们**不在** I4 判据的禁列里，但**同属"它连审计与确认门都靠注入"那句话**。
  搬它们是**另一件事**（`approvals` 的注入点在 `_audit` 的回调里，牵动面更大），
  **本轮不动**，登记在 §7 的触发器里

## 4. 后果

**好处**：I4 现在**真的成立**（`plugin_api.py` 一处宿主 import 都没有）；那条判据的
**解析缺口**也修好了（它此前对本仓最常见的导入写法是瞎的）；装配器的位置**写下来了**。

**代价**：多一个顶层模块；调用点从 `plugin_api.get_hub` 改成 `hub.get_hub` 是一次
**破坏性改名**（外部脚本若直接引用会断）；16 个测试文件被机械改动。

**风险**：★ 机械改动**量大**（16 个测试文件），而 `git diff` 里它们大多是
`setattr(pa, "_hub", …)` → `setattr(hub_mod, "_hub", …)` —— **看起来都一样**，
所以"漏了一处"不会被肉眼发现。⇒ 判据是**全量 pytest**（它当场抓到了 4 处漏改：
`tests/eval/runner.py`、`test_tools_doc.py`、`test_plugin_api.py` 的 import）。

## 5. 诚实边界

**★ 这个决策做到的**：`plugin_api` 对宿主的依赖**清零**，"插件整体拆除而不碰宿主"这句话
现在有机器守卫撑着。

**★ 它做不到的**：

1. **`plugin_api` 里仍有两处宿主导入**（`logging_setup` / `approvals`，都是局部导入）。
   ⇒ **不要从这里读出"`plugin_api` 已经与宿主解耦"** —— 它只是**不再 import 插件**。
2. **修好的 `_imports()` 只覆盖"静态可解析"的导入。** `importlib.import_module("workbench.plugins")`
   这类**字符串导入**它看不到。★ 全仓今天没有这种写法，但**没有守卫**。
3. **`hub.py` 自身不在任何"依赖方向"判据的覆盖里** —— 那三条判据分别管
   `plugins/`、`services/`、`harness/`、`plugin_api.py`。`hub.py` 是新的，
   它**允许**依赖宿主（那正是它的职责），但这条"允许"**没有被写进任何判据**。

## 6. 外部参照

| 参照对象 | 它的做法 | 与本决策的关系 | 我们的理由 |
|---|---|---|---|
| 依赖倒置（[Martin, DIP](https://en.wikipedia.org/wiki/Dependency_inversion_principle)） | 高层与低层都依赖抽象；**装配**由第三方（composition root）负责 | **一致**（我们跟随） | `plugin_api` ＝ 抽象侧；`hub.py` ＝ composition root。★ 我们此前把 composition root 放进了抽象侧，那是 DIP 的教科书反例 |
| [Cosmic Python](https://www.cosmicpython.com/book/chapter_13_dependency_injection.html) 的 "composition root" 一节 | 明确要求装配**只在入口处发生一次**，业务代码不自己 new 依赖 | **一致** | `hub.get_hub()` 就是那个唯一入口；它的文档串写着"进程级工具中枢（唯一装配点）" |
| Python 的循环导入惯例 | 常见处置是**函数内局部 import**（我们原先就是） | **分歧** | ★ 局部 import 能解**循环**，但**解不了方向** —— 它把一条架构违规变成一行看不见的代码。**能 import 成功 ≠ 方向对** |
| `pytest` 的 `monkeypatch` 与全局单例 | 官方建议避免模块级可变全局 | 参考 | 16 个测试文件在打桩 `_hub`。★ 这暴露的是**单例本身**的味道，不是搬迁造成的 —— 搬迁只是把桩点换了个模块 |

**由本次对照得出的结论**：

- ★ **可以简化的**：composition root 的位置行业里没有争议 —— 放宿主侧、放一个**不认识业务**
  的模块。不需要自己发明。
- ★ **必须自己定的**：**它住哪个文件**（D-3 那一段：三条判据各禁一部分，交集很窄），
  以及**要不要留兼容再导出**（D-2：不留，因为那是违规最容易复活的方式）。

## 7. 重新评估的触发器

| 触发器 | 到时再考虑 |
|---|---|
| 有人需要 `plugin_api.get_hub` 的**兼容再导出** | ★ 先回来读 D-2：那会把反向依赖请回来。真需要的话，改的是调用方，不是这里 |
| `plugin_api` 里那两处宿主导入（`logging_setup` / `approvals`）开始碍事 | 把 `approvals` 的注入点也搬到宿主 —— 那是一件独立的事（牵动 `_audit` 回调） |
| 出现**字符串导入**（`importlib.import_module("workbench.plugins")`） | `_imports()` 的 AST 守卫看不见它 —— 要另加一条判据 |
| `hub.py` 长到需要自己的内部结构 | 说明装配职责不止"建中枢 + 装 approvals + load_all"了 —— 重新划它的边界 |

> **本栏不是可选的**：本条的前提是「**装配器可以住在一个扁平模块里**」与
> 「三条依赖判据的禁列**合起来仍留得下它**」。前者变了（装配需要更多上下文）
> 或后者变了（判据改动让 `hub.py` 也进禁列）都要回来看这里。
