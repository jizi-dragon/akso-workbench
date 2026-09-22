"""键名审计：把"真机见过请求体"的端点及其顶层键抽出来，供与 `writers/` 逐键对照。

## 为什么要有它（而不是一行命令）

D-A（`columns` vs `listlayoutColumns`）与 D-C（`basicObjectId` vs `objectId`）
是**同一种缺陷**：照着猜的键名写代码。要防住它，就得能**反复**回答一个问题——
"这个端点，真机到底发哪些键？"

第一版把它写成了一条 `python -c "…"` 一行命令。**它不可用**：命令里嵌了双层引号，
而本仓的 PowerShell 通道会把 `"` 吃掉（同一类问题在本项目里已踩过多次，
见 `docs/PITFALLS.md` 里"写文本文件不要用 `powershell >`"那条的同一族）。
结论：**判据要么是一行能跑的，要么就别假装成一行**。

## 用法

    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\audit_keys.py
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\audit_keys.py --json

输出：每个端点 → 见过的顶层键组合（同一端点不同字段类型会有不同键集，**这是正常的**，
见 `2026-09-22-键名审计-真机vs工厂.md` §3 A2）。
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

FACTORY_TUNING = pathlib.Path(__file__).resolve().parents[1]
SESSION_ROOT = FACTORY_TUNING / "data" / "sessions"


def collect(root: pathlib.Path | None = None) -> dict[str, set[tuple[str, ...]]]:
    """端点 → { 顶层键元组 }（只收**带请求体的写请求**；空体与 GET 无意义）。"""
    root = root or SESSION_ROOT
    seen: dict[str, set[tuple[str, ...]]] = {}
    if not root.exists():
        return seen
    for path in sorted(root.glob("*/traffic.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            body = row.get("postData")
            if not isinstance(body, dict) or not body:
                continue
            endpoint = str(row.get("path") or "").split("?", 1)[0]
            if endpoint:
                seen.setdefault(endpoint, set()).add(tuple(sorted(body)))
    return seen


def collect_arrays(root: pathlib.Path | None = None) -> dict[str, dict[str, Any]]:
    """数组名 → `{"by_endpoint": {端点: 元素键集}, "samples": 元素条数}`。

    ## 为什么要单开一层（2026-09-22 段 2）

    主表 `collect()` 只看**顶层键**，而真机的 `sections[]` / `controls[]` 元素是**嵌套结构**；
    工厂那一侧对应的是循环里的 `controls.append({...})` —— 它**不在 `client.post(…, {…})`
    的实参里**，于是主表**根本看不见它**。**D-D**（控件用了
    `fieldCode` / `ctrlType` / `invisible` / `disabled`，真机是 `code` / `type` / …）
    就是从这个**静默盲区**漏过去的，最后靠人工对照才逮到。

    ## 为什么键集要**按端点分开存**

    数组名会**撞车**：`controls` 既是 `Layout/SaveLayoutDetail` 的元素数组，
    也是工作流步骤端点里的元素数组——**两者是不同的契约**。
    若只留一份全局并集，工作流那侧就会拿**布局的**键集去判定，凭空多出一个假阳性。
    ⇒ 这里按端点分开存，由调用方（`keygate.compare_subliterals`）**按 writer 模块**取用。

    ⚠️ 同一个端点内部，键集也仍是**并集**：平台对同一个数组名会发**两种形状**
    （新建组件 8 键 vs 服务端已有组件 23 键，见 F36）——拿单条样本比会把合法的键误报成"多发"。
    """
    root = root or SESSION_ROOT
    arrays: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return arrays
    for path in sorted(root.glob("*/traffic.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            body = row.get("postData")
            if not isinstance(body, dict):
                continue
            endpoint = str(row.get("path") or "").split("?", 1)[0]
            for name, value in body.items():
                if not isinstance(value, list):
                    continue
                elements = [x for x in value if isinstance(x, dict)]
                if not elements:
                    continue  # 空数组 / 标量数组：没有"元素键集"可言
                slot = arrays.setdefault(name, {"by_endpoint": {}, "samples": 0})
                bucket = slot["by_endpoint"].setdefault(endpoint, set())
                for element in elements:
                    bucket.update(str(k) for k in element)
                    slot["samples"] += 1
    return arrays


#: 收集"像枚举"的字符串值时的长度上限。
#: 超过它的多半是 `name`/`code`/自由文本，不是**词汇表**——收进来只会把"真机见过"
#: 稀释成"什么都见过"，反而让闸门失去抓力。
_VALUE_MAX_CHARS = 40


def collect_values(root: pathlib.Path | None = None) -> dict[str, dict[str, list[str]]]:
    """数组名 → {元素键: 真机见过的**字符串值**（排序后）}。

    ## 它补的是 D-D 的**另一半**

    键名闸门只比**键名**，而 D-D 是两半：键名（`ctrlType` vs `type`）**与词汇表**
    （`attachment`/`object`/`date` vs **`AKAttachment`**/…）。只比键名，
    等于"键名改对了、值继续自造"照样过闸门。

    ⚠️ 长度阈值只是**筛子**，不是判据。判据由调用方
    （`keygate.VOCAB_PAIRS`）**显式声明**——"哪张工厂词表 ↔ 哪个真机字段"，
    没声明的就是未核，绝不靠名字像不像去猜。
    """
    root = root or SESSION_ROOT
    values: dict[str, dict[str, list[str]]] = {}
    if not root.exists():
        return values
    for path in sorted(root.glob("*/traffic.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            body = row.get("postData")
            if not isinstance(body, dict):
                continue
            for name, value in body.items():
                if not isinstance(value, list):
                    continue
                for element in value:
                    if not isinstance(element, dict):
                        continue
                    bucket = values.setdefault(str(name), {})
                    for key, item in element.items():
                        if not isinstance(item, str) or len(item) > _VALUE_MAX_CHARS:
                            continue
                        seen = bucket.setdefault(str(key), [])
                        if item not in seen:
                            seen.append(item)
    for bucket in values.values():
        for key in bucket:
            bucket[key] = sorted(bucket[key])
    return values


def describe(value: object) -> str:
    """把一个值压成"形状"（响应侧审计用）——不比结构，只比**形状**。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"bool({value})"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str" if len(value) > 24 else f"str({value!r})"
    if isinstance(value, list):
        if not value:
            return "list[0]"
        first = value[0]
        if isinstance(first, dict):
            return f"list[{len(value)}] of dict({','.join(sorted(first)[:6])}…)"
        return f"list[{len(value)}] of {type(first).__name__}"
    if isinstance(value, dict):
        return f"dict({','.join(sorted(value)[:8])}…)"
    return type(value).__name__


