"""真机验收：洞察产出的 .drawio 能否被 draw.io 真正打开（不只离线 XML 良构）。

离线门禁在 `tests/test_drawio.py`（断言良构 + 语义）。本工具补最后一环：
把产物交给 **draw.io 桌面版 CLI** 做一次真实转换——能被它读写，才算「标准 drawio」。

用法：
    .\\.venv\\Scripts\\python.exe tools\\verify_insight_drawio.py
    .\\.venv\\Scripts\\python.exe tools\\verify_insight_drawio.py --cli "D:\\path\\draw.io.exe"

退出码：0 = 全过；1 = 有失败项。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from workbench.services.egmp.insight import drawio  # noqa: E402

DEFAULT_CLI_CANDIDATES = [
    r"F:\Software\drawio\draw.io\draw.io.exe",
    r"C:\Program Files\draw.io\draw.io.exe",
    r"C:\Program Files (x86)\draw.io\draw.io.exe",
]

# 对抗性输入：每一条都曾或可能打破 XML（控制字符、裸 &、非法 id、换行、中文、emoji）
ADVERSARIAL_LABELS = [
    "常规节点",
    "A & B",
    'He said "hi"',
    "<script>alert(1)</script>",
    "计划\u000b审批\u0000通过\u001f",
    "多行\n标签",
    "中文（全角）＋ emoji 🎯",
    "a<br>b",
    "&amp;",
    "",
]

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" —— {detail}" if detail and not ok else ""))


def find_cli(explicit: str | None) -> str | None:
    if explicit:
        return explicit if Path(explicit).exists() else None
    for candidate in DEFAULT_CLI_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("drawio") or shutil.which("draw.io")


def build_graph(labels: list[str]) -> dict:
    ids = [f"s{i}" for i in range(len(labels))]
    return {
        "nodes": [
            {"id": ids[i], "name": labels[i], "stepType": [30, 70, 10, 20, 40][i % 5],
             "behaviors": [{"behaviorType": 9, "summary": "修改状态 → 已验证"}],
             "dispatchMode": "或签", "approver": "张三 & 李四"}
            for i in range(len(labels))
        ],
        "edges": [
            {"source": ids[i], "target": ids[i + 1], "kind": "flow", "label": ""}
            for i in range(len(labels) - 1)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", default=None, help="draw.io 可执行文件路径（缺省自动探测）")
    parser.add_argument("--keep", action="store_true", help="保留临时产物便于排查")
    args = parser.parse_args()

    cli = find_cli(args.cli)
    print(f"draw.io CLI：{cli or '（未找到）'}\n")

    work = Path(tempfile.mkdtemp(prefix="insight-drawio-"))
    print(f"临时目录：{work}\n")

    # ---- 1) 用真实 insight 代码产出 drawio ----
    graph = build_graph(ADVERSARIAL_LABELS)
    single = work / "workflow-adversarial.drawio"
    single.write_text(drawio.workflow_to_drawio(graph), encoding="utf-8")
    check("产出单页 drawio", single.exists() and single.stat().st_size > 0)

    multipage = work / "multipage.drawio"
    pages = [(f"页{i}-{label[:12]}", "".join(drawio.workflow_cells(build_graph([label, "次" + label]))))
             for i, label in enumerate(ADVERSARIAL_LABELS)]
    multipage.write_text(drawio.mxfile(pages), encoding="utf-8")
    check("产出多页 drawio（含对抗性页名）", multipage.exists())

    # ---- 2) 离线良构 + 结构断言 ----
    for path in (single, multipage):
        text = path.read_text(encoding="utf-8")
        check(f"{path.name} 离线良构", drawio.is_well_formed(text))
        check(f"{path.name} 无 XML 注释", "<!--" not in text)
    check("单页无双重转义（无 &amp;#xa;）", "&amp;#xa;" not in single.read_text(encoding="utf-8"))

    # ---- 3) 真机：交给 draw.io CLI 读一遍再写出来 ----
    if not cli:
        print("\n⚠ 未找到 draw.io 桌面版，跳过真机项（离线项已全部执行）")
    else:
        for path in (single, multipage):
            out = work / f"roundtrip-{path.name}"
            proc = subprocess.run(
                [cli, "-x", "-f", "xml", "-o", str(out), str(path)],
                capture_output=True, text=True, timeout=180,
            )
            ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            check(f"draw.io 可读并回写 {path.name}", ok, detail)
            if ok:
                check(f"{path.name} 回写结果仍良构",
                      drawio.is_well_formed(out.read_text(encoding="utf-8", errors="replace")))

    print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "；".join(FAILED))
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"产物保留在：{work}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
