# Akso Workbench 一键构建 v3（Electron 壳）
# 用法：powershell -ExecutionPolicy Bypass -File tools\build.ps1 [-Bump build|push|none] [-SkipPush]
# 产物：desktop\dist\AksoWorkbench-<ver>-setup.exe（NSIS；内含 AksoServer sidecar + chromium + 浏览器扩展）
#
# 链路：
#   1. 版本演进（-Bump：build=MINOR+1 / push=PATCH+1 / none=沿用当前）
#   2. 重建浏览器扩展并校验版本一致（安装包会携带它）
#   3. 提交并推送版本号（release commit；含扩展 manifest/package 与 uv.lock）
#   4. uv sync（确保 PyInstaller）
#   5. PyInstaller 打包服务端 sidecar（workbench/server.spec → dist/AksoServer）
#   6. electron-builder（NSIS 安装包；extraResources 带 sidecar 与扩展；updater 产物 latest.yml）
#
# 更新发布（可选）：设 GH_TOKEN 后改用 --publish always，或手动上传 desktop\dist\*.exe
# 与 latest.yml 到 GitHub Releases（electron-updater 按 latest.yml 检查更新）。

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

# 2) 版本号入库并推送（release commit）
if (-not $SkipPush) {
    git add pyproject.toml workbench\__init__.py desktop\package.json .version.json `
        extensions\quick-login\package.json extensions\quick-login\packages\extension\manifest.json
    git add uv.lock   # uv sync 会把项目版本写进 lock；不纳管则 lock 永远滞后一版
    git commit -m "chore(release): v$version" 2>$null | Out-Null
    $pushed = $false
    foreach ($i in 1..3) {
        git -c http.proxy= push 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Start-Sleep -Seconds 4
    }
    Write-Host ("[3/6] 版本号推送: " + $(if ($pushed) { "OK" } else { "失败（网络）——稍后手动 git push" }))
} else {
    Write-Host "[3/6] 跳过推送（-SkipPush）"
}

# 3) 依赖
& python -m uv sync --extra dev --extra build 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "✗ uv sync 失败" -ForegroundColor Red; exit 1 }
Write-Host "[4/6] 依赖就绪"

# 4) 服务端 sidecar（PyInstaller onedir，含 chromium）
Write-Host "[5/6] AksoServer sidecar 打包中（数分钟）…"
& python -m PyInstaller workbench\server.spec --noconfirm --distpath dist --workpath build 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "✗ AksoServer 打包失败" -ForegroundColor Red; exit 1 }

# 5) Electron 壳（NSIS 安装包）
Write-Host "[6/6] electron-builder 打包中…"
Push-Location desktop
try {
    & npx electron-builder --win nsis --publish never 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "✗ electron-builder 失败" -ForegroundColor Red; exit 1 }
} finally {
    Pop-Location
}

$installer = Join-Path $root "desktop\dist\AksoWorkbench-$version-setup.exe"
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
