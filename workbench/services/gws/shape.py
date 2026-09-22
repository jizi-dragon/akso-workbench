"""形状对照 —— 判断两套线格式是不是同一个东西。

## 它存在的原因

整个 CLI 改造路线的**决定性未知**是一个问句：

> `component instance save` 的实例形状，与配置工厂已经真机取证的那套
> （`/api/platform/Layout/SaveLayoutDetail` 的 17 键布局体、控件的 8/23 键…）
> **是不是同一个东西？**

这个问题如果靠"我把两份 JSON 并排贴出来，人眼扫一遍"，会犯两类错：

1. **看漏**——17 键 vs 14 键，漏掉的那 3 个恰恰是承重的（D-C/D-F 就是这么栽的：
   工厂只发 4 键，丢的是 `code`/`name`/`source`/`isMain` 这些**身份键**）；
2. **把数字当结论**——"看起来差不多"不是一个能写进文档、能被别人复算的判据。

所以这里把它变成**可复算的三个数**：交集 / 只在参考侧 / 只在候选侧。

## 判定是**分诊标签**，不是证明

`verdict()` 给 A/B/C 三个字母，它是**决定下一步往哪走**用的，不是"证明了可迁移"。
真正的证明只有一条：**用这个形状真的写一次、再回读对上**（本仓 PITFALLS #62 的同族教训——
接口回 `code: 0` 不等于改对了）。所以报告里**数字必须与字母一起出现**，
让人能推翻字母。

## 为什么是"逐元素"而不是"逐请求"

2026-09-22 真机取证（段 2/段 3）纠正过一次错口径：同一份请求体里，
**新建的元素是精简形状、已存在的元素是完整形状**——两种形状**并存于同一条请求**。
所以任何"整份是精简的 / 整份是完整的"这种判断都是错的，
比较必须在**元素层**做（`controls[]` / `sections[]` 各自比）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

#: A 档的门槛：交集占并集的比例达到它就认为"高度重合"。
#: **这是分诊用的启发式，不是统计结论**——选 0.6 的理由是：
#: 工厂与真机在同一端点上的已知偏差量级是"少 1–3 个键 / 错 1–2 个键名"
#: （D-C 少 13 键是极端案例），0.6 能把那一类与"完全是另一套模型"分开。
OVERLAP_THRESHOLD = 0.6


@dataclass(frozen=True)
class ShapeDiff:
    """一个作用域下的键集差异（如"顶层"或"`controls[]` 的元素"）。

    `normalized=True` 表示这是**抹平命名约定之后**的对照（见 :func:`normalize_key`）。
    """

    scope: str
    common: tuple[str, ...]
    only_reference: tuple[str, ...]
    only_candidate: tuple[str, ...]
    normalized: bool = False

    @property
    def jaccard(self) -> float:
        """交集 / 并集。两边都空时定义为 0（**不定义成 1**——"都没键"不是"吻合"）。"""
        union = set(self.common) | set(self.only_reference) | set(self.only_candidate)
        if not union:
            return 0.0
        return len(self.common) / len(union)

    @property
    def recall_reference(self) -> float:
        """**参考侧有多少键在候选侧找得到对应**（召回）。

        为什么单有 Jaccard 不够（2026-09-22 真机实测）：两边**基数差异很大**时，
        Jaccard 会被"候选侧多出来的那些列"压得很低，而多出来的往往只是**行元数据**
        （`created_by` / `created_time` / `full_path` / `sort` / `active`…）——
        它们的存在**不是问题**。真正要问的是："我们那套知识，有多少能在新通道里落地？"
        那是召回，不是 Jaccard。
        """
        total = len(self.common) + len(self.only_reference)
        return len(self.common) / total if total else 0.0

    @property
    def precision_candidate(self) -> float:
        """候选侧有多少键在参考侧有对应（精确率）。低 = 候选侧多出一大堆我们不认识的列。"""
        total = len(self.common) + len(self.only_candidate)
        return len(self.common) / total if total else 0.0

    @property
    def verdict(self) -> str:
        """分诊档位。**A 需要"像"与"覆盖得住"同时成立**。

        只看 Jaccard 会把"候选侧多了一批行元数据列"判成模型不同；
        只看召回会把"最要紧的那几个内容键缺失"放过去（实测里缺的正是
        `controls` / `sections` / `pages` / `rules` —— 布局的**内容本体**）。
        所以两个都要过。
        """
        if not self.common:
            return "C"
        ok = self.recall_reference >= OVERLAP_THRESHOLD and self.jaccard >= OVERLAP_THRESHOLD
        return "A" if ok else "B"

    def render(self) -> str:
        tag = "（归一后）" if self.normalized else ""
        return (
            f"{self.scope}{tag}: 交集 {len(self.common)} / "
            f"仅参考侧 {len(self.only_reference)} / 仅候选侧 {len(self.only_candidate)} "
            f"(Jaccard {self.jaccard:.2f} · 参考侧覆盖 {self.recall_reference:.2f} · "
            f"候选侧命中 {self.precision_candidate:.2f} ⇒ {self.verdict})"
        )


def normalize_key(key: str) -> str:
    """抹平**命名约定**差异：`activePageCode` / `active_page_code` / `ActivePageCode` → 同一个键。

    ## 为什么必须有这一步（2026-09-22 真机实测）

    第一次对照给出的是 **Jaccard 0.14 ⇒ B（另一套模型）**，而真相是
    **同一套模型、两种命名约定**：

    | 工厂真机录制（camelCase，私有控制台接口） | gws component 实例（snake_case，公开 API） |
    |---|---|
    | `objectId` / `objectTypeId` / `isMain` / `labelCol` | `object_id` / `object_type_id` / `is_main` / `label_col` |
    | `activePageCode` / `activeSectionCode` / `formEditStatus` | `active_page_code` / `active_section_code` / `form_edit_status` |
    | `skipRequiredValidation` | `skip_required_validation` |
    | `code` / `id` / `name` / `source` / `status` | 逐字相同 |

    裸键集比较会把这种**机械可映射**的差异算成"模型对不上"，
    而那会把结论从「换传输层」错误地推到「全部重新取证」——
    两者工作量差一个数量级。所以归一后的对照**必须单独给一份**，
    并且**由它来出分诊档位**；裸对照仍然保留，它回答的是另一个问题
    （"要不要写一层命名转换"）。

    ⚠️ 归一化**只去下划线 + 转小写**，刻意不做更激进的模糊匹配：
    `rules` 与 `rules_json` 归一后是 `rules` / `rulesjson`，**仍然不相等**——
    这是对的，后者是"序列化成字符串的一列"，不是同一个东西。

    ## 外部参照

    与 OpenAPI 生态的 `case-conversion` 中间件是同一目的（前后端各自用自己的命名约定），
    但这里**只在对照/诊断层做**，不在请求构造层做——因为"写出去用哪个约定"
    是必须实证的事，不能靠自动转换猜。
    """
    return key.replace("_", "").lower()


@dataclass
class ShapeReport:
    """整份对照结果。

    `verdict` 取**归一后**各作用域里**最弱**的那个——最弱环节决定可行性；
    裸对照（`normalized=False`）只作展示，它回答"要不要写一层命名转换"。
    """

    diffs: list[ShapeDiff] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def scored(self) -> list[ShapeDiff]:
        """参与分诊的那些差异——**只有归一后的**。"""
        return [d for d in self.diffs if d.normalized]

    @property
    def verdict(self) -> str:
        if not self.scored:
            return "?"
        rank = {"A": 0, "B": 1, "C": 2, "?": 3}
        return max((d.verdict for d in self.scored), key=lambda v: rank[v])

    @property
    def convention_gap(self) -> str:
        """命名约定差异有多大——裸 Jaccard 与归一 Jaccard 的落差。

        落差大 + 归一后高 ⇒ **同一套模型、两种命名约定**（一次机械映射就能对齐），
        这与"另一套模型"是完全不同的工作量。
        """
        raw = {d.scope: d.jaccard for d in self.diffs if not d.normalized}
        norm = {d.scope: d.jaccard for d in self.scored}
        shared = [s for s in raw if s in norm]
        if not shared:
            return "—"
        worst = max(shared, key=lambda s: norm[s] - raw[s])
        lift = norm[worst] - raw[worst]
        if lift < 0.15:
            return f"命名约定差异不显著（最大的作用域 {worst} 只抬升 {lift:+.2f}）"
        return (
            f"⚠️ **命名约定差异显著**：{worst} 归一后 Jaccard 抬升 {lift:+.2f}"
            f"（{raw[worst]:.2f} → {norm[worst]:.2f}）⇒ 大概率是**同一套模型的两种拼写**，"
            "可机械映射，不等于「模型不同」"
        )

    def render(self) -> str:
        lines = [d.render() for d in self.diffs]
        lines.append("")
        lines.append(f"命名约定落差：{self.convention_gap}")
        lines.extend(self.notes)
        return "\n".join(lines)


# ---------------------------------------------------------------- 形状抽取


def key_profile(payload: Any, *, depth: int = 1) -> dict[str, Any]:
    """描述一个对象/请求体的**形状**（只记键，不记值）。

    数组元素除了给**键的并集**，还给一份 `shapeHistogram`（"几个元素的键集是多大"）
    ——这一栏是必需的，不是装饰：

    > 2026-09-22 真机取证实测（段 2/段 3）：**同一份请求体的同一个数组里**，
    > **新建元素是精简形状（控件 8 键）、已存在元素是完整形状（控件 23 键）**。
    > 只报并集，读者会把"这次全是新建"误读成"两套模型不吻合"。

    真机 `SaveLayoutDetail`（n=25）上这两个形状是：
    `controls[]` 并集 23 键 / 新建元素 8 键；`sections[]` 并集 62 键 / 新建元素 5 键。

    `depth` 控制往下钻几层：1 = 顶层键 + 数组元素的键；2 = 再往里一层。
    再深就没意义了——线格式的契约在"有哪些键"，不在值。
    """
    profile: dict[str, Any] = {"kind": type(payload).__name__}
    if isinstance(payload, Mapping):
        profile["keys"] = sorted(str(k) for k in payload)
        for name, value in payload.items():
            if isinstance(value, list) and value and isinstance(value[0], Mapping):
                elem_keys: set[str] = set()
                histogram: dict[int, int] = {}
                for el in value:
                    if isinstance(el, Mapping):
                        elem_keys |= {str(k) for k in el}
                        histogram[len(el)] = histogram.get(len(el), 0) + 1
                profile[f"{name}[]"] = {
                    "len": len(value),
                    "elemKeys": sorted(elem_keys),
                    "shapeHistogram": {str(k): v for k, v in sorted(histogram.items())},
                }
                if depth >= 2:
                    nested: dict[str, list[str]] = {}
                    for el in value:
                        if not isinstance(el, Mapping):
                            continue
                        for sub_name, sub_val in el.items():
                            if isinstance(sub_val, list) and sub_val and isinstance(sub_val[0], Mapping):
                                nested.setdefault(str(sub_name), [])
                                for inner in sub_val:
                                    if isinstance(inner, Mapping):
                                        nested[str(sub_name)] = sorted(
                                            set(nested[str(sub_name)]) | {str(k) for k in inner}
                                        )
                    if nested:
                        profile[f"{name}[].*"] = nested
    return profile


def _keys_at(payload: Any, scope: str) -> set[str]:
    """按 `scope` 取键集。`scope` 形如 `顶层` / `controls[]` / `controls[].attrs`。"""
    if scope == "顶层":
        return {str(k) for k in payload} if isinstance(payload, Mapping) else set()
    if scope.endswith("[]"):
        name = scope[:-2]
        value = payload.get(name) if isinstance(payload, Mapping) else None
        if not isinstance(value, list):
            return set()
        out: set[str] = set()
        for el in value:
            if isinstance(el, Mapping):
                out |= {str(k) for k in el}
        return out
    if "[]." in scope:
        name, sub = scope.split("[].", 1)
        value = payload.get(name) if isinstance(payload, Mapping) else None
        if not isinstance(value, list):
            return set()
        out = set()
        for el in value:
            if isinstance(el, Mapping) and isinstance(el.get(sub), list):
                for inner in el[sub]:
                    if isinstance(inner, Mapping):
                        out |= {str(k) for k in inner}
        return out
    return set()


def diff_key_sets(
    reference: Iterable[str], candidate: Iterable[str], *, scope: str, normalized: bool = False
) -> ShapeDiff:
    ref, cand = set(reference), set(candidate)
    if normalized:
        ref = {normalize_key(k) for k in ref}
        cand = {normalize_key(k) for k in cand}
    return ShapeDiff(
        scope=scope,
        common=tuple(sorted(ref & cand)),
        only_reference=tuple(sorted(ref - cand)),
        only_candidate=tuple(sorted(cand - ref)),
        normalized=normalized,
    )


def compare_shapes(
    reference: Any,
    candidate: Any,
    *,
    scopes: Iterable[str] = ("顶层",),
    reference_label: str = "参考（工厂真机录制）",
    candidate_label: str = "候选（gws component 实例）",
) -> ShapeReport:
    """按 `scopes` 逐层对照两边的键集。每个作用域**出两份**：裸对照 + 归一后对照。

    分诊档位由**归一后**那份决定（理由见 :func:`normalize_key`）；裸那份回答
    "要不要写一层命名转换"。

    `scopes` 默认只比顶层；要去比数组元素就显式传 `("顶层", "controls[]", "sections[]")`
    —— **不自动发现**，因为"该比哪几层"本身是判断，不该藏在代码里。
    """
    report = ShapeReport()
    for scope in scopes:
        ref_keys = _keys_at(reference, scope)
        cand_keys = _keys_at(candidate, scope)
        if not ref_keys and not cand_keys:
            report.notes.append(f"[{scope}] 两边都没有这个作用域，跳过")
            continue
        if not ref_keys:
            report.notes.append(f"[{scope}] ⚠️ 参考侧为空 —— 不是「不吻合」，是**没得比**（别把它读成 C）")
            continue
        if not cand_keys:
            report.notes.append(f"[{scope}] ⚠️ 候选侧为空 —— 同上，先确认取到了实例再判")
            continue
        report.diffs.append(diff_key_sets(ref_keys, cand_keys, scope=scope))
        report.diffs.append(diff_key_sets(ref_keys, cand_keys, scope=scope, normalized=True))
    return report


def compare_key_sets(
    reference: Iterable[str],
    candidate: Iterable[str],
    *,
    scope: str,
) -> ShapeReport:
    """直接按**键集**对照（不经 payload）。用于"元素层"比较。

    ## 为什么需要这一路（2026-09-22 真机实测）

    工厂的 `controls[]` / `sections[]` 是**嵌在布局体里的数组元素**，
    而 component 模型里它们是**独立的组件类型**（`layout_control` / `layout_section`）。
    拿"整份布局体"去比"一行控件"，比出来的是**投影差异**（Jaccard 0.14 ⇒ B），
    而正确的比法是**元素对元素**：

    | 比较对象 | 裸 Jaccard | 归一后 | 参考侧覆盖 | 判定 |
    |---|---:|---:|---:|---|
    | `sections[]` ↔ `layout_section` | 0.10 | **0.66** | **0.84** | **A** |
    | `controls[]` ↔ `layout_control` | 0.16 | **0.49** | **0.83** | B |

    ⇒ **比错对象会把结论从"可迁移"推到"重新取证"。**
    """
    report = ShapeReport()
    ref, cand = set(reference), set(candidate)
    if not ref:
        report.notes.append(f"[{scope}] ⚠️ 参考侧为空 —— 没得比")
        return report
    if not cand:
        report.notes.append(f"[{scope}] ⚠️ 候选侧为空 —— 先确认取到了样本")
        return report
    report.diffs.append(diff_key_sets(ref, cand, scope=scope))
    report.diffs.append(diff_key_sets(ref, cand, scope=scope, normalized=True))
    return report


#: **已知的名称映射**：同一个概念在两侧的不同写法。`键 → (对应键, 种类)`。
#:
#: ⚠️ 这几条**不是猜的**，是 2026-09-22 真机逐键对照出来的，而且种类不同、处置也不同：
#:
#: - **序列化**（`attrs` → `attrs_json`）：一边是对象/数组，一边是 **JSON 字符串列**。
#:   `normalize_key` **刻意不**把它们并成一个（`attrs` ≠ `attrsjson`）——
#:   因为写请求体时"对象"与"字符串"是两个不同的东西，必须**显式声明**。
#: - **引用改编码**（`sectionId` → `section_code`）：component 模型里子组件用
#:   **编码**互相引用，而不是 Guid。这是**结构差异**，不是拼写差异。
KNOWN_RENAMES: dict[str, tuple[str, str]] = {
    "attrs": ("attrs_json", "序列化"),
    "externalDataConfig": ("external_data_config_json", "序列化"),
    "relationFilter": ("relation_filter_json", "序列化"),
    "rules": ("rules_json", "序列化"),
    "columns": ("columns_json", "序列化"),
    "conditions": ("conditions_json", "序列化"),
    "sortFields": ("sort_fields_json", "序列化"),
    "sectionId": ("section_code", "引用改编码"),
}

#: 兼容旧名（真机对照文档里用的是这个词）。
SERIALIZED_COLUMNS: dict[str, str] = {
    key: twin for key, (twin, kind) in KNOWN_RENAMES.items() if kind == "序列化"
}


def explain_gap(reference: set[str], candidate: set[str]) -> dict[str, list[str]]:
    """把"归一后仍对不上"的键**分类**，而不是丢一个大列表给人看。

    真机实测里剩下的差异只有这几类，分开看才知道该做什么：

    - `known`：**已知映射**（序列化变体 / 引用改编码）⇒ 查表即可，不是模型差异
    - `row_meta`：候选侧的行元数据列（`created_by` / `modified_by` / `full_path`…）
      ⇒ 读侧多出来的，**写侧通常不用发**
    - `unexplained`：**真正需要人去查的**
    - `candidate_only_extra`：候选侧多出来的、既不是行元数据也不是已知映射的列

    ## ⚠️ 必须**先归一再判缺**（2026-09-22 实测踩到）

    第一版拿**裸键集**做差，于是 `combinationDisplayFieldId`（候选侧其实有
    `combination_display_field_id`）被算进"真正需要查的"——13 条里 **9 条是假的**。
    形态与本仓 PITFALLS 那一族一样：**判据自己撒谎**，而且撒得"看起来很专业"
    （一长串待查项）。正确的顺序是：先归一判"是否真的缺"，再对**确实缺的**做归因。
    """
    norm_cand = {normalize_key(k) for k in candidate}
    norm_ref = {normalize_key(k) for k in reference}

    known: list[str] = []
    unexplained: list[str] = []
    matched_twins: set[str] = set()
    for key in sorted(reference):
        if normalize_key(key) in norm_cand:
            continue  # 归一后对得上 ⇒ 不是缺口
        twin_kind = KNOWN_RENAMES.get(key)
        if twin_kind and normalize_key(twin_kind[0]) in norm_cand:
            known.append(f"{key} → {twin_kind[0]}（{twin_kind[1]}）")
            matched_twins.add(normalize_key(twin_kind[0]))
        else:
            unexplained.append(key)

    row_meta_tokens = (
        "createdby", "createdtime", "modifiedby", "modifiedtime", "active",
        "belongid", "fullpath", "description", "sort", "status", "id",
    )
    extra = [
        k for k in sorted(candidate)
        if normalize_key(k) not in norm_ref and normalize_key(k) not in matched_twins
    ]
    return {
        "known": known,
        "serialized": [k for k in known if "序列化" in k],  # 兼容旧名
        "row_meta": sorted(k for k in extra if normalize_key(k) in row_meta_tokens),
        "unexplained": unexplained,
        "candidate_only_extra": sorted(k for k in extra if normalize_key(k) not in row_meta_tokens),
    }


#: A/B/C 三档的**处置口径**。写在这里而不是散在文档里，是为了让"判出 B"之后
#: 下一步动作不依赖谁的记忆。
VERDICT_ACTIONS: dict[str, str] = {
    "A": "工厂线格式知识**可迁移** ⇒ 这是「替换传输层」，风险集中在认证与错误映射",
    "B": "**另一套规范化模型** ⇒ F20–F66 需重新取证；新模型很可能更干净（公开 API 比私有控制台接口规整）",
    "C": "**完全对不上** ⇒ 先向研发要 component 实例的 schema 文档，再谈替代",
    "?": "**没比成** ⇒ 先确认两边都真的取到了样本，再重跑",
}
