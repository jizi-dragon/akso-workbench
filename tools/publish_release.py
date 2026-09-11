"""发布安装包到 GitHub Releases（electron-updater 的更新通道）。

为什么自己走 REST API 而不直接用 `electron-builder --publish always`：
- 那样要重新跑一遍 NSIS 打包（几分钟），而我们已经有构建好的产物；
- 发布内容需要**可核对**：release notes 用仓库 CHANGELOG、资产清单固定为
  `setup.exe + .blockmap + latest.yml`（少任何一个，自动更新都会静默失效：
  没有 latest.yml 就不知道有新版本，没有 blockmap 就退化成全量下载）。

流程：创建 draft release → 上传资产 → 校验 latest.yml 可匿名读取 → 转正发布。
draft 期间资产不对外可见，全部成功才转正——避免用户看到"有版本但没文件"的半成品。

⚠ 更新器是**匿名**读取 Release 的：仓库必须公开，私有仓库的自动更新会静默失效。

用法（token 建议只在当前 shell 注入，用完即弃）：
    $env:GH_TOKEN = "github_pat_..."
    .venv\\Scripts\\python.exe tools\\publish_release.py --notes-from-changelog
    .venv\\Scripts\\python.exe tools\\publish_release.py --version 0.3.4 --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "desktop" / "dist"
CHANGELOG = ROOT / "CHANGELOG.md"
REPO = "jizi-dragon/akso-mogul"
API = "https://api.github.com"


def project_version() -> str:
    import re

    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("pyproject.toml 中未找到 version")
    return m.group(1)


def token() -> str:
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not tok:
        raise SystemExit(
            "未设置 GH_TOKEN。获取路径见 docs/EXTENSION-INSTALL.md 第四节：\n"
            "  GitHub 头像 → Settings → Developer settings → Personal access tokens →\n"
            "  fine-grained（Contents: Read and write，选 jizi-dragon/akso-mogul）或 classic（勾 repo）"
        )
    return tok


def api(path: str, method: str = "GET", body: dict | None = None, tok: str | None = None):
    """GitHub REST 调用。

    ⚠ 只对**读取**重试：本机网络走代理且不稳定（实测 `SSL: UNEXPECTED_EOF_WHILE_READING`、
    连接被重置），读操作重试安全；而 POST/PATCH 重试可能建出重复 draft 或重复资产，
    宁可让用户看到失败再手动重跑（本脚本对同 tag 的 draft 是幂等的）。
    """
    data = json.dumps(body).encode() if body is not None else None
    opener = build_opener(proxy_of(bool(os.environ.get("AKSO_NO_PROXY"))))

    def attempt() -> bytes:
        req = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "akso-publish",
                "Content-Type": "application/json",
            },
        )
        with opener.open(req, timeout=120) as resp:
            return resp.read()

    read_only = method == "GET"
    last: Exception | None = None
    for i in range(4 if read_only else 1):
        try:
            raw = attempt()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 403 and "not accessible" in detail:
                raise SystemExit(
                    "✗ 403：这个 token 能读仓库，但没有**写**权限。\n"
                    "  fine-grained token 的 Contents 权限必须显式设为 Read and write：\n"
                    "  https://github.com/settings/tokens?type=beta → 点开该 token →\n"
                    "  Repository permissions → Contents → Read and write → Save。\n"
                    "  （改权限**不需要**重新生成 token，旧 token 立即生效）\n"
                    f"  GitHub 原文：{detail}"
                ) from exc
            if exc.code == 404:
                raise SystemExit(
                    f"✗ 404：{REPO} 不可见或 token 未授权该仓库。\n"
                    "  fine-grained token 需要在 Repository access 里显式选中该仓库；\n"
                    "  更新器另有硬约束：**仓库必须公开**。\n"
                    f"  GitHub 原文：{detail}"
                ) from exc
            raise SystemExit(f"✗ GitHub API {method} {path} 失败：HTTP {exc.code} {detail}") from exc
        except Exception as exc:  # noqa: BLE001 —— 网络抖动（SSL EOF/连接重置）
            last = exc
            if read_only and i < 3:
                time.sleep(2 * (i + 1))
                continue
            raise SystemExit(f"✗ GitHub API {method} {path} 网络失败：{type(exc).__name__}: {exc}") from exc
    raise SystemExit(f"✗ GitHub API {method} {path} 网络失败：{last}")


def proxy_of(force_direct: bool = False) -> str | None:
    """上传大文件走哪条通道：代理 或 直连。

    取用顺序：显式 --no-proxy（强制直连）→ 环境变量 → git 的 http.proxy → 直连。
    ⚠ git 的 http.proxy 是**独立的一份配置**：用户关掉 Clash 后它仍然写着 127.0.0.1:7890，
      于是脚本会对着一个没人监听的端口连（WinError 10061）。所以必须提供强制直连开关，
      否则「我关了代理」这句话在脚本里根本不生效。
    """
    if force_direct:
        return None
    for key in ("AKSO_HTTPS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if os.environ.get(key):
            return os.environ[key]
    try:
        import subprocess

        out = subprocess.run(
            ["git", "config", "--get", "http.proxy"],
            capture_output=True, text=True, timeout=10, cwd=str(ROOT),
        )
        val = (out.stdout or "").strip()
        if val:
            return val
    except Exception:  # noqa: BLE001 —— 没有 git/超时都不影响直连
        pass
    return None


def build_opener(proxy: str | None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))  # 显式直连，忽略系统代理设置
    return urllib.request.build_opener(*handlers)


def upload_asset(release_id: int, upload_url: str, path: Path, tok: str, proxy: str | None, retries: int = 3) -> dict:
    """上传资产；失败重试（先删掉可能已存在的半截资产，避免 422 already_exists）。

    为什么不用 GitHub 的断点续传：资产上传接口不支持分片续传。大文件（350MB）在本机网络上
    会偶发中途重置，所以做法是「失败 → 删残留 → 整体重传」，并在每次重试前报告进度。
    """
    url = upload_url.split("{")[0] + f"?name={urllib.parse.quote(path.name)}"
    opener = build_opener(proxy)
    size_mb = round(path.stat().st_size / (1024 * 1024), 1)
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=path.read_bytes(),
                method="POST",
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "akso-publish",
                    "Content-Type": "application/octet-stream",
                },
            )
            with opener.open(req, timeout=3600) as resp:
                return json.loads(resp.read() or b"{}")
        except Exception as exc:  # noqa: BLE001 —— 网络类异常都值得重试
            last = exc
            print(f"  上传 {path.name}（{size_mb} MB）第 {attempt} 次失败：{type(exc).__name__}: {exc}")
            if attempt == retries:
                break
            # 清掉这次可能已经建出来的半截资产，否则重传会撞 already_exists
            try:
                for asset in api(f"/repos/{REPO}/releases/{release_id}/assets", tok=tok):
                    if asset.get("name") == path.name:
                        api(f"/repos/{REPO}/releases/assets/{asset['id']}", method="DELETE", tok=tok)
                        print(f"    已清理半截资产 {path.name}，准备重传")
            except SystemExit:
                pass
            time.sleep(3 * attempt)
    raise SystemExit(f"✗ 上传 {path.name} 连续 {retries} 次失败：{last}")


def sha512_b64(path: Path) -> str:
    import base64

    h = hashlib.sha512()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


def notes_from_changelog(version: str = "") -> str:
    """取 CHANGELOG 的 [Unreleased] 段作为发布说明。

    两条纪律：
    - 按**条目**截断（顶层 `- ` 起算），不按字符硬切——半句话的发布说明比没有更糟；
    - 给定 `version` 时，只保留**属于该版本**的条目：本仓库的写法是在条目里标 `（0.3.3，…）`，
      故从头收集、遇到第一个含"更低版本号"的条目就停 —— 否则首次发布会把历史版本的
      修复说明也贴进 0.3.3 的 Release 里（用户读到的是"这一版改了什么"）。
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == "## [Unreleased]")
    except StopIteration:
        return ""

    blocks: list[list[str]] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("- ") or line.startswith("### "):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
        # 段首空行/说明行丢弃

    out: list[str] = []
    total = 0
    for block in blocks:
        head = block[0]
        chunk = "\n".join(block).rstrip()
        # 条目里出现"更低的 (0.x.y"标记 = 上一版的内容，到此为止（标题行除外）
        if version and head.startswith("- "):
            import re

            marks = re.findall(r"（(\d+\.\d+\.\d+)", head)
            if marks and all(_older(m, version) for m in marks):
                break
        if total + len(chunk) > 5500:
            break
        out.append(chunk)
        total += len(chunk)
    return "\n".join(out)


