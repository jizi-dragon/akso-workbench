"""跑一份蓝图（**直连产品编排层**，不经过 HTTP 路由）。

## 为什么要有这个工具（而不是直接 POST `/api/factory/run`）

路由那条路走的是**已在跑的 sidecar 进程**里的代码；改了 `workbench/**` 之后，
那个进程还捧着**旧模块**——于是"我明明改了，跑起来还是旧行为"（PITFALLS #20/#25 的家族）。
本工具**新起一个进程**直连 `egmp.orchestrate.run_full_workflow`，用的就是磁盘上的当前代码，
不需要为了验证一次而重启整个工作台（重启会关掉用户的平台窗口、打断采集）。

代价（说清楚）：**不写 `blueprint_jobs` 表、UI 上不会出现这次运行**——它是验证工具，
不是产品的执行入口。要留痕就把 `--artifact` 目录一起提交/引用。

## 用法

    # 只校验、不写平台
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\run_blueprint.py ^
        --blueprint factory-tuning\\data\\blueprints\\xxx.json --account <账号id> --dry-run

    # 真的执行（**会对平台写入**）
    .\\.venv\\Scripts\\python.exe factory-tuning\\tools\\run_blueprint.py ^
        --blueprint factory-tuning\\data\\blueprints\\xxx.json --account <账号id>

**退出码**：0 全步成功；4 有步骤失败（明细打印在下面）；1 用法/校验错。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNS_DIR = ROOT / "factory-tuning" / "data" / "factory-runs"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跑一份蓝图（直连编排层）")
    ap.add_argument("--blueprint", required=True, help="蓝图 JSON 文件")
    ap.add_argument("--account", required=True, help="账号 id（凭证与环境来源）")
    ap.add_argument("--dry-run", action="store_true", help="只做两层校验，不碰平台")
    args = ap.parse_args(argv)

    from workbench.services.egmp import orchestrate
    from workbench.services.egmp.client import egmp_client_for_account
    from workbench.services.egmp.writers import blueprint as bp

    raw = json.loads(pathlib.Path(args.blueprint).read_text(encoding="utf-8"))
    issues = bp.validate_blueprint(raw)
    blocking = bp.has_blocking(issues)
    print(f"== 两层校验：{len(issues)} 条问题，阻断={blocking} ==")
    for issue in issues:
        print(f"   [{issue.level}] {issue.path}: {issue.message}")
    if blocking:
        print("✗ 校验未通过，未执行")
        return 1
    if args.dry_run:
        print("（--dry-run：未执行、未写平台）")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    artifact = RUNS_DIR / stamp
    artifact.mkdir(parents=True, exist_ok=True)
    print(f"\n== 执行（产物与 checkpoint 落 {artifact.relative_to(ROOT)}）==")

    with egmp_client_for_account(args.account) as client:
        result = orchestrate.run_full_workflow(
            client, raw, approved=True, confirmed_env=True, artifact_dir=artifact,
            on_log=lambda line: print(f"   {line}"),
        )

    for obj in result.get("objects", []):
        print(f"\n对象 {obj.get('code')}：")
        for step, outcome in (obj.get("steps") or {}).items():
            status = outcome.get("status") or ("success" if outcome.get("success") else "failed")
            print(f"   {step:<12} [{status}] {str(outcome.get('message') or '')[:120]}")
    # 顶层 message 必须打出来：**"预检查拒绝写入"这类结论只在这里**（例如"对象不存在但存在相近对象"）。
    # 不打它，使用者只会看到一个 `success=False`，而那正是本工具最该说清的场景。
    if result.get("message"):
        print(f"\n结论：{result['message']}")
    for item in result.get("needsObjectChoice") or []:
        print(f"   ⚠️ 找不到对象 {item['code']}；候选：" +
              "、".join(f"{c['code']}（{c['name']}）" for c in item.get("candidates") or []))
    print(f"\nsuccess={result.get('success')}  precheck={result.get('precheck') if result.get('success') else '（未进入执行）'}")
    (artifact / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    return 0 if result.get("success") else 4


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
