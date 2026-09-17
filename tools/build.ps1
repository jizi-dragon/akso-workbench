# Workbench 一键构建 v3（Electron 壳）
# 用法：powershell -ExecutionPolicy Bypass -File tools\build.ps1 [-Bump build|push|none] [-SkipPush]
# 产物：desktop\dist\Workbench-<ver>-setup.exe（NSIS；内含 WorkbenchServer sidecar + chromium + 浏览器扩展）
#
# 链路：
#   1. 版本演进（-Bump：build=MINOR+1 / push=PATCH+1 / none=沿用当前）
#   2. 重建浏览器扩展并校验版本一致（安装包会携带它）
#   3. 提交并推送版本号（release commit；含扩展 manifest/package 与 uv.lock）
#   4. uv sync（确保 PyInstaller）
#   5. PyInstaller 打包服务端 sidecar（workbench/server.spec → dist/WorkbenchServer）
#   6. electron-builder（NSIS 安装包；extraResources 带 sidecar 与扩展；updater 产物 latest.yml）
#
# 更新发布（可选）：设 GH_TOKEN 后改用 --publish always，或手动上传 desktop\dist\*.exe
# 与 latest.yml 到 GitHub Releases（electron-updater 按 latest.yml 检查更新）。
# GH_TOKEN 的完整获取路径（fine-grained vs classic、需要哪些权限、只显示一次的复制时机）
# 见 docs/EXTENSION-INSTALL.md 第四节「发布更新到 GitHub Releases」。
# ⚠ 更新器匿名读取 Release ⇒ 仓库必须公开，私有仓库的自动更新静默失效。

param(
    [switch]$SkipPush,
    # 版本演进方式：build = MINOR+1 的正式发布（默认）；push = PATCH+1 的补丁发布；
    # none = 沿用当前版本号只重出安装包。此前只有 MINOR 一条路，补丁修复也会被抬成 MINOR。
    [ValidateSet('build', 'push', 'none')][string]$Bump = 'build'
)

# 注意：不要用 $ErrorActionPreference="Stop"——PS5.1 会把 git/npm 写到 stderr 的
# 正常进度当作终止错误。关键步骤一律显式检查 $LASTEXITCODE。
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:Path = (Join-Path $root ".venv\Scripts") + ";" + $env:Path
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"

# 1) 版本演进（build：MINOR+1 且 PATCH=1；push：PATCH+1；none：沿用当前版本）
if ($Bump -eq 'none') {
    $version = (& python tools\bump.py show).Trim()
    Write-Host "[1/6] 沿用当前版本 → v$version（-Bump none）"
} else {
    $version = (& python tools\bump.py $Bump).Trim()
    Write-Host "[1/6] 版本演进（$Bump）→ v$version"
}

# 1.5) 重建浏览器扩展（安装包会携带它：desktop/package.json 的 extraResources → resources/extension）
#      必须在 bump 之后——否则打进安装包的扩展 manifest 版本号会停在上一版
Write-Host "[2/6] 浏览器扩展重建中…"
Push-Location (Join-Path $root "extensions\quick-login")
try {
    & npm run build 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "✗ 扩展构建失败" -ForegroundColor Red; exit 1 }
} finally {
    Pop-Location
}
$extManifest = Join-Path $root "extensions\quick-login\dist\manifest.json"
if (-not (Test-Path $extManifest)) { Write-Host "✗ 未找到扩展构建产物" -ForegroundColor Red; exit 1 }
if (-not (Select-String -Path $extManifest -Pattern "`"version`": `"$version`"" -Quiet)) {
    Write-Host "✗ 扩展产物版本号与本次发布不一致（$extManifest）" -ForegroundColor Red; exit 1
}

# 2) 依赖（**必须在 release commit 之前**：uv sync 会把项目版本写进 uv.lock，
#    顺序反了则 commit 里的 lock 立刻过期、工作区再次变脏——0.3.2 构建实测踩到）
Write-Host "[3/6] uv sync…"
& python -m uv sync --extra dev --extra build 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "✗ uv sync 失败" -ForegroundColor Red; exit 1 }

# 3) 版本号入库并推送（release commit：含扩展 manifest/package 与 uv.lock）
#    + 打 tag（tag 是"这一版对应哪个 commit"的唯一锚点，见 docs/VERSIONING.md §3.2）
if (-not $SkipPush) {
    git add pyproject.toml workbench\__init__.py desktop\package.json .version.json uv.lock `
        extensions\quick-login\package.json extensions\quick-login\packages\extension\manifest.json
    # commit 可能因"无改动"返回非 0（例如连续两次 -Bump none 构建）：那种情况不该打 tag
    git commit -m "chore(release): v$version" 2>$null | Out-Null
    $committed = ($LASTEXITCODE -eq 0)

    $pushed = $false
    foreach ($i in 1..3) {
        git -c http.proxy= push 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Start-Sleep -Seconds 4
    }
    Write-Host ("[4/6] 版本号推送: " + $(if ($pushed) { "OK" } else { "失败（网络）——稍后手动 git push" }))

    # 打 tag 并单独推送。四条纪律：
    #   ① **必须在 commit 推送成功之后**——否则 tag 指向一个远端还不存在的 commit，推送必被拒；
    #   ② 只在 `-Bump build|push` 时打：`-Bump none` 是"沿用当前版本只重出安装包"，
    #      此时版本号早已发过，再打会撞上已存在的 tag（属于噪音）；
    #   ③ 先查 tag 是否已存在：重复构建同版本时 `git tag -a` 会报 already exists；
    #   ④ 失败只告警不改退出码：安装包已经出来了，一个 tag 推不上去不该作废整次构建。
    # 历史教训：2026-09-17 实测本仓**发布了 12 个版本却只有 3 个 tag**，
    # 导致 `git checkout v0.3.11` 直接报错、回滚与"用户跑的是哪一版"都没有锚点。
    if ($Bump -eq 'none') {
        Write-Host "[4/6] -Bump none：沿用已发布版本，不打 tag"
    } elseif (-not $committed) {
        Write-Host "[4/6] 无版本改动（commit 未产生），不打 tag"
    } elseif (-not $pushed) {
        Write-Host "[4/6] ⚠ commit 未推上去，跳过打 tag（否则 tag 指向远端不存在的 commit）" -ForegroundColor Yellow
    } elseif (git rev-parse -q --verify "refs/tags/v$version" 2>$null) {
        Write-Host "[4/6] tag v$version 已存在，跳过"
    } else {
        git tag -a "v$version" -m "Akso Workbench $version"
        git -c http.proxy= push origin "v$version" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[4/6] tag v$version 已推送"
        } else {
            Write-Host "[4/6] ⚠ tag 推送失败（网络）——稍后手动：git push origin v$version" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[4/6] 跳过推送与打 tag（-SkipPush）"
}

# 4) 服务端 sidecar（PyInstaller onedir，含 chromium）
Write-Host "[5/6] WorkbenchServer sidecar 打包中（数分钟）…"
& python -m PyInstaller workbench\server.spec --noconfirm --distpath dist --workpath build 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "✗ WorkbenchServer 打包失败" -ForegroundColor Red; exit 1 }

# 5) Electron 壳（NSIS 安装包）
Write-Host "[6/6] electron-builder 打包中…"
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
