"""布局回读（**只读**）：把平台上的表单布局抓下来，做前后对比。

## 为什么必须有这一个工具

验证"工厂往默认布局加控件"到底**生没生效**，不能只看工厂的返回值：

- `layouts.py::save_form_layout` 无论平台怎么处理，**一律**返回
  `{"success": True, "message": "表单布局保存成功（全量替换）"}` —— 它连响应体都不看；
- 而 `build_form_layout` 里字段查不到时是 `continue`（**静默跳过**，D-G），
  所以"工厂说成功"与"布局里真的多了那个控件"是**两件事**。

⇒ 判据只能落在平台侧：**回读一次布局，比"多了哪个控件、平台把哪些键补上了/丢掉了"**。
这正是本仓反复出现的那一族（#62/#63/#67/#69）：**接口回成功、回读才发现空**。

## 用法

    # 改动前先存一份基线
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\layout_read.py ^
        --account <账号id> --object-code sdfsdf__c --dump factory-tuning/data/layout-snapshots/before.json

    # 跑完工厂后再回读，并与基线对比
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\layout_read.py ^
        --account <账号id> --object-code sdfsdf__c --diff factory-tuning/data/layout-snapshots/before.json

**读的路径**：只读地 import 产品自己的 `egmp.client`（账号 → 已登录客户端，
token 由产品的 token 通道给，**本工具不碰口令、也不打印 token**）；
端点用产品 `writers/endpoints.py` 的**唯一事实源**，不在这里另抄字符串。

**退出码**：`0` 正常；`1` 出错（取不到对象/布局）；`3` `--diff` 时**发现差异**（供脚本判"变了"）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]     # 仓库根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SNAP_DIR = ROOT / "factory-tuning" / "data" / "layout-snapshots"


def _fetch(account: str, object_code: str) -> dict[str, Any]:
    """从平台读对象 + 全部表单布局 + 默认布局明细（只读 GET/POST 查询）。"""
    from workbench.services.egmp.client import egmp_client_for_account
    from workbench.services.egmp.writers.endpoints import LAYOUT_DETAIL, LAYOUT_LIST

    with egmp_client_for_account(account) as client:
        obj = client.get_object(object_code) or {}
        object_id = str(obj.get("id") or "")
        if not object_id:
            raise SystemExit(f"✗ 平台上看不到对象 {object_code}（get_object 返回空）")
        layouts = client.get(LAYOUT_LIST, params={"basicObjectId": object_id,
                                                 "pageIndex": 1, "pageSize": 100}) or []
        # ⚠️ **这个端点回的是分页信封** `{count, hasUp, hasNext, datas:[…]}`，**不是数组**。
        # 第一版这里写的是 `[x for x in layouts if isinstance(x, dict)]` —— 对 dict 迭代
        # 拿到的是**键（字符串）**，于是全部被过滤掉、工具报"布局 0 个"（实测踩过，2026-09-22）。
        # 同一形状的坑在**产品里也有一份**：`writers/layouts.py::get_form_layouts` 是
        # `list(client.get(LAYOUT_LIST, …) or [])` ⇒ 拿到的是 `['count','hasUp','hasNext','datas']`，
        # 后面 `layout.get("source")` 必然炸/找不到 —— 见本条下面的读数。
        if isinstance(layouts, dict):
            layouts = layouts.get("datas") or layouts.get("items") or []
        layouts = [lay for lay in layouts if isinstance(lay, dict)]
        default = next((lay for lay in layouts if lay.get("source") == 2), None)
        detail: dict[str, Any] = {}
        if default:
            detail = client.get(LAYOUT_DETAIL, params={"layoutId": str(default.get("id")),
                                                      "basicObjectId": object_id}) or {}
        return {
            "object": {"id": object_id, "code": str(obj.get("code") or ""),
                       "name": str(obj.get("name") or "")},
            "layouts": [{"id": str(lay.get("id")), "name": lay.get("name"),
                         "source": lay.get("source"), "isMain": lay.get("isMain")}
                        for lay in layouts],
            "default_layout": ({"id": str(default.get("id")), "name": default.get("name"),
                                "source": default.get("source"), "isMain": default.get("isMain")}
                               if default else None),
            "sections": [{"id": str(sec.get("id")), "name": sec.get("name"),
                          "code": sec.get("code"), "columnsNum": sec.get("columnsNum"),
                          "keys": len(sec)} for sec in (detail.get("sections") or [])],
            "controls": [{"id": str(ctl.get("id")), "sectionId": str(ctl.get("sectionId")),
                          "code": ctl.get("code"), "type": ctl.get("type"),
                          "fieldId": str(ctl.get("fieldId")), "keys": sorted(ctl)}
                         for ctl in (detail.get("controls") or [])],
        }


def _print(snap: dict[str, Any]) -> None:
    obj = snap["object"]
    print(f"对象：{obj['name']}（{obj['code']}）  id={obj['id']}")
    print(f"表单布局 {len(snap['layouts'])} 个：")
    for lay in snap["layouts"]:
        mark = " ← 默认(source=2)" if lay["source"] == 2 else ""
        print(f"  - {lay['name']}  source={lay['source']} isMain={lay['isMain']}"
              f"  id={lay['id']}{mark}")
    default = snap.get("default_layout")
    if not default:
        print("  ⚠️ **没有 source=2 的默认布局** —— 工厂那条路会直接返回「默认表单布局未找到」")
        return
    print(f"\n默认布局明细：{len(snap['sections'])} 个分组 / {len(snap['controls'])} 个控件")
    for sec in snap["sections"]:
        kids = [c for c in snap["controls"] if c["sectionId"] == sec["id"]]
        print(f"  分组 {sec['name']}（{sec['code']}）columnsNum={sec['columnsNum']} "
              f"键数={sec['keys']}  → {len(kids)} 个控件")
        for ctl in kids:
            print(f"      · {ctl['code']}  type={ctl['type']}  keys={ctl['keys']}")


def _diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """前后对比：分组/控件的增删与键集变化。返回**差异行**（空 = 无变化）。"""
    out: list[str] = []
    b_sec = {s["id"]: s for s in before["sections"]}
    a_sec = {s["id"]: s for s in after["sections"]}
    for sid, sec in a_sec.items():
        if sid not in b_sec:
            out.append(f"＋ 新分组 {sec['name']}（{sec['code']}）id={sid} 键数={sec['keys']}")
        elif b_sec[sid]["keys"] != sec["keys"]:
            out.append(f"~ 分组键集变了 {sec['name']}：{b_sec[sid]['keys']} → {sec['keys']}")

    def key(ctl: dict[str, Any]) -> str:
        return str(ctl.get("fieldId") or ctl.get("code") or ctl.get("id"))

    b_ctl = {key(c): c for c in before["controls"]}
    a_ctl = {key(c): c for c in after["controls"]}
    for ckey, ctl in a_ctl.items():
        if ckey not in b_ctl:
            out.append(f"＋ 新控件 fieldId={ckey} code={ctl['code']} type={ctl['type']} "
                       f"sectionId={ctl['sectionId']}")
            out.append(f"     它的键集（{len(ctl['keys'])}）：{ctl['keys']}")
    for ckey, ctl in b_ctl.items():
        if ckey not in a_ctl:
            out.append(f"－ 控件没了 fieldId={ckey} code={ctl['code']}（**要警惕全量替换把旧的清掉**）")
    for ckey in b_ctl.keys() & a_ctl.keys():
        if b_ctl[ckey]["keys"] != a_ctl[ckey]["keys"]:
            out.append(f"~ 控件键集变了 {a_ctl[ckey]['code']}："
                       f"{b_ctl[ckey]['keys']} → {a_ctl[ckey]['keys']}")

    if before.get("default_layout") != after.get("default_layout"):
        out.append(f"~ 默认布局自身变了：{before.get('default_layout')} → {after.get('default_layout')}")
    if [lay["id"] for lay in before["layouts"]] != [lay["id"] for lay in after["layouts"]]:
        out.append(f"~ 布局清单变了：{[lay['name'] for lay in before['layouts']]} → "
                   f"{[lay['name'] for lay in after['layouts']]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="布局回读（只读）")
    ap.add_argument("--account", required=True, help="账号 id（凭证来源）")
    ap.add_argument("--object-code", required=True, help="对象编码，如 sdfsdf__c")
    ap.add_argument("--dump", default="", help="把当前快照写到这个文件")
    ap.add_argument("--diff", default="", help="与这份基线快照对比，只报差异")
    args = ap.parse_args(argv)

    snap = _fetch(args.account, args.object_code)
    if args.diff:
        base_path = pathlib.Path(args.diff)
        if not base_path.is_file():
            print(f"✗ 基线不存在：{base_path}")
            return 1
        before = json.loads(base_path.read_text(encoding="utf-8"))
        print(f"== 与基线对比（{base_path.name}）==")
        rows = _diff(before, snap)
        if not rows:
            print("  （无变化：布局里没多控件，也没少控件）")
            return 0
        for row in rows:
            print(f"  {row}")
        return 3

    _print(snap)
    if args.dump:
        path = pathlib.Path(args.dump)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已存快照：{path}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
