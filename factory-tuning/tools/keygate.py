"""键名闸门：静态扫出工厂的"字面量请求体"，与真机键集自动比对。

## 它防的是哪一类缺陷

`#62 / #63 / #67 / #69` 与 **D-A / D-C** 全都是同一个成因：**照着猜的键名写代码**。
症状一律是「接口回成功、回读才发现是空的」。

`audit_keys.py` 把**真机**那一侧抽了出来，但工厂那一侧一直靠人肉对照——
而人肉对照不会在每次改动时自动重跑。这个脚本把工厂那一侧也变成机的：

1. 用 AST 解析 `writers/endpoints.py`，得到 `常量名 → 路径`；
2. 用 AST 扫 `writers/*.py`，找出所有 `client.post(常量, {字面量})` —— **只认字面量**；
3. 与 `audit_keys.collect()` 的真机键集比对，分三类报出来。

## 为什么要"只认字面量"

请求体是 `Name`（如 `client.post(SAVE_FIELD, body)`）时，键在运行时才拼出来，
静态看不见 —— 这类**如实报成"未核"**，不假装覆盖。这不是缺陷：
**两个已知缺陷（D-A / D-C）恰好都是字面量**，所以闸门对它们有效。

## 用法与退出码

    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\keygate.py

- 退出码 0：没有"可疑键"（工厂发了、而真机样本里从未出现的键）；
- 退出码 1：**有可疑键** —— 那正是 D-A/D-C 的形态，别提交。

> **两层都算**（2026-09-22 段 2 起）：顶层请求体 **与** `NAME.append({…})` 拼出来的
> **嵌套元素**（`controls[]` / `sections[]`）都会与真机**同名数组**的元素键集比对。
> 在第一版里嵌套那层只"列出来、标未核"，于是 **D-D 是静默盲区**——现在它是红的。

> ⚠️ 它**不进** `selfcheck.py` 的 PASS/FAIL：当前仓库里**确实存在**两个可疑键
> （`columns`、`basicObjectId`），把它们写成断言会变成"断言缺陷存在"，
> 反而挡住修复。自检里对应的是 **J 组**：用合成输入单测 `compare()` 本身。
"""

from __future__ import annotations

import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
FACTORY_TUNING = HERE.parent
sys.path.insert(0, str(HERE))

from audit_keys import collect, collect_arrays, collect_values  # noqa: E402

WRITERS = FACTORY_TUNING.parent / "workbench" / "services" / "egmp" / "writers"
EGMP = WRITERS.parent

#: 端点常量的来源，按顺序解析并合并。
#:
#: ⚠️ `writers/endpoints.py` 是**再导出的垫片**（`from ..kernel.endpoints import *`），
#: 它自己一个赋值都没有——第一版只扫它，于是闸门**一个调用点都没找到却报了 "✓ 没有可疑键"**。
#: 这正是本仓最怕的形态：**闸门自己空转，而输出看起来是绿的**。
#: 所以：以 `kernel/endpoints.py` 为准，垫片只作兜底。
ENDPOINT_SOURCES = (EGMP / "kernel" / "endpoints.py", WRITERS / "endpoints.py")

#: **已知合法差异**：真机样本里有、工厂（故意）不发，且已有实验结论背书。
#: 每一条都必须写着"为什么合法"——否则它就是一条没判据的豁免（PITFALLS #66 的形态）。
KNOWN_ABSENT_OK: dict[str, dict[str, str]] = {
    "/api/platform/ObjectPicklist/save": {
        "0": "F11：界面路径带顶层数字键（按位置重排 sort）；工厂不带则平台认 `options[].sort`，是合法路径",
        "1": "同上（F11）",
        "2": "同上（F11）",
    },
}


def endpoint_paths() -> dict[str, str]:
    """`常量名 → 路径`（含 `A = B` 形式的别名）。按 `ENDPOINT_SOURCES` 顺序合并。"""
    direct: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for source in ENDPOINT_SOURCES:
        if not source.is_file():
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):  # `NAME: str = "…"` 也要认
                target, value = node.target, node.value
            if not isinstance(target, ast.Name) or value is None:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                direct.setdefault(target.id, value.value)
            elif isinstance(value, ast.Name):
                aliases.setdefault(target.id, value.id)
    for name, src in aliases.items():
        if src in direct:
            direct.setdefault(name, direct[src])
    return direct


