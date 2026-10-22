param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,
    [string]$CodexPath,
    [switch]$Check,
    [switch]$Force,
    [ValidateSet(
        "start",
        "quick_hunt",
        "free_gacha",
        "arena",
        "daily_claims",
        "task_rewards",
        "activity_rewards",
        "pass_rewards",
        "mail_rewards"
    )]
    [string]$ForcePhase
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Codex = & (Join-Path $PSScriptRoot "resolve_codex.ps1") -CodexPath $CodexPath
Write-Output "codex=$($Codex.Path)"
Write-Output "codex_version=$($Codex.Version)"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SupervisorScript = Join-Path $PSScriptRoot "daily_supervisor.py"
$SupervisorArguments = @(
    $SupervisorScript,
    "--project-root", $ProjectRoot,
    "--python-path", $PythonPath,
    "--codex-path", $Codex.Path,
    "--retention-days", "7",
    "--max-repair-rounds", "2"
)
if ($Check) {
    $SupervisorArguments += "--check"
}
if ($Force) {
    $SupervisorArguments += "--force"
}
if ($ForcePhase) {
    if ($Force) {
        throw "Force and ForcePhase cannot be used together."
    }
    $SupervisorArguments += @("--force-phase", $ForcePhase)
}

& $PythonPath @SupervisorArguments
$SupervisorExitCode = $LASTEXITCODE

if ($SupervisorExitCode -ne 0) {
    Write-Host ""
    Write-Host "Daily automation and recovery did not finish. See logs\supervisor." -ForegroundColor Red
}

exit $SupervisorExitCode