def _older(a: str, b: str) -> bool:
    """a 是否比 b 旧（语义化版本三段比较）。"""
    pa = [int(x) for x in a.split(".")[:3]] + [0, 0, 0]
    pb = [int(x) for x in b.split(".")[:3]] + [0, 0, 0]
    return pa[:3] < pb[:3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=None, help="默认取 pyproject.toml 的版本")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不调用 API")
    ap.add_argument("--notes", default="", help="发布说明（默认取 CHANGELOG 的 Unreleased 段）")
    ap.add_argument("--notes-from-changelog", action="store_true")
    ap.add_argument("--no-proxy", action="store_true", help="强制直连（关掉本机代理后必须加，否则会去连 git 里配的旧代理）")
    ap.add_argument("--prerelease", action="store_true")
    args = ap.parse_args()
    if args.no_proxy:
        os.environ["AKSO_NO_PROXY"] = "1"

    version = args.version or project_version()
    tag = f"v{version}"
    installer = DIST / f"AksoWorkbench-{version}-setup.exe"
    blockmap = DIST / f"AksoWorkbench-{version}-setup.exe.blockmap"
    latest = DIST / "latest.yml"

    # 资产必须齐全：latest.yml 少了 → 更新器查不到新版本；blockmap 少了 → 增量更新退化为全量
    missing = [p.name for p in (installer, blockmap, latest) if not p.exists()]
    if missing:
        print(f"✗ 缺少构建产物：{missing}\n  先跑 tools\\build.ps1 -Bump none（或对应版本）")
        return 2

    # latest.yml 自检：版本号必须等于本次发布版本，sha512 必须与安装包一致
    yml = latest.read_text(encoding="utf-8")
    local_sha = sha512_b64(installer)
    checks = {
        "latest.yml 版本号 == 发布版本": f"version: {version}" in yml,
        "latest.yml sha512 == 安装包实际 sha512": local_sha in yml,
        "latest.yml 指向本次安装包": f"AksoWorkbench-{version}-setup.exe" in yml,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not all(checks.values()):
        print("✗ latest.yml 与产物不一致，拒绝发布（发布错的清单会让所有客户端更新失败）")
        return 2

    size_mb = round(installer.stat().st_size / (1024 * 1024), 1)
    notes = args.notes or (notes_from_changelog(version) if args.notes_from_changelog else "")
    print(f"\n将发布 {tag}：{installer.name}（{size_mb} MB）+ {blockmap.name} + latest.yml")
    print(f"说明长度：{len(notes)} 字符")
    if args.dry_run:
        print("\n[dry-run] 未调用 GitHub API")
        return 0

    tok = token()
    proxy = proxy_of(args.no_proxy)
    print(f"通道：{'代理 ' + proxy if proxy else '直连'}")
    who = api("/user", tok=tok)
    print(f"以 {who.get('login')} 身份发布到 {REPO}")

    # 幂等：同 tag 的 draft 先删掉重来（re-run 不残留半成品）
    for rel in api(f"/repos/{REPO}/releases", tok=tok):
        if rel.get("tag_name") == tag:
            if rel.get("draft"):
                api(f"/repos/{REPO}/releases/{rel['id']}", method="DELETE", tok=tok)
                print(f"已删除同 tag 的旧 draft：{tag}")
            else:
                print(f"✗ {tag} 已正式发布过：请先升版本号，或手动删除该 Release 后重试")
                return 1

    rel = api(
        f"/repos/{REPO}/releases",
        method="POST",
        body={
            "tag_name": tag,
            "target_commitish": "main",
            "name": f"Akso Workbench {version}",
            "body": notes or f"Akso Workbench {version}",
            "draft": True,
            "prerelease": args.prerelease,
        },
        tok=tok,
    )
    print(f"已创建 draft：{rel['html_url']}")

    # 顺序有意为之：先传小的（latest.yml / blockmap，秒级），最后传 350MB 的安装包 ——
    # 大文件最容易失败，先确认小文件通道畅通，再把重试预算花在真正的大头上。
    for path in (latest, blockmap, installer):
        asset = upload_asset(int(rel["id"]), rel["upload_url"], path, tok, proxy)
        print(f"  上传 {path.name} → {asset.get('state')}（{asset.get('size')} 字节）")

    # 转正前校验：latest.yml 必须能被匿名读到（更新器的读取方式）
    pub = api(
        f"/repos/{REPO}/releases/{rel['id']}",
        method="PATCH",
        body={"draft": False, "make_latest": "true"},
        tok=tok,
    )
    print(f"\n✔ 已发布：{pub['html_url']}")

    try:
        with build_opener(proxy).open(
            f"https://github.com/{REPO}/releases/latest/download/latest.yml", timeout=60
        ) as resp:
            anon = resp.read().decode("utf-8")
        ok_anon = f"version: {version}" in anon
        print(f"{'PASS' if ok_anon else 'FAIL'}  匿名下载 latest.yml（更新器的读取路径）")
        return 0 if ok_anon else 1
    except urllib.error.HTTPError as exc:
        print(f"FAIL  匿名下载 latest.yml：HTTP {exc.code}（仓库是否私有？）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
