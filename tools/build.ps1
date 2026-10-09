# Workbench 一键构建 v4（Electron 壳）
#
# 用法：powershell -ExecutionPolicy Bypass -File tools\build.ps1 [-Bump push|build|none] [-SkipPush]
# 产物：desktop\dist\Workbench-<ver>-setup.exe（NSIS；内含 WorkbenchServer sidecar + chromium）
#
# ## 版本号怎么管（**先读这段，2026-09-18 重排**）
#
# 真源是 `tools/bump.py`（唯一真源 = pyproject.toml 的 version），本脚本**只是它的一个调用方**。
# 只想升版本号、不出安装包时，根本不需要本脚本：
#     python tools\bump.py push     # PATCH+1（日常小改动/修复）→ 提交推送即可
#     python tools\bump.py build    # MINOR+1 且 PATCH=1（大版本）
#
# `-Bump` 语义：
#   none（**默认**）= 沿用当前版本，只出安装包。**默认不升版**是刻意的：
#                    构建的失败方式很多（网络/依赖/浏览器内核/打包），
#                    若默认升版，每次失败都会白白吃掉一个版本号且无从察觉
#                    ——2026-09-17 实测：两次失败烧掉 0.4.0 与 0.5.0，版本号从 0.3.12 直接跳到 0.6.1。
#   push           = PATCH+1，补丁发布
#   build          = MINOR+1 且 PATCH=1，正式发布
# 构建失败后**用同一条命令重跑**即可（默认 none 幂等，不会再吃号）。
#
# ## 链路（bump 刻意排在第 3 步：前两步失败不该消耗版本号）
#   1. uv sync（依赖，最常因网络失败）
#   2. 打包静态体检（verify_packaging.py：缺 chromium / 缺 BOM / 插件漏打，都在这里拦）
#   3. 版本演进（-Bump）+ 五写同步自查
#   4. 探测出网通道（直连 / 系统代理）→ release commit + push + 打 tag
#   5. PyInstaller 打包服务端 sidecar（workbench/server.spec → dist/WorkbenchServer）
#   6. 打包后复检（bump 改了版本文件，再确认一次产物与版本号一致）
#   7. electron-builder（NSIS 安装包；extraResources 带 sidecar；updater 产物 latest.yml）
#
# 失败原则：**任何一步失败都要中止构建**，不做"打一行告警然后继续"——
# 那会产出"标签没入库 / 版本号是错误文本 / 包里没有浏览器"的安装包，
# 而它们在构建输出里都长得像成功（2026-09-17 逐条实测确认）。
#
# 更新发布（可选）：设 GH_TOKEN 后改用 --publish always，或手动上传 desktop\dist\*.exe
# 与 latest.yml 到 GitHub Releases（electron-updater 按 latest.yml 检查更新）。
# GH_TOKEN 的完整获取路径（fine-grained vs classic、需要哪些权限、只显示一次的复制时机）
# 见 GitHub Docs: Personal access tokens（`publish_release.py` 的错误提示里有同一路径）。
# ⚠ 更新器匿名读取 Release ⇒ 仓库必须公开，私有仓库的自动更新静默失效。

param(
    [switch]$SkipPush,
    # 版本演进方式。**默认 none**（沿用当前版本）——理由见上方「版本号怎么管」。
    # 想升版必须显式写出来，这是刻意的：版本号是稀缺的、不可回收的资产。
    [ValidateSet('push', 'build', 'none')][string]$Bump = 'none'
)

# 注意：不要用 $ErrorActionPreference="Stop"——PS5.1 会把 git/npm 写到 stderr 的
# 正常进度当作终止错误。关键步骤一律显式检查 $LASTEXITCODE。
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:Path = (Join-Path $root ".venv\Scripts") + ";" + $env:Path
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"