def _assigned_value(name: str, func: ast.AST) -> ast.expr | None:
    """在**同一个函数体内**找 `NAME = <任意表达式>` 的右值。

    这是"先拼字典、再 post"这个常见写法的解析器——工厂里几处动态体都是这个形状。
    """
    for node in ast.walk(func):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
    return None


def _returned_dict_literal(func_name: str, tree: ast.Module) -> ast.Dict | None:
    """`helper()` 的 `return {字面量}`——**一层函数跳转，不递归**。

    为什么只跳一层：跳多层就要做真正的数据流分析，而闸门宁可在边界上**如实报未核**
    （`objects.py` 的体正是"本函数拼变量 → helper 返回字面量"，这一层就够用）。
    """
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func_name):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                return sub.value
    return None


def _is_extended(name: str, func: ast.AST) -> bool:
    """该变量在运行期是否被 `NAME.update(...)` 扩充过。

    扩充过 ⇒ 静态字面量**不是完整请求体** ⇒ **"漏发"这一列不成立**，必须说清楚，
    否则闸门会用一个不完整的静态体去报一堆假的"少发键"（假阳性，PITFALLS #75 同族）。
    """
    for node in ast.walk(func):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"update", "setdefault"}
                and isinstance(node.func.value, ast.Name) and node.func.value.id == name):
            return True
    return False


def literal_posts() -> list[dict[str, object]]:
    """所有 `client.post(常量, {字面量})` 调用点。

    ## 2026-09-22 扩展：**"先拼字典再 post"也要看得见**

    原来只认 post 的**实参是字面量**的那些调用点，于是 `fields.create_field` 这种
    「`body: dict = {…}` → `body.update(extras)` → `client.post(SAVE_FIELD, body)`」
    一律被报成「未核（动态体）」——**闸门声明了盲区，而缺陷就住在盲区里**：
    实测工厂发的 `isRequired` 在真机 11 条 `SaveField` 里**一条都没出现过**，
    而闸门一个字都没说（见 `factory-tuning/reports/2026-09-22-控件与字段的词汇对照.md` §7）。

    ⇒ 现在解析**同函数内的字典字面量**；被 `update()` 扩充过的那些**如实标注**
    ——它的"漏发"列不成立，但"可疑键"列**成立**（可疑键问的是"真机有没有发过"）。
    """
    paths = endpoint_paths()
    found: list[dict[str, object]] = []
    for path in sorted(WRITERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tuples = _module_str_tuples(tree)
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                # ① `client.post(常量, {…})`
                # ② 工厂自己的助手：`created_id(client, path=常量, body=…)`
                #    —— 2026-09-22 补：**对象与字段的创建体走的正是这条路**
                #    （`objects.py:52` / `fields.py:39`），而它们此前**一条都没被检查过**。
                endpoint_name: str | None = None
                body_arg: ast.expr | None = None
                if (isinstance(node.func, ast.Attribute) and node.func.attr == "post"
                        and len(node.args) >= 2 and isinstance(node.args[0], ast.Name)):
                    endpoint_name, body_arg = node.args[0].id, node.args[1]
                else:
                    for kw in node.keywords:
                        if kw.arg == "path" and isinstance(kw.value, ast.Name):
                            endpoint_name = kw.value.id
                        elif kw.arg in {"body", "data"}:
                            body_arg = kw.value
                if endpoint_name is None:
                    continue
                resolved = paths.get(endpoint_name)
                if not resolved:
                    continue
                base = {"file": path.name, "line": node.lineno, "path": resolved,
                        "endpoint": endpoint_name, "extended": False}
                if isinstance(body_arg, ast.Dict):
                    found.append({**base, "keys": _dict_keys(body_arg)})
                    continue
                if isinstance(body_arg, ast.Name):
                    value = _assigned_value(body_arg.id, func)
                    literal = value if isinstance(value, ast.Dict) else None
                    if (literal is None and isinstance(value, ast.DictComp)
                            and value.generators
                            and isinstance(value.generators[0].iter, ast.Name)):
                        # `{key: … for key in CONST_TUPLE}` —— 键集来自**模块级字符串元组**。
                        # 2026-09-22 补：D-C②/D-F 修完之后，保存体的 17 个身份键改成
                        # "从读回的详情原样带回"，静态就只剩这一个可读的**声明**；
                        # 不认它的话，闸门会把这个体报成「未核（动态体）」——
                        # 而 D-C① 正是靠这个闸门盯的**（自己把覆盖打瞎是最糟的修法）**。
                        # 这里按 **AST 形状**认（元组 + 推导），不按变量名认。
                        declared = tuples.get(value.generators[0].iter.id)
                        if declared:
                            found.append({**base, "keys": sorted(declared)})
                            continue
                    if (literal is None and isinstance(value, ast.Call)
                            and isinstance(value.func, ast.Name)):
                        literal = _returned_dict_literal(value.func.id, tree)
                    if literal is not None:
                        found.append({**base, "keys": _dict_keys(literal),
                                      "extended": _is_extended(body_arg.id, func)})
                        continue
                found.append({**base, "keys": None})
    return found


def _dict_keys(node: ast.Dict) -> list[str]:
    return sorted(k.value for k in node.keys
                  if isinstance(k, ast.Constant) and isinstance(k.value, str))


def _module_str_tuples(tree: ast.Module) -> dict[str, list[str]]:
    """**模块顶层**的字符串元组/列表：`NAME = ("a", "b", …)` → `{NAME: ["a", "b", …]}`。

    为什么需要它：有些请求体的键集是**声明式**的（`{k: … for k in CONST_TUPLE}`），
    静态看不到字面量字典。这条按 **AST 形状**（模块级 + 全字符串元素的 tuple/list）认，
    **不按名字认**——名字会漂，形状不会。
    """
    out: dict[str, list[str]] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, (ast.Tuple, ast.List)):
            items = [element.value for element in value.elts
                     if isinstance(element, ast.Constant) and isinstance(element.value, str)]
            if items:
                out[target.id] = items
    return out


