# Switches the daily run on or off. Double-click daily-on.cmd or daily-off.cmd.
# The run happens every day at the given time while you are signed in; if the computer
# was off or asleep at that time, it runs once it is back on.
param([switch]$Off, [string]$At = '06:00')

$name = 'Clipper daily run'
$poster = 'Clipper posting'

if ($Off) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $poster -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host 'The daily run and the posting task are switched off.'
    return
}

$script = Join-Path $PSScriptRoot 'run.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`" -Daily"
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

# The poster: every 15 minutes it sends the posts whose time has come (TikTok and Instagram cannot hold a
# post until later themselves) and reads back what became of the ones that went out. It runs without a
# window, does nothing when nothing is due, and writes what it sent to data\logs\posting.log.
$data = Join-Path $PSScriptRoot 'data'
$arguments = "-m clipper --data `"$data`""
if ($env:CLIPPER_CONFIG) { $arguments += " --config `"$($env:CLIPPER_CONFIG)`"" }
elseif (Test-Path (Join-Path $data 'config.toml')) { $arguments += " --config `"$(Join-Path $data 'config.toml')`"" }
$arguments += " post --log `"$(Join-Path $data 'logs\posting.log')`""
$postAction = New-ScheduledTaskAction -Execute (Join-Path $PSScriptRoot 'venv\Scripts\pythonw.exe') -Argument $arguments -WorkingDirectory (Join-Path $PSScriptRoot 'app')
$postTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 7300)
$postSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $poster -Action $postAction -Trigger $postTrigger -Settings $postSettings -Force | Out-Null

Write-Host "The daily run is switched on: every day at $At."
Write-Host 'Posts whose time has come are sent every 15 minutes while this computer is on.'

Write-Host "Clips appear in $(Join-Path $PSScriptRoot 'data\out'), a log of each run in $(Join-Path $PSScriptRoot 'data\logs')."
Write-Host 'To switch it off, double-click daily-off.cmd.'
