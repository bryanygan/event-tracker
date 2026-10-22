# Removes the tasks created by install_tasks.ps1. Touches nothing else.
foreach ($name in "PhillyEvents-Pipeline", "PhillyEvents-Midday", "PhillyEvents-Dashboard") {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed $name"
    }
}
