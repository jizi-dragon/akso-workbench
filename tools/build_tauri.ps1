# Tauri 构建 + Python sidecar + 内部更新清单（Phase 1）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File tools\build_tauri.ps1
#   powershell -ExecutionPolicy Bypass -File tools\build_tauri.ps1 -UpdateBaseUrl https://updates.example.com/workbench/0.4.1
#
# 前置：
#   - `uv sync --extra dev --extra build`
#   - `TAURI_SIGNING_PRIVATE_KEY` 指向私钥（或默认 ~/.tauri/workbench.key）
#   - 若私钥有密码：设置 TAURI_SIGNING_PRIVATE_KEY_PASSWORD
#   - 内部 HTTPS 静态源已就绪（latest.json 由本脚本生成，上传由发布流程负责）

param(
    [ValidateSet('none', 'push', 'build')][string]$Bump = 'none',
    [string]$UpdateBaseUrl = '',
    [string]$Notes = '',
    [switch]$SkipSidecar
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:Path = (Join-Path $root '.venv\Scripts') + ';' + $env:Path

function Fail([string]$message) {
    Write-Host "✗ $message" -ForegroundColor Red
    exit 1
}

if ($Bump -eq 'none') {
    $version = (& python tools\bump.py show).Trim()
} else {
    $version = (& python tools\bump.py $Bump).Trim()
    if ($LASTEXITCODE -ne 0) { Fail "bump.py $Bump 失败" }
}
if ($version -notmatch '^\d+\.\d+\.\d+$') { Fail "版本号非法：[$version]" }
if ($LASTEXITCODE -ne 0) { Fail 'bump.py show 失败' }

Write-Host "[1/4] 版本自检 v$version"
& python tools\bump.py check | Out-Null
if ($LASTEXITCODE -ne 0) { Fail 'VERSION 与派生副本不同步' }

Write-Host '[2/4] 打包 Python sidecar（PyInstaller onedir）'
if (-not $SkipSidecar) {
    & python -m PyInstaller workbench\server.spec --noconfirm | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'PyInstaller 失败' }
    $sidecar = Join-Path $root 'dist\WorkbenchServer\WorkbenchServer.exe'
    if (-not (Test-Path $sidecar)) { Fail "sidecar 不存在：$sidecar" }
} else {
    Write-Host '  （-SkipSidecar：复用 dist\WorkbenchServer）'
}

$key = $env:TAURI_SIGNING_PRIVATE_KEY
if (-not $key) {
    $key = Join-Path $env:USERPROFILE '.tauri\workbench.key'
    $env:TAURI_SIGNING_PRIVATE_KEY = $key
}
if (-not (Test-Path $key)) {
    Fail "找不到 Tauri updater 私钥：$key（先生成并离线备份，绝不能提交）"
}

Write-Host '[3/4] Tauri build（NSIS per-user + updater 签名）'
& npx --yes '@tauri-apps/cli@2' build
if ($LASTEXITCODE -ne 0) { Fail 'tauri build 失败' }

$bundleDir = Join-Path $root 'src-tauri\target\release\bundle\nsis'
$artifact = Join-Path $bundleDir "Workbench_${version}_x64-setup.exe"
if (-not (Test-Path $artifact)) { Fail "找不到安装包：$artifact" }
if (-not (Test-Path "$artifact.sig")) { Fail "找不到更新签名：$artifact.sig" }

Write-Host '[4/4] 生成内部更新清单 latest.json'
if ($UpdateBaseUrl) {
    $manifest = Join-Path $bundleDir 'latest.json'
    & python tools\tauri_update_manifest.py `
        --version $version `
        --artifact $artifact `
        --base-url $UpdateBaseUrl `
        --notes $Notes `
        --output $manifest
    if ($LASTEXITCODE -ne 0) { Fail 'latest.json 生成失败' }
    Write-Host "  $manifest"
} else {
    Write-Host '  未提供 -UpdateBaseUrl，跳过 latest.json（上传前再用工具生成即可）'
}

Write-Host ""
Write-Host "✔ Tauri 产物：$artifact" -ForegroundColor Green
Write-Host "  更新签名：$artifact.sig"