def compare(calls: list[dict[str, object]],
            real: dict[str, set[tuple[str, ...]]]) -> list[dict[str, object]]:
    """逐调用点分类：可疑键 / 漏发键 / 未核（真机没样本或体是动态的）。"""
    out: list[dict[str, object]] = []
    for call in calls:
        keys = call["keys"]
        if keys is None:
            out.append({**call, "verdict": "未核（动态体，静态看不见）", "suspect": [], "absent": []})
            continue
        variants = real.get(str(call["path"]))
        if not variants:
            out.append({**call, "verdict": "未核（这个端点还没有真机样本）", "suspect": [], "absent": []})
            continue
        seen = set().union(*variants)
        suspect = sorted(set(keys) - seen)
        allowed = KNOWN_ABSENT_OK.get(str(call["path"]), {})
        absent = sorted(k for k in (seen - set(keys)) if k not in allowed)
        if call.get("extended"):
            # 静态字面量**不是**完整请求体（运行期被 `update()` 扩充）
            # ⇒「少发」这一列不成立，报出来就是假阳性（PITFALLS #75 同族）。
            # 「可疑键」仍然成立：它问的是"真机有没有发过这个键"。
            absent = []
        verdict = "🔴 可疑键" if suspect else ("⚠️ 少发键" if absent else "✅ 一致")
        out.append({**call, "verdict": verdict, "suspect": suspect, "absent": absent})
    return out