def collect_responses(root: pathlib.Path | None = None) -> dict[str, set[str]]:
    """端点 → { 响应信封里 `data` 的形状 }。

    ⚠️ 已建模端点的大响应会被**裁剪**（F14）：`data` 会变成**截断后的字符串**。
    看到 `str` 先怀疑裁剪，不要当结构解析。
    """
    root = root or SESSION_ROOT
    seen: dict[str, set[str]] = {}
    if not root.exists():
        return seen
    for path in sorted(root.glob("*/traffic.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            body = row.get("body")
            if not isinstance(body, dict):
                continue
            endpoint = str(row.get("path") or "").split("?", 1)[0]
            if endpoint:
                seen.setdefault(endpoint, set()).add(describe(body.get("data")))
    return seen


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--responses" in args:
        rows = collect_responses()
        if not rows:
            print(f"✗ {SESSION_ROOT} 下没有带响应体的采集数据")
            return 0
        print(f"真机见过的响应：{len(rows)} 个端点（`data` 的形状）\n")
        for endpoint, shapes in sorted(rows.items()):
            print(endpoint)
            for shape in sorted(shapes):
                print(f"    data = {shape}")
            print()
        print("⚠️ 已建模端点的大响应会被裁剪（F14）：`data` 可能是**截断后的字符串**，别当结构解析。")
        return 0
    seen = collect()
    if "--json" in args:
        print(json.dumps({k: sorted(list(v) for v in variants)
                          for k, variants in sorted(seen.items())},
                         ensure_ascii=False, indent=2))
        return 0
    if not seen:
        print(f"✗ {SESSION_ROOT} 下没有带请求体的采集数据"
              "（干净 clone 上是正常的：先跑 capture.py start/stop → ingest.py）")
        return 0
    print(f"真机见过请求体的端点：{len(seen)} 个\n")
    for endpoint, variants in sorted(seen.items()):
        print(endpoint)
        for keys in sorted(variants):
            print("    " + ", ".join(keys))
        print()
    print("下一步：对每个端点，去 workbench/services/egmp/writers/** 找它的请求体，"
          "逐键比一遍（见 factory-tuning/reports/2026-09-22-键名审计-真机vs工厂.md）。")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
