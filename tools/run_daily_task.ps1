param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AutomationScript = Join-Path $PSScriptRoot "daily_automation.py"

& $PythonPath $AutomationScript --scheduled --project-root $ProjectRoot
$AutomationExitCode = $LASTEXITCODE

if ($AutomationExitCode -ne 0) {
    Write-Host ""
    Write-Host "每日任务没有执行成功。中文日志保留在上方，标注网站会自动打开。" -ForegroundColor Red
    Write-Host "请在“每日错误”中标记有问题的截图并填写原因。" -ForegroundColor Yellow
    Read-Host "查看完日志后，按 Enter 关闭此窗口"
}

exit $AutomationExitCode
