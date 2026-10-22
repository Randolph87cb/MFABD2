param(
    [string]$CodexPath
)

$ErrorActionPreference = "Stop"

# Long-lived desktop/task processes may still have the PATH from before a CLI update.
$MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($MachinePath -or $UserPath) {
    $env:Path = [Environment]::ExpandEnvironmentVariables("$MachinePath;$UserPath")
}

if (-not $CodexPath) {
    $CodexPath = Get-Command codex -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -match '\.(exe|cmd)$' } |
        Select-Object -First 1 -ExpandProperty Source
}
if (-not $CodexPath -or -not (Test-Path -LiteralPath $CodexPath -PathType Leaf)) {
    throw "Codex CLI not found on PATH. Install the CLI or specify -CodexPath."
}
$ResolvedPath = (Resolve-Path -LiteralPath $CodexPath).Path
$VersionOutput = & $ResolvedPath --version
if ($LASTEXITCODE -ne 0) {
    throw "Cannot start Codex CLI: $CodexPath"
}
$Version = ($VersionOutput | Out-String).Trim()
if ($Version -notmatch '^codex-cli\s+\S+') {
    throw "Unexpected Codex CLI version output: $Version"
}
[PSCustomObject]@{
    Path = $ResolvedPath
    Version = $Version
}
