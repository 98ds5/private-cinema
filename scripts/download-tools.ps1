#!/usr/bin/env pwsh
<#
.SYNOPSIS
    下载并解压 mpv + ffmpeg/ffprobe Windows x86_64 构建包到 _tools/ 目录
.DESCRIPTION
    从 shinchiro/mpv-winbuild-cmake 获取最新发布版。
    需要 7z（7-Zip）解压；如果系统没有，会自动下载便携版 7zr.exe。
.LINK
    https://github.com/shinchiro/mpv-winbuild-cmake/releases
#>
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectDir = Split-Path -Parent $ScriptDir
$ToolsDir = Join-Path $ProjectDir "_tools"
$TmpDir = Join-Path $env:TEMP "private-cinema-dl"
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

# 获取最新 tag
Write-Host "[1/5] 查询最新发布..." -ForegroundColor Cyan
try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest" `
        -Headers @{ "User-Agent" = "pwsh/7.0" }
    $tag = $rel.tag_name
    Write-Host "  最新 tag: $tag" -ForegroundColor Gray
} catch {
    Write-Host "[!] API 查询失败: $_" -ForegroundColor Yellow
    $tag = "20260903"
}

# 找 7z
Write-Host "[2/5] 准备解压工具..." -ForegroundColor Cyan
$7z = Get-Command "7z.exe" -ErrorAction SilentlyContinue
if (-not $7z) {
    $7zrPath = Join-Path $TmpDir "7zr.exe"
    if (-not (Test-Path $7zrPath)) {
        Write-Host "  下载 7zr (便携版 7-Zip)..." -ForegroundColor Gray
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile("https://www.7-zip.org/a/7zr.exe", $7zrPath)
    }
    $7z = Get-Command $7zrPath
}
Write-Host "  ✓ 使用: $($7z.Source)" -ForegroundColor Green

# 下载 & 解压 mpv
$mpvUrl = "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/$tag/mpv-x86_64-$tag-git-69e63f425a.7z"
$mpvArc = Join-Path $TmpDir "mpv.7z"
if (-not (Test-Path $mpvArc)) {
    Write-Host "[3/5] 下载 mpv..." -ForegroundColor Cyan
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($mpvUrl, $mpvArc)
}
Write-Host "  解压 mpv..."
& $7z.Source x $mpvArc "-o$ToolsDir" -y | Out-Null

# 下载 & 解压 ffmpeg
$ffUrl = "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/$tag/ffmpeg-x86_64-git-9fc8c785e.7z"
$ffArc = Join-Path $TmpDir "ffmpeg.7z"
if (-not (Test-Path $ffArc)) {
    Write-Host "[4/5] 下载 ffmpeg/ffprobe..." -ForegroundColor Cyan
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($ffUrl, $ffArc)
}
Write-Host "  解压 ffmpeg..."
& $7z.Source x $ffArc "-o$TmpDir\ff_out" -y | Out-Null
# 从 ffmpeg 包里复制出 ffmpeg.exe（同一包里还有 ffprobe 时一并复制）
Get-ChildItem "$TmpDir\ff_out" -Recurse -Filter "ffmpeg.exe" | Copy-Item -Destination (Join-Path $ToolsDir "ffmpeg.exe") -Force

# 清理
Write-Host "[5/5] 清理多余文件..." -ForegroundColor Cyan
@('doc', 'installer') | ForEach-Object {
    $d = Join-Path $ToolsDir $_
    if (Test-Path $d) { Remove-Item -Recurse -Force $d }
}
@('mpv-register.bat', 'mpv-unregister.bat', 'mpv-install.bat', 'mpv-uninstall.bat', 'updater.bat', 'updater.ps1', 'manual.pdf', 'mpbindings.png', 'mpv-icon.ico') | ForEach-Object {
    $f = Join-Path $ToolsDir $_
    if (Test-Path $f) { Remove-Item -Force $f }
}
Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue

# 验证
$ok = $true
@('mpv.exe', 'ffmpeg.exe') | ForEach-Object {
    $f = Join-Path $ToolsDir $_
    if (-not (Test-Path $f) -or (Get-Item $f).Length -lt 1MB) {
        Write-Host "[✗] $_ 缺失" -ForegroundColor Red
        $ok = $false
    }
}
if ($ok) {
    Write-Host ""
    Write-Host "╔══════════════════════════════════╗" -ForegroundColor Green
    Write-Host "║  ✓ 工具已就绪                      ║" -ForegroundColor Green
    Write-Host "║  $ToolsDir  ║" -ForegroundColor Green
    Write-Host "╚══════════════════════════════════╝" -ForegroundColor Green
} else {
    Write-Host "[✗] 部分工具下载失败" -ForegroundColor Red
    exit 1
}