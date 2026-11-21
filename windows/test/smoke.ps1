# The Windows zip, tested end to end against a pretend provider (fake_provider.py):
# login and lists, record now and stop, a schedule kdtv starts itself, a movie
# download. Run on Windows with Python, from the repository root:
#
#   pwsh windows/test/smoke.ps1 -Zip kdtv-windows.zip
param([string]$Zip = "kdtv-windows.zip")
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$ProgressPreference = "SilentlyContinue"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Check($ok, $text) {
    if (-not $ok) { throw "FAILED: $text" }
    Write-Host "ok  $text" -ForegroundColor Green
}

$root = Join-Path ([IO.Path]::GetTempPath()) "kdtv-smoke"
Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive $Zip $root
$app = Join-Path $root "kdtv"
$kdtv = Join-Path $app "kdtv.exe"
function Kd { & $kdtv @args }
function KdJson { ((& $kdtv --json @args) -join "`n") | ConvertFrom-Json }

# A computer of its own: kdtv's files in LOCALAPPDATA, the Videos folder in USERPROFILE.
$env:LOCALAPPDATA = Join-Path $root "appdata"
$env:USERPROFILE = Join-Path $root "user"
Remove-Item Env:HOME -ErrorAction SilentlyContinue
Remove-Item Env:KDTV_CONFIG -ErrorAction SilentlyContinue
$videos = Join-Path $env:USERPROFILE "Videos\kdtv"
$home_dir = Join-Path $env:LOCALAPPDATA "kdtv"

Step "The program starts"
Kd --version
$check = KdJson check
Check ($check | Where-Object { $_.what -eq "ffmpeg" -and $_.ok }) "ffmpeg found next to kdtv.exe"
Check ($check | Where-Object { $_.what -eq "mpv" -and $_.ok }) "mpv found next to kdtv.exe"
Check ($check | Where-Object { $_.what -eq "yt-dlp" -and $_.ok }) "yt-dlp found next to kdtv.exe"
Check (-not ($check | Where-Object { $_.what -eq "systemctl" })) "no systemd needed"

Step "A pretend provider"
$here = $PSScriptRoot
New-Item -ItemType Directory -Force (Join-Path $here "files") | Out-Null
& (Join-Path $app "ffmpeg.exe") -loglevel error -y -f lavfi -i testsrc=size=640x360:rate=25 -f lavfi -i sine=frequency=440 -t 20 -c:v libx264 -preset ultrafast -c:a aac -shortest (Join-Path $here "files\movie.mp4")
$env:FFMPEG = Join-Path $app "ffmpeg.exe"
$provider = Start-Process python -ArgumentList (Join-Path $here "fake_provider.py"), "18555" -PassThru -WindowStyle Hidden
Start-Sleep 2

try {
    Step "Login and lists (what the setup screen saves and downloads)"
    New-Item -ItemType Directory -Force $home_dir | Out-Null
    Set-Content (Join-Path $home_dir ".env") "IPTV_SERVER_URL=http://127.0.0.1:18555`nIPTV_USERNAME=son`nIPTV_PASSWORD=secret"
    Kd refresh --everything
    $channels = KdJson channels search test
    Check ($channels.channels.Count -ge 1) "channel list downloaded"

    Step "Record now, then stop"
    $started = KdJson record 101 --minutes 2
    Write-Host "  $($started.unit) -> $($started.output)"
    Start-Sleep 10
    $status = KdJson status
    Check ($status.recording.Count -eq 1) "the recording runs in the background"
    $file = Get-Item $started.output
    Check ($file.Length -gt 0 -and $file.DirectoryName -eq $videos) "the file grows in Videos\kdtv"
    Kd stop $started.unit
    Start-Sleep 2
    Check ((KdJson status).recording.Count -eq 0) "stopped"
    Start-Sleep 1
    $size = (Get-Item $started.output).Length
    Start-Sleep 3
    Check ((Get-Item $started.output).Length -eq $size -and $size -gt 0) "nothing writes to the file any more, and it is kept"

    Step "A schedule kdtv starts by itself"
    $at = (Get-Date).AddMinutes(1)
    $sched = KdJson schedule add 101 --time $at.ToString("HH:mm") --minutes 3
    Check (-not (Test-Path (Join-Path $env:USERPROFILE ".config\systemd"))) "no systemd timer written"
    $nothing = KdJson start-due
    Check ($nothing.started.Count -eq 0) "nothing starts before its time"
    while ((Get-Date) -lt $at.Date.AddHours($at.Hour).AddMinutes($at.Minute).AddSeconds(2)) { Start-Sleep 1 }
    $due = KdJson start-due
    Check ($due.started.Count -eq 1) "started at its time"
    $again = KdJson start-due
    Check ($again.started.Count -eq 0) "started once only"
    Start-Sleep 8
    $status = KdJson status
    Check ($status.recording.Count -eq 1) "the scheduled recording runs"
    Kd stop "kdcli-rec-$($sched.id)"
    Start-Sleep 2
    Check ((KdJson status).recording.Count -eq 0) "stopped"

    Step "Download a movie"
    Kd download movie 201
    $done = $false
    for ($i = 0; $i -lt 60 -and -not $done; $i++) {
        Start-Sleep 2
        $job = (KdJson downloads) | Select-Object -First 1
        Write-Host "  $($job.state) $($job.bytes) bytes"
        $done = $job.state -in "done", "failed"
    }
    Check ($job.state -eq "done") "downloaded"
    $movie = Get-Item (Join-Path $videos "Downloads\Test_Movie.mp4")
    Check ($movie.Length -eq (Get-Item (Join-Path $here "files\movie.mp4")).Length) "the whole movie is in Videos\kdtv\Downloads"

    Write-Host "`nAll good." -ForegroundColor Green
}
finally {
    Stop-Process -Id $provider.Id -Force -ErrorAction SilentlyContinue
    Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $logs = Join-Path $home_dir "data\jobs"
    if (Test-Path $logs) {
        Get-ChildItem $logs -Filter *.log | ForEach-Object { Write-Host "--- $($_.Name)"; Get-Content $_.FullName -Tail 15 }
    }
}
