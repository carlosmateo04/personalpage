# Clipper installer for Windows.
#
# Paste this one line into PowerShell:
#
#   irm https://raw.githubusercontent.com/carlosmateo04/personalpage/refs/heads/claude/quirky-ramanujan-pk7f77/clipper/install.ps1 | iex
#
# Everything goes into one folder in your user profile (default: clipper-run):
#   tools\   uv (installs Python), ffmpeg (video), deno (needed by the YouTube downloader)
#   app\     the clipper code            venv\    Python and its packages
#   models\  the speech model            data\    downloads, finished clips (data\out), ledger, reports
# Delete that folder to remove everything. Running the line again updates the code and
# keeps the data. In that folder afterwards:
#   run.cmd        make clips again          daily-on.cmd   make them every morning by itself
#   daily-off.cmd  stop the daily run
#
# Optional environment variables:
#   CLIPPER_DIR       install somewhere else
#   CLIPPER_SKIP_RUN  set to 1 to install without making clips

function Install-Clipper {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # the progress bar makes downloads many times slower
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $repo = 'carlosmateo04/personalpage'
    $branch = 'claude/quirky-ramanujan-pk7f77'
    if ($env:CLIPPER_DIR) { $root = $env:CLIPPER_DIR } else { $root = Join-Path $HOME 'clipper-run' }
    $tools = Join-Path $root 'tools'
    $app = Join-Path $root 'app'
    $data = Join-Path $root 'data'
    $venv = Join-Path $root 'venv'
    $py = Join-Path $venv 'Scripts\python.exe'

    function Step([string]$text) {
        Write-Host ''
        Write-Host "== $text ==" -ForegroundColor Cyan
    }

    function Fetch([string]$url, [string]$dest) {
        for ($try = 1; $try -le 4; $try++) {
            try {
                Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
                return
            } catch {
                if ($try -eq 4) { throw }
                Write-Host "download interrupted, trying again"
                Start-Sleep -Seconds (3 * $try)
            }
        }
    }

    # Download a zipped tool once and return the folder that holds its program.
    function Get-Tool([string]$name, [string]$url, [string]$exe) {
        $dir = Join-Path $tools $name
        $found = Get-ChildItem -Path $dir -Recurse -Filter $exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $found) {
            Write-Host "downloading $name"
            $zip = Join-Path $tools "$name.zip"
            Fetch $url $zip
            Expand-Archive -Path $zip -DestinationPath $dir -Force
            Remove-Item $zip
            $found = Get-ChildItem -Path $dir -Recurse -Filter $exe | Select-Object -First 1
        }
        if (-not $found) { throw "$exe was not found after unpacking $name" }
        return $found.DirectoryName
    }

    function Invoke-Step([string]$what, [scriptblock]$command) {
        & $command
        if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
    }

    New-Item -ItemType Directory -Force -Path $tools, $data | Out-Null

    Step 'Tools: uv, ffmpeg, deno'
    $uvDir = Get-Tool 'uv' 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' 'uv.exe'
    $denoDir = Get-Tool 'deno' 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' 'deno.exe'
    $ffDir = Get-Tool 'ffmpeg' 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' 'ffmpeg.exe'
    $toolPath = "$uvDir;$denoDir;$ffDir"
    $env:PATH = "$toolPath;$env:PATH"

    Step 'Clipper code'
    $zip = Join-Path $root 'code.zip'
    $unpacked = Join-Path $root 'code'
    Fetch "https://github.com/$repo/archive/refs/heads/$branch.zip" $zip
    if (Test-Path $unpacked) { Remove-Item -Recurse -Force $unpacked }
    Expand-Archive -Path $zip -DestinationPath $unpacked -Force
    Remove-Item $zip
    $source = Get-ChildItem -Path $unpacked -Directory | Select-Object -First 1
    if (Test-Path $app) { Remove-Item -Recurse -Force $app }
    Move-Item -Path (Join-Path $source.FullName 'clipper') -Destination $app
    Remove-Item -Recurse -Force $unpacked

    Step 'Python and its packages'
    if (-not (Test-Path $py)) {
        Invoke-Step 'creating the Python environment' { uv venv --python 3.12 $venv }
    }
    Invoke-Step 'installing the Python packages' { uv pip install --python $py -r (Join-Path $app 'requirements.txt') }

    # Scripts for later: run.cmd makes clips again, daily-on.cmd and daily-off.cmd switch the daily run.
    Copy-Item -Path (Join-Path $app 'windows\*') -Destination $root -Force

    $env:CLIPPER_DATA = $data
    $env:CLIPPER_MODELS = Join-Path $root 'models'
    Push-Location $app
    try {
        Step 'Speech model (about 480 MB, first time only) and health check'
        Invoke-Step 'setup' { & $py -m clipper setup }
        Invoke-Step 'the health check' { & $py -m clipper doctor }

        if ($env:CLIPPER_SKIP_RUN -eq '1') {
            Write-Host ''
            Write-Host "Installed in $root. Nothing was downloaded or clipped (CLIPPER_SKIP_RUN is set)." -ForegroundColor Green
            return
        }

        Step 'Finding and downloading gameplay-only GTA 6 videos, picking the highlights'
        Invoke-Step 'planning the clips' { & $py -m clipper plan }

        Step 'Rendering the clips'
        & $py -m clipper execute   # exit code 1 here means some clips failed; the report says which

        $out = Join-Path $data 'out'
        Write-Host ''
        Write-Host "Done. Clips and their post text are in $out" -ForegroundColor Green
        Write-Host "To make clips again later, double-click run.cmd in $root"
        Write-Host "To have it run by itself every morning, double-click daily-on.cmd in $root"
        if (Test-Path $out) { Start-Process explorer.exe $out }
    } finally {
        Pop-Location
    }
}

Install-Clipper
