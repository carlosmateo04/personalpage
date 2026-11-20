# Clipper installer for Windows.
#
# Paste this one line into PowerShell:
#
#   irm https://raw.githubusercontent.com/carlosmateo04/personalpage/refs/heads/claude/quirky-ramanujan-pk7f77/clipper/install.ps1 | iex
#
# Everything goes into one folder in your user profile (default: clipper-run):
#   tools\   uv (installs Python), ffmpeg (video), deno (needed by the YouTube downloader), ant (Anthropic's sign-in tool, optional)
#   app\     the clipper code            venv\    Python and its packages
#   models\  the speech and sound models  data\    downloads, finished clips (data\out), ledger, reports
# Delete that folder to remove everything. Running the line again updates the code and
# keeps the data. Afterwards, use the Clipper shortcut on the desktop. In the folder:
#   Clipper.cmd    open the app              run.cmd        make clips without the app
#   daily-on.cmd   make clips every morning  daily-off.cmd  stop the daily run
#
# Optional environment variables:
#   CLIPPER_DIR       install somewhere else
#   CLIPPER_SKIP_RUN  set to 1 to install without opening the app
#   CLIPPER_FRESH     set to 1 to forget which videos were already looked at

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

    # Anthropic's own sign-in tool (ant), which "Sign in with Claude" in the app uses. It is optional:
    # without it the app still works with an API key or an AWS profile, so a failed download only prints a note.
    function Get-Ant {
        $dir = Join-Path $tools 'ant'
        $found = Get-ChildItem -Path $dir -Recurse -Filter 'ant.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { return $found.DirectoryName }
        try {
            $headers = @{ 'User-Agent' = 'clipper-installer'; 'Accept' = 'application/vnd.github+json' }
            if ($env:GITHUB_TOKEN) { $headers['Authorization'] = "Bearer $env:GITHUB_TOKEN" }
            $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/anthropics/anthropic-cli/releases/latest' -Headers $headers
            $names = @($release.assets | ForEach-Object { $_.name })
            $asset = $release.assets | Where-Object { $_.name -match 'windows' -and $_.name -match 'amd64|x86_64|x64' -and $_.name -match '\.zip$' } | Select-Object -First 1
            if (-not $asset) { throw "no Windows build in release $($release.tag_name) (files: $($names -join ', '))" }
            Write-Host "downloading ant $($release.tag_name) ($($asset.name))"
            $zip = Join-Path $tools 'ant.zip'
            Fetch $asset.browser_download_url $zip
            Expand-Archive -Path $zip -DestinationPath $dir -Force
            Remove-Item $zip
            $found = Get-ChildItem -Path $dir -Recurse -Filter 'ant.exe' | Select-Object -First 1
            if (-not $found) { throw "ant.exe was not in $($asset.name)" }
            return $found.DirectoryName
        } catch {
            Write-Host "Anthropic's sign-in tool could not be installed: $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host 'Clipper works without it: under Accounts, add a Claude API key instead of signing in. Running the installer again tries once more.'
            return $null
        }
    }

    function Invoke-Step([string]$what, [scriptblock]$command) {
        & $command
        if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
    }

    New-Item -ItemType Directory -Force -Path $tools, $data | Out-Null

    if ($env:CLIPPER_FRESH -eq '1') {
        # Forget which videos were already looked at, so they are considered again.
        $ledger = Join-Path $data 'state\ledger.json'
        if (Test-Path $ledger) { Remove-Item $ledger; Write-Host 'Starting fresh: the record of videos already looked at was cleared.' }
    }

    Step 'Tools: uv, ffmpeg, deno'
    $uvDir = Get-Tool 'uv' 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' 'uv.exe'
    $denoDir = Get-Tool 'deno' 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' 'deno.exe'
    $ffDir = Get-Tool 'ffmpeg' 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' 'ffmpeg.exe'
    $toolPath = "$uvDir;$denoDir;$ffDir"
    $antDir = Get-Ant
    if ($antDir) { $toolPath = "$toolPath;$antDir" }
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

    # Clipper.cmd opens the app; run.cmd makes clips without it; daily-on.cmd and daily-off.cmd switch the daily run.
    Copy-Item -Path (Join-Path $app 'windows\*') -Destination $root -Force
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Clipper.lnk'))
        $link.TargetPath = Join-Path $root 'Clipper.cmd'
        $link.WorkingDirectory = $root
        $link.WindowStyle = 7   # start minimised; the app itself opens in the browser
        $link.Description = 'Clipper: make GTA 6 clips and post them'
        $link.Save()
    } catch {
        Write-Host 'Could not create the desktop shortcut; open Clipper.cmd in the install folder instead.'
    }

    $env:CLIPPER_DATA = $data
    $env:CLIPPER_MODELS = Join-Path $root 'models'
    Push-Location $app
    try {
        Step 'Speech model (about 480 MB, first time only) and health check'
        Invoke-Step 'setup' { & $py -m clipper setup }
        Invoke-Step 'the health check' { & $py -m clipper doctor }

        if ($env:CLIPPER_SKIP_RUN -eq '1') {
            Write-Host ''
            Write-Host "Installed in $root. The app was not opened (CLIPPER_SKIP_RUN is set)." -ForegroundColor Green
            return
        }

        Write-Host ''
        Write-Host 'Installed. Opening Clipper in your browser.' -ForegroundColor Green
        Write-Host 'Click "Make clips now" there. Next time, use the Clipper shortcut on your desktop.'
        Start-Process -FilePath (Join-Path $root 'Clipper.cmd') -WorkingDirectory $root -WindowStyle Minimized
    } finally {
        Pop-Location
    }
}

Install-Clipper
