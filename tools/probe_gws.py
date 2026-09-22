"""gws 只读探针 —— 回答**唯一那个决定性问题**：component 通道的实例形状，与工厂已取证的线格式能不能对得上。

## 为什么必须先跑这个（而不是先写代码）

`gws component instance save <type> --data @file.json` 里的 `instances` 是**「动态配置」**——
CLI **不校验、不提示、不给 schema**。所以：

> **没有一份真实的组件实例样本，就写不出一个正确的 `save` 请求体。**
> 不是"写不好"，是**写不出来**——键名从哪来？

而配置工厂那套来之不易的知识（F20–F66：17 键布局体、控件 8/23 键、`AK*` 七值对九类型、
`GetStableComponentIds` 的增量语义）**全部是在 `/api/platform/Layout/SaveLayoutDetail`
这个语义下测出来的**。换成 `component/instances/save` 是**另一条通道**，
能不能迁移**必须用样本证明，不能假设**。

## 它做什么（全部只读，一条写命令都不发）

| 步 | 命令 | 回答什么 |
|---|---|---|
| S0 | `version` / `doctor` | 二进制能不能跑、CLI 自己怎么看这台机器 |
| S1 | `component metadata list` | **组件类型全表**（`save <type>` 的 `<type>` 只能从这里拿） |
| S2 | `component instance list <type>` | 每类有多少实例（顺带验证通道是否真的通） |
| S3 | `component instance show <type> <full_path>` | **一份真实实例的完整形状**（决定性证据） |
| S4 | `metadata object fields <code>` | 对象字段结构（与工厂读路径对照） |
| S5 | **对照** | 把 S3 的键集与 `factory-tuning` 里录到的真机请求体**逐字对照**，输出键集差异 |

## 用法

```powershell
# 看有哪些可对照的历史样本
.\\.venv\\Scripts\\python.exe tools\\probe_gws.py --list-samples

# 跑探针（需要 service / app_id / app_secret）
.\\.venv\\Scripts\\python.exe tools\\probe_gws.py --type layout --full-path layout.xxx__ak

# 只要类型表，不做对照
.\\.venv\\Scripts\\python.exe tools\\probe_gws.py --skip-compare
```

退出码：`0` = 探针跑通并出报告；`2` = **未配置，没跑**（这是"缺东西"，不是"坏了"）；`1` = 跑了但失败。

⚠️ **本脚本只读**。`component instance save` / `migration import|deploy` 一行都不出现——
原因见 `workbench/services/gws/client.py` 的约束 ④。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workbench.services.gws import (  # noqa: E402
    GwsBinaryNotFound,
    GwsClient,
    GwsConfig,
    GwsError,
    GwsNotConfigured,
    GwsPlatformError,
    GwsProcessError,
    GwsTimeout,
)
from workbench.services.gws.shape import VERDICT_ACTIONS, compare_shapes, key_profile  # noqa: E402

SESSIONS_DIR = ROOT / "factory-tuning" / "data" / "sessions"


# ---------------------------------------------------------------- 历史样本（对照基线）


def load_recorded_bodies() -> list[dict]:
    """读 `factory-tuning/data/sessions/*/traffic.jsonl` 里录到的真机请求体。

    这是**对照基线**：工厂的线格式知识就是从这里来的（真机取证，n 见各报告）。
    只读；目录不存在时返回空表（`factory-tuning/` 不属于产品，允许缺席）。
    """
    rows: list[dict] = []
    if not SESSIONS_DIR.is_dir():
        return rows
    for session in sorted(SESSIONS_DIR.iterdir()):
        traffic = session / "traffic.jsonl"
        if not traffic.is_file():
            continue
        for line in traffic.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rec["_session"] = session.name
            rows.append(rec)
    return rows


def pick_reference(bodies: list[dict], needle: str) -> list[dict]:
    """挑出路径里含 `needle`、且有请求体的记录。"""
    out = []
    for rec in bodies:
        path = str(rec.get("path") or "")
        if needle.lower() in path.lower() and isinstance(rec.get("postData"), (dict, list)):
            out.append(rec)
    return out


# ---------------------------------------------------------------- 报告


class Report:
    def __init__(self, outdir: Path) -> None:
        self.outdir = outdir
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.lines: list[str] = []
        self.dumps: list[str] = []

    def h(self, text: str) -> None:
        self.lines.append(f"\n## {text}\n")

    def p(self, text: str = "") -> None:
        self.lines.append(text)

    def raw(self, name: str, payload: Any) -> Path:
        """把一份原始响应原样落盘（**不裁剪**——探针的产出就是要给人逐字看的）。"""
        path = self.outdir / f"{name}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        self.dumps.append(name)
        self.p(f"- 原始响应：`{path.name}`")
        return path

    def write(self) -> Path:
        path = self.outdir / "SUMMARY.md"
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8", newline="\n")
        return path


# ---------------------------------------------------------------- 主流程


def probe(args: argparse.Namespace) -> int:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out) if args.out else ROOT / "tmp" / f"gws-probe-{stamp}"
    rep = Report(outdir)

    client = GwsClient(GwsConfig.from_env(timeout_s=args.timeout, service=args.service or None))
    rep.h(f"gws 只读探针 · {stamp}")
    rep.p(f"- 输出目录：`{outdir}`")
    rep.p("- 发送的命令**全部只读**（白名单见 `workbench/services/gws/client.py`）")

    # -------- S0 二进制自身
    rep.h("S0 · 二进制与环境")
    try:
        version = client.version()
    except GwsBinaryNotFound as exc:
        print(f"[SKIP] 未找到 gws：{exc}")
        return 2
    except GwsError as exc:
        print(f"[FAIL] S0: {exc}")
        return 1
    rep.p(f"- `gws version` → **{version}**")
    try:
        doctor = client.doctor()
        (outdir / "doctor.txt").write_text(doctor, encoding="utf-8", newline="\n")
        rep.p("- `gws doctor` 原文：`doctor.txt`")
        rep.p("  > ⚠️ 实测：doctor 在「部分检查未通过」时**仍 exit=0**，所以它只能当信息，不能当闸门。")
    except GwsError as exc:
        rep.p(f"- `gws doctor` 失败：`{exc}`")

    # -------- S1 组件类型全表（第一块地基）
    rep.h("S1 · 组件类型全表（`save <type>` 的取值只能从这里拿）")
    try:
        types = client.component_types()
    except GwsNotConfigured as exc:
        rep.p(f"**未配置，探针到此为止**：{exc}")
        rep.p("")
        rep.p("处置（三步，见 `docs/CLI-CONFIG-PLAN.md` §2）：")
        rep.p("1. `gws config set service <你的服务地址>`")
        rep.p("2. `gws config set-app`（AppId + SecretKey，eGMP 后台 →「应用管理」申请）")
        rep.p("3. `gws auth authorize -o`（**浏览器授权，需要人点一下**）")
        path = rep.write()
        print(f"[UNCONFIGURED] 未配置，报告（含处置）已写到 {path}")
        return 2
    except (GwsPlatformError, GwsProcessError, GwsTimeout, GwsBinaryNotFound) as exc:
        rep.p(f"**失败**：`{exc}`")
        path = rep.write()
        print(f"[FAIL] S1: {exc}\n报告：{path}")
        return 1

    rep.raw("component-metadata", types)
    rep.p(f"- 共 **{len(types)}** 个组件类型")
    rep.p("")
    rep.p("| component_type | name_cn | name_en | sort | parent_code |")
    rep.p("|---|---|---|---|---|")
    for item in types:
        if isinstance(item, dict):
            rep.p(
                "| `{}` | {} | {} | {} | {} |".format(
                    item.get("component_type", ""), item.get("name_cn", ""),
                    item.get("name_en", ""), item.get("sort", ""), item.get("parent_code", ""),
                )
            )

    # -------- S2 每类实例数
    rep.h("S2 · 各类型实例数（顺带验证通道是否真的通）")
    counts: dict[str, Any] = {}
    wanted = [args.type] if args.type else [
        str(t.get("component_type")) for t in types if isinstance(t, dict) and t.get("component_type")
    ]
    for ctype in wanted[: args.max_types]:
        try:
            inst = client.component_instances(ctype)
            counts[ctype] = len(inst)
            rep.p(f"- `{ctype}`：**{len(inst)}** 个实例")
            if inst:
                rep.raw(f"instances-{ctype}", inst[:20])
        except GwsError as exc:
            counts[ctype] = f"失败：{exc}"
            rep.p(f"- `{ctype}`：❌ {exc}")

    # -------- S3 单个实例（决定性证据）
    fetched: Any = None
    if args.full_path:
        rep.h(f"S3 · 单个实例 `{args.type or '?'} / {args.full_path}` —— **决定性证据**")
        try:
            inst = client.component_instance(args.type, args.full_path)
            fetched = inst
            rep.raw("instance", inst)
            prof = key_profile(inst)
            keys = prof.get("keys", [])
            rep.p(f"- 顶层键数：**{len(keys)}**")
            rep.p(f"- 顶层键：`{keys}`")
            for key, sub in prof.items():
                if key.endswith("[]"):
                    rep.p(f"- `{key}`：{sub['len']} 个元素，元素键 `{sub['elemKeys']}`")
                    rep.p(f"  - 形状直方图（键数: 元素个数）`{sub['shapeHistogram']}`"
                          " ← **同一数组里可以两种形状并存**（新建 8 键 / 已存在 23 键）")
        except GwsError as exc:
            rep.p(f"**失败**：`{exc}`")

    if args.object_code:
        rep.h("S4 · 对象字段结构")
        try:
            fields = client.object_fields(args.object_code)
            rep.raw("object-fields", fields)
            rep.p(f"- `{args.object_code}`：{len(fields)} 个字段")
        except GwsError as exc:
            rep.p(f"- 失败：{exc}")

    # -------- S5 与真机录到的形状对照
    if not args.skip_compare:
        rep.h("S5 · 与 `factory-tuning` 录到的真机形状**逐字对照**")
        bodies = load_recorded_bodies()
        if not bodies:
            rep.p("- `factory-tuning/data/sessions/` 里没有可用的录制数据（该目录不属于产品，允许缺席）")
        else:
            references: dict[str, dict] = {}
            for needle, label in (
                ("SaveLayoutDetail", "表单布局 · SaveLayoutDetail"),
                ("SaveBasicObject", "对象 · SaveBasicObject"),
                ("SaveField", "字段 · SaveField"),
                ("AddColumns", "列表布局 · AddColumns"),
                ("GetStableComponentIds", "稳定 id · GetStableComponentIds"),
            ):
                refs = pick_reference(bodies, needle)
                if not refs:
                    rep.p(f"- **{label}**：录制里没有 —— 无从对照")
                    continue
                newest = refs[-1]
                references[needle] = newest
                prof = key_profile(newest["postData"])
                keys = prof.get("keys", [])
                rep.p(f"- **{label}**（{len(refs)} 条样本，取最后一条 · 会话 `{newest['_session']}`）")
                rep.p(f"  - 顶层键数 **{len(keys)}**：`{keys}`")
                for key, sub in prof.items():
                    if key.endswith("[]"):
                        rep.p(f"  - `{key}`：{sub['len']} 个元素，元素键 `{sub['elemKeys']}`")
                        rep.p(f"    - 形状直方图 `{sub['shapeHistogram']}`")

            # ---- 真正的判定：只在**两边都拿到了样本**时才做
            rep.h("S5b · 判定（§5 的 A / B / C）")
            needle = args.compare_against
            reference = references.get(needle)
            if reference is None:
                rep.p(f"- ⏸ **没比成**：录制里没有 `{needle}` 的样本（可用 `--compare-against` 换一个）")
            elif fetched is None:
                rep.p("- ⏸ **没比成**：这次没有取到 component 实例（缺 `--type` + `--full-path`，或 S3 失败了）")
                rep.p("  ⇒ 判定**未发生**，不要把它读成 C。补上参数重跑即可。")
            else:
                scopes = tuple(s for s in args.scopes.split(",") if s)
                report = compare_shapes(
                    reference["postData"], fetched, scopes=scopes,
                    reference_label=f"{needle}（会话 {reference['_session']}）",
                    candidate_label=f"component instance {args.type}",
                )
                rep.p("```")
                rep.p(report.render())
                rep.p("```")
                rep.p(f"- **判定：{report.verdict}** —— {VERDICT_ACTIONS.get(report.verdict, '')}")
                rep.p("")
                rep.p("> ⚠️ **这是分诊标签，不是证明。** 真正的证明只有一条：")
                rep.p("> **用这个形状真的写一次、再回读对上**（接口回 `code: 0` 不算，见 PITFALLS #62）。")
                rep.p("> 三个数字都在上一段里，可以推翻这个字母。")
                rep.p(">")
                rep.p("> ⚠️ **口径提醒（2026-09-22 实测）**：`component instance show` 回的是**行投影**"
                       "（带 `created_by`/`modified_by`/`sort`/`full_path` 这类行元数据，"
                       "且把嵌套内容压成 `rules_json` 这样的字符串列），"
                       "而参考侧是**写入体**（含 `controls`/`sections`/`pages`/`rules` 四个内容本体）。")
                rep.p("> ⇒ **两边不是同一个投影**。'仅参考侧'里出现内容键、'仅候选侧'里全是行元数据，"
                       "是**投影差异**，不等于'模型不同'。要判定写入体的形状，得看"
                       " `component instance save` 到底收什么——那需要一次**真机写入**（要有你的下达）。")

            rep.p("")
            rep.p("### 怎么读这张对照表")
            rep.p("1. 若 component 实例的键集与上面某一行**高度重合** ⇒ 工厂的线格式知识**可迁移**，")
            rep.p("   这次改造是「替换传输层」，风险主要在认证与错误映射。")
            rep.p("2. 若**只有少数几个键对得上** ⇒ 是**另一套规范化模型**，工厂的 F20–F66 需要**重新取证**，")
            rep.p("   但它很可能比现在更干净（公开 API 通常比私有控制台接口规整）。")
            rep.p("3. 若**完全对不上** ⇒ 要先问研发要 component 实例的 schema，再谈替代。")

    path = rep.write()
    print(f"[OK] 探针完成，报告：{path}")
    print(f"     原始响应 {len(rep.dumps)} 份在 {outdir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="gws 只读探针（不写平台）")
    parser.add_argument("--type", default="", help="组件类型，如 layout / object / control")
    parser.add_argument("--service", default="", help="服务地址（默认取 WORKBENCH_GWS_SERVICE）")
    parser.add_argument("--full-path", default="", help="实例全路径，形如 object.aa__c")
    parser.add_argument("--object-code", default="", help="顺带读该对象的字段结构")
    parser.add_argument("--out", default="", help="输出目录（默认 tmp/gws-probe-<时间戳>）")
    parser.add_argument("--timeout", type=float, default=90.0, help="单条命令超时秒数")
    parser.add_argument("--max-types", type=int, default=30, help="最多枚举多少个组件类型的实例")
    parser.add_argument("--skip-compare", action="store_true", help="跳过与历史录制数据的对照")
    parser.add_argument(
        "--compare-against",
        default="SaveLayoutDetail",
        help="拿哪个真机录制的端点当参考基线（默认 SaveLayoutDetail；布局是键最多、最有判别力的那个）",
    )
    parser.add_argument(
        "--scopes",
        default="顶层,controls[],sections[]",
        help="逐层比较哪些作用域，逗号分隔（默认 顶层,controls[],sections[]）",
    )
    parser.add_argument("--list-samples", action="store_true", help="只列出可对照的历史样本，不跑探针")
    args = parser.parse_args()

    if args.list_samples:
        bodies = load_recorded_bodies()
        if not bodies:
            print(f"没读到录制数据（{SESSIONS_DIR}）；该目录不属于产品，允许缺席。")
            return 0
        from collections import Counter

        counter = Counter(str(r.get("path") or "").split("?")[0] for r in bodies)
        print(f"共 {len(bodies)} 条录制请求，{len(counter)} 个不同路径：\n")
        for path, n in counter.most_common():
            print(f"  {n:>4}  {path}")
        return 0

    return probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
