# Switches the daily run on or off. Double-click daily-on.cmd or daily-off.cmd.
# The run happens every day at the given time while you are signed in; if the computer
# was off or asleep at that time, it runs once it is back on.
param([switch]$Off, [string]$At = '06:00')

$name = 'Clipper daily run'

if ($Off) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host 'The daily run is switched off.'
    return
}

$script = Join-Path $PSScriptRoot 'run.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`" -Daily"
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "The daily run is switched on: every day at $At."
Write-Host "Clips appear in $(Join-Path $PSScriptRoot 'data\out'), a log of each run in $(Join-Path $PSScriptRoot 'data\logs')."
Write-Host 'To switch it off, double-click daily-off.cmd.'
