"""路径安全工具：把**外部输入**变成安全的文件名成分，并断言落点不越界。

存在理由（见 docs/INSIGHT-SPEC.md §4.2）：洞察的对象 code 由用户输入，此前只做 ``strip()``
就拼进文件名——实测 ``../../PWNED`` 会把产物写到 job 目录**之外**，``C:/Temp/x`` 会写到 ``C:\\Temp\\``。

两条规则：

1. :func:`safe_path_component` —— 任何外部字符串进入路径成分前必须过这里。
   正常平台编码（``capa_plan__c`` / ``training_mgr__c`` / ``v1.2``）**原样通过**；
   含中文也原样保留（NTFS 合法且可读）；只有危险成分被替换或剥离。
2. :func:`resolve_within` —— 拼接后断言仍在目标根内，作为纵深防御的兜底。
"""

from __future__ import annotations

import re
from pathlib import Path

# Windows 非法文件名字符 + ASCII 控制字符
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
# 零宽 / 双向文本控制符（可用来伪装扩展名，如 U+202E）
_FORMAT_CONTROL = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
# Windows 保留设备名（不区分大小写，含带扩展名形式）
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_path_component(raw: object, *, fallback: str = "item", max_len: int = 80) -> str:
    """把任意外部输入转为**单个**安全的路径成分（不含分隔符、不是绝对路径、不是保留名）。

    正常编码保持恒等；不安全成分被替换为 ``_``；纯点/空串回落到 ``fallback``。
    """
    text = _FORMAT_CONTROL.sub("", str(raw if raw is not None else ""))
    text = _UNSAFE.sub("_", text)
    # 去掉首尾空白与点：杜绝 "." / ".." / "..." 以及 Windows 会静默剥掉的尾点尾空格
    text = _strip_dots_and_space(text)
    text = text[:max_len]
    text = _strip_dots_and_space(text)  # 截断后可能又露出尾点
    if not text:
        text = fallback
    # 保留名撞名时加前缀（"CON" / "NUL" / "COM1" … 在 Windows 上无法作为文件名）
    if text.split(".")[0].upper() in _RESERVED_NAMES:
        text = f"_{text}"
    return text


def _strip_dots_and_space(text: str) -> str:
    return text.strip().strip(".").strip()


def resolve_within(base: Path, *parts: str) -> Path:
    """在 ``base`` 下解析 ``parts``，并断言结果**仍在 base 内**；越界抛 ``ValueError``。

    这是纵深防御：即使调用方漏用了 :func:`safe_path_component`，越界也会被拦下。
    """
    root = Path(base).resolve()
    target = root.joinpath(*parts).resolve()
    if target != root and not target.is_relative_to(root):
        raise ValueError(f"路径越界：{target} 不在 {root} 内")
    return target
