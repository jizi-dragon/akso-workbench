"""把 Monitor 的产物**脱敏后**收进 `factory-tuning/data/`。

## 输入 / 输出

    输入（产品侧，Monitor 写的）       输出（本目录，可提交）
    <RUNTIME>/monitor/<acct8>-<stamp>/   data/sessions/<假名>-<stamp>/
      ├─ monitor-log.json          →      ├─ traffic.jsonl
      ├─ checkpoints.jsonl         →      ├─ checkpoints.jsonl（原样，里边没有凭据）
      └─ filter-stats.json         →      └─ meta.json（过滤统计 + 采集元信息）

## 两个刻意的设计

**① 落盘即脱敏，磁盘上不存在原始流量。**
`redact.py` 在写第一行之前就被调用。这样"数据进仓库"与"凭据不落明文"可以同时成立
——前者要的是平台结构，后者防的是能拿去冒充你的东西，两者不冲突。

**② 主机名与账号 GUID 不进数据。**
报告只需要"打了哪个端点"，不需要"在哪个环境、用哪个账号"。账号 id 换成稳定假名
（同一账号两次采集对得上号，但反推不出是谁）。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from redact import pseudonym, redact_body, redact_url  # noqa: E402

FACTORY_TUNING = pathlib.Path(__file__).resolve().parents[1]
DATA = FACTORY_TUNING / "data" / "sessions"


def runtime_monitor_root() -> pathlib.Path:
    """Monitor 产物的根目录。

    **不自己拼路径**：那个位置的口径属于产品（`config.RUNTIME_DIR`），
    在这里重写一份就是"两个会漂的事实源"——本仓已经因为这种事栽过两次。
    只读地 import 一下配置即可。
    """
    try:
        from workbench import config  # noqa: PLC0415

        return pathlib.Path(config.RUNTIME_DIR) / "monitor"
    except Exception as exc:  # noqa: BLE001 —— 允许不经产品运行（比如从备份目录收）
        raise SystemExit(
            f"✗ 取不到产品的运行时目录（{type(exc).__name__}: {exc}）。\n"
            "  请在仓库根目录、用 venv 里的解释器跑；"
            "或用 --from <dir> 直接指定 monitor 产物目录。"
        ) from exc


def pick_session(root: pathlib.Path, *, latest: bool, name: str = "") -> pathlib.Path:
    if not root.exists():
        raise SystemExit(f"✗ 找不到 {root}——还没有录过。先跑 capture.py start / stop。")
    candidates = sorted((d for d in root.iterdir() if (d / "monitor-log.json").is_file()),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"✗ {root} 下没有含 monitor-log.json 的会话目录")
    if name:
        match = next((d for d in candidates if d.name == name), None)
        if match is None:
            raise SystemExit(f"✗ 没有名为 {name} 的会话。可选：{[d.name for d in candidates]}")
        return match
    if not latest:
        raise SystemExit("✗ 请给 --latest 或 --session <名字>。可选：" +
                         ", ".join(d.name for d in candidates))
    return candidates[0]


def clean_entry(entry: dict[str, Any], index: int) -> dict[str, Any]:
    """一条记录 → 可提交的形状（**这是脱敏的唯一入口**）。"""
    actions = []
    for action in (entry.get("precedingActions") or []):
        if not isinstance(action, dict):
            continue
        actions.append({
            "type": str(action.get("type") or ""),
            # 页面上点/填的文本：**保留**（"哪个操作触发了哪个请求"全靠它），但过一遍脱敏
            # ——用户有可能把一个令牌粘进某个输入框里，那种事不该因为"它在 DOM 里"就放行
            "text": _safe_text(action),
            "tag": str(action.get("tag") or ""),
        })
    return {
        "seq": index,
        "at": entry.get("timestamp"),
        "method": str(entry.get("method") or ""),
        "path": redact_url(str(entry.get("url") or entry.get("path") or "")),
        "status": entry.get("status"),
        "known": bool(entry.get("known")),
        "postData": redact_body(entry.get("postData")),
        "body": redact_body(entry.get("body")),
        "precedingActions": actions,
    }


def _safe_text(action: dict[str, Any]) -> str:
    from redact import redact_text  # noqa: PLC0415

    return redact_text(str(action.get("text") or ""))[:80]


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 Monitor 产物脱敏后收进 factory-tuning/data/")
    parser.add_argument("--from", dest="source", default="", help="monitor 产物根目录（默认取产品的运行时目录）")
    parser.add_argument("--latest", action="store_true", help="收最近一次会话")
    parser.add_argument("--session", default="", help="按目录名指定会话")
    parser.add_argument("--out", default="", help="输出目录（默认 data/sessions/<假名>-<原名后缀>）")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.source) if args.source else runtime_monitor_root()
    source = pick_session(root, latest=args.latest, name=args.session)

    raw = json.loads((source / "monitor-log.json").read_text(encoding="utf-8", errors="replace"))
    entries = raw.get("entries") or []
    stats = raw.get("filterStats") or {}

    # 会话目录名形如 `<account8>-<stamp>`：账号那半截换成稳定假名
    head, _, stamp = source.name.partition("-")
    session_name = f"{pseudonym(head)}-{stamp or '0'}"
    out_dir = pathlib.Path(args.out) if args.out else (DATA / session_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    cleaned = [clean_entry(e, i + 1) for i, e in enumerate(entries)]
    traffic_path = out_dir / "traffic.jsonl"
    with traffic_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in cleaned:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    checkpoints = read_jsonl(source / "checkpoints.jsonl")
    if checkpoints:
        (out_dir / "checkpoints.jsonl").write_text(
            "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in checkpoints),
            encoding="utf-8", newline="\n")

    meta = {
        "session": session_name,
        # 这里曾经有一行 `"source_dir": source.name`。删掉的理由不是"没用"，是**它泄密**：
        # 运行时目录名是 `<原始账号id>-<时间戳>`，而 `session` 是同一个东西的假名形式
        # （`pseudonym(account) + 时间戳`）。也就是说这行**没有增加任何信息量**，
        # 只是把脱敏白做的账重新用明文写了一遍——`scan_secrets.py` 会把它扫出来（实测 3 处）。
        # 教训：脱敏要脱的是"值的来源"，不是"值的每一个副本"；漏一个副本等于没脱。
        "collected_from": "workbench 运行时目录 runtime/monitor"
                          "（那个目录在仓库外、**不入库**；进仓库的是这份脱敏后的副本）",
        "entry_count": len(cleaned),
        "filter_stats": stats,
        "note": "已脱敏：无令牌/口令/会话 id，无主机名，账号为稳定假名。见 tools/redact.py",
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    # `--out` 允许指到仓库外（彩排时写临时目录）。那时 `relative_to` 会抛 ValueError，
    # 而它发生在**产物已经正确落盘之后**——于是"数据好好的，进程却带堆栈、非零码退出"，
    # 看起来像失败。这个 `--out` 参数存在的唯一理由就是写仓库外，等于它从不可用。
    # 见 docs/PITFALLS.md #61。
    try:
        shown: object = out_dir.relative_to(FACTORY_TUNING.parent)
    except ValueError:
        shown = out_dir
    print(f"✓ 已收进 {shown}")
    print(f"  {len(cleaned)} 条请求 → traffic.jsonl（脱敏后）")
    print(f"  过滤统计：{stats}")
    print(f"  原始目录仍留在 {source}（那是产品运行时目录，不在仓库里）")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