def module_of_endpoints() -> dict[str, set[str]]:
    """`模块文件名 → 它实现的端点集`，取自**产品自己的** `monitor/interpreter.py::API_MAP`。

    ## 为什么需要它（这条是 2026-09-22 第一版嵌套比对的**假阳性**修出来的）

    第一版按**数组名**全局匹配：`controls.append({…})` 一律拿 `controls` 的真机键集比。
    但 `controls` 这个名字在**两个不同的契约**里都出现——`Layout/SaveLayoutDetail`
    的元素数组，与**工作流步骤**端点的元素数组。于是 `workflows.py:156` 那个控件字面量
    被拿**布局的**键集判成 🔴 —— 那是**凭空多出来的假阳性**，
    而假阳性会和真缺陷一样毁掉闸门（PITFALLS #72 的同族）。

    ⇒ 归属改成**机械的**：只拿"与该文件同属一个 writer 模块的端点"的样本去比；
    比不到样本就如实报**未核**。

    ⚠️ 不自己维护一份端点↔模块表：那会是**第二个会漂的事实源**（本仓已因这种事栽过多次）。
    `API_MAP` 是产品自己在用的映射，只读它。
    """
    source = WRITERS.parent / "monitor" / "interpreter.py"
    mapping: dict[str, set[str]] = {}
    if not source.is_file():
        return mapping
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "API_MAP" and isinstance(node.value, ast.Dict)):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=False):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and isinstance(value, ast.Dict)):
                continue
            module = ""
            for k, v in zip(value.keys, value.values, strict=False):
                if (isinstance(k, ast.Constant) and k.value == "module"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    module = v.value
            if not module:
                continue
            mapping.setdefault(f"{module.rsplit('.', 1)[-1]}.py", set()).add(key.value)
    return mapping


#: **显式声明**的「工厂词表 ↔ 真机量纲」对应关系。
#:
#: 为什么是一张**声明表**而不是自动配对：自动配对只能靠"名字像不像"去猜，
#: 而猜出来的配对会产出**假阳性**——假阳性和真缺陷一样会毁掉闸门（PITFALLS #75）。
#: ⇒ 没写进这张表的一律**未核**，如实上报。
#:
#: 为什么值得单列一层：键名对了、**值继续自造**，只比键名是抓不住的（D-D 的另一半）。
#: 这一层真正的价值在 **D-D 修好之后**——那时工厂会新引入一张 `AK*` 词表，
#: 而这张表会替我们盯着"有没有人又编了一个我们**从未实测过**的类型名"。
VOCAB_PAIRS: list[dict[str, str]] = [
    {
        "file": "layouts.py",
        "dict": "AK_BY_DATA_TYPE",
        "array": "controls",
        "key": "type",
        "why": "控件类型：真机 `controls[].type` 的值域是 `AK*`（F30；实测 6 个值，F42）",
    },
]


def dict_literals() -> dict[tuple[str, str], dict[str, object]]:
    """`writers/*.py` 里的**模块级字典字面量** → 它的字符串值集合。

    只认模块级 `NAME = {…}`（词表就是那么写的）。用 AST 而不是正则：
    正则会把函数体里的临时字典也捞进来，而"哪个才是词表"应该是**声明**出来的。
    """
    found: dict[tuple[str, str], dict[str, object]] = {}
    for path in sorted(WRITERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            if not (isinstance(target, ast.Name) and isinstance(value, ast.Dict)):
                continue
            values = [v.value for v in value.values
                      if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            if values:
                found[(path.name, target.id)] = {"line": node.lineno,
                                                 "values": sorted(set(values))}
    return found


def compare_vocab(dicts: dict[tuple[str, str], dict[str, object]],
                  values: dict[str, dict[str, list[str]]],
                  module_paths: dict[str, set[str]]) -> list[dict[str, object]]:
    """按 `VOCAB_PAIRS` 逐条比对；比不到样本就**未核**。"""
    out: list[dict[str, object]] = []
    for pair in VOCAB_PAIRS:
        found = dicts.get((pair["file"], pair["dict"]))
        row: dict[str, object] = {**pair, "suspect": [], "seen": "", "absent": True}
        if not found:
            out.append({**row, "verdict": "未核（找不到这张词表——改名了？）"})
            continue
        # 与嵌套层同一套归属规则：只认**同 writer 模块**端点的样本（PITFALLS #75）
        own = module_paths.get(pair["file"], set())
        per_key = values.get(pair["array"], {}).get(pair["key"], [])
        if not per_key or not own:
            out.append({**row, "verdict": "未核（这个数组/字段还没有真机样本）"})
            continue
        seen = set(per_key)
        fabricated = sorted(v for v in found["values"] if v not in seen)  # type: ignore[union-attr]
        out.append({**row, "verdict": "🔴 词表可疑" if fabricated else "✅ 一致",
                    "suspect": fabricated, "seen": ", ".join(sorted(seen)),
                    "absent": False})
    return out


def subliterals() -> list[dict[str, object]]:
    """**子结构字面量**：`xxx.append({字面量})` 的键集 **+ 它挂在哪个数组上**。

    ## 为什么单列一节（而不是并进主表）

    闸门的主表只认 `client.post(常量, {字面量})` 的实参 —— **顶层**请求体。
    而真机里的**嵌套结构**（`sections[]` 元素、`controls[]` 元素）在工厂里是循环里
    `controls.append({...})` 拼出来的，**不在 post 的实参里** ⇒ 主表**根本看不见它们**。

    后果不是"误判"，而是**静默的盲区**：第一个走这条路的缺陷（**D-D**：控件用了
    `fieldCode`/`ctrlType`/`invisible`/`disabled`，而真机是 `code`/`type`/`visible`/`readOnly`）
    就是这样被主表漏掉的，最后靠人工对照真机数据才发现。

    ⇒ **2026-09-22 段 2 起**：本节不再"只列出来供人工对照"，而是把 `target`
    （挂载对象名，如 `controls`）也带出来，交给 :func:`compare_subliterals`
    与真机**同名数组**的元素键集机械比对。
    """
    found: list[dict[str, object]] = []
    for path in sorted(WRITERS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "append"
                        and node.args):
                    continue
                # ① `NAME.append({字面量})`（原形）
                # ② `NAME.append(变量)`，变量在同函数里被赋成字典字面量
                #    —— 2026-09-22 补：D-D 修完后，控件体在赋值时就多了一步
                #    （新元素要留着换稳定 id），于是 `controls.append(control)` 这种**同样形状**的
                #    写法会被漏掉 ⇒ 嵌套层**静默少一行**（K9 就是钉这个的）。
                #    这里按 **AST 形状**认（append 的实参解析后是字典字面量），不按名字认。
                argument = node.args[0]
                literal = argument if isinstance(argument, ast.Dict) else None
                if literal is None and isinstance(argument, ast.Name):
                    assigned = _assigned_value(argument.id, func)
                    if isinstance(assigned, ast.Dict):
                        literal = assigned
                if literal is None:
                    continue
                keys = [k.value for k in literal.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if not keys:
                    continue
                holder = node.func.value
                if isinstance(holder, ast.Name):
                    target = holder.id
                elif isinstance(holder, ast.Attribute):
                    target = holder.attr
                else:
                    target = ""
                found.append({"file": path.name, "line": node.lineno,
                              "target": target, "keys": sorted(keys)})
    return found


def compare_subliterals(subs: list[dict[str, object]],
                        arrays: dict[str, dict[str, object]],
                        module_paths: dict[str, set[str]]) -> list[dict[str, object]]:
    """`NAME.append({字面量})` 的键集 ↔ **同模块端点**的真机同名数组元素键集。

    ## 归属是机械的（不靠猜）

    ① 挂载对象名（`controls.append` → `controls`）就是线上那个数组字段的名字；
    ② 这个字面量属于哪个**端点**，静态看不见（值要流过好几个函数才进 `post`），
       所以退一步按 **writer 模块**归属（见 :func:`module_of_endpoints`）——
       `layouts.py` 的字面量只与 `layouts.py` 实现的那些端点的样本比。

    ## 四种如实上报（Fail-Closed，宁可未核也不假装覆盖、更不借样本）

    - 挂载对象不是简单名字 ⇒ **未核（挂载对象名取不到）**；
    - 真机从没见过这个数组名 ⇒ **未核（真机没见过这个数组）**；
    - 见过，但样本**不属于本模块的端点** ⇒ **未核（本模块端点还没有真机样本）**
      —— 这一条是 2026-09-22 的**假阳性**修出来的：`workflows.py:156` 的 `controls`
      一度被拿**布局的**键集判红，那是凭空多出来的红；
    - 样本可用 ⇒ 逐键比对：**可疑**（真机同名字段里从未出现）/ **漏发**（真机有、工厂没发）。
    """
    out: list[dict[str, object]] = []
    for sub in subs:
        target = str(sub.get("target") or "")
        keys = list(sub.get("keys") or [])
        base = {**sub, "suspect": [], "absent": [], "real": "", "sources": ""}
        if not target:
            out.append({**base, "verdict": "未核（挂载对象名取不到）"})
            continue
        slot = arrays.get(target)
        by_endpoint = dict(slot["by_endpoint"]) if slot else {}  # type: ignore[arg-type]
        if not by_endpoint:
            out.append({**base, "verdict": "未核（真机没见过这个数组）"})
            continue
        own = module_paths.get(str(sub["file"]), set())
        relevant = sorted(set(by_endpoint) & own)
        if not relevant:
            out.append({**base, "verdict": "未核（本模块端点还没有真机样本）"})
            continue
        seen: set[str] = set()
        for endpoint in relevant:
            seen |= set(by_endpoint[endpoint])
        suspect = sorted(set(keys) - seen)
        absent = sorted(seen - set(keys))
        if suspect:
            verdict = "🔴 可疑键"
        elif absent:
            verdict = "⚠️ 少发键"
        else:
            verdict = "✅ 一致"
        out.append({**base, "verdict": verdict, "suspect": suspect, "absent": absent,
                    "real": ", ".join(sorted(seen)), "sources": ", ".join(relevant)})
    return out


def main() -> int:
    real = collect()
    rows = compare(literal_posts(), real)
    if not real:
        print("✗ 没有真机数据（data/sessions/ 为空）——先跑 capture → ingest，或只当 lint 用。")
    print(f"字面量 POST 调用点：{len(rows)} 个\n")
    suspect_total = 0
    for row in rows:
        keys = row["keys"]
        shown = "—" if keys is None else ("空体" if not keys else ", ".join(keys))
        print(f"{row['verdict']:<18} {row['file']}:{row['line']}  {row['endpoint']}")
        print(f"{'':<18} 工厂发：{shown}")
        if row["suspect"]:
            suspect_total += len(row["suspect"])
            print(f"{'':<18} 🔴 可疑键（真机样本里从未出现）：{', '.join(row['suspect'])}")
        if row["absent"]:
            print(f"{'':<18} ⚠️ 真机有、工厂没发：{', '.join(row['absent'])}")
        if row.get("extended"):
            print(f"{'':<18} ℹ️ 该体在运行期被 `update()` 扩充 ⇒ 静态字面量不是完整请求体，"
                  "**「少发」不报**（报了就是假阳性）；**「可疑键」仍然成立**")
        print()
    if suspect_total:
        print(f"✗ 共 {suspect_total} 个可疑键 —— 这正是 D-A/D-C 的形态："
              "真机自己的客户端从不发这些键名。**别提交**，先定性再改。")
    else:
        print("✓ 没有可疑键。")
    subs = compare_subliterals(subliterals(), collect_arrays(), module_of_endpoints())
    if subs:
        print()
        print(f"=== 子结构字面量（{len(subs)} 处）：`NAME.append({{字面量}})` ↔ **同模块端点**的真机同名数组 ===")
        for row in subs:
            where = f"{row['target']}.append" if row["target"] else "（挂载对象名取不到）"
            print(f"{row['verdict']:<18} {row['file']}:{row['line']}  {where}")
            print(f"{'':<18} 工厂发：{', '.join(row['keys'])}")
            if row["sources"]:
                print(f"{'':<18} 样本来自：{row['sources']}")
            if row["real"]:
                print(f"{'':<18} 真机同名字段见过：{row['real']}")
            if row["suspect"]:
                suspect_total += len(row["suspect"])
                print(f"{'':<18} 🔴 可疑键（真机同名字段里从未出现）：{', '.join(row['suspect'])}")
            if row["absent"]:
                print(f"{'':<18} ⚠️ 真机有、工厂没发：{', '.join(row['absent'])}")
            print()
        print("⚠️ 这一层的「少发键」要**分形状**看，别一律当缺陷：真机对**新建**组件只发精简形状"
              "（部分 5 键 / 控件 8 键），对**服务端已有的**组件才发完整形状（62 / 23 键）——"
              "见 F36。⇒ **少发 ≠ 缺陷；可疑键才是**（真机**任何**形状里都没出现过）。")
        print("   另：「未核（真机没见过这个数组）」也可能本身就是信号——`columns.append` 就是 "
              "D-A 的另一面（工厂的局部变量叫 `columns`，而线上的字段名是 `listlayoutColumns`）。")
    vocab = compare_vocab(dict_literals(), collect_values(), module_of_endpoints())
    if vocab:
        print()
        print("=== 词表（**键名对了、值自造**也能被抓住的那一层）===")
        for row in vocab:
            print(f"{row['verdict']:<18} {row['file']}::{row['dict']}  →  {row['array']}[].{row['key']}")
            print(f"{'':<18} 依据：{row['why']}")
            if row["seen"]:
                print(f"{'':<18} 真机见过：{row['seen']}")
            if row["suspect"]:
                suspect_total += len(row["suspect"])
                print(f"{'':<18} 🔴 真机从未见过的值：{', '.join(row['suspect'])}")
            print()
        print("> 这一层的价值在 **D-D 修好之后**：那时工厂会新引入 `AK*` 词表，")
        print(">  它替我们盯着「有没有人又编了一个我们**从未实测过**的类型名」（Fail-Closed）。")
        print(">  对应关系是**显式声明**的（`VOCAB_PAIRS`）——没声明的就是未核，不靠名字猜。")
    return 1 if suspect_total else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
