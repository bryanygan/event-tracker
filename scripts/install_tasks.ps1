# Registers three Windows Task Scheduler tasks for the current user:
#   PhillyEvents-Pipeline   fetch + detect + draft at 6:30 AM and 4:30 PM
#   PhillyEvents-Midday     quick fetch + detect at 11:30 AM (no LLM, no paid lookups); turn off with
#                           Disable-ScheduledTask -TaskName PhillyEvents-Midday
#   PhillyEvents-Dashboard  local dashboard at http://127.0.0.1:8765, started at logon
#
# Both run windowless (pythonw), at below-normal priority, only while you're logged on,
# so they never compete with Homebridge for CPU. Undo with scripts\uninstall_tasks.ps1.
#
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Missing $pythonw. Create the venv first (see README)." }

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -Priority 7 `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

# Pipeline: twice a day. Presales/on-sales usually open at 10 AM local, so a morning and
# afternoon run catches anything opening in the next 72h with at least a day's notice.
$pipelineAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m tracker.run" -WorkingDirectory $root
$triggers = @(
    (New-ScheduledTaskTrigger -Daily -At "6:30AM"),
    (New-ScheduledTaskTrigger -Daily -At "4:30PM")
)
Register-ScheduledTask -TaskName "PhillyEvents-Pipeline" -Action $pipelineAction -Trigger $triggers `
    -Settings $settings -Principal $principal -Description "Philly events: fetch, detect, draft" -Force | Out-Null

# Midday: tours and presales are often announced late morning, after the 6:30 run. Data + detectors only, so
# Ollama never starts. If another run is going, it logs "skipped" and exits (lock in tracker/run.py).
$middaySettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -Priority 7 `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$middayAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m tracker.run --midday" -WorkingDirectory $root
Register-ScheduledTask -TaskName "PhillyEvents-Midday" -Action $middayAction `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "11:30AM") `
    -Settings $middaySettings -Principal $principal -Description "Philly events: quick midday check, no drafts" -Force | Out-Null

# Dashboard: always-on while logged in; restart if it crashes.
$dashSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -Priority 7 `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$dashAction = New-ScheduledTaskAction -Execute $pythonw -Argument "-m tracker.api" -WorkingDirectory $root
Register-ScheduledTask -TaskName "PhillyEvents-Dashboard" -Action $dashAction `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) `
    -Settings $dashSettings -Principal $principal -Description "Philly events dashboard on 127.0.0.1:8765" -Force | Out-Null

Start-ScheduledTask -TaskName "PhillyEvents-Dashboard"
Write-Host "Installed. Dashboard: http://127.0.0.1:8765  (pipeline runs 6:30 AM and 4:30 PM, quick check 11:30 AM)"
