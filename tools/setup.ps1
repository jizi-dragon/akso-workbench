# 新同事环境自检脚本（双人协作 · 问题 1 的落地件）
# 用法：powershell -ExecutionPolicy Bypass -File tools\setup.ps1
# 兼容 Windows PowerShell 5.1 与 PowerShell 7；只读检查，绝不触碰四个原项目。

$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$script:fail = 0

function Check($name, $ok, $detail, [switch]$Info) {
    $mark = "OK  "
    if (-not $ok) { $mark = "MISS"; if (-not $Info) { $script:fail++ } }
    if ($Info) { $mark = "INFO" }
    Write-Host ("[{0}] {1,-30} {2}" -f $mark, $name, $detail)
}

Write-Host "=== Akso Workbench 环境自检 ===" -ForegroundColor Cyan

# 解析解释器：优先项目 .venv（uv sync 产物），未激活 venv 也不会误报
$pyExe = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$venvReady = Test-Path $pyExe
if (-not $venvReady) { $pyExe = "python" }
Check "项目 .venv" $venvReady $(if ($venvReady) { ".venv\Scripts\python.exe" } else { "缺失 → 先执行 uv sync --extra dev" })

# 1) Python 版本（>=3.12）
$pyver = & $pyExe -c "import sys; print('%d.%d' % (sys.version_info.major, sys.version_info.minor))" 2>$null
$pyOk = $false
if ($pyver) {
    $parts = $pyver.Split('.')
    $pyOk = ([int]$parts[0] -gt 3) -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 12)
}
$pyDetail = "未找到 python"
if ($pyver) { $pyDetail = $pyver }
Check "Python >= 3.12" $pyOk $pyDetail

# 2) uv + lockfile（uv 可能在 PATH / .venv / 独立安装目录）
$uvCmd = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $uvCmd = "uv"
} else {
    $venvUv = Join-Path $PSScriptRoot "..\.venv\Scripts\uv.exe"
    $localUv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $venvUv) { $uvCmd = $venvUv }
    elseif (Test-Path $localUv) { $uvCmd = $localUv }
}
$uvAvailable = ($null -ne $uvCmd)
$uvDetail = "pip install uv 或 winget install astral-sh.uv"
if ($uvAvailable) { $uvDetail = (& $uvCmd --version 2>$null) -join "" }
Check "uv 可用（uv sync 用）" $uvAvailable $uvDetail
$lockPath = Join-Path $PSScriptRoot "..\uv.lock"
Check "uv.lock 存在" (Test-Path $lockPath) "依赖锁定文件（随仓库分发，勿手改）"

# 3) 核心依赖
foreach ($mod in @("fastapi", "uvicorn", "httpx", "pydantic", "cryptography", "playwright")) {
    & $pyExe -c "import $mod" 2>$null
    $ok = ($LASTEXITCODE -eq 0)
    $detail = "ok"
    if (-not $ok) { $detail = "缺依赖 → uv sync --extra dev" }
    Check "python:$mod" $ok $detail
}

# 4) Playwright chromium（托管浏览器内核，单行 -c 保证 5.1 兼容）
$pwCheck = (& $pyExe -c "from playwright.sync_api import sync_playwright; from pathlib import Path; p = sync_playwright().start(); print('1' if Path(p.chromium.executable_path).exists() else '0'); p.stop()" 2>$null) -join ""
$chromiumOk = ($LASTEXITCODE -eq 0 -and "$pwCheck".Trim() -eq "1")
$chromiumDetail = "ok"
if (-not $chromiumOk) { $chromiumDetail = "执行: uv run playwright install chromium" }
Check "Playwright chromium" $chromiumOk $chromiumDetail

# 5) Node（**仅构建桌面壳 / 浏览器扩展时需要**；运行时能力已全部原生化，见 docs/adr/0005）
#    —— 信息项，不计入失败（历史口径「洞察/工厂功能不可用」已过时）
$nodeCmd = "node"
if ($env:NODE_COMMAND) { $nodeCmd = $env:NODE_COMMAND }
$nodePath = Get-Command $nodeCmd -ErrorAction SilentlyContinue
$nodeVer = $null
if ($nodePath) { $nodeVer = (& $nodeCmd --version 2>$null) -join "" }
$nodeDetail = "未找到；仅影响 desktop/ 与 extensions/ 的构建（运行时不需要）"
if ($nodeVer) { $nodeDetail = "$nodeVer（仅构建桌面壳/扩展时需要）" }
Check "Node 20+（仅构建需要）" ($null -ne $nodeVer -and $nodeVer -ne "") $nodeDetail -Info

# 6) 原项目只读引用（adapters 声明，只检查不写入）
$adaptersDir = Join-Path $PSScriptRoot "..\adapters"
$adapters = Get-ChildItem $adaptersDir -Filter "*.json" -ErrorAction SilentlyContinue
foreach ($file in $adapters) {
    try {
        $json = Get-Content $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $repo = $json.repoPath
        if ($json.repoPathEnv) {
            $envVal = [Environment]::GetEnvironmentVariable($json.repoPathEnv)
            if ($envVal) { $repo = $envVal }
        }
        $exists = ($repo -and (Test-Path $repo))
        $entryOk = $false
        if ($exists) { $entryOk = (Test-Path (Join-Path $repo $json.entry)) }
        $detail = "$repo"
        if (-not $exists) { $detail = "原仓库不存在：$repo" }
        elseif (-not $entryOk) { $detail = "入口缺失：$($json.entry)（原仓库需构建）" }
        Check "适配器 $($json.id)" ($exists -and $entryOk) $detail
    } catch { Check "适配器 $($file.Name)" $false "JSON 解析失败" }
}

# 7) 数据目录可写
$dataDir = Join-Path $env:APPDATA "AksoWorkbench"
if ($env:WORKBENCH_DATA) { $dataDir = $env:WORKBENCH_DATA }
$dataOk = $true
try { New-Item -ItemType Directory -Force -Path $dataDir | Out-Null } catch { $dataOk = $false }
Check "数据目录可写" $dataOk $dataDir

Write-Host ""
if ($script:fail -eq 0) {
    Write-Host "✔ 环境就绪：uv run python -m workbench.main 启动" -ForegroundColor Green
    exit 0
} else {
    Write-Host "✗ 有 $script:fail 项未就绪——按上方提示修复后重跑本脚本" -ForegroundColor Yellow
    exit 1
}
