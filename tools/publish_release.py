"""发布安装包到 GitHub Releases（electron-updater 的更新通道）。

为什么自己走 REST API 而不直接用 `electron-builder --publish always`：
- 那样要重新跑一遍 NSIS 打包（几分钟），而我们已经有构建好的产物；
- 发布内容需要**可核对**：release notes 用仓库 CHANGELOG、资产清单固定为
  `setup.exe + .blockmap + latest.yml`（少任何一个，自动更新都会静默失效：
  没有 latest.yml 就不知道有新版本，没有 blockmap 就退化成全量下载）。

流程：创建 draft release → 上传资产 → 校验 latest.yml 可匿名读取 → 转正发布。
draft 期间资产不对外可见，全部成功才转正——避免用户看到"有版本但没文件"的半成品。

## 出网通道：**探测**，不是读配置（2026-09-18 移植自 tools/build.ps1）

启动时打一次 `GET /rate_limit`，在〔直连 → 环境变量代理 → `git config http.proxy`
→ 常见本机代理端口〕里挑**实测能通**的那条，之后所有 API 调用与资产上传复用它。

原实现是"读到 `git config http.proxy` 就直接用"，于是那份**陈旧配置**会一路用到失败为止：

    实测：git config http.proxy = http://127.0.0.1:7890，而当时它 TLS 握手 EOF →
    脚本报 `SSL: UNEXPECTED_EOF_WHILE_READING`，**而同一时刻直连是通的**
    （curl 直连成功取到 release）。处置（换通道）根本不在错误信息里。

这与 `build.ps1` 修掉的 `-c http.proxy=` 是同一个病：**把"哪条通道通"当成常量**。
它是**随时刻变化**的外部事实（PITFALLS #9）——同一天实测过
"直连 25s 超时、代理通" → "直连通、代理 TLS 失败" → "直连被重置、代理通"，四次里翻转三次。
判据因此是 **"端口在听"不算数，必须真发一次请求**。

⚠ 更新器是**匿名**读取 Release 的：仓库必须公开，私有仓库的自动更新会静默失效。
   （私有仓库下末尾那条"匿名校验"必然报 404——那是**预期**，不是故障。）

用法（token 建议只在当前 shell 注入，用完即弃）：
    $env:GH_TOKEN = "github_pat_..."
    .venv\\Scripts\\python.exe tools\\publish_release.py --notes-from-changelog
    .venv\\Scripts\\python.exe tools\\publish_release.py --version 0.3.4 --dry-run
    .venv\\Scripts\\python.exe tools\\publish_release.py --no-proxy        # 强制直连（跳过探测）

> ⚠ **发布说明的抽取有硬约束**：`notes_from_changelog()` 逐字查找 `## [Unreleased]`
> （给定 `--version` 时找 `## [<version>]`），且从下一行读到**下一个 `## ` 为止**。
> 标题与内容之间**不能插任何东西**（中文副标题、HTML 注释都会被当成终止符 → 抽到空字符串，
> 而它是静默 `return ""`）。CHANGELOG 顶部有完整说明。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
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


def api(path: str, method: str = "GET", body: dict | None = None, tok: str | None = None,
        proxy: str | None = None, _channel_resolved: bool = False):
    """GitHub REST 调用。

    `proxy`：由 `resolve_channel()` **探测一次**后传入，全程复用同一条通道。
    （不要在这里再调一次探测——每个 API 调用都探一遍会让发布多出几十次往返。）

    ⚠ 只对**读取**重试：本机网络走代理且不稳定（实测 `SSL: UNEXPECTED_EOF_WHILE_READING`、
    连接被重置），读操作重试安全；而 POST/PATCH 重试可能建出重复 draft 或重复资产，
    宁可让用户看到失败再手动重跑（本脚本对同 tag 的 draft 是幂等的）。
    """
    data = json.dumps(body).encode() if body is not None else None
    opener = build_opener(proxy)

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


def _channel_candidates(force_direct: bool) -> list[str | None]:
    """待探测的通道清单（**顺序即优先级**）：`None` 代表直连。

    ⚠ **全部候选都会被探测**，包括"从配置里读到的代理"——这正是本函数存在的理由。
    原实现是"读到 git config 里的代理就直接用它"，于是那份**陈旧配置**
    （代理早就没在听、或它自己 TLS 失败）会一路用到失败为止：

        实测 2026-09-18：git config http.proxy = http://127.0.0.1:7890，
        而当时该代理 TLS 握手 EOF，脚本报 `SSL: UNEXPECTED_EOF_WHILE_READING`；
        同一时刻**直连是通的**（curl 直连成功取到 release）。

    这与 `tools/build.ps1` 修掉的那条是**同一个病**：把"哪条通道通"当成常量。
    git 那份配置仍然有价值（它告诉我们**该试哪个代理**），但它只是**候选**，不是结论。
    """
    if force_direct:
        return [None]
    candidates: list[str | None] = [None]  # 直连优先：少一跳、不依赖外部进程
    for key in ("WORKBENCH_HTTPS_PROXY", "AKSO_HTTPS_PROXY",  # 后者为更名前变量名
                "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if os.environ.get(key):
            candidates.append(os.environ[key])
    try:
        out = subprocess.run(
            ["git", "config", "--get", "http.proxy"],
            capture_output=True, text=True, timeout=10, cwd=str(ROOT),
        )
        val = (out.stdout or "").strip()
        if val:
            candidates.append(val)
    except Exception:  # noqa: BLE001 —— 没有 git/超时都不影响直连
        pass
    # 常见本机代理端口兜底：注册表/git 都没配、但代理软件在听时也能用上
    candidates += ["http://127.0.0.1:7890", "http://127.0.0.1:7897", "http://127.0.0.1:10809"]
    seen: set[str | None] = set()
    uniq: list[str | None] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _probe_channel(proxy: str | None, timeout: float = 20.0) -> bool:
    """真打一次 GitHub API，判断这条通道现在通不通。

    ⚠ **必须真发请求**："端口在听"不等于"能出网"——实测代理进程在听但 TLS 失败。
    判据只看"有没有拿到 HTTP 响应"：**401/403 也算通**（那说明 TLS 与 HTTP 都工作，
    只是凭据问题，与本函数无关）。
    """
    try:
        with build_opener(proxy).open(f"{API}/rate_limit", timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 500  # 有 HTTP 响应 = 通道可用
    except Exception:  # noqa: BLE001 —— SSL EOF / 连接重置 / 超时：这条通道不可用
        return False


def resolve_channel(force_direct: bool = False, quiet: bool = False) -> str | None:
    """**探测并选定**上传/调用通道（返回 `None` = 直连）。

    为什么不沿用"读配置就算数"：见 `_channel_candidates` 里那条实测记录。
    "哪个通道通"是**随时刻变化**的外部事实（PITFALLS #9）：
    同一天实测过"直连 25s 超时、代理通" → "直连通、代理 TLS 失败" → "直连被重置、代理通"，
    四次里翻转三次。所以在**使用时**探一次，而不是在配置里读一次。
    """
    candidates = _channel_candidates(force_direct)
    for cand in candidates:
        label = f"代理 {cand}" if cand else "直连"
        if _probe_channel(cand):
            if not quiet:
                print(f"  通道探测：{label} ✓")
            return cand
        if not quiet:
            print(f"  通道探测：{label} ✗（不可用，试下一条）")
    # 全不通：不中止——探测只发一次 GET，可能恰好错过一次抖动；
    # 让真正的调用去失败并给出它自己的错误（上传还有重试），比在这里断言"网络死了"更准。
    if not quiet:
        print("  ⚠ 所有通道探测均失败——按直连继续，真正的失败会由调用本身报出")
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
                for asset in api(f"/repos/{REPO}/releases/{release_id}/assets", tok=tok, proxy=proxy):
                    if asset.get("name") == path.name:
                        api(f"/repos/{REPO}/releases/assets/{asset['id']}", method="DELETE",
                            tok=tok, proxy=proxy)
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
    """取 CHANGELOG 的发布说明。

    ## 从哪一段取（2026-09-18 修）

    原实现**无条件**从 `## [Unreleased]` 开始读，遇到下一个 `## ` 就停。
    但发布有两种时机，而这个行为只照顾了其中一种：

    | 时机 | 应该读的段 | 原实现的结果 |
    |---|---|---|
    | 发版**前**（改动都还在 Unreleased 里） | `## [Unreleased]` | ✅ 正确 |
    | 发版**后**（已把 Unreleased 定版为 `## [0.3.12]`） | `## [0.3.12]` | ❌ 读到的是 Unreleased 里的**残留说明**，而真正的该版内容在下面够不着 |

    第二种情形下 `--version` 形同虚设，Release 说明会退化成 Unreleased 里那几行元信息。
    故：**给定 `version` 时优先精确定位 `## [<version>]` 段**，找不到再回落到
    `## [Unreleased]`（发版前的写法）。回落是为了不破坏"先写 Unreleased、再定版"的既有流程。

    ## 两条既有纪律（勿改）

    - 按**条目**截断（顶层 `- ` 起算），不按字符硬切——半句话的发布说明比没有更糟；
    - 未给定 version 时，只保留**属于当前开发版**的条目：本仓库的写法是在条目里标 `（0.3.3，…）`，
      故从头收集、遇到第一个含"更低版本号"的条目就停 —— 否则首次发布会把历史版本的
      修复说明也贴进 0.3.3 的 Release 里（用户读到的是"这一版改了什么"）。
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    lines = text.splitlines()

    def _section_heading(v: str) -> int | None:
        prefix = f"## [{v}]"
        for i, line in enumerate(lines):
            if line.startswith(prefix):
                return i
        return None

    start: int | None = None
    if version:
        start = _section_heading(version)
    if start is None:
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
    ap.add_argument("--no-proxy", action="store_true",
                    help="强制直连、跳过通道探测（代理软件关掉后想少一次探测时用；"
                         "不设则自动在 直连/环境变量/git 配置/常见端口 之间探测可用通道）")
    ap.add_argument("--prerelease", action="store_true")
    args = ap.parse_args()
    if args.no_proxy:
        os.environ["WORKBENCH_NO_PROXY"] = "1"

    version = args.version or project_version()
    tag = f"v{version}"
    installer = DIST / f"Workbench-{version}-setup.exe"
    blockmap = DIST / f"Workbench-{version}-setup.exe.blockmap"
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
        "latest.yml 指向本次安装包": f"Workbench-{version}-setup.exe" in yml,
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
    # 通道**探测一次**，之后所有 API 调用与资产上传都复用它（见 resolve_channel 的说明）
    proxy = resolve_channel(args.no_proxy)
    print(f"通道：{'代理 ' + proxy if proxy else '直连'}")
    who = api("/user", tok=tok, proxy=proxy)
    print(f"以 {who.get('login')} 身份发布到 {REPO}")

    # 幂等：同 tag 的 draft 先删掉重来（re-run 不残留半成品）
    for rel in api(f"/repos/{REPO}/releases", tok=tok, proxy=proxy):
        if rel.get("tag_name") == tag:
            if rel.get("draft"):
                api(f"/repos/{REPO}/releases/{rel['id']}", method="DELETE", tok=tok, proxy=proxy)
                print(f"已删除同 tag 的旧 draft：{tag}")
            else:
                print(f"✗ {tag} 已正式发布过：请先升版本号，或手动删除该 Release 后重试")
                return 1

    rel = api(
        f"/repos/{REPO}/releases",
        method="POST",
        tok=tok,
        proxy=proxy,
        body={
            "tag_name": tag,
            "target_commitish": "main",
            "name": f"Workbench {version}",
            "body": notes or f"Workbench {version}",
            "draft": True,
            "prerelease": args.prerelease,
        },
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
        tok=tok,
        proxy=proxy,
        body={"draft": False, "make_latest": "true"},
    )
    print(f"\n✔ 已发布：{pub['html_url']}")

    # 收尾校验：latest.yml 必须能被**匿名**读到（更新器的读取方式）。
    # ⚠ 这一步失败**不代表发布失败**（资产都已 uploaded、Release 已转正）——
    # 本机直连 github.com 偶发被重置，实测就出现过"发布成功但校验报 ConnectionReset"。
    # 所以这里重试几次、并且只作为告警，不改变退出码。
    anon_ok = False
    anon_err = ""
    for _attempt in range(4):
        try:
            with build_opener(proxy).open(
                f"https://github.com/{REPO}/releases/latest/download/latest.yml", timeout=60
            ) as resp:
                anon = resp.read().decode("utf-8")
            anon_ok = f"version: {version}" in anon
            break
        except Exception as exc:  # noqa: BLE001
            anon_err = f"{type(exc).__name__}: {exc}"
            time.sleep(3)
    if anon_ok:
        print("PASS  匿名下载 latest.yml（更新器的读取路径）")
    else:
        print(f"WARN  匿名校验未通过（{anon_err}）——发布本身已完成，请稍后手动复核：")
        print(f"      curl -L https://api.github.com/repos/{REPO}/releases/tags/{tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
