"""**页面身份探针**：回答一个此前无法回答的问题——「你在哪个窗口操作，我在听哪个窗口」。

## 为什么必须单独有这一个工具（2026-09-22，用户当面指出）

> 「现在都分不清我在哪个页面窗口操作的，该问题不解决，后续会产生大问题。」

他是对的，而且这**不是粗心，是接口层就没有这个信息**：

| 我以为能用来判定的东西 | 实测事实 |
|---|---|
| `/api/browser/sessions` 的 `title` | 只有 `page.title()`。平台**所有页面**的标题都是 `Akso eGMP (配置)` ⇒ **两个窗口标题相同，分不出谁是谁** |
| 接口里的 URL / 窗口 id / target id | **一个都没有**（实测响应里只有 account_id/status/title/detail/started_at/restored/monitoring/monitor_log） |
| `autologin.userTouched` | 只在自动登录 `phase: running` 期间挂监听；`idle` 时是**陈旧值**，不能当"用户碰没碰过这个页面" |
| 靠 `filterStats` 事后发现 | 只能证伪（`total=0` ⇒ 被监听页面**连一个 GET 都没有**），**指不出你在哪** |

而监听只有一个挂点，这一点有代码级证据：

- `browser_pool.py::_do_open_cdp`（:387-401）——壳先开一个专用窗口，里面是标记页
  `/static/blank.html?w=acc-<账号>`；池子连上 CDP 后**靠 marker 精确定位那一个 page**，
  再 `goto` 到平台（:416）⇒ `entry.page` = **那一个 Electron 窗口**。
- 全仓 `context.on("page")` **零命中** ⇒ **新开的窗口/标签页永远不进监听**。
- `desktop/main.js` 未设 `setWindowOpenHandler` ⇒ Electron 默认：平台的 `window.open`
  **会真的开出一个新窗口**，它有独立 CDP target，而监听**看不见它**。

⇒ 所以"你在哪个窗口"必须靠**壳 + CDP 两侧对账**来回答，而这个工具就是把那次对账固化下来。

## 它做什么（全部只读）

1. 壳身份：控制服务 `/whoami`（只读端点，不在 `CHANGING_PATHS` 里）+ CDP `/json/version`；
2. **页面清单**：CDP `/json/list` 的每个 page 目标（id / title / url），并把非本机地址的标为
   **外部页面**（平台页就是这一类；工作台 UI 是 `127.0.0.1:18765`）；
3. 池子与监听：`/api/browser/sessions` + `/api/monitor/status/<账号>` + 运行期
   `filter-stats.json` / `checkpoints.jsonl`；
4. **判定**：外部页面 0 / 1 / N 三种情形分别给结论——**1 个才是可判定的绑定**。

## 用法

    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\where.py
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\where.py --save     # 存基线
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\where.py --diff     # 与基线对比（证明"新窗口出现了"）

**退出码**：`0` 绑定明确（外部页面恰好 1 个且与会话标题一致）；`2` 需要人裁定（0 个或多个）；
`1` 连不上工作台/壳。

> 不抢鼠标：本工具**只发 GET**（`_get_json` 走 `urlopen`），既不调"抬窗/聚焦"那个端点
> （会抢焦点、把窗口抬到你面前），也不碰壳侧的建窗/抬窗/关窗写接口。
> 这条承诺由自检 M3 机械守着。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "where"

DEFAULT_SERVER = os.environ.get("WORKBENCH_SERVER", "http://127.0.0.1:18765")
DEFAULT_CONTROL = os.environ.get("WORKBENCH_CONTROL", "http://127.0.0.1:18767")
DEFAULT_CDP = os.environ.get("WORKBENCH_CDP", "http://127.0.0.1:18766")

_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _get_json(url: str, timeout: float = 5.0) -> tuple[int, object]:
    """只读 GET。失败不抛栈：返回 `(0, {"error": ...})`，由调用方决定怎么报。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:  # noqa: S310 —— 只打本机回环
            return res.status, json.loads(res.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode("utf-8", "replace")[:200]}
    except Exception as exc:  # noqa: BLE001 —— 没起服务是最常见的情况
        return 0, {"error": str(exc)}