# 1) 依赖（**必须在 release commit 之前**：uv sync 会把项目版本写进 uv.lock，
#    顺序反了则 commit 里的 lock 立刻过期、工作区再次变脏——0.3.2 构建实测踩到）
#    ⚠ 排在 bump **之前**：这是最常失败的一步（网络/PyPI TLS），失败时不该消耗版本号。
Write-Host "[1/7] uv sync…"
# 行的存在理由（2026-09-17 实测）：`uv` 在本机有**两种互斥形态**，而 `python -m uv` 只认其中一种：
#   - exe 形态：`.venv\Scripts\uv.exe`（本仓一直用它跑 lint，见 CONTRIBUTING §1.1）；
#   - pip 包形态：`site-packages\uv`，此时 `python -m uv` 可用。
# 旧实现在 36/39 行用 `python -m uv`、在文档里用 `uv.exe`，于是**重建过一次 venv 之后**
# 包形态消失 → 构建以一个看不出原因的方式失败。两种都认，谁也拦不住。
$uvExe = Join-Path $root ".venv\Scripts\uv.exe"
if (Test-Path $uvExe) {
    & $uvExe sync --extra dev --extra build 2>&1 | Out-Null
} else {
    & python -m uv sync --extra dev --extra build 2>&1 | Out-Null
}
if ($LASTEXITCODE -ne 0) { Write-Host "✗ uv sync 失败（版本号未消耗，修好后原样重跑即可）" -ForegroundColor Red; exit 1 }

# 2) 打包静态体检（**必须在 bump 与打包之前**）
#    `verify_packaging.py` 此前只在 CI 里跑，本地发版可以完全绕过它——
#    而它守的正是"打包态才会暴露"的那一类（见 P8：缺 chromium 时会打出没有浏览器的包）。
#    放在 bump 之前有两个作用：① 体检红的时候工作区还没被改过、安装包还没生成，返工最便宜；
#    ② **失败不消耗版本号**（P8b「本机 chromium 已装」是本机最容易缺的前置条件）。
#
#    ⚠ `WORKBENCH_REQUIRE_CHROMIUM=1` 是**构建机的声明**：它让 P8b（本机真有浏览器）参与判定。
#    CI 不设它——runner 上本来就没有浏览器、也不负责打包，那条判据在 CI 里**永远修不好**，
#    只会把整条门禁变成噪声（2026-09-18 首次 CI 红就是这个原因）。
Write-Host "[2/7] 打包静态体检（verify_packaging.py）…"
$env:WORKBENCH_REQUIRE_CHROMIUM = "1"
& python tools\verify_packaging.py 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ 打包静态体检未通过——跑 python tools\verify_packaging.py 看逐项结果" -ForegroundColor Red
    Write-Host "  （版本号未消耗，修好后原样重跑即可）" -ForegroundColor DarkGray
    exit 1
}

# 3) 版本演进（push：PATCH+1；build：MINOR+1 且 PATCH=1；none：沿用当前版本）
if ($Bump -eq 'none') {
    $version = (& python tools\bump.py show).Trim()
    Write-Host "[3/7] 沿用当前版本 → v$version（-Bump none，默认）"
} else {
    $version = (& python tools\bump.py $Bump).Trim()
    Write-Host "[3/7] 版本演进（$Bump）→ v$version"
}
# **必须校验**：旧实现不查 $LASTEXITCODE 也不查格式，bump.py 失败时 stdout 里的
# 错误文本会被当成版本号，一路流到产物命名与 latest.yml（实测形态：
# `Workbench-<错误文本>-setup.exe`，而构建脚本仍报"完成"）。
if ($LASTEXITCODE -ne 0) { Write-Host "✗ 版本演进失败（bump.py $Bump，exit=$LASTEXITCODE）" -ForegroundColor Red; exit 1 }
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    Write-Host "✗ 版本号格式非法：[$version]（bump.py 的 stdout 只应有版本号本身）" -ForegroundColor Red
    exit 1
}
# 四写同步自查：pyproject / __init__ / desktop pkg / .version.json
& python tools\bump.py check 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ 版本四写不同步——跑 python tools\bump.py check 看逐文件明细" -ForegroundColor Red
    exit 1
}

