param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AutomationScript = Join-Path $PSScriptRoot "daily_automation.py"

$AutomationArguments = @($AutomationScript, "--scheduled", "--project-root", $ProjectRoot)
if ($Force) {
    $AutomationArguments += "--force"
}

& $PythonPath @AutomationArguments
$AutomationExitCode = $LASTEXITCODE

if ($AutomationExitCode -ne 0) {
    Write-Host ""
    Write-Host "每日任务没有执行成功。中文日志保留在上方，标注网站会自动打开。" -ForegroundColor Red
    Write-Host "请在“每日错误”中标记有问题的截图并填写原因。" -ForegroundColor Yellow
    Write-Host "此窗口会保持打开；查看完后请直接关闭窗口。" -ForegroundColor Cyan
    while ($true) {
        Start-Sleep -Seconds 30
    }
}

exit $AutomationExitCode