def _is_external(url: str) -> bool:
    """非本机地址 = 外部页面（平台页就在这一类；工作台 UI 是 127.0.0.1:18765）。"""
    after_scheme = url.split("://", 1)[-1]
    host = after_scheme.split("/", 1)[0].split("@")[-1].split(":")[0]
    return host not in _LOCAL_HOSTS


def _read_monitor_dir(log_path: str | None) -> dict:
    """从 `monitor_log` 路径反推运行期目录，读出已落盘的过滤统计与检查点。"""
    out: dict = {}
    if not log_path:
        return out
    p = Path(log_path)
    d = p.parent
    out["dir"] = str(d)
    fs = d / "filter-stats.json"
    if fs.exists():
        try:
            out["filter_stats"] = json.loads(fs.read_text(encoding="utf-8"))
        except ValueError:
            out["filter_stats"] = {"error": "unreadable"}
    cp = d / "checkpoints.jsonl"
    if cp.exists():
        lines = [ln for ln in cp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        out["checkpoints"] = len(lines)
        try:
            out["last_checkpoint"] = json.loads(lines[-1]) if lines else None
        except ValueError:
            out["last_checkpoint"] = None
    out["log_exists"] = p.exists()
    return out


def collect() -> dict:
    """采一份快照（纯只读）。返回的结构同时用于打印、存盘与 `--diff`。"""
    snap: dict = {"at": int(time.time() * 1000), "shell": {}, "cdp": {}, "sessions": []}

    code, who = _get_json(f"{DEFAULT_CONTROL}/whoami")
    if code == 200 and isinstance(who, dict):
        snap["shell"] = {
            "pid": who.get("pid"), "version": who.get("version"),
            "controlPort": who.get("controlPort"), "serverPort": who.get("serverPort"),
            "cdpPort": who.get("cdpPort"),
        }

    cdp_base = f"http://127.0.0.1:{snap['shell'].get('cdpPort') or 18766}"
    code, ver = _get_json(f"{cdp_base}/json/version")
    snap["cdp"]["reachable"] = code == 200 and isinstance(ver, dict)
    if snap["cdp"]["reachable"]:
        snap["cdp"]["browser"] = ver.get("Browser")
        snap["cdp"]["ua"] = ver.get("User-Agent")
    code, targets = _get_json(f"{cdp_base}/json/list")
    pages = []
    if code == 200 and isinstance(targets, list):
        for t in targets:
            if t.get("type") != "page":
                continue
            url = str(t.get("url") or "")
            # ⚠️ target id 是 **32 位连续 hex**，与 `redact.py:114` 的 `[0-9a-fA-F]{32,}`
            # 判据**形状上无法区分**。落盘一条就会让 `scan_secrets.py` 变红
            # （2026-09-22 实测：本工具的 `--save` 第一次跑就被扫中 2 处）。
            # 处置是**在采集时就截短**（12 位足以在个位数页面里唯一），
            # 而不是把 data 目录加进 ALLOWLIST —— 那等于让这块以后不再被扫。
            pages.append({
                "id": str(t.get("id") or "")[:12], "title": t.get("title"), "url": url,
                "external": _is_external(url),
            })
    snap["cdp"]["pages"] = pages

    code, payload = _get_json(f"{DEFAULT_SERVER}/api/browser/sessions")
    rows = []
    if code == 200:
        rows = payload.get("sessions") if isinstance(payload, dict) else payload
        rows = rows if isinstance(rows, list) else []
    for row in rows:
        acct = row.get("account_id") or row.get("id")
        item = {
            "account_id": acct, "status": row.get("status"),
            "monitoring": bool(row.get("monitoring")), "title": row.get("title"),
            "detail": row.get("detail"), "restored": row.get("restored"),
            "monitor_log": row.get("monitor_log"),
        }
        item.update(_read_monitor_dir(row.get("monitor_log")))
        if acct:
            st_code, st = _get_json(f"{DEFAULT_SERVER}/api/monitor/status/{acct}")
            item["monitor_status_http"] = st_code
            if st_code == 200 and isinstance(st, dict):
                item["monitoring_now"] = bool(st.get("monitoring"))
        snap["sessions"].append(item)
    snap["server_reachable"] = code == 200
    return snap


def diff_snapshots(prev: dict, cur: dict) -> dict:
    """纯函数：两份快照的差异（供 `--diff` 与自检使用）。

    按 **target id** 对齐；URL 变化单独报（这是"这个窗口被导航过"的唯一客观痕迹）。
    """
    def index(snap: dict) -> dict:
        return {p.get("id"): p for p in (snap.get("cdp", {}).get("pages") or [])}

    a, b = index(prev), index(cur)
    added = [b[k] for k in b if k not in a]
    removed = [a[k] for k in a if k not in b]
    changed = []
    for k in a.keys() & b.keys():
        if a[k].get("url") != b[k].get("url") or a[k].get("title") != b[k].get("title"):
            changed.append({"id": k, "was": a[k], "now": b[k]})
    return {
        "count_was": len(a), "count_now": len(b),
        "added": added, "removed": removed, "changed": changed,
    }


def _print_snapshot(snap: dict) -> None:
    shell = snap.get("shell") or {}
    print("== ① 壳与浏览器 ==")
    if shell:
        print(f"  壳 pid={shell.get('pid')} v{shell.get('version')} "
              f"｜ 服务 {shell.get('serverPort')} ｜ 控制 {shell.get('controlPort')} "
              f"｜ CDP {shell.get('cdpPort')}")
    else:
        print("  ✗ 控制服务 /whoami 取不到（壳没起？）——下面只能看 CDP 与产品接口")
    cdp = snap.get("cdp") or {}
    print(f"  CDP 可达：{'是' if cdp.get('reachable') else '否'}"
          + (f"（{cdp.get('browser')}）" if cdp.get("browser") else ""))

    print("\n== ② 内置浏览器里的页面（CDP /json/list）==")
    pages = cdp.get("pages") or []
    if not pages:
        print("  （一个 page 都没有）")
    for i, p in enumerate(pages, 1):
        mark = "外部" if p.get("external") else "本机"
        print(f"  [{i}] {mark}  id={str(p.get('id'))[:12]}  title={p.get('title')}")
        print(f"        url={p.get('url')}")

    print("\n== ③ 池子与监听（产品接口）==")
    if not snap.get("server_reachable"):
        print("  ✗ /api/browser/sessions 取不到（工作台没起？）")
    for s in snap.get("sessions") or []:
        print(f"  账号 {s.get('account_id')}  status={s.get('status')} "
              f"监听中={'是' if s.get('monitoring') else '否'}")
        print(f"     会话标题：{s.get('title')}")
        print(f"     说明：{s.get('detail')}（restored={s.get('restored')}）")
        if s.get("filter_stats") is not None:
            print(f"     上次落盘过滤统计：{s['filter_stats']}")
        if s.get("checkpoints") is not None:
            print(f"     检查点 {s['checkpoints']} 条，最后一条：{s.get('last_checkpoint')}")
        if s.get("log_exists") is False:
            print("     （日志还没落盘：监听进行中，未停过）")


def verdict(snap: dict) -> tuple[int, list[str]]:
    """判定"我听的是哪一个窗口"，以及这个判定是否成立。**返回 (退出码, 行列表)**。"""
    out: list[str] = []
    pages = [p for p in (snap.get("cdp", {}).get("pages") or []) if p.get("external")]
    sessions = snap.get("sessions") or []
    bound_title = next((s.get("title") for s in sessions if s.get("monitoring")), None)
    if bound_title is None:
        bound_title = next((s.get("title") for s in sessions), None)

    out.append("== ④ 判定 ==")
    if not pages:
        out.append("  ✗ 内置浏览器里**没有任何外部页面** ⇒ 池子的那个窗口不在这个浏览器里"
                   "（或已被关闭）。此时**你在哪操作都录不到**。")
        return 2, out

    if len(pages) == 1:
        p = pages[0]
        out.append("  ✅ 外部页面恰好 1 个 ⇒ **绑定可判定**：")
        out.append(f"     我听的就是它：id={str(p.get('id'))[:12]}")
        out.append(f"     title={p.get('title')}")
        out.append(f"     url={p.get('url')}")
        if bound_title and p.get("title") and bound_title != p.get("title"):
            out.append(f"  ⚠️ 但它与会话标题不一致（会话：{bound_title}）"
                       "⇒ 池子的 page 可能已被导航/替换，需要人裁定。")
            return 2, out
        out.append("     ⇒ 只要你在**这个内置浏览器窗口**里操作平台，我听的就是它。")
        return 0, out

    out.append(f"  ⚠️ 内置浏览器里有 **{len(pages)} 个外部页面** ⇒ **无法判定你在哪一个**。")
    out.append("     这两个窗口在接口层完全同构（title 相同、都没有 url 字段），"
               "所以只能由你来指认或关掉多余的：")
    for i, p in enumerate(pages, 1):
        out.append(f"       ({i}) id={str(p.get('id'))[:12]}  {p.get('title')}")
        out.append(f"           {p.get('url')}")
    out.append("     ★ 注意：**新窗口不在监听里**（全仓无 `context.on(\"page\")`；壳没设 "
               "`setWindowOpenHandler` ⇒ 平台的 `window.open` 会真开一个新窗口）。")
    return 2, out


def _print_diff(prev: dict, cur: dict) -> None:
    d = diff_snapshots(prev, cur)
    print("\n== ⑤ 与基线对比 ==")
    print(f"  页面数：{d['count_was']} → {d['count_now']}")
    for p in d["added"]:
        print(f"  ＋ 新页面 id={str(p.get('id'))[:12]}  {p.get('title')}\n      {p.get('url')}")
    for p in d["removed"]:
        print(f"  － 消失  id={str(p.get('id'))[:12]}  {p.get('title')}\n      {p.get('url')}")
    for c in d["changed"]:
        print(f"  ~ URL/标题变了 id={str(c['id'])[:12]}")
        print(f"      旧：{c['was'].get('url')}")
        print(f"      新：{c['now'].get('url')}")
    if not (d["added"] or d["removed"] or d["changed"]):
        print("  （无变化：没有新开/关闭窗口，被监听页面的 URL 也没动过）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="页面身份探针（只读）")
    ap.add_argument("--save", action="store_true",
                    help="把本次快照存为基线（旧的挪到 previous.json）")
    ap.add_argument("--diff", action="store_true", help="与上次基线对比")
    ap.add_argument("--json", action="store_true", help="额外打印原始快照 JSON")
    args = ap.parse_args(argv)

    snap = collect()
    _print_snapshot(snap)
    code, lines = verdict(snap)
    print()
    for line in lines:
        print(line)

    if args.diff:
        prev_file = DATA / "previous.json"
        if prev_file.exists():
            try:
                _print_diff(json.loads(prev_file.read_text(encoding="utf-8")), snap)
            except ValueError:
                print("\n== ⑤ 与基线对比 ==\n  （基线文件读不了，忽略）")
        else:
            print("\n== ⑤ 与基线对比 ==\n  （还没有基线：先跑一次 `--save`）")

    if args.save:
        DATA.mkdir(parents=True, exist_ok=True)
        latest, prev = DATA / "latest.json", DATA / "previous.json"
        if latest.exists():
            prev.write_text(latest.read_text(encoding="utf-8"), encoding="utf-8")
        latest.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已存基线：{latest}")

    if args.json:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
