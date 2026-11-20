# Brings Clipper up to date: runs the installer again, which fetches the newest code, keeps the data folder
# (clips, ledger, settings, accounts) and opens the app when it is done. Started by "Update now" in the app.
$root = $PSScriptRoot
Start-Sleep -Seconds 3   # the app that started this is closing
$env:CLIPPER_DIR = $root
$installer = Join-Path $root 'app\install.ps1'
try {
    $fresh = Join-Path $env:TEMP 'clipper-install.ps1'
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/carlosmateo04/personalpage/refs/heads/claude/quirky-ramanujan-pk7f77/clipper/install.ps1' -OutFile $fresh -UseBasicParsing
    $installer = $fresh
} catch {
    Write-Host 'The newest installer could not be fetched; using the one already here.'
}
Get-Content -Raw $installer | Invoke-Expression
