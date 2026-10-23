"""静态资源完整性：没有"写了但永不加载"的 CSS，也没有指不到的本地资源。

## 为什么

`cloud.css` 曾是一整个 21 KB / 1,077 行的孤儿文件：没有任何 HTML 加载它，
但构建仍会把它打进静态资源。后果不是报错——是"改样式看不到变化，改错文件"。
这条判据把"孤儿"变成红。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "workbench" / "static"
HTML = sorted(STATIC.rglob("*.html"))
CSS = sorted(STATIC.rglob("*.css"))


def _html_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in HTML)


def _css_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in CSS)


def test_every_css_file_is_loaded_by_some_page() -> None:
    html = _html_text()
    css_imports = _css_text()
    orphans = [
        path.relative_to(STATIC).as_posix()
        for path in CSS
        if path.name not in html and f"/static/css/{path.name}" not in html
        and path.name not in css_imports
    ]
    assert not orphans, f"这些 CSS 没有任何页面加载（孤儿文件）：{orphans}"


def test_local_assets_referenced_by_html_exist() -> None:
    missing: list[str] = []
    for page in HTML:
        text = page.read_text(encoding="utf-8")
        for ref in re.findall(r'(?:href|src)="(/static/[^"?#]+)', text):
            target = STATIC / ref.removeprefix("/static/")
            if not target.exists():
                missing.append(f"{page.name} → {ref}")
    assert not missing, f"HTML 指向了不存在的静态资源：{missing}"


def test_no_duplicate_script_per_page() -> None:
    duplicates: list[str] = []
    for page in HTML:
        text = page.read_text(encoding="utf-8")
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', text)
        seen: set[str] = set()
        for src in scripts:
            if src in seen:
                duplicates.append(f"{page.name} → {src}")
            seen.add(src)
    assert not duplicates, f"同一页面重复加载脚本：{duplicates}"
