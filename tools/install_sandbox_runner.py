"""把 DSH Windows ACL runner 安装到数据目录（可选、需要 Node/npm）。

用法：

    python tools/install_sandbox_runner.py

安装后 `win_acl_runner` 会自动发现
`<数据目录>/sandbox-runner/node_modules/.../lib/runner.js`，不需要设环境变量。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workbench import config  # noqa: E402
from workbench.sandbox import win_acl_runner  # noqa: E402

PACKAGE = "@deepseek-ai/dsh-sandbox-windows-acl@0.0.1-rc.1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", default=str(config.DATA_DIR / "sandbox-runner"),
                        help="安装目录（默认 <数据目录>/sandbox-runner）")
    args = parser.parse_args()
    target = Path(args.prefix).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print(f"安装 {PACKAGE} 到 {target}")
    proc = subprocess.run([npm, "install", "--prefix", str(target), PACKAGE,
                           "--no-audit", "--no-fund"], cwd=str(target))
    if proc.returncode != 0:
        print("npm install 失败；确认 Node/npm 在 PATH，且网络可访问 npm registry。",
              file=sys.stderr)
        return proc.returncode
    runner = target / win_acl_runner.RUNNER_REL
    if not runner.is_file():
        print(f"安装完成但找不到 runner：{runner}", file=sys.stderr)
        return 2
    print(f"OK：{runner}")
    print("Workbench 重启后自动启用；也可设置 WORKBENCH_WINDOWS_ACL_RUNNER 指向它。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
