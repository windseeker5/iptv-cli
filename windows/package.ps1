# Build kdtv-windows.zip: kdtv.exe plus the programs it uses (FFmpeg, mpv,
# yt-dlp) and a short how-to. Run on Windows from the repository root, after
# `cargo build --release` in rust/:
#
#   pwsh windows/package.ps1
#
# GitHub does this for you (.github/workflows/windows.yml).
param(
    [string]$Exe = "rust/target/release/kdtv.exe",
    [string]$Out = "kdtv-windows.zip"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$work = Join-Path ([IO.Path]::GetTempPath()) "kdtv-package"
Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
$dir = Join-Path $work "kdtv"
New-Item -ItemType Directory -Force $dir | Out-Null
Copy-Item $Exe $dir

$headers = @{ "User-Agent" = "kdtv-package" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }

# FFmpeg and FFprobe: record, convert, check files (BtbN's builds).
Write-Host "FFmpeg..."
$ffZip = Join-Path $work "ffmpeg.zip"
Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile $ffZip
Expand-Archive $ffZip (Join-Path $work "ffmpeg")
Get-ChildItem (Join-Path $work "ffmpeg") -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $dir

# mpv: the player (shinchiro's builds, a .7z; 7-Zip is on GitHub's Windows machines).
Write-Host "mpv..."
$release = Invoke-RestMethod "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest" -Headers $headers
$asset = $release.assets | Where-Object { $_.name -match '^mpv-x86_64-\d{8}-git-.*\.7z$' } | Select-Object -First 1
if (-not $asset) { throw "No mpv build found in release $($release.tag_name)" }
$mpv7z = Join-Path $work "mpv.7z"
Invoke-WebRequest $asset.browser_download_url -OutFile $mpv7z
$mpvDir = Join-Path $work "mpv"
& 7z x $mpv7z "-o$mpvDir" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw "7-Zip could not open $($asset.name)" }
$mpvExe = Get-ChildItem $mpvDir -Recurse -Filter mpv.exe | Select-Object -First 1
if (-not $mpvExe) { throw "mpv.exe is not in $($asset.name)" }
Copy-Item $mpvExe.FullName $dir
Get-ChildItem $mpvExe.DirectoryName -Filter *.dll | Copy-Item -Destination $dir

# yt-dlp: YouTube search and downloads.
Write-Host "yt-dlp..."
Invoke-WebRequest "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" -OutFile (Join-Path $dir "yt-dlp.exe")

Copy-Item (Join-Path $PSScriptRoot "README.txt") $dir
Remove-Item $Out -ErrorAction SilentlyContinue
Compress-Archive -Path $dir -DestinationPath $Out
Get-ChildItem $dir | Format-Table Name, Length
Write-Host "Made $Out ($([math]::Round((Get-Item $Out).Length / 1MB)) MB)"
