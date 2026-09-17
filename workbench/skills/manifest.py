"""Skill 包格式与解析：`SKILL.md` 的 YAML frontmatter → 规范化清单。

## 格式（与 ADP 的 Skills 规范同源，都遵循 Agent Skills 开放标准）

    skillhub-demo.zip
    └── SKILL.md            ← 必须在**根目录**（不可套一层文件夹）
        scripts/            ← 可选（本期不执行，见下）
        references/         ← 可选

`SKILL.md` 的 frontmatter：

    ---
    id: demo-lookup                    # 能力 id（= 插件 id），[a-z0-9._-]
    name: 示例查询                      # 显示名
    version: 1.0.0
    description: 一句话说明做什么 + 什么时候用（模型靠它选工具）
    capabilities: [hosttools]           # 需要的能力，见 §capabilities
    tools:
      - name: demo_lookup
        description: 按编码查东西
        wraps: insight_read_config      # 声明式：复用已有工具
        args:                           # 可选：默认继承被包装工具的 schema
          code: {type: string, description: 对象编码}
    ---
    # 正文（写给人看的说明；本期不注入上下文）

## `capabilities` 与沙箱策略的映射（**最小权限**）

`capabilities` 是**声明需求**，不是授予权限。安装时按它算出所需沙箱策略，
只在执行该能力的工具时使用——**装一个只读查询不该拿到能联网的策略**。

| 声明 | 含义 | 影响 |
|---|---|---|
| `hosttools` | 复用本工作台已有的工具 | 无额外权限（被包装工具自己已有风险分级） |
| `network` | 需要联网 | 本期**拒绝**（策略里 `allow_network` 未强制，声明了也给不了） |
| `filesystem` | 需要读写沙箱内文件 | 允许（沙箱根内） |
| `subprocess` | 需要派生进程 | 本期**拒绝**（无消费方） |

## 为什么本期只支持**声明式**工具

用户的路线是"先建沙箱，再接入 skill"。沙箱底座已就绪，但**执行用户脚本**还差
"把脚本作为工具暴露"这一步，它需要：脚本入口约定、参数如何传给脚本、
输出如何回灌给模型、失败如何归类。那是独立的一块，混进来会让本期无法收敛。
**声明式先跑通"上传→安装→生效→卸载"整条链路**，脚本式作为下一期增量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: 支持的 capabilities。**有限枚举**，未知值一律拒（不做"忽略不懂的"）。
KNOWN_CAPABILITIES: frozenset[str] = frozenset({"hosttools", "filesystem", "network", "subprocess"})

#: 本期**允许**的 capabilities。其余声明了也拒——不是"忽略"，是**安装失败**。
#: 理由：静默忽略一个权限声明，会让用户以为"它需要联网，平台给了"，
#: 而实际没给，最终表现为"这个 Skill 装了但功能不对"。Fail-Closed 比"看起来装上了"好。
ALLOWED_CAPABILITIES: frozenset[str] = frozenset({"hosttools", "filesystem"})

#: 工具名允许的形状（与 `plugin_api.FUNCTION_NAME_RE` 同源：模型侧 function name 限制）。
TOOL_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

MAX_TOOLS_PER_SKILL = 20
"""一个 Skill 最多贡献多少工具。上限存在的理由与报告 §7.5 一致：
工具超过约 20 个后模型选错的概率显著上升，而"装一个 Skill 塞 50 个工具"
是把这个坑从产品自带转移到了用户身上。"""


class SkillPackageError(ValueError):
    """包不合规。**Fail-Closed**：上传/安装直接失败，不"尽量解析"。"""


@dataclass
class SkillTool:
    """Skill 声明的一个工具。"""

    name: str
    description: str
    wraps: str
    """被包装的**已有工具名**。声明式 Skill 的全部能力都来自复用。"""
    args_schema: dict[str, Any] = field(default_factory=dict)
    """参数 JSON Schema。为空 = 继承被包装工具的 schema。"""


@dataclass
class SkillManifest:
    """解析后的规范化清单（落 `manifest.json`，只算一次）。"""

    id: str
    name: str
    version: str
    description: str
    capabilities: list[str]
    tools: list[SkillTool]
    body: str = ""
    """SKILL.md 正文（本期不注入上下文，但保留——它是写给人看的使用说明）。"""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "description": self.description, "capabilities": list(self.capabilities),
            "tools": [
                {"name": t.name, "description": t.description, "wraps": t.wraps,
                 "args": t.args_schema}
                for t in self.tools
            ],
            "body_chars": len(self.body),
        }


#: JSON Schema 的 `type` → Python 类型。**只支持这几种**，其余一律拒。
#: 不支持 object/array 嵌套：那需要完整的递归转换，而本期工具参数都是标量
#: （与既有 20 个工具的形态一致）。**宁可拒，不要"尽力转换"**——
#: 一个被静默降级成 `Any` 的参数会让模型传什么都通过，等于没有契约。
_SCALAR_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def json_schema_to_fields(schema: dict[str, Any]) -> dict[str, Any]:
    """工具参数 → pydantic 字段定义 dict，供 `create_model` 用。

    这是"声明式工具"的关键一步：Skill 侧只有 JSON Schema，而 `ToolSpec.args_model`
    要求 pydantic 模型（本仓的**契约单一来源**纪律：schema 从类型派生，不手写第二份）。
    本函数就是那座桥。

    ## 接受**两种**写法（实测踩过才加的）

    ```yaml
    # ① 简写（推荐，ADP 的规范也是这么写的）：args 直接是"参数名 → 规格"
    args:
      code: {type: string, description: 对象编码}

    # ② 完整 JSON Schema
    args:
      type: object
      properties:
        code: {type: string, description: 对象编码}
      required: [code]
    ```

    初版只认 ②，于是 ① 被当成"没有 properties"→ **静默产出零个参数**，
    工具装上了却什么参数都收不到。这类"解析成功但结果为空"最危险，
    所以现在**两种都认**，且无法判定形状时**抛**而不是返回空。

    **不支持的构造一律抛**，不静默忽略——见 `_SCALAR_TYPES` 的说明。
    """
    from pydantic import Field

    if not schema:
        return {}

    if "properties" in schema or "type" in schema:
        # 形态 ②：完整 JSON Schema
        if schema.get("type") not in (None, "object"):
            raise SkillPackageError(f"args 的根必须是 object，得到 {schema.get('type')!r}")
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
    else:
        # 形态 ①：简写 —— 每个键就是一个参数，其值是该参数的规格
        props = schema
        # 简写无法表达"必填"，故**默认全部必填**（Fail-Closed：宁可要求传，
        # 也不要"模型不传也放过"）。想可选就显式写 `default`。
        required = {k for k, v in props.items()
                    if not (isinstance(v, dict) and "default" in v)}

    if not isinstance(props, dict):
        raise SkillPackageError("args.properties 必须是对象")

    fields: dict[str, Any] = {}
    for key, spec in props.items():
        if not isinstance(spec, dict):
            raise SkillPackageError(f"args.{key} 必须是对象（参数规格）")
        kind = spec.get("type")
        if kind not in _SCALAR_TYPES:
            raise SkillPackageError(
                f"args.{key} 的类型 {kind!r} 本期不支持（只支持 "
                f"{'/'.join(sorted(_SCALAR_TYPES))}）——需要复杂类型请改用脚本式 Skill"
            )
        py_type = _SCALAR_TYPES[kind]
        constraints: dict[str, Any] = {}
        if "enum" in spec:
            # 枚举用 Literal 表达，pydantic 会生成 JSON Schema 的 enum
            from typing import Literal

            values = spec["enum"]
            if not isinstance(values, list) or not values:
                raise SkillPackageError(f"args.{key}.enum 必须是非空数组")
            py_type = Literal[tuple(values)]  # type: ignore[valid-type]
        else:
            for src, dst in (("minimum", "ge"), ("maximum", "le"),
                             ("minLength", "min_length"), ("maxLength", "max_length")):
                if src in spec:
                    constraints[dst] = spec[src]
        if "pattern" in spec:
            constraints["pattern"] = spec["pattern"]
        description = spec.get("description", "")
        if "default" in spec:
            fields[key] = (py_type, Field(default=spec["default"], description=description,
                                          **constraints))
        elif key in required:
            fields[key] = (py_type, Field(..., description=description, **constraints))
        else:
            # 非必填：给 None 默认值。**不给类型默认值**（如 "" / 0）——
            # 那会让"调用方没传"与"调用方传了空串"变得无法区分。
            fields[key] = (py_type | None,
                           Field(default=None, description=description, **constraints))
    return fields


def build_args_model(tool_name: str, fields: dict[str, Any]):
    """按字段定义建一个 pydantic 模型（工具的参数契约）。"""
    from pydantic import create_model

    return create_model(f"{tool_name}_Args", **fields)


def parse_skill_md(text: str) -> SkillManifest:
    """解析 `SKILL.md`。任何不合规都抛 `SkillPackageError`（Fail-Closed）。"""
    import yaml

    if not text.startswith("---"):
        raise SkillPackageError("SKILL.md 必须以 `---` 开头的 YAML frontmatter 起始")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillPackageError("SKILL.md 的 frontmatter 未闭合（缺少第二处 `---`）")
    raw, body = parts[1], parts[2]
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise SkillPackageError(f"frontmatter 不是合法 YAML：{exc}") from exc
    if not isinstance(meta, dict):
        raise SkillPackageError("frontmatter 必须是键值映射")

    def _need(key: str) -> str:
        value = meta.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SkillPackageError(f"frontmatter 缺少必填字段：{key}")
        return value.strip()

    capability_id = _need("id")
    if not all(c.islower() or c.isdigit() or c in "._-" for c in capability_id):
        raise SkillPackageError(f"id 只允许小写字母/数字/._- ：{capability_id!r}")
    if not (capability_id[0].isalnum()):
        raise SkillPackageError(f"id 必须以字母或数字开头：{capability_id!r}")

    version = _need("version")
    from .layout import VERSION_RE

    if not VERSION_RE.fullmatch(version):
        raise SkillPackageError(f"version 必须是 x.y.z：{version!r}")

    capabilities = meta.get("capabilities") or []
    if not isinstance(capabilities, list) or not all(isinstance(c, str) for c in capabilities):
        raise SkillPackageError("capabilities 必须是字符串数组")
    unknown = [c for c in capabilities if c not in KNOWN_CAPABILITIES]
    if unknown:
        raise SkillPackageError(
            f"未知 capabilities：{unknown}（已知：{'/'.join(sorted(KNOWN_CAPABILITIES))}）"
        )
    unsupported = [c for c in capabilities if c not in ALLOWED_CAPABILITIES]
    if unsupported:
        raise SkillPackageError(
            f"capabilities 本期不支持：{unsupported}。"
            "**这不是「忽略」而是「拒绝」**——静默忽略会让你以为权限给了、实际没给，"
            "最终表现为「装上了但功能不对」。本期允许："
            f"{'/'.join(sorted(ALLOWED_CAPABILITIES))}"
        )

    raw_tools = meta.get("tools") or []
    if not isinstance(raw_tools, list) or not raw_tools:
        raise SkillPackageError("tools 必须是非空数组（一个 Skill 至少贡献一个工具）")
    if len(raw_tools) > MAX_TOOLS_PER_SKILL:
        raise SkillPackageError(
            f"一个 Skill 最多 {MAX_TOOLS_PER_SKILL} 个工具，得到 {len(raw_tools)}"
            "（工具过多会显著拉低模型选对工具的概率）"
        )

    tools: list[SkillTool] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_tools):
        if not isinstance(item, dict):
            raise SkillPackageError(f"tools[{index}] 必须是对象")
        name = item.get("name")
        if not isinstance(name, str) or not name or not set(name) <= TOOL_NAME_CHARS:
            raise SkillPackageError(
                f"tools[{index}].name 不合法：{name!r}（只允许字母/数字/_/-，且非空）"
            )
        if not (name[0].isalpha() or name[0] == "_"):
            raise SkillPackageError(f"tools[{index}].name 必须以字母或 _ 开头：{name!r}")
        if name in seen:
            raise SkillPackageError(f"工具名重复：{name}")
        seen.add(name)
        description = item.get("description")
        if not isinstance(description, str) or not description.strip():
            raise SkillPackageError(f"tools[{index}] 缺少 description（模型靠它选工具）")
        wraps = item.get("wraps")
        if not isinstance(wraps, str) or not wraps.strip():
            raise SkillPackageError(
                f"tools[{index}] 缺少 wraps（声明式 Skill 必须声明它复用哪个已有工具）"
            )
        args_schema = item.get("args") or {}
        if not isinstance(args_schema, dict):
            raise SkillPackageError(f"tools[{index}].args 必须是对象")
        tools.append(SkillTool(name=name, description=description.strip(),
                               wraps=wraps.strip(), args_schema=args_schema))

    return SkillManifest(
        id=capability_id,
        name=str(meta.get("name") or capability_id).strip(),
        version=version,
        description=_need("description"),
        capabilities=list(capabilities),
        tools=tools,
        body=body.strip(),
    )


def manifest_to_json(manifest: SkillManifest) -> str:
    return json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
