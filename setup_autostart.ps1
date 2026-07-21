# setup_autostart.ps1
# Registers scheduler.py as a Windows Task Scheduler job that auto-starts at logon.
# Run once as Administrator (or current user) from E:\Trade:
#   powershell -ExecutionPolicy Bypass -File setup_autostart.ps1

$taskName    = "VNStock_Scheduler"
$pythonExe   = "C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe"
$scriptPath  = "E:\Trade\scheduler.py"
$workingDir  = "E:\Trade"
$logFile     = "E:\Trade\logs\scheduler_autostart.log"

# Remove old task if exists
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action  = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument $scriptPath `
    -WorkingDirectory $workingDir

# Trigger: at logon of current user
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# Also add a daily 07:50 trigger as failsafe in case session was already active
$trigger2 = New-ScheduledTaskTrigger -Daily -At "07:50"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 20) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($trigger, $trigger2) `
    -Settings $settings `
    -Principal $principal `
    -Description "VN Stock autonomous trading scheduler. Starts at logon and 07:50 daily." `
    -Force

Write-Host ""
Write-Host "Task '$taskName' registered successfully." -ForegroundColor Green
Write-Host "  Triggers: at logon + daily 07:50"
Write-Host "  To verify: Get-ScheduledTask -TaskName '$taskName'"
Write-Host "  To start now: Start-ScheduledTask -TaskName '$taskName'"
Write-Host "  To remove: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
