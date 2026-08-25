# pyPortProxy 一键构建脚本。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build.ps1 [-OneDir]
# @author ai-lhg
param(
    [string]$OutputDir = "dist",
    [switch]$OneDir
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 0. 把临时目录固定到工作区内
#    （受限环境下系统 Temp 可能不可写，pip/PyInstaller 均依赖 TMP/TEMP）
$buildTmp = Join-Path $projectRoot ".tmp\build"
New-Item -ItemType Directory -Path $buildTmp -Force | Out-Null
$env:TMP = $buildTmp
$env:TEMP = $buildTmp
$env:TMPDIR = $buildTmp

# 1. 确保 PyInstaller 可用（唯一开发依赖；已安装则本步秒过）
# 注意：不要用 "2>$null" 探测——PS5.1 在 ErrorActionPreference=Stop 下会把
# 外部命令的重定向 stderr 当作终止性错误，导致脚本被误杀。
Write-Host "[build] Ensuring build dependencies (pyinstaller)..."
python -m pip install -r requirements-dev.txt --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 2. 生成图标（幂等：已存在则跳过）
if (-not (Test-Path "assets\icon.ico")) {
    python scripts\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "make_icon failed" }
}

# 3. 打包（onefile 默认；-OneDir 输出目录版）
if ($OneDir) { $env:PORTPROXY_ONEDIR = "1" }
Write-Host "[build] Running PyInstaller..."
python -m PyInstaller --noconfirm --clean portproxy.spec --distpath $OutputDir --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 4. 校验产物并输出信息
$exe = if ($OneDir) { Join-Path $OutputDir "pyPortProxy\pyPortProxy.exe" } else { Join-Path $OutputDir "pyPortProxy.exe" }
if (-not (Test-Path $exe)) { throw "Build output not found: $exe" }

$sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
$sha256 = (Get-FileHash -Algorithm SHA256 $exe).Hash
Write-Host ""
Write-Host "[build] Success: $exe"
Write-Host ("[build] Size  : {0} MB" -f $sizeMB)
Write-Host ("[build] SHA256: {0}" -f $sha256)
