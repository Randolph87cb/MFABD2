param(
    [string]$TaskName = "BrownDust2DailyAutomation"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AutomationScript = Join-Path $PSScriptRoot "daily_automation.py"
$TaskRunner = Join-Path $PSScriptRoot "run_daily_task.ps1"
if (-not (Test-Path -LiteralPath $AutomationScript)) {
    throw "Automation script not found: $AutomationScript"
}
if (-not (Test-Path -LiteralPath $TaskRunner)) {
    throw "Task runner not found: $TaskRunner"
}

$PythonCommand = Get-Command python.exe -ErrorAction Stop
$PythonPath = $PythonCommand.Source
$PowerShellCommand = Get-Command powershell.exe -ErrorAction Stop
$PowerShellPath = $PowerShellCommand.Source

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$TaskRunner`" -PythonPath `"$PythonPath`""
$Action = New-ScheduledTaskAction `
    -Execute $PowerShellPath `
    -Argument $Arguments `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
$Principal = New-ScheduledTaskPrincipal `
    -UserId $Identity `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "BrownDust II daily free gacha and quick hunt automation." `
    -Force | Out-Null

$Task = Get-ScheduledTask -TaskName $TaskName
Write-Output "task_name=$($Task.TaskName)"
Write-Output "state=$($Task.State)"
Write-Output "user=$Identity"
Write-Output "trigger=AtLogOn"
Write-Output "python=$PythonPath"
Write-Output "script=$AutomationScript"
Write-Output "runner=$TaskRunner"