# 3) 版本号入库并推送（release commit：含 uv.lock）
#    + 打 tag（tag 是"这一版对应哪个 commit"的唯一锚点，见 docs/VERSIONING.md §3.2）
#
# ## 出网通道：探测，而不是写死（2026-09-17 定稿）
# 旧实现在这里写死 `git -c http.proxy= push`（**强制直连**）。本机的现实是
# "**哪个通道通随时刻变化**"（PITFALLS #9）：有时系统代理 `127.0.0.1:7890` 是唯一活路，
# 有时代理不稳、直连反而通。写死任一侧都会在另一侧**必然三次全败**，
# 而旧实现在全败时只打一行告警就继续 → 静默发出版本号没入库、没有 tag 的安装包。
#
# 所以这里两件事一起做对：
#   ① **先探测再选路**：直连与系统代理各试一次（各 ~15s 上限），用实测能通的通道；
#   ② **推不上去就中止**：要让构建输出与仓库状态一致，而不是"东西出来了但版本没入库"。
function Get-PushProbeArgs([string]$proxyUrl) {
    $a = @('-c', 'http.proxy=', '-c', 'https.proxy=')
    if ($proxyUrl) { $a = @('-c', "http.proxy=$proxyUrl", '-c', "https.proxy=$proxyUrl") }
    return ($a + @('ls-remote', '--exit-code', 'origin', 'HEAD'))
}

function Test-GitChannel([string]$proxyUrl) {
    $out = & git @(Get-PushProbeArgs $proxyUrl) 2>&1
    if ($LASTEXITCODE -eq 0) { return $true }
    # stderr 落到 $out：把失败原因露出来（旧实现 2>$null 吞掉后无从判断）
    foreach ($line in $out) {
        if ("$line" -match 'proxy|timed out|Could not resolve|Failed to connect|refused') {
            Write-Host "    （$line）" -ForegroundColor DarkGray
            break
        }
    }
    return $false
}

$pushArgs = @('-c', 'http.proxy=', '-c', 'https.proxy=')
$channel = ''
$proxies = @()
$reg = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
if ($reg -and $reg.ProxyEnable -eq 1 -and $reg.ProxyServer) {
    $raw = ($reg.ProxyServer -split ';')[0]
    if ($raw -notmatch '://') { $raw = "http://$raw" }
    $proxies += $raw
}
# 常见本机代理端口兜底：注册表没开代理，但代理软件在听时也能用上
$proxies += @('http://127.0.0.1:7890', 'http://127.0.0.1:7897', 'http://127.0.0.1:10809')
$proxies = $proxies | Select-Object -Unique

