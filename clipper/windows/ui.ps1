# Opens the Clipper app in your browser. Double-click Clipper.cmd or the desktop shortcut.
# Leave this window open while you use the app; closing it quits Clipper.
$root = $PSScriptRoot
$py = Join-Path $root 'venv\Scripts\python.exe'
$data = Join-Path $root 'data'
$dirs = Get-ChildItem -Path (Join-Path $root 'tools') -Recurse -Include uv.exe, deno.exe, ffmpeg.exe |
    ForEach-Object { $_.DirectoryName } | Select-Object -Unique
$env:PATH = ($dirs -join ';') + ';' + $env:PATH
$env:CLIPPER_DATA = $data
$env:CLIPPER_MODELS = Join-Path $root 'models'

$options = @()
if ($env:CLIPPER_CONFIG) { $options += @('--config', $env:CLIPPER_CONFIG) }
$app = @('ui')
if ($env:CLIPPER_NO_BROWSER -eq '1') { $app += '--no-browser' }
if ($env:CLIPPER_UI_TOKEN) { $app += @('--token', $env:CLIPPER_UI_TOKEN) }

Set-Location (Join-Path $root 'app')
& $py -m clipper @options @app
