#!/usr/bin/env pwsh
<#
.SYNOPSIS
    下载并解压 mpv Windows x86_64 构建包到 _mpv/ 目录
.DESCRIPTION
    从 shinchiro/mpv-winbuild-cmake 获取最新 mpv 发布版。
    需要 7z（7-Zip）解压；如果系统没有，会自动下载便携版 7zr.exe。
.LINK
    https://github.com/shinchiro/mpv-winbuild-cmake/releases
#>
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectDir = Split-Path -Parent $ScriptDir
$MpvDir = Join-Path $ProjectDir "_mpv"

# 如果 _mpv/ 已存在且有 mpv.exe，直接跳过
if ((Test-Path (Join-Path $MpvDir "mpv.exe")) -and
    (Get-Item (Join-Path $MpvDir "mpv.exe")).Length -gt 1MB) {
    Write-Host "[✓] mpv 已存在 ($MpvDir)" -ForegroundColor Green
    exit 0
}

# 1. 查最新发布版号
Write-Host "[1/4] 查询最新 mpv 发布..." -ForegroundColor Cyan
try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest" `
        -Headers @{ "User-Agent" = "pwsh/7.0"; "Accept" = "application/json" }
    $tag = $rel.tag_name
    Write-Host "  最新 tag: $tag" -ForegroundColor Gray
} catch {
    Write-Host "[!] API 查询失败: $_" -ForegroundColor Yellow
    # 回退到已知版本
    $tag = "20260903"
    $ver = "20260903-git-69e63f425a"
}

# 2. 找到 x86_64 包
$asset = $rel.assets | Where-Object { $_.name -like "mpv-x86_64-*-git-*.7z" -and $_.name -notlike "*v3*" } | Select-Object -First 1
if (-not $asset) {
    # 找不到最新就硬编码
    $assetName = "mpv-x86_64-20260903-git-69e63f425a.7z"
    $assetUrl = "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/$tag/$assetName"
} else {
    $assetName = $asset.name
    $assetUrl = $asset.browser_download_url
}

$archivePath = Join-Path $env:TEMP $assetName
Write-Host "  下载: $assetName ($([math]::Round($asset.size / 1MB, 1)) MB)" -ForegroundColor Gray

# 3. 下载
Write-Host "[2/4] 下载 mpv..." -ForegroundColor Cyan
if (-not (Test-Path $archivePath)) {
    $wc = New-Object System.Net.WebClient
    $wc.Headers.Add("User-Agent", "Mozilla/5.0")
    $wc.DownloadFile($assetUrl, $archivePath)
}
Write-Host "  ✓ $((Get-Item $archivePath).Length / 1MB -as [int]) MB 已下载" -ForegroundColor Green

# 4. 找 7z
Write-Host "[3/4] 准备解压工具..." -ForegroundColor Cyan
$7z = Get-Command "7z.exe" -ErrorAction SilentlyContinue
if (-not $7z) {
    $7zrPath = Join-Path $env:TEMP "7zr.exe"
    if (-not (Test-Path $7zrPath)) {
        Write-Host "  下载 7zr (便携版 7-Zip)..." -ForegroundColor Gray
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile("https://www.7-zip.org/a/7zr.exe", $7zrPath)
    }
    $7z = Get-Command $7zrPath
}
Write-Host "  ✓ 使用: $($7z.Source)" -ForegroundColor Green

# 5. 解压
Write-Host "[4/4] 解压到 _mpv/..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $MpvDir -Force | Out-Null
& $7z.Source x $archivePath "-o$MpvDir" -y | Out-Null

# 6. 清理多余文件
@('doc', 'installer') | ForEach-Object {
    $d = Join-Path $MpvDir $_
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
@('mpv-register.bat', 'mpv-unregister.bat', 'mpv-install.bat', 'mpv-uninstall.bat', 'updater.bat', 'updater.ps1', 'manual.pdf', 'mpbindings.png', 'mpv-icon.ico') | ForEach-Object {
    $f = Join-Path $MpvDir $_
    if (Test-Path $f) { Remove-Item -Force $f }
}

# 7. 验证
$mpvExe = Join-Path $MpvDir "mpv.exe"
if ((Test-Path $mpvExe) -and (Get-Item $mpvExe).Length -gt 1MB) {
    Write-Host "" -ForegroundColor Cyan
    Write-Host "╔══════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║  ✓ mpv 已就绪                     ║" -ForegroundColor Green
    Write-Host "║  $((Get-Item $mpvExe).Length / 1MB -as [int]) MB                     ║" -ForegroundColor Green
    Write-Host "║  $MpvDir  ║" -ForegroundColor Green
    Write-Host "╚══════════════════════════════════╝" -ForegroundColor Green
} else {
    Write-Host "[✗] mpv.exe 未找到或文件为空" -ForegroundColor Red
    exit 1
}