r"""`tools/verify_*.py` 的**自足性**守卫（2026-09-24 加）。

## 为什么需要它

这一排脚本是**手动跑**的（要起服务 + chromium），所以它们和
`verify_regression_tests.py` 是同一族：**不进 CI ⇒ 烂掉没人知道**（PITFALLS #115）。

★ 而 2026-09-24 的收口核对实测撞到了**更严重的一种**：
`verify_accounts_ui.py` / `verify_box_ops.py` / `verify_wheel_page.py` 三个脚本

- **没有设 `WORKBENCH_DATA`** ⇒ 读写的是**用户那份真实数据库**
- **绑的是生产端口 18765 且没有端口守卫**
- 而 `verify_box_ops.py` 会做**新建/重命名/停用/删除盒子**，且 `dialog` **一律 accept**

⇒ 于是「**应用正在运行**」这个再普通不过的情况下，`uvicorn` 起不来（报错被丢进
`DEVNULL`）、就绪探测**连上那个正在跑的应用** —— 整套"验收"打在**真实实例**上，
**对真实数据做破坏性操作**。这正是 记的
那个危险：**"旧进程还活着"与"测试戳到了真端口"两件事都不报错。**

## ★ 判据的口径（这是我第一版判错、后来改对的）

我第一版的启发式是"**绑了生产端口就可疑**" —— 而它把一个**正确的**脚本判成了可疑：
`verify_extension_version.py` **必须**用 18765，因为 **MV3 扩展把桌面地址编译成了常量**
（文档串里写着），而它同时**有端口守卫**（"端口已被占用…在旧代码的服务上跑本脚本毫无意义"）
且**用临时库**。

⇒ 真正的问题是两条，与"哪个端口"无关：

| 规则 | 问的是 |
|---|---|
| **数据隔离** | 起服务前有没有把 `WORKBENCH_DATA` 指到临时目录？（否则读写用户真实数据） |
| **端口守卫** | 端口被占时**拒跑**了吗？（否则会连上正在运行的真实实例） |
| **端口唯一** | 两个脚本不能抢同一个端口（否则不能先后连着跑） |

★ **例外必须写下来**：文件里写一行 `# verify-guard: <规则名> —— 理由：<...>`，
且**必须带"理由："**（没有理由的豁免不是豁免，是遗漏）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

#: 起服务的标志。用 `uvicorn` 而不是 `subprocess.Popen`：
#: 后者也被 packaging 之类的静态脚本用到，而那些不起服务。
SERVER_MARKER = "uvicorn"

EXEMPT = "# verify-guard:"
"""豁免标记。★ 后面的「理由：」是**必须**的 —— 见 `_exempt`。"""


def _tree(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _called_names(text: str) -> set[str]:
    """**被调用**的函数名（不是被定义的）。

    ★ 这一个区别是实测撞出来的：只搜文本的话，`def _port_free()` 的存在
    会让"有端口守卫"通过 —— 而那个函数**从来没被调用**。
    """
    tree = _tree(text)
    if tree is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            names.add(getattr(fn, "id", "") or getattr(fn, "attr", ""))
    return names


def _has_temp_dir(text: str) -> bool:
    """真的建了临时目录（`mkdtemp` / `TemporaryDirectory`），而不是指向一个固定路径。

    ★ 同上的理由：只看 `WORKBENCH_DATA` 这个名字的话，
    `WORKBENCH_DATA = Path(".")` 也算"隔离了"。
    """
    names = _called_names(text)
    if {"mkdtemp", "TemporaryDirectory", "mkstemp", "gettempdir"} & names:
        return True
    # ★ 也认「建在 TEMP 下面」这个形态：`Path(os.environ["TEMP"]) / "ql_version_bench"`
    # 同样是隔离，只是没调 `mkdtemp`（实测：两个**正确**的脚本栽在这条上）。
    # 只认 `mkdtemp` 会把它们判成"没隔离"，而那会**逼人去改对的代码**。
    return "TEMP" in text or "TMPDIR" in text


def _scripts() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8"))
            for p in sorted(TOOLS.glob("verify_*.py"))]


def _starts_server(text: str) -> bool:
    """**真的起了一个服务**：既有 `uvicorn` 又有 `--port`。

    ★ 两个条件都要。只判 `uvicorn` 会把 `verify_packaging.py` 算进来 ——
    它是**静态检查**，只在文档串里提了一句 uvicorn（实测踩到）。
    "提到某个词"与"用了那个东西"是两件事，而前者会让守卫指向错的文件。
    """
    return SERVER_MARKER in text and "--port" in text


def _exempt(text: str, rule: str) -> bool:
    """这个文件是否**写明理由**地豁免了某条规则。"""
    for line in text.splitlines():
        if EXEMPT not in line or rule not in line:
            continue
        if "理由：" in line and len(line.split("理由：", 1)[1].strip()) >= 8:
            return True
    return False


def test_every_server_script_isolates_its_data() -> None:
    """★★ 起服务的脚本必须把 `WORKBENCH_DATA` 指到临时目录。

    ★ 不隔离的后果不是"结果不准"，是**它会写用户那份真实库** ——
    而 `verify_box_ops.py` 会做删除，且对话框一律 accept。
    """
    bad = [name for name, text in _scripts()
           if _starts_server(text)
           and not (("WORKBENCH_DATA" in text or "WORKBENCH_DB" in text)
                    and _has_temp_dir(text))
           and not _exempt(text, "数据隔离")]
    assert not bad, (
        "这些脚本起服务却**没有隔离数据目录**（会读写用户真实数据）：\n  "
        + "\n  ".join(bad)
        + "\n  ⇒ 加 `WORKBENCH_DATA=tempfile.mkdtemp()`，或写一行"
          " `# verify-guard: 数据隔离 —— 理由：…`"
    )


def test_every_server_script_refuses_when_the_port_is_taken() -> None:
    """★★ 端口被占时必须**拒跑**，而不是连上去。

    ★ 这一条是整族里最危险的那一条：`uvicorn` 绑不上端口时只是**退出**
    （报错常被丢进 `DEVNULL`），而脚本的就绪探测会**连上正在运行的那个应用** ——
    于是这一整套"验收"打在**真实实例**上，且**没有任何一步会报错**。
    """
    # ★ **认调用，不认定义，也不认错误消息。**
    #
    # 这里被文本版骗过**两次**（都实测）：
    #   ① `def _port_free()` 的**定义** —— 从不调用也算数；
    #   ② `raise SystemExit("端口 {PORT} 被占用…")` 那句**错误消息** ——
    #      删掉调用只留那句话，文本版照样通过。
    # ⇒ 改成 AST：必须**真的调用**一个探端口的函数。
    # ★ connect_ex 也要认：有的脚本把探测**内联**写（
    # erify_extension_version.py 就是）——
    # 认函数名而漏掉机制，会把一个**正确**的脚本判成可疑（实测）。
    guard_names = _called_names("_port_free()") | {
        "_port_free", "port_free", "is_port_free", "connect_ex",
    }
    bad = [name for name, text in _scripts()
           if _starts_server(text)
           and not (_called_names(text) & guard_names)
           and not _exempt(text, "端口守卫")]
    assert not bad, (
        "这些脚本起服务却**没有端口守卫**（应用在跑时会打在真实实例上）：\n  "
        + "\n  ".join(bad)
        + "\n  ⇒ 起服务前探一次端口并拒跑，或写一行"
          " `# verify-guard: 端口守卫 —— 理由：…`"
    )


def test_server_scripts_do_not_share_a_port() -> None:
    """★ 端口在脚本之间**唯一** —— 两个脚本抢同一个端口就没法先后连着跑。

    ★ 实测撞到过：`verify_agent_ui.py`（2026-09-24 新写的）与 `verify_attachments.py`
    **都用了 18797** —— 我挑端口时没查。
    """
    owners: dict[str, list[str]] = {}
    for name, text in _scripts():
        if not _starts_server(text):
            continue
        if _exempt(text, "端口唯一"):
            continue
        for port in set(re.findall(r'"--port",\s*"(\d+)"', text)) or \
                set(re.findall(r"^PORT\s*=\s*(\d+)", text, re.M)):
            owners.setdefault(port, []).append(name)
    shared = {p: names for p, names in owners.items() if len(names) > 1}
    assert not shared, (
        "这些端口被多个校验脚本占用：\n  "
        + "\n  ".join(f"{p} → {names}" for p, names in shared.items())
        + "\n  ⇒ 换一个，或写一行 `# verify-guard: 端口唯一 —— 理由：…`"
    )


def test_the_exemptions_are_real_and_reasoned() -> None:
    """★ 每一条豁免都**必须带理由**（"没有理由的豁免不是豁免，是遗漏"）。

    ★ 反向断言：一个只写标记、不写理由的实现，会让上面三条全部静默放行。
    """
    problems: list[str] = []
    for name, text in _scripts():
        for line in text.splitlines():
            if EXEMPT not in line:
                continue
            if "理由：" not in line or len(line.split("理由：", 1)[1].strip()) < 8:
                problems.append(f"{name}：{line.strip()[:70]}")
    assert not problems, "这些豁免没写理由：\n  " + "\n  ".join(problems)


def test_there_are_still_server_scripts_to_guard() -> None:
    """★ 前提断言：这一排脚本还在。

    没有它的话，"一个都没找到"会让上面三条**全部通过** ——
    而那是"守护对象不见了"，不是"都合规"。
    """
    found = [name for name, text in _scripts() if _starts_server(text)]
    assert len(found) >= 5, f"只找到 {len(found)} 个起服务的校验脚本：{found}"
