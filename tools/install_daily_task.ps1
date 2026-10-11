param(
    [string]$TaskName = "BrownDust2DailyAutomation",
    [string]$At = "08:30",
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AutomationScript = Join-Path $PSScriptRoot "daily_automation.py"
$SupervisorScript = Join-Path $PSScriptRoot "daily_supervisor.py"
$TaskRunner = Join-Path $PSScriptRoot "run_daily_task.ps1"
if (-not (Test-Path -LiteralPath $AutomationScript)) {
    throw "Automation script not found: $AutomationScript"
}
if (-not (Test-Path -LiteralPath $SupervisorScript)) {
    throw "Supervisor script not found: $SupervisorScript"
}
if (-not (Test-Path -LiteralPath $TaskRunner)) {
    throw "Task runner not found: $TaskRunner"
}

$PythonCommand = Get-Command python.exe -ErrorAction Stop
$BootstrapPython = $PythonCommand.Source
$VenvRoot = Join-Path $ProjectRoot ".venv"
$PythonPath = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    & $BootstrapPython -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment: $VenvRoot"
    }
}
if (-not $SkipDependencyInstall) {
    & $PythonPath -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install Python dependencies."
    }
}

$CodexCommand = Get-Command codex.cmd -ErrorAction Stop
$CodexPath = $CodexCommand.Source
$PowerShellCommand = Get-Command powershell.exe -ErrorAction Stop
$PowerShellPath = $PowerShellCommand.Source

$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File `"$TaskRunner`" -PythonPath `"$PythonPath`" -CodexPath `"$CodexPath`""
$Action = New-ScheduledTaskAction `
    -Execute $PowerShellPath `
    -Argument $Arguments `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Principal = New-ScheduledTaskPrincipal `
    -UserId $Identity `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "BrownDust II daily automation with safe cleanup and Codex recovery." `
    -Force | Out-Null

$Task = Get-ScheduledTask -TaskName $TaskName
$TaskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Output "task_name=$($Task.TaskName)"
Write-Output "state=$($Task.State)"
Write-Output "user=$Identity"
Write-Output "trigger=Daily $At"
Write-Output "next_run=$($TaskInfo.NextRunTime)"
Write-Output "python=$PythonPath"
Write-Output "codex=$CodexPath"
Write-Output "script=$AutomationScript"
Write-Output "supervisor=$SupervisorScript"
Write-Output "runner=$TaskRunner"
