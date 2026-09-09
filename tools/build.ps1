# Akso Workbench 一键构建（版本规则 A + PyInstaller + Inno Setup）
# 用法：powershell -ExecutionPolicy Bypass -File tools\build.ps1
# 产物：dist\installer\AksoWorkbench-<ver>-setup.exe
#
# 步骤：
#   1. 版本演进（build：MINOR+1，PATCH 重置 1）
#   2. 提交并推送版本号（release commit）
#   3. uv sync --extra build（确保 PyInstaller）
#   4. PyInstaller 打包（onedir）
#   5. Inno Setup 生成安装包
#
# 可选参数：-SkipPush   （只构建，不提交/推送版本号）

param(
    [switch]$SkipPush
)

# 注意：不要用 $ErrorActionPreference="Stop"——PS5.1 会把 git/uv 写到 stderr 的
# 正常进度当作终止错误。关键步骤一律显式检查 $LASTEXITCODE。
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:Path = (Join-Path $root ".venv\Scripts") + ";" + $env:Path

# 0) Inno Setup 检查
$iscc = @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe", "C:\Program Files\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "✗ 未找到 Inno Setup 6（ISCC.exe）。" -ForegroundColor Red
    Write-Host "  安装：https://jrsoftware.org/isdl.php 或 winget install JRSoftware.InnoSetup"
    exit 1
}
Write-Host "[0/5] Inno Setup: $iscc"

# 1) 版本演进（build 规则：MINOR+1，PATCH=1）
$version = (& python tools\bump.py build).Trim()
Write-Host "[1/5] 版本演进 → v$version"

# 2) 版本号入库并推送（release commit）
if (-not $SkipPush) {
    git add pyproject.toml workbench\__init__.py
    git commit -m "chore(release): v$version" 2>$null | Out-Null
    $pushed = $false
    foreach ($i in 1..3) {
        git -c http.proxy= push 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Start-Sleep -Seconds 4
    }
    Write-Host ("[2/5] 版本号推送: " + $(if ($pushed) { "OK" } else { "失败（网络）——稍后手动 git push" }))
} else {
    Write-Host "[2/5] 跳过推送（-SkipPush）"
}

# 3) 依赖（PyInstaller 在 build extra）
uv sync --extra dev --extra desktop --extra build 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "✗ uv sync 失败" -ForegroundColor Red; exit 1 }
Write-Host "[3/5] 依赖就绪（PyInstaller）"

# 4) PyInstaller 打包
Write-Host "[4/5] PyInstaller 打包中（数分钟）…"
& python -m PyInstaller shell\shell.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { Write-Host "✗ PyInstaller 失败" -ForegroundColor Red; exit 1 }

# 5) Inno Setup 安装包
Write-Host "[5/5] Inno Setup 生成安装包…"
& $iscc "/DAppVersion=$version" (Join-Path $root "tools\installer.iss") | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "✗ Inno Setup 失败" -ForegroundColor Red; exit 1 }

$installer = Join-Path $root "dist\installer\AksoWorkbench-$version-setup.exe"
if (Test-Path $installer) {
    $size = [math]::Round((Get-Item $installer).Length / 1MB, 1)
    Write-Host ""
    Write-Host "✔ 构建完成：$installer（$size MB）" -ForegroundColor Green
} else {
    Write-Host "✗ 未找到安装包产物" -ForegroundColor Red
    exit 1
}
