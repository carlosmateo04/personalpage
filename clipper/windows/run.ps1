# Makes today's clips: finds and downloads the videos, cuts the highlights, renders them.
# Double-click run.cmd to use it. The daily schedule calls it with -Daily, which writes
# to a log file instead of the screen and tidies up old files afterwards.
param([switch]$Daily)

$root = $PSScriptRoot
$py = Join-Path $root 'venv\Scripts\python.exe'
$data = Join-Path $root 'data'
$dirs = Get-ChildItem -Path (Join-Path $root 'tools') -Recurse -Include uv.exe, deno.exe, ffmpeg.exe |
    ForEach-Object { $_.DirectoryName } | Select-Object -Unique
$env:PATH = ($dirs -join ';') + ';' + $env:PATH
$env:CLIPPER_DATA = $data
$env:CLIPPER_MODELS = Join-Path $root 'models'

# Your own settings, if you have made any, win over the ones that come with the code:
# copy app\config.toml to data\config.toml and edit the copy.
$config = @()
if ($env:CLIPPER_CONFIG) {
    $config = @('--config', $env:CLIPPER_CONFIG)
} elseif (Test-Path (Join-Path $data 'config.toml')) {
    $config = @('--config', (Join-Path $data 'config.toml'))
}

Set-Location (Join-Path $root 'app')

function Invoke-Clipper {
    & $py -m clipper @config @args
}

if ($Daily) {
    $logs = Join-Path $data 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $log = Join-Path $logs ((Get-Date -Format 'yyyy-MM-dd') + '.log')
    & {
        "---- daily run started $(Get-Date -Format 'yyyy-MM-dd HH:mm') ----"
        Invoke-Clipper status
        Invoke-Clipper run   # plan, automatic review (when a Claude key is saved), render
        Invoke-Clipper cleanup
        "---- daily run finished $(Get-Date -Format 'yyyy-MM-dd HH:mm') ----"
    } 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $log -Append -Encoding utf8
} else {
    Invoke-Clipper status
    Invoke-Clipper run
    $out = Join-Path $data 'out'
    if (Test-Path $out) { Start-Process explorer.exe $out }
}
