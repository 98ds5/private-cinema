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

# 获取最新 tag 与资源名
# 注意: 上游 asset 文件名里带构建的 git hash (mpv-x86_64-<日期>-git-<hash>.7z),
# 每次上游发版都会变, 写死必然 404 —— 之前 CI 从 2026-09-10 起一直红就是这个原因。
# 所以这里一律从 API 返回的 assets 列表里按正则挑, 不再拼文件名。
Write-Host "[1/5] 查询最新发布..." -ForegroundColor Cyan
try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest" `
        -Headers @{ "User-Agent" = "pwsh/7.0" }
} catch {
    Write-Host "[✗] 查询上游发布失败: $_" -ForegroundColor Red
    exit 1
}
$tag = $rel.tag_name
# 只认 x86_64 的普通构建: 排除 mpv-dev-* 与 *-x86_64-v3-*
$mpvAsset = $rel.assets | Where-Object { $_.name -match '^mpv-x86_64-\d{8}-git-[0-9a-f]+\.7z$' } | Select-Object -First 1
if (-not $mpvAsset) {
    Write-Host "[✗] 在 $tag 里找不到预期的 mpv 资源" -ForegroundColor Red
    Write-Host "    现有资源: $($rel.assets.name -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "  最新 tag: $tag" -ForegroundColor Gray
Write-Host "  mpv    : $($mpvAsset.name)" -ForegroundColor Gray

# ffmpeg/ffprobe 另找源: shinchiro 的 ffmpeg-x86_64-*.7z 里**只有 ffmpeg.exe**,
# 没有 ffprobe.exe —— 而"参数解析"整条链路靠 ffprobe, 少了它 app 只能入库不能解析。
# 改用 BtbN/FFmpeg-Builds 的 win64-gpl 包 (bin/ 下 ffmpeg + ffprobe 齐全),
# 它拿不到时再退回 gyan.dev 的 release-essentials 包。
$ffUrl = $null
try {
    $ffRel = Invoke-RestMethod -Uri "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest" `
        -Headers @{ "User-Agent" = "pwsh/7.0" }
    $ffAsset = $ffRel.assets | Where-Object { $_.name -match '^ffmpeg-master-latest-win64-gpl\.zip$' } | Select-Object -First 1
    if ($ffAsset) { $ffUrl = $ffAsset.browser_download_url }
} catch { }
if (-not $ffUrl) {
    $ffUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
}
Write-Host "  ffmpeg : $ffUrl" -ForegroundColor Gray

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
$mpvUrl = $mpvAsset.browser_download_url
$mpvArc = Join-Path $TmpDir "mpv.7z"
if (-not (Test-Path $mpvArc)) {
    Write-Host "[3/5] 下载 mpv..." -ForegroundColor Cyan
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($mpvUrl, $mpvArc)
}
Write-Host "  解压 mpv..."
& $7z.Source x $mpvArc "-o$ToolsDir" -y | Out-Null

# 下载 & 解压 ffmpeg/ffprobe
$ffArc = Join-Path $TmpDir "ffmpeg.zip"
if (-not (Test-Path $ffArc)) {
    Write-Host "[4/5] 下载 ffmpeg/ffprobe..." -ForegroundColor Cyan
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($ffUrl, $ffArc)
}
Write-Host "  解压 ffmpeg..."
# 注意: 7zr.exe 是 7-Zip 精简版, 只能解 .7z —— 上游给的是 .zip, 必须用 Expand-Archive
if ($ffArc -like "*.zip") {
    Expand-Archive -Path $ffArc -DestinationPath "$TmpDir\ff_out" -Force
} else {
    & $7z.Source x $ffArc "-o$TmpDir\ff_out" -y | Out-Null
}
# 从包里捞 ffmpeg.exe / ffprobe.exe（不同包目录层级不同: bin/ 或直接根目录，递归找）
@('ffmpeg.exe', 'ffprobe.exe') | ForEach-Object {
    $src = Get-ChildItem "$TmpDir\ff_out" -Recurse -Filter $_ | Select-Object -First 1
    if ($src) { Copy-Item $src.FullName -Destination (Join-Path $ToolsDir $_) -Force }
}

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
@('mpv.exe', 'ffmpeg.exe', 'ffprobe.exe') | ForEach-Object {
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