if (-not $SkipPush) {
    Write-Host "[4/7] 探测出网通道…"
    if (Test-GitChannel '') {
        $channel = '直连'
        Write-Host "    ✓ 直连可用" -ForegroundColor Green
    } else {
        foreach ($p in $proxies) {
            $pPort = 0
            try { $pPort = ([uri]$p).Port } catch { $pPort = 0 }
            if ($pPort -gt 0) {
                $listening = Get-NetTCPConnection -LocalPort $pPort -State Listen -ErrorAction SilentlyContinue
                if (-not $listening) { continue }   # 端口没在听：不必为它耗一轮超时
            }
            Write-Host "    直连不通，试代理 $p …"
            if (Test-GitChannel $p) {
                $channel = "代理 $p"
                $pushArgs = @('-c', "http.proxy=$p", '-c', "https.proxy=$p")
                Write-Host "    ✓ $p 可用" -ForegroundColor Green
                break
            }
        }
    }
    if (-not $channel) {
        Write-Host "✗ 直连与已知代理均不通（各试一次 ls-remote）——发布中止，避免产生'没入库的安装包'" -ForegroundColor Red
        Write-Host "  处置：确认代理状态后重跑：powershell -File tools\build.ps1 -Bump none" -ForegroundColor Yellow
        exit 1
    }

    git add pyproject.toml workbench\__init__.py desktop\package.json .version.json uv.lock
    # ⚠ commit 的 stderr **不再吞掉**：旧实现的 `2>$null` 会把"git 身份没配"这类
    # 硬错误伪装成"无改动可提交"，于是后面静默跳过打 tag。
    $commitOut = & git commit -m "chore(release): v$version" 2>&1
    $committed = ($LASTEXITCODE -eq 0)
    $nothingToCommit = ($commitOut | Out-String) -match 'nothing to commit|无文件要提交|working tree clean'
    if (-not $committed -and -not $nothingToCommit) {
        Write-Host "✗ release commit 失败（不是'无改动'）：" -ForegroundColor Red
        $commitOut | ForEach-Object { Write-Host "    $_" }
        exit 1
    }

    $pushed = $false
    foreach ($i in 1..3) {
        & git @pushArgs push 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Start-Sleep -Seconds 4
    }
    if (-not $pushed) {
        Write-Host "✗ 推送失败（通道=$channel，已重试 3 次）——发布中止" -ForegroundColor Red
        Write-Host "  安装包尚未生成，仓库状态未被留在半途；处置后重跑即可。" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "[4/7] 版本号推送: OK（$channel）"

    # 打 tag 并单独推送。四条纪律：
    #   ① **必须在 commit 推送成功之后**——否则 tag 指向一个远端还不存在的 commit，推送必被拒；
    #   ② 只在 `-Bump build|push` 时打：`-Bump none` 是"沿用当前版本只重出安装包"，
    #      此时版本号早已发过，再打会撞上已存在的 tag（属于噪音）；
    #   ③ 先查 tag 是否已存在：重复构建同版本时 `git tag -a` 会报 already exists；
    #   ④ 失败只告警不改退出码：安装包已经出来了，一个 tag 推不上去不该作废整次构建。
    # 历史教训：2026-09-17 实测本仓**发布了 12 个版本却只有 3 个 tag**，
    # 导致 `git checkout v0.3.11` 直接报错、回滚与"用户跑的是哪一版"都没有锚点。
    if ($Bump -eq 'none') {
        Write-Host "[4/7] -Bump none：沿用已发布版本，不打 tag"
    } elseif (-not $committed) {
        Write-Host "[4/7] 无版本改动（commit 未产生），不打 tag"
    } elseif (git rev-parse -q --verify "refs/tags/v$version" 2>$null) {
        Write-Host "[4/7] tag v$version 已存在，跳过"
    } else {
        git tag -a "v$version" -m "Akso Workbench $version"
        & git @pushArgs push origin "v$version" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[4/7] tag v$version 已推送"
        } else {
            Write-Host "[4/7] ⚠ tag 推送失败（$channel）——稍后手动：git push origin v$version" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[4/7] 跳过推送与打 tag（-SkipPush）"
}

# 6) 服务端 sidecar（PyInstaller onedir，含 chromium）
Write-Host "[5/7] WorkbenchServer sidecar 打包中（数分钟）…"
& python -m PyInstaller workbench\server.spec --noconfirm --distpath dist --workpath build 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "✗ WorkbenchServer 打包失败" -ForegroundColor Red; exit 1 }

# 7) 打包后复检（**为什么不省**：第 2 步的体检是在 bump **之前**跑的，
#    而 bump 改了 4 个版本文件。
#    这一步确认"改动之后仍然自洽"——尤其 P9（四写同步），
#    它正是 bump 影响的那一项，也是发出去错版本号的那条路。
#    秒级成本，换"安装包与版本号一致"这条断言，值得。）
Write-Host "[6/7] 打包后复检（版本一致性）…"
& python tools\verify_packaging.py 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ 打包后复检未通过——产物与版本号可能不一致，**先别发布**" -ForegroundColor Red
    Write-Host "  跑 python tools\verify_packaging.py 看逐项结果" -ForegroundColor DarkGray
    exit 1
}

# 8) Electron 壳（NSIS 安装包）
Write-Host "[7/7] electron-builder 打包中…"
Push-Location desktop
try {
    & npx electron-builder --win nsis --publish never 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "✗ electron-builder 失败" -ForegroundColor Red; exit 1 }
} finally {
    Pop-Location
}

$installer = Join-Path $root "desktop\dist\Workbench-$version-setup.exe"
if (Test-Path $installer) {
    $size = [math]::Round((Get-Item $installer).Length / 1MB, 1)
    Write-Host ""
    Write-Host "✔ 构建完成：$installer（$size MB）" -ForegroundColor Green
    $latest = Join-Path $root "desktop\dist\latest.yml"
    if (Test-Path $latest) { Write-Host "  更新清单：$latest（发布 release 时一并上传）" }
} else {
    Write-Host "✗ 未找到安装包产物" -ForegroundColor Red
    exit 1
}
