"""数据层维护：体积诊断 + 备份 + 瘦身（回收已下线功能的遗留数据）。

背景（实测）：workbench.db 曾达 82 MB，其中 74 MB 是 `doc_chunks.embedding`
—— 知识库功能下线后遗留的向量数据，运行时代码已零处读取（全仓库仅 db.py 的
迁移定义与 storage.py 的注释提到这两张表）。真实业务数据不到 20 KB。

用法（默认只读诊断，不改任何数据）：
    .venv\\Scripts\\python.exe tools\\db_maintenance.py                # 只报告
    .venv\\Scripts\\python.exe tools\\db_maintenance.py --all          # 备份+瘦身+回收
    .venv\\Scripts\\python.exe tools\\db_maintenance.py --backup       # 只做备份
    .venv\\Scripts\\python.exe tools\\db_maintenance.py --trim         # 只置空向量
    .venv\\Scripts\\python.exe tools\\db_maintenance.py --vacuum       # 只回收文件空间

纪律说明：
- 这不是迁移（migration）。迁移历史不可变，且「自动删除用户数据」的迁移风险过高，
  故瘦身必须以显式命令触发。
- `--trim` 只把 embedding 置空，保留 documents / doc_chunks 的正文（不可再生数据），
  丢弃的是可再生的向量（原 embedding 配置仍在 settings 表内）。
- `--vacuum` 需要独占写锁：请先关闭 AksoWorkbench 桌面端，否则可能 SQLITE_BUSY。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

# 表格 -> 需要单独统计体积的大列（用于定位「谁在占空间」）
FAT_COLUMNS: dict[str, tuple[str, ...]] = {
    "doc_chunks": ("embedding", "content", "chunk_hash"),
    "documents": ("content",),
    "messages": ("content",),
    "settings": ("value",),
    "account": ("password_enc",),
}

# 已下线功能遗留的 settings 键（默认只报告，不清除）
LEGACY_SETTINGS_KEYS = ("dingtalkLastSync", "embeddingApiKey", "embeddingModel", "embeddingBaseUrl")

SERVER_PORT = 18765


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("WORKBENCH_DB") or os.environ.get("MOGUL_DB")
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "AksoWorkbench" / "workbench.db"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def mb(n: float) -> str:
    return f"{n / 1024 / 1024:.2f} MB"


def tables_of(conn: sqlite3.Connection) -> list[str]:
    return [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]


def report(path: Path, conn: sqlite3.Connection) -> None:
    size = path.stat().st_size
    wal = path.with_name(path.name + "-wal")
    print(f"库文件 : {path}")
    print(f"文件   : {mb(size)}" + (f"   WAL {mb(wal.stat().st_size)}" if wal.exists() else ""))
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    print(f"页     : size={page_size} count={page_count} 空闲={free} ({mb(free * page_size)})")
    print(f"journal: {conn.execute('PRAGMA journal_mode').fetchone()[0]}")

    print("\n-- 表规模 --")
    rows: list[tuple[str, int]] = []
    for name in tables_of(conn):
        try:
            n = conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"').fetchone()["c"]
        except sqlite3.Error as exc:  # noqa: BLE001
            print(f"  {name:<22} 读取失败: {exc}")
            continue
        rows.append((name, n))
    for name, n in sorted(rows, key=lambda r: r[1], reverse=True):
        print(f"  {name:<22} rows={n}")

    print("\n-- 大列体积（定位占空间元凶）--")
    total_text = 0
    for name, cols in FAT_COLUMNS.items():
        if name not in [r[0] for r in rows]:
            continue
        for col in cols:
            try:
                got = conn.execute(
                    f'SELECT SUM(LENGTH("{col}")) AS s FROM "{name}"'
                ).fetchone()["s"] or 0
            except sqlite3.Error:
                continue
            if got:
                total_text += got
                print(f"  {name}.{col:<12} {mb(got)}")
    print(f"  合计 {mb(total_text)}（文件 {mb(size)}，其余为索引/页开销/空闲页）")

    print("\n-- 已下线功能遗留 settings 键 --")
    for key in LEGACY_SETTINGS_KEYS:
        got = conn.execute("SELECT LENGTH(value) AS n FROM settings WHERE key = ?", (key,)).fetchone()
        print(f"  {key:<20} {'存在 len=' + str(got['n']) if got else '无'}")


def exclusive_ok(conn: sqlite3.Connection) -> bool:
    """探测能否拿到独占写锁（判断桌面端是否正持有库）。"""
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("ROLLBACK")
        return True
    except sqlite3.Error:
        return False


def backup(path: Path, conn: sqlite3.Connection) -> Path | None:
    """VACUUM INTO 生成一致性快照（可并发于读，比复制文件安全）。"""
    target = path.with_name(f"{path.stem}.backup-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
    if target.exists():
        print(f"备份已存在，跳过：{target}")
        return target
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.Error as exc:  # noqa: BLE001
        print(f"备份失败：{exc}")
        return None
    print(f"备份完成：{target} ({mb(target.stat().st_size)})")
    return target


def trim(conn: sqlite3.Connection) -> int:
    """把已下线功能遗留的向量置空（保留正文）。"""
    try:
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM doc_chunks WHERE embedding IS NOT NULL"
        ).fetchone()["c"]
    except sqlite3.Error as exc:  # noqa: BLE001
        print(f"doc_chunks 不可读，跳过瘦身：{exc}")
        return 0
    if not before:
        print("doc_chunks.embedding 已全部为空，无需瘦身")
        return 0
    conn.execute("UPDATE doc_chunks SET embedding = NULL WHERE embedding IS NOT NULL")
    conn.commit()
    print(f"已置空 {before} 行 embedding（正文保留）")
    return before


def vacuum(conn: sqlite3.Connection, path: Path, force: bool) -> None:
    size_before = path.stat().st_size
    if not exclusive_ok(conn):
        print("当前无法取得独占写锁：请先关闭 AksoWorkbench 桌面端再执行 --vacuum")
        if not force:
            return
        print("--force：仍尝试执行（可能 SQLITE_BUSY）")
    try:
        conn.execute("VACUUM")
    except sqlite3.Error as exc:  # noqa: BLE001
        print(f"VACUUM 失败：{exc}")
        return
    # WAL 模式下 VACUUM 的产出先进 WAL，且主库文件在连接关闭时才截断；
    # 所以此处只报逻辑页数，最终文件体积以「维护后」段落（新连接）为准。
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    except sqlite3.Error as exc:  # noqa: BLE001
        print(f"检查点未完成（不影响数据正确性）：{exc}")
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    print(f"VACUUM 完成：{mb(size_before)} -> 逻辑 {pages} 页"
          f"（{mb(pages * conn.execute('PRAGMA page_size').fetchone()[0])}）")


def purge_legacy_settings(conn: sqlite3.Connection) -> None:
    for key in LEGACY_SETTINGS_KEYS:
        n = conn.execute("DELETE FROM settings WHERE key = ?", (key,)).rowcount
        if n:
            print(f"已清除遗留设置键：{key}")
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="数据层维护：诊断 / 备份 / 瘦身")
    parser.add_argument("--db", help="数据库路径（默认取 WORKBENCH_DB 或 %%APPDATA%%\\AksoWorkbench）")
    parser.add_argument("--report", action="store_true", help="只输出诊断（默认行为）")
    parser.add_argument("--backup", action="store_true", help="VACUUM INTO 生成一致性备份")
    parser.add_argument("--trim", action="store_true", help="置空 doc_chunks.embedding（保正文）")
    parser.add_argument("--vacuum", action="store_true", help="回收文件空间（需独占）")
    parser.add_argument("--purge-legacy-settings", action="store_true", help="清除已下线功能的 settings 键")
    parser.add_argument("--all", action="store_true", help="备份 + 瘦身 + 回收")
    parser.add_argument("--force", action="store_true", help="VACUUM 前不复检独占锁")
    args = parser.parse_args(argv)

    path = resolve_db_path(args.db)
    if not path.exists():
        print(f"数据库不存在：{path}")
        return 2

    print("=" * 68)
    print("维护前")
    print("=" * 68)
    conn = connect(path)
    try:
        report(path, conn)

        do_backup = args.backup or args.all
        do_trim = args.trim or args.all
        do_vacuum = args.vacuum or args.all
        if not (do_backup or do_trim or do_vacuum or args.purge_legacy_settings):
            print("\n（只读诊断完成；未改动任何数据。加 --all 执行备份+瘦身+回收）")
            return 0

        print("\n" + "=" * 68)
        print("执行")
        print("=" * 68)
        # 顺序不可调：先备份，再置空，最后回收
        if do_backup and backup(path, conn) is None:
            print("备份失败，已中止后续写操作（避免不可逆改动）")
            return 1
        if do_trim:
            trim(conn)
        if args.purge_legacy_settings:
            purge_legacy_settings(conn)
        if do_vacuum:
            vacuum(conn, path, args.force)
    finally:
        conn.close()  # 关闭时才完成最终检查点与主库截断

    print("\n" + "=" * 68)
    print("维护后")
    print("=" * 68)
    fresh = connect(path)
    try:
        report(path, fresh)
    finally:
        fresh.close()
    print("\n提示：旧版库 %%APPDATA%%\\MogulWorkbench\\mogul.db（约 80 MB）已不再被读取，"
          "\n      确认新版数据无误后可自行归档或删除。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